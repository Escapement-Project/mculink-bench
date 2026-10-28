# i2c-eeprom

Firmware that turns an LP-MSPM0L1306 LaunchPad into an I2C EEPROM, a test target for a probe's I2C
bridge. It answers at 0x50 the way a 24C32 does, with a smaller array:

- A write sends a two-byte word address, then data. Data wraps within its 32-byte page.
- A read continues from where the last access ended. A write of the address alone, or a write
  followed by a repeated start, sets where it starts. Reads wrap at the end of the array.
- After a write that stores data, the address is not acknowledged for 5 ms, as during a real
  part's write cycle.

It differs from a 24C32 in these ways:

- It holds 1 KiB, not 4 KiB. Address bits from bit 10 up are ignored.
- It stretches the clock while it fetches data for a read: before the first byte, then every 8
  bytes. A 24C32 never does.
- After a write's STOP, the address still answers until the firmware turns the target off, which
  takes an interrupt and a task wake. A transaction that starts in that time can be acknowledged
  and then cut off, and a read that starts in it can lose the write's data.
- Two writes that arrive within an interrupt and task wake of each other are taken as one write.
- It takes writes of up to 512 data bytes. A longer one stores nothing and starts no write cycle.
  The controller sees a NACK partway through, unless the write ends within 7 bytes of the limit.
- A read followed by a repeated start and another read skips the bytes the first read did not
  take.
- A read followed by a repeated start and a write is not supported. With more than 8 bytes
  written, the clock stays stretched until the LaunchPad is reset. With fewer, the bytes are taken
  as the start of the next write.
- Its contents start as byte `a` holding `a ^ (a >> 8)`, not erased, and are lost at power-off.

The I2C target driver is embassy-mspm0's, from the CordlessCoder fork's `mspm0-low-power` branch.
Unlike upstream's, it keeps the bytes a short read did not take from being sent to the next read.

## Wiring

I2C0 on the LaunchPad's J1 header: SDA is PA0 (pin 10), SCL is PA1 (pin 9). Both are 5 V-tolerant
open-drain pins, so pull them up to the bus's own supply. Remove J9 and J10, the board's pull-ups,
and J2, which connects PA0 to an LED. Join the grounds.

## Build and load

```
cargo build --release
probe-rs download --chip MSPM0L1306 --protocol swd target/thumbv6m-none-eabi/release/i2c-eeprom
probe-rs reset --chip MSPM0L1306 --protocol swd
```

`cargo run --release` does the same and then shows the log over RTT.

The addressing is a separate module with host tests:

```
cargo test --lib --target x86_64-unknown-linux-gnu
```

## Counters

`EEPROM_STATS` holds five `u32` counters, in this order: writes that stored data, writes of an
address alone, reads, bytes read, and driver errors (each one resets the I2C peripheral). Find its
address with `nm` and read it with a debugger while the firmware runs.
