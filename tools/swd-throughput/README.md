# swd-throughput

Measures throughput through probe-rs and estimates fixed and per-byte transfer costs.

It builds against verge-probe, cloned beside this repository as `../verge-probe`. The first build
fetches the probe-rs fork pinned in `Cargo.toml`.

```
cargo run --release --manifest-path tools/swd-throughput/Cargo.toml -- \
    --probe 1fc9:0143 --chip MSPM0L1306 --csv sweep.csv
```

Select `--protocol swd`, `jtag` or `cjtag`. The default is SWD. Use separate result files for each
protocol. The report only shows the ideal wire ceiling for SWD because its clock model is specific
to SWD transactions.

cJTAG needs a probe running Verge. probe-rs frames cJTAG over CMSIS-DAP only through a probe's own
vendor commands, and verge-probe supplies Verge's.

`tools/throughput.py` runs it as one half of a firmware comparison and keeps the result in
`results/throughput/`. probe-rs is pinned to a revision of the fork that carries mculink-verge's
CMSIS-DAP changes. Record pin changes when comparing results; host changes can affect throughput.

## Transfer costs

The report separates two costs:

- **Per byte.** The probe's bit loop and SWD clock.
- **Per transfer.** Command dispatch, USB round trips and firmware setup before the first clock
  edge. These costs apply to both small and large transfers.

Each workload runs at several transfer sizes. Times are fitted to `a + b * bytes`. `a` is the
fixed cost of a transfer and `1/b` is the ceiling a large one approaches. A firmware change that
changes the bit loop can affect `1/b`; an extra command-dispatch check can affect `a`.

The clock sweep says which cost binds. Where throughput stops rising with the clock, something off
the wire bounds it, and a slower bit loop there costs no throughput the tool can measure. Check
which cost binds before changing a probe's firmware.

## Workloads

`read32`, `write32`, `read8`, `write8` at each size and clock, plus `rt`: single-word reads timed
one at a time. Nothing can be batched in `rt`, so it is one round trip per word and it is the
figure most sensitive to per-command overhead.

The `of wire` column compares the fitted ceiling against an idealised SWD wire at the clock the
probe reports: 46 clocks per word for the request, turnarounds, acknowledgement, data and parity.
A result near 100% approaches that model's wire limit.

## What it does to the target

The core is halted (pass `--no-halt` to leave it running, which puts the target's own bus traffic
into the measurement). The region under test is read first and written back before exit, so the
write workloads restore the original RAM contents.

Transfers must fit the selected RAM region. The tool checks this because unmapped reads can
return data without an error. A target with 4 KiB of SRAM limits the largest transfer to 4096
bytes, giving the fit a smaller size range. Use a target with more RAM to estimate per-byte costs
over larger transfers.

## Reading the result

Quote both the clock asked for and the clock the probe reports. Clock dividers can round a request
down. The tool warns when the two differ and uses the reported clock for its wire comparison. A
reported clock is not a wire measurement; use an analyser recording to verify it.
