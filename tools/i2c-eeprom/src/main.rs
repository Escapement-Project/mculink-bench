//! A 24C32-style I2C EEPROM at 0x50 on the LP-MSPM0L1306: I2C0, SDA on PA0, SCL on PA1.
#![no_std]
#![no_main]

use core::sync::atomic::{AtomicU32, Ordering};

use defmt::{debug, info, warn};
use defmt_rtt as _;
use embassy_executor::Spawner;
use embassy_mspm0::i2c::{Address, BusSpeed, Config};
use embassy_mspm0::i2c_target::{self, Command, I2cTarget, ReadStatus};
use embassy_mspm0::peripherals::I2C0;
use embassy_mspm0::{bind_interrupts, i2c};
use embassy_time::{Duration, Timer};
use i2c_eeprom::Eeprom;
use panic_probe as _;
use static_cell::ConstStaticCell;

bind_interrupts!(struct Irqs {
    I2C0 => i2c::InterruptHandler<I2C0>;
});

const ADDRESS: u8 = 0x50;

/// The 24C32's longest write cycle. The address goes unanswered this long after a write's STOP.
const WRITE_CYCLE: Duration = Duration::from_millis(5);

/// The longest write taken whole, word address included. A longer one is refused partway.
const WRITE_MAX: usize = 2 + 512;

/// The L1306's I2C FIFO depth (SYS_FENTRIES). `respond_to_read` misreports any response longer
/// than the FIFO: it queues the next chunk on every wake before it looks for a STOP, and it never
/// consumes the request that asked for the second chunk, so a read that ends cleanly comes back as
/// `NeedMoreBytes`. `READ_CHUNK=<n>` at build time overrides it, only to reproduce that.
const READ_CHUNK: usize = match option_env!("READ_CHUNK") {
    Some(n) => parse(n),
    None => 8,
};

const fn parse(digits: &str) -> usize {
    let digits = digits.as_bytes();
    assert!(!digits.is_empty(), "READ_CHUNK is empty");
    let mut value = 0;
    let mut i = 0;
    while i < digits.len() {
        assert!(digits[i].is_ascii_digit(), "READ_CHUNK is not a number");
        value = value * 10 + (digits[i] - b'0') as usize;
        i += 1;
    }
    assert!(value > 0, "READ_CHUNK is zero");
    value
}

static EEPROM: ConstStaticCell<Eeprom> = ConstStaticCell::new(Eeprom::new());

/// Counters for a debugger to read by symbol. One task writes them.
#[repr(C)]
pub struct Stats {
    /// Writes that stored data, each followed by a write cycle.
    writes: AtomicU32,
    /// Writes that set the address only.
    address_writes: AtomicU32,
    /// Reads, with or without an address written first.
    reads: AtomicU32,
    bytes_read: AtomicU32,
    /// Driver errors; each resets the target.
    errors: AtomicU32,
}

#[unsafe(no_mangle)]
pub static EEPROM_STATS: Stats = Stats {
    writes: AtomicU32::new(0),
    address_writes: AtomicU32::new(0),
    reads: AtomicU32::new(0),
    bytes_read: AtomicU32::new(0),
    errors: AtomicU32::new(0),
};

fn add(counter: &AtomicU32, n: u32) {
    counter.store(
        counter.load(Ordering::Relaxed).wrapping_add(n),
        Ordering::Relaxed,
    );
}

#[embassy_executor::main]
async fn main(_spawner: Spawner) -> ! {
    let p = embassy_mspm0::init(Default::default());
    let (mut instance, mut scl, mut sda) = (p.I2C0, p.PA1, p.PA0);
    let eeprom = EEPROM.take();
    let mut buffer = [0; WRITE_MAX];

    let mut config = Config::default();
    // A target's speed setting only picks its functional clock: above 200 kHz the 32 MHz bus
    // clock, which keeps the driver's 20x margin up to Fast-mode Plus at every bus rate.
    config.bus_speed = BusSpeed::FastModePlus;
    let mut target_config = i2c_target::Config::default();
    target_config.target_addr = Address::SevenBit(ADDRESS);

    info!("i2c-eeprom at {=u8:#x}", ADDRESS);
    if READ_CHUNK != 8 {
        warn!(
            "built with READ_CHUNK={}: reads are unreliable, for the driver repro only",
            READ_CHUNK
        );
    }
    loop {
        let mut i2c = I2cTarget::new(
            instance.reborrow(),
            scl.reborrow(),
            sda.reborrow(),
            Irqs,
            config,
            target_config,
        )
        .unwrap();
        let len = serve(&mut i2c, eeprom, &mut buffer).await;
        // Dropping the target disables it and disconnects both pins, so the address is NACKed
        // until the next one starts. Before the copy, so the address stops answering sooner.
        drop(i2c);
        eeprom.write(&buffer[..len]);
        add(&EEPROM_STATS.writes, 1);
        Timer::after(WRITE_CYCLE).await;
    }
}

/// Answers transactions until a write carries data, and returns that write's length in `buffer`.
async fn serve(i2c: &mut I2cTarget<'_>, eeprom: &mut Eeprom, buffer: &mut [u8]) -> usize {
    loop {
        match i2c.listen(buffer).await {
            Ok(Command::Write(len)) if len > 2 => return len,
            Ok(Command::Write(len)) => {
                if eeprom.set_address(&buffer[..len]) {
                    add(&EEPROM_STATS.address_writes, 1);
                }
            }
            // A real part commits a write only at a STOP, so data before a repeated start is
            // dropped.
            Ok(Command::WriteRead(len)) => {
                eeprom.set_address(&buffer[..len]);
                read(i2c, eeprom).await;
            }
            Ok(Command::Read) => read(i2c, eeprom).await,
            Ok(Command::GeneralCall(_)) => {}
            Err(e) => {
                warn!("listen: {}", e);
                add(&EEPROM_STATS.errors, 1);
                i2c.reset().unwrap();
            }
        }
    }
}

/// Sends from the pointer until the controller stops, wrapping at the end of the array.
async fn read(i2c: &mut I2cTarget<'_>, eeprom: &mut Eeprom) {
    add(&EEPROM_STATS.reads, 1);
    loop {
        let run = eeprom.read_run(READ_CHUNK);
        let len = run.len();
        // Fails only on an empty buffer, and a run holds at least one byte.
        let status = i2c.respond_to_read(run).await.unwrap();
        debug!("run of {} from the pointer: {}", len, status);
        let sent = match status {
            ReadStatus::NeedMoreBytes | ReadStatus::Done => len,
            ReadStatus::LeftoverBytes(left) => {
                // After a real STOP the driver keeps these off the wire already. A read that began
                // before `listen` saw the previous write's STOP is reported as a write-read, and
                // that old STOP ends this run while its bytes are still going out; without the
                // flush they would be sent again from the pointer.
                i2c.flush_tx_fifo();
                len - usize::from(left)
            }
        };
        eeprom.advance(sent);
        add(&EEPROM_STATS.bytes_read, sent as u32);
        if status != ReadStatus::NeedMoreBytes {
            return;
        }
    }
}
