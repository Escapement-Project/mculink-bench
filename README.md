# mculink-bench

Throughput and wire-clock measurements comparing Verge with NXP's stock firmware on the same
MCU-Link and target.

- `tools/throughput.py` measures SWD throughput over a fixed set of cases, through its own CMSIS-DAP
  host (`tools/cmsis_dap.py`) and through probe-rs (`tools/swd-throughput/`). Results are in
  `results/throughput/`.
- `tools/wireclock.py` compares the SWD clock asked for with logic analyser recordings. Results are
  in `results/wireclock/`.
- `page/` builds the results page from the throughput and wire-clock results.
- `tools/i2c-eeprom/` is firmware that makes an LP-MSPM0L1306 an I2C EEPROM, the target for
  testing a probe's I2C bridge.
- `tools/dio-marker/` is firmware that makes an LP-MSPM0L1306 an edge source for the MCU-Link
  Pro's DIO edges.

`--image` flashes Verge or a stock S-record through `verge` before measuring. Put `verge` on the
PATH or select it with `--verge`. `wireclock.py model` also needs the firmware source:
`--firmware` names a checkout of mculink-verge, whose emulator it runs.

Results identify the measuring code by path and hash, and the firmware by build. These identifiers
refer to private development history; they cannot be checked out from the published repositories.
The 2026-10-20 results predate the repository split. Their provenance includes paths from the
firmware repository, including `tools/dap.py`, `tools/coverage.py` and `tools/flash.py`.

## Results

The [measurements page](page/index.html) compares Verge build `2edfec6` with GCC and Clang against
stock V3.174, NXP's current release, and includes a J-Link Ultra reference. Each set has a note
beside its result files, which says what was measured and under what conditions:

| date | what | note |
| --- | --- | --- |
| 2026-10-25 | throughput, GCC and Clang against stock V3.174 (latest) | `results/throughput/2026-10-25-RETAKE.md` |
| 2026-10-24 | throughput, against stock V3.172; replaced by 2026-10-25 | `results/throughput/2026-10-24-RETAKE.md` |
| 2026-10-21 | throughput with reads at 50 MHz, a timer path removed the same day | `results/throughput/2026-10-21.md` |
| 2026-10-21 | the wire: the clock asked for against the clock delivered | `results/wireclock/2026-10-21.md` |
| 2026-10-20 | throughput, GCC and Clang against stock V3.172 | `results/throughput/2026-10-20.md` |

The page keeps the 2026-10-24 set for comparison. The 2026-10-21 files labelled `gcc-stage-on`,
`gcc-stage-off`, `plain-gcc` and `pro-gcc` have no note yet. The older sets keep their original
firmware versions and test conditions.

## Retaking

Retake with the same matrices and commands. Compare results with the same matrix name (`swd-1`,
`wire-1`); rename the matrix if its cases change. Keep the stock S-record fixed for a controlled
comparison. Record any stock version change separately.

1. Build gcc and clang from the tree to be measured. Keep both images of each build
   (`mculink_cjtag.elf` and `mculink_cjtag_core1.elf`, which `wireclock.py model` needs) and the
   stock S-record in one directory, `<dir>`, with a `SHA256SUMS`.
2. With no analyser recording running, throughput for each image:
   `uv run tools/throughput.py run --label gcc --serial <serial> --image <dir>/gcc-<build>.elf`,
   the same for clang, and `--label stock --image <dir>/MCU-LINK_CMSIS-DAP_V3_174.s19 --note
   "LinkServer's ..."`.
3. With the analyser on D6 SWDIO and D7 SWCLK at the target:
   `uv run tools/wireclock.py run --label gcc --serial <serial> --image <dir>/gcc-<build>.elf
   --keep <captures>/gcc`, the same for stock, and clang with `--clock 10 --clock 20 --clock 50`.
   A full sweep takes about a minute after the image is flashed.
4. Put the bench image back with `verge update --file <the image>`.
5. Set the dates side by side: `throughput.py compare` and `wireclock.py compare` take both dates'
   files, and label a repeated firmware with its date; `wireclock.py model --firmware <checkout>
   <dir>/gcc-<build>-core1.elf results/wireclock/<date>-gcc.json` checks the emulator again.
6. The results page: add the new date to `page/sets.json` with its result labels, then build and
   publish the page as `page/README.md` says. It opens on the newest date, and its set picker offers
   the others.

## Licence

Apache-2.0, in `LICENSE`.
