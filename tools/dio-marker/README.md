# dio-marker

Firmware that turns an LP-MSPM0L1306 LaunchPad into an edge source for the MCU-Link Pro's DIO
edges. Wire PA12 (J4 pin 40) to the Pro's DIO3 (J19 pin 5), and the LaunchPad's ground to J19
pin 11.

- `MODE=marker`, the default: every 10 ms, PA12 rises with LED2's red (PA26, J12 fitted), the CPU
  spins for `BUSY_US` microseconds (2,000 by default), and both fall. The rest of the period it
  sleeps. When the LaunchPad runs from the Pro's target supply, a recording's current steps where
  the marker's edges are. Before the first period, PA12 and the LED stay high for 50 ms while the
  CPU spins, then low for 20 ms. No period repeats that pulse, so a Verge recording and an analyser
  recording of the same boot line up on its rising edge; the 10 ms marker alone can't tell one
  period from the next. Start both recordings, then cycle the LaunchPad's supply (`verge power off`,
  then `on`) or reset it.
- `MODE=toggle`: PA12 toggles from a busy loop, `TOGGLE_HZ` full periods a second (47,500 by
  default), so twice that many edges reach the Pro. The loop's own instructions make the rate a
  little lower than asked.

```
cargo build --release
MODE=toggle TOGGLE_HZ=47500 cargo build --release --target-dir target-toggle
BUSY_US=200 cargo build --release --target-dir target-burst
```

Between bursts the default build idles on embassy-executor's `WFE`, where the LaunchPad draws about
1.1 mA. Built with embassy-mspm0's own executor and its `low-power` feature, it idles in deep sleep
instead. The LaunchPad then drew about 166 µA between bursts, not the few µA expected; what draws
the rest has not been found. That is still under the 0.36 mA where the Pro's high current range
range reads reliably:

```
cargo build --release --no-default-features --features low-power --target-dir target-low-power
```

It is built on embassy-mspm0, from the CordlessCoder fork's `mspm0-low-power` branch, as
i2c-eeprom is.
