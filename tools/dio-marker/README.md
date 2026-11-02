# dio-marker

Firmware that turns an LP-MSPM0L1306 LaunchPad into an edge source for the MCU-Link Pro's DIO
edges. Wire PA12 (J4 pin 40) to the Pro's DIO3 (J19 pin 5), and the LaunchPad's ground to J19
pin 11.

- `MODE=marker`, the default: every 10 ms, PA12 rises with LED2's red (PA26, J12 fitted), the CPU
  spins for `BUSY_US` microseconds (2,000 by default), and both fall. The rest of the period it
  sleeps. When the LaunchPad runs from the Pro's target supply, a recording's current steps where
  the marker's edges are. Before the first period, PA12 and the LED stay high for 50 ms while the
- `MODE=toggle`: PA12 toggles from a busy loop, `TOGGLE_HZ` full periods a second (47,500 by
  default), so twice that many edges reach the Pro. The loop's own instructions make the rate a
  little lower than asked.

```
cargo build --release
MODE=toggle TOGGLE_HZ=47500 cargo build --release --target-dir target-toggle
BUSY_US=200 cargo build --release --target-dir target-burst
```

It is built on embassy-mspm0, from the CordlessCoder fork's `mspm0-low-power` branch, as
i2c-eeprom is.
