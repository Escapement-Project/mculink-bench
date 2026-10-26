# mculink-bench

Throughput and wire-clock measurements comparing Verge with NXP's stock firmware on the same
MCU-Link and target.

- `tools/throughput.py` measures SWD throughput over a fixed set of cases, through its own CMSIS-DAP
  host (`tools/cmsis_dap.py`) and through probe-rs (`tools/swd-throughput/`). Results are in
  `results/throughput/`.
- `tools/wireclock.py` compares the SWD clock asked for with logic analyser recordings. Results are
  in `results/wireclock/`.
- `page/` builds the results page from the throughput and wire-clock results.

`--image` flashes Verge or a stock S-record through `verge` before measuring. Put `verge` on the
PATH or select it with `--verge`. `wireclock.py model` also needs the firmware source:
`--firmware` names a checkout of mculink-verge, whose emulator it runs.

Results identify the measuring code by path and hash, and the firmware by build. These identifiers
refer to private development history; they cannot be checked out from the published repositories.
The 2026-10-20 results predate the repository split. Their provenance includes paths from the
firmware repository, including `tools/dap.py`, `tools/coverage.py` and `tools/flash.py`.

## Latest results: 2026-10-25

The [measurements page](page/index.html) compares Verge build `2edfec6` with GCC and Clang against
stock V3.174, NXP's current release, and includes a J-Link Ultra reference. Raw results are in
`results/throughput/2026-10-25-{gcc,clang,stock}.json`, and `2026-10-25-RETAKE.md` describes the
set. The 2026-10-24 set, with build `e7c7acf` and stock V3.172, stays on the page for comparison.
The older results below keep their original firmware versions and test conditions.

## Measured 2026-10-21: after reads at 50 MHz

This run used the same matrix, probe and target as 2026-10-20. The Verge build is `34431f1` and
stock is V3.172. Results are in `results/throughput/2026-10-21-{gcc,clang,stock}.json`.
This build used a timer to reach 50 MHz SWD reads, up from 37.5 MHz. The timer path was removed
the same day.

At a requested 50 MHz, KiB/s:

| case | gcc 10-20 | gcc 10-21 | clang 10-20 | clang 10-21 |
| --- | --- | --- | --- | --- |
| block read, 127 words, depth 1 | 1947 | 1948 | 1947 | 2020 |
| block read, 127 words, depth 8 | 2676 | 2707 | 2686 | 2717 |
| DAP_Transfer read, 127 words, depth 8 | 1646 | 1547 | 1645 | 1548 |
| probe-rs read32, 4 KiB | 1657 | 1725 | 1548 | 1813 |

- Full-packet block reads improved by 1% with eight commands queued. Starting and stopping the
  timer stalls the core on the peripheral bus, consuming most of the time saved by the faster wire.
- DAP_Transfer reads at 50 MHz are 6% slower, on gcc and clang alike, for AP and DP registers alike.
- probe-rs results remained within run-to-run variation. Its unchanged write path varied by up
  to 10% between the two dates at 64 and 256 bytes. A single-word round trip still costs 68 µs.
- At 10 and 20 MHz, where reads do not use the timer, every case is within a few percent of
  2026-10-20.
- Stock throughput is 1 to 3% below its 2026-10-20 figures, showing variation between runs. It
  fails at 50 MHz as before (53 of 480 cases), and probe-rs through stock failed at 30 MHz this time.

## Measured 2026-10-20: ours with gcc and clang against stock V3.172

`tools/throughput.py`, matrix `swd-1`, on the MSPM0L1306, one probe flashed in turn with each image
by the script itself; results in `results/throughput/2026-10-20-{gcc,clang,stock}.json`, which carry
identical hashes of the measuring code. Our images are mculink-verge `4a7ca13`. Retake with the
same three commands (the script's docstring) once the firmware is feature-complete and optimised.

Full-packet block commands through the AP, KiB/s (127-word reads, 126-word writes):

| requested | depth | GCC read | Clang read | stock read | GCC write | Clang write | stock write |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 MHz | 1 | 82 | 83 | 77 | 82 | 82 | 77 |
| 5 MHz | 1 | 392 | 393 | 293 | 389 | 392 | 296 |
| 10 MHz | 1 | 722 | 728 | 503 | 731 | 730 | 521 |
| 10 MHz | 8 | 817 | 818 | 536 | 823 | 824 | 555 |
| 20 MHz | 1 | 1186 | 1231 | 654 | 1214 | 1222 | 653 |
| 20 MHz | 8 | 1470 | 1473 | 711 | 1502 | 1504 | 719 |
| 50 MHz | 1 | 1947 | 1947 | 702 | 2249 | 2254 | 733 |
| 50 MHz | 8 | 2676 | 2686 | failed | 3239 | 3248 | failed |

probe-rs, 4 KiB at a time, attach excluded, KiB/s:

| requested | GCC read32 | Clang | stock | GCC write32 | Clang | stock |
| --- | --- | --- | --- | --- | --- | --- |
| 1 MHz | 82 | 80 | 76 | 82 | 82 | 77 |
| 5 MHz | 383 | 386 | 287 | 389 | 392 | 296 |
| 10 MHz | 707 | 698 | 491 | 739 | 721 | 520 |
| 20 MHz | 1127 | 1164 | 634 | 1195 | 1170 | 664 |
| 30 MHz | 1575 | 1591 | 661 | 1606 | 1777 | 733 |
| 50 MHz | 1657 | 1548 | failed | 2266 | 2283 | failed |

- Verge is faster at every completed probe-rs clock: 6 to 8% at 1 MHz, 1.4× at 10 MHz and 1.8×
  at 20 MHz. From a requested 20 to 30 MHz, stock reads rise from 634 to 661 KiB/s and writes from
  664 to 733 KiB/s. Verge throughput continues increasing up to a requested 50 MHz.
- A single-word round trip takes 68 to 69 µs on all three builds from 10 MHz upward, so the host
  and USB set it, not the probe.
- At a requested 50 MHz, stock misreads acknowledgements (3 or 6, neither valid) with pipelined or
  queued commands: 55 and 56 of 480 cases in two runs, all at depths 2 and 8, none at depth 1.
  probe-rs also failed at 50 MHz and in one of two runs at 30 MHz.
- GCC and Clang are within a few percent except for one-word commands with eight queued at
  50 MHz. GCC handles 21,586 commands/s for a one-word AP read through DAP_Transfer, against
  Clang's 36,473. At 10 MHz both handle about 51,000 commands/s.
- probe-rs's 4 KiB reads rise from 1575 to 1657 KiB/s on Verge between requested clocks of 30 and
  50 MHz. Writes rise from 1606 to 2266 KiB/s. The higher requested clock improves reads much less
  than writes.

## The wire, measured 2026-10-21: requested against delivered clock

`tools/wireclock.py`, matrix `wire-1`, the same images as the throughput above, the analyser at the
L1306's header (D6 SWDIO, D7 SWCLK, 100 MS/s). Results in
`results/wireclock/2026-10-21-{gcc,clang,stock}.json`. The exported captures and probe logs are
too large to keep here; `wireclock.py analyse <dir> --result <json>` reads them again. clang was taken at three clocks
only, as a cross-check.

These results supersede `results/wireclock/2026-10-20-*.json`, whose captures were unreliable.
The earlier captures showed spurious SWDIO edges: a quarter of Verge's changes at 10 MHz, against
0.6% here. They also misplaced crossings with SWCLK, making one Verge edge appear early at 10 MHz.
The analyser's ground was disconnected from the target. With the same images and ground connected,
that edge is on time. The earlier files remain available.

Data bits, the median period over a transfer's data phase, in MHz (s: timed from SWDIO, see
below), and stock's fastest period anywhere in a transfer:

| requested | GCC read / write | Clang | stock read / write | stock fastest, where |
| --- | --- | --- | --- | --- |
| 1 | 1.00 / 1.00 | | 0.93 / 0.94 | 0.99, acknowledge |
| 2 | 2.00 / 2.00 | | 1.79 / 1.85 | 2.06, acknowledge |
| 3 | 3.00 / 3.00 | | 2.50 / 2.63 | 3.07, acknowledge |
| 5 | 5.00 / 5.00 | | 3.56 / 3.85 | 4.84, acknowledge |
| 8 | 7.89 / 7.89 | | 5.01 / 5.56 | 7.95, acknowledge |
| 10 | 10.00 / 10.00 | 10.00 / 10.00 | 6.40 / 7.14 | 11.61, acknowledge |
| 12 | 11.54 / 11.54 | | 6.39 / 7.14 | 11.66, acknowledge |
| 14 | 13.64 / 13.63 | | 6.38 / 7.14 | 11.59, acknowledge |
| 16 | 14.99 / 14.99 | | 9.09 / 9.38 | 18.81, acknowledge |
| 20 | 18.75 / 18.74 | 18.75 / 18.75 | 9.08 / 9.37 | 18.92, acknowledge |
| 25 | 25.00 / 24.98 | | 9.63 / 10.72 | 18.94, request |
| 30 | 30.03 / 29.84 | | 9.62 / 10.72 | 18.98, request |
| 40 | 37.33 s / 37.50 s | | 9.63 / 10.72 | 18.99, request |
| 50 | 37.33 s / 50.00 s | 37.33 s / 50.00 s | 9.60 / 10.72 | 18.98, request |

- **Clock steps.** Verge delivers the requested clock or the next lower step. The steps divide
  150 MHz by whole cycles: 11.54 MHz is 13 cycles, 13.64 MHz is 11, 18.75 MHz is 8.
  At a requested 50 MHz, reads run at 37.5 MHz and writes at 50 MHz, matching the emulator.
  A read clock is four instructions. The timer path for 50 MHz reads was removed; see
  mculink-verge's BUSES.md for the reason.
- **Shortest periods.** Verge's fastest period anywhere in a transfer is within
  1 ns of the emulator's at every clock from 1 to 30 MHz. The figures `compare` prints run up to 3%
  over the step at 30 MHz, which is 1 ns at that period.
- **Emulator comparison.** `wireclock.py model --firmware <checkout> <core1 elf>
  <result>` compares every position with mculink-verge's `tools/cycles.py` prediction, for every role
  the emulator has (its target never waits, so a WAIT transfer has no prediction). Every position of
  every kind agrees within 1 ns at every clock from 1 to 30 MHz, except one (next bullet). The
  2026-10-20 run's larger differences, up to 3.3 ns and an alternation in writes, are gone. clang
  matches gcc within 1 ns at every position at 10 and 20 MHz, WAIT retries included.
- **Write turnaround.** The measured turnaround exceeds the model by 4 cycles from 1 to
  10 MHz (26.7 ns), 3 from 12 to 20, split over two positions from 14, then 1 at 25 and 2 at 30.
  It is a DIRSET store to the GPIO block, not an APB one. Longer is the safe direction; it costs a
  10 MHz write about 0.6% and is a gap in mculink-verge's `tools/m33.py`.
- **Stock data rates.** Stock reaches 9.6 MHz reads and 10.7 MHz writes at a requested 25 MHz.
  Wire periods remain the same for requests from 25 to 50 MHz. Below that, requests fall into
  three groups: up to 8 MHz, 10 to 14 MHz, and 16 to 20 MHz. The data rate stays below the request:
  reads run at 6.4 MHz for a 10 MHz request.
- **Stock acknowledgement rates.** Stock reaches 11.6 MHz for a 10 MHz request and 18.8 MHz for
  16 MHz. At requests of 2 and 3 MHz, it exceeds the requested clock by 2 to 3%.
  From a requested 25 MHz, the request phase has the shortest period: 53 ns (19.0 MHz).
- **Stock acknowledgement errors.** At requested clocks of 25 MHz and above, stock reports errors
  for replies that are correct on the wire. The depth-2 case used 2,000 commands of eight DPIDR
  reads. It misread 5, 4, 7 and 1 times at requested clocks of 25, 30, 40 and 50 MHz, and never
  below. On the wire, every misread transfer carries OK and the expected DPIDR. Every one
  follows a 3.24 µs stall at position 8, the clock that launches the first acknowledgement bit. The
  stalls that misread come at depth 2, when the next command arrives. After the stall, position 9,
  where the second acknowledge bit is read, lasts 40 to 50 ns against the usual 59. Stalls land at
  every position about equally; only position 8 stalls led to misreads: 17 of 79 at requests of
  25 MHz and above, and 0 of 553 at 20 MHz and below.
  The reported acknowledgement, 3, is OK read one bit late on the second bit, and
  the clocks that follow on the wire are the probe's protocol-error recovery, not data.
- **Target reply delay.** The L1306's reply arrives about 20 ns after the rising edge that launches
  it, measured at the target pins (median, in 10 ns samples).

Capture limits:

- **Pulse resolution.** The analyser resolves every Verge SWCLK pulse up to a requested 30 MHz.
  From a requested 40 MHz, both phases of a read clock are two cycles, 13.3 ns. The analyser loses
  them, so the data bits are timed from SWDIO: TAR holds 0x55555555, so every data bit changes the
  line, and a bit lasts a whole period rather than a phase. Stock is resolved at every clock.
- **SWCLK phase offset.** High phases read about 1.6 ns long and low phases 1.6 ns short against
  the generator's exact 3 + 3 cycles at 25 MHz. Rise-to-rise periods have no such offset. The result
  files retain the captured phases; this Logic 8 has a fixed threshold.
- **SWDIO threshold offset.** Rising and falling target edges cross the threshold about 10 ns
  apart, so target bits are timed rising to rising. The analysis does not depend on decoding
  target data. The probe checks each word; command layout and edge count determine transfer length.
- **Crosstalk.** SWCLK edges produce one-sample SWDIO pulses: 0.4 to 1.2% of SWDIO's changes on Verge
  from 2 to 10 MHz and 3% on stock at 10, nearly all at a SWCLK edge. SWCLK carries none at 2 to
  10 MHz. None reached the target: every written word read back correctly.

## Retaking, after the optimisation work

Retake with the same matrices and commands. Compare results with the same matrix name (`swd-1`,
`wire-1`); rename the matrix if its cases change. Keep the stock S-record fixed for a controlled
comparison. Record any stock version change separately.

1. Build gcc and clang from the tree to be measured. Keep both images of each build
   (`mculink_cjtag.elf` and `mculink_cjtag_core1.elf`, which `wireclock.py model` needs) and the
   stock S-record in one directory, `<dir>`, with a `SHA256SUMS`.
2. With no analyser recording running, throughput for each image:
   `uv run tools/throughput.py run --label gcc --serial <serial> --image <dir>/gcc-<build>.elf`,
   the same for clang, and `--label stock --image <dir>/MCU-LINK_CMSIS-DAP_V3_172.s19 --note
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
