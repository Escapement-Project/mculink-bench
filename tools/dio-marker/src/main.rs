//! An edge source for the MCU-Link Pro's DIO edges, on the LP-MSPM0L1306: PA12 (J4 pin 40) wired
//! to the Pro's DIO3.
//!
//! - `MODE=marker` (the default): every 10 ms PA12 rises with LED2's red (PA26, J12 fitted), the
//!   CPU spins for `BUSY_US` (2 ms by default), and both fall; the rest of the period it sleeps. The current step and the
//!   marker's edges come from the same moments, so a recording shows how well they line up. The
//!   time driver times both phases, to its 30.5 us tick: `asm::delay` only promises at least the
//!   cycles asked for, and its 2 ms ran about 6.
//! - `MODE=toggle`: PA12 toggles as fast as `TOGGLE_HZ` full periods a second, by a busy loop, so
//!   twice that many edges reach the Pro.
#![no_std]
#![no_main]

use defmt::info;
use defmt_rtt as _;
use embassy_executor::Spawner;
use embassy_mspm0::gpio::{Level, Output};
use embassy_time::{Duration, Ticker, block_for};
use panic_probe as _;

/// MCLK out of reset, from SYSOSC.
const MCLK_HZ: u32 = 32_000_000;

const fn parse(digits: Option<&str>, default: u32) -> u32 {
    let Some(digits) = digits else {
        return default;
    };
    let digits = digits.as_bytes();
    assert!(!digits.is_empty(), "an empty number");
    let mut value = 0u32;
    let mut i = 0;
    while i < digits.len() {
        assert!(digits[i].is_ascii_digit(), "not a number");
        value = value * 10 + (digits[i] - b'0') as u32;
        i += 1;
    }
    value
}

const TOGGLE_HZ: u32 = parse(option_env!("TOGGLE_HZ"), 47_500);
/// How long the marker's CPU spins each period, to the time driver's tick.
const BUSY_US: u32 = parse(option_env!("BUSY_US"), 2_000);
const fn same(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut i = 0;
    while i < a.len() {
        if a[i] != b[i] {
            return false;
        }
        i += 1;
    }
    true
}

const TOGGLE: bool = match option_env!("MODE") {
    None => false,
    Some(mode) if same(mode, "marker") => false,
    Some(mode) if same(mode, "toggle") => true,
    Some(_) => panic!("MODE is marker or toggle"),
};

#[cfg_attr(
    feature = "low-power",
    embassy_executor::main(executor = "embassy_mspm0::executor::Executor", entry = "cortex_m_rt::entry")
)]
#[cfg_attr(not(feature = "low-power"), embassy_executor::main)]
async fn main(_spawner: Spawner) -> ! {
    let p = embassy_mspm0::init(Default::default());
    let mut marker = Output::new(p.PA12, Level::Low);
    let mut led = Output::new(p.PA26, Level::Low);
    if TOGGLE {
        info!("toggling PA12 at {} Hz", TOGGLE_HZ);
        let half = MCLK_HZ / (2 * TOGGLE_HZ);
        loop {
            marker.toggle();
            cortex_m::asm::delay(half);
        }
    }
    info!("marking {} us of every 10 ms on PA12, with LED2 red", BUSY_US);
    let mut ticker = Ticker::every(Duration::from_millis(10));
    loop {
        marker.set_high();
        led.set_high();
        block_for(Duration::from_micros(u64::from(BUSY_US)));
        led.set_low();
        marker.set_low();
        ticker.next().await;
    }
}
