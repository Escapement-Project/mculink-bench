# mculink-bench

Throughput and wire-clock measurements comparing Verge with NXP's firmware on the same MCU-Link
and target.

- `tools/throughput.py` measures SWD, JTAG and cJTAG throughput over a fixed set of cases: over SWD
  through its own CMSIS-DAP host (`tools/cmsis_dap.py`) and through probe-rs
  (`tools/swd-throughput/`), over JTAG and cJTAG through probe-rs alone. Results are in
  `results/throughput/`.
- `tools/wireclock.py` compares the SWD clock asked for with logic analyser recordings. Results are
  in `results/wireclock/`.
- `tools/wire/` decodes analyser recordings into tickwave's annotations: SWD transfers
  (`wire_swd.py`), cJTAG frames (`wire_cjtag.py`) and clock bursts (`wire_clock.py`). `wire_chart.py`
  charts a set's session figures. The recordings are in the mculink-recordings repository.
- `tools/flash-timing/` times flash programming of a CC1312R1 through probe-rs, and through SEGGER's
  Flasher ARM for comparison. Results are in `results/flash-timing/`.
- `page/` builds the results page from the throughput and wire-clock results.
- `tools/i2c-eeprom/` is firmware that makes an LP-MSPM0L1306 an I2C EEPROM, the target for
  testing a probe's I2C bridge.
- `tools/dio-marker/` is firmware that makes an LP-MSPM0L1306 an edge source for the MCU-Link
  Pro's DIO edges.

`--image` flashes Verge, or NXP's firmware as an S-record, through `verge` before measuring. Put
`verge` on the PATH or select it with `--verge`. `wireclock.py model` also needs the firmware
source: `--firmware` names a checkout of mculink-verge, whose emulator it runs.

The Rust tools, `tools/swd-throughput/` and `tools/flash-timing/`, build against verge-probe: clone
[verge-probe](https://github.com/Escapement-Project/verge-probe) beside this repository, as
`../verge-probe`. Their first build fetches the probe-rs fork pinned in each `Cargo.toml`.
`throughput.py run` builds swd-throughput itself, unless `--no-probe-rs` leaves probe-rs out.

`uv run tools/tools_test.py` checks the Python tools against a fake probe, analyser and probe-rs.

Results identify the measuring code by path and hash, and the firmware by build. These identifiers
refer to private development history; they cannot be checked out from the published repositories.
The 2026-10-20 results predate the repository split. Their provenance includes paths from the
firmware repository, including `tools/dap.py`, `tools/coverage.py` and `tools/flash.py`.

## Results

The [measurements page](page/out/index.html) compares Verge build `df11e14`, with GCC and Clang,
against NXP's firmware V3.174, its current release, and includes a J-Link Ultra reference. Each set
has a note beside its result files, which says what was measured and under what conditions:

| date | what | note |
| --- | --- | --- |
| 2026-11-05 | flash programming of a CC1312R1, against SEGGER's Flasher ARM; not on the page | `results/flash-timing/2026-11-05.md` |
| 2026-11-04 | throughput over SWD, JTAG and cJTAG, GCC and Clang against NXP's firmware V3.174 | `results/throughput/2026-11-04.md` |
| 2026-10-25 | throughput, GCC and Clang against NXP's firmware V3.174 | `results/throughput/2026-10-25-RETAKE.md` |
| 2026-10-24 | throughput, against NXP's firmware V3.172; replaced by 2026-10-25 | `results/throughput/2026-10-24-RETAKE.md` |
| 2026-10-21 | throughput with reads at 50 MHz, a timer path removed the same day | `results/throughput/2026-10-21.md` |
| 2026-10-21 | throughput with CPU 0 staging commands, on and off in one image | `results/throughput/2026-10-21-staging.md` |
| 2026-10-21 | throughput and the wire, the MCU-Link Pro against the standalone MCU-Link | `results/throughput/2026-10-21-boards.md` |
| 2026-10-21 | the wire: the clock asked for against the clock delivered | `results/wireclock/2026-10-21.md` |
| 2026-10-20 | throughput, GCC and Clang against NXP's firmware V3.172 | `results/throughput/2026-10-20.md` |

The page keeps the older sets for comparison, with their original firmware versions and test
conditions.

## Retaking

Retake with the same matrices and commands. Compare results with the same matrix name (`swd-1`,
`wire-1`); rename the matrix if its cases change. Keep NXP's S-record fixed for a controlled
comparison. Record any change of its version separately.

1. Build gcc and clang from the tree to be measured. Copy both images of each build into one
   directory, `<dir>`: `mculink_cjtag.elf` as `gcc-<build>.elf`, and `mculink_cjtag_core1.elf`,
   which `wireclock.py model` needs, as `gcc-<build>-core1.elf`, and the same for clang. Put NXP's
   S-record beside them, with a `SHA256SUMS`.
2. With no analyser recording running, throughput for each image:
   `uv run tools/throughput.py run --label gcc --serial <serial> --image <dir>/gcc-<build>.elf`,
   the same for clang, and `--label stock --image <dir>/MCU-LINK_CMSIS-DAP_V3_174.s19 --note
   "LinkServer's ..."`.
3. With the analyser on D6 SWDIO and D7 SWCLK at the target, and Logic 2 running with its MCP
   server on (wireclock.py talks to `127.0.0.1:10530`; `--analyser` changes it):
   `uv run tools/wireclock.py run --label gcc --serial <serial> --image <dir>/gcc-<build>.elf
   --keep <recordings>/gcc`, the same with `--label stock` for NXP's firmware, and clang with
   `--clock 10 --clock 20 --clock 50`. A full sweep takes about a minute after the image is
   flashed.
4. Put the bench image back with `verge update --file <the image>`.
5. Set the dates side by side: `throughput.py compare` and `wireclock.py compare` take both dates'
   files, and label a repeated firmware with its date; `wireclock.py model --firmware <checkout>
   <dir>/gcc-<build>-core1.elf results/wireclock/<date>-gcc.json` checks the emulator again.
6. The results page: add the new date to `page/sets.json` with its result labels, then build and
   publish the page as `page/README.md` says. It opens on the newest date, and its set picker offers
   the others.

## Licence

Apache-2.0, in `LICENSE`. The page's copies of the Escapement Project's marks, the dial and Verge's
V, are under CC BY 4.0 in `page/CC-BY-4.0.txt`. Credit the Escapement Project. Use the marks to
refer to the project or its tools. Don't use them, changed or not, as another project's mark, or to
suggest that the project made or endorses something it didn't.
