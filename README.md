# mculink-bench

Throughput and wire-clock measurements of the MCU-Link running mculink-verge's firmware, set
against NXP's stock firmware on the same probe and target.

- `tools/throughput.py` measures SWD throughput over a fixed set of cases, through its own CMSIS-DAP
  host (`tools/cmsis_dap.py`) and through probe-rs (`tools/swd-throughput/`). Results are in
  `results/throughput/`.
- `tools/wireclock.py` records the SWD clock on the wire with a logic analyser, and sets it beside
  the clock that was asked for. Results are in `results/wireclock/`.
- `page/` builds the results page from the throughput and wire-clock results.

`--image` flashes the probe through verge before measuring, ours or a stock S-record alike; put
`verge` on the PATH or name it with `--verge`. Only `wireclock.py model` needs the firmware itself:
`--firmware` names a checkout of mculink-verge, whose emulator it runs.

The results of 2026-10-20 were taken while these tools lived in the firmware's repository. Their
provenance names that repository's paths, `tools/dap.py`, `tools/coverage.py` and `tools/flash.py`
among them, with their hashes. Those hashes stay true of mculink-verge's history, so the files are
left as recorded.

## Measured 2026-10-21: after reads at 50 MHz

The same matrix, probe and target. Our images are mculink-verge `34431f1`, and stock is V3.172
again; results in `results/throughput/2026-10-21-{gcc,clang,stock}.json`.
In this image our SWD reads run at 50 MHz on the wire when 50 is asked, where they ran at 37.5,
with a timer making the clock. mculink-verge removed them again the same day.

At 50 MHz asked, KiB/s:

| case | gcc 10-20 | gcc 10-21 | clang 10-20 | clang 10-21 |
| --- | --- | --- | --- | --- |
| block read, 127 words, depth 1 | 1947 | 1948 | 1947 | 2020 |
| block read, 127 words, depth 8 | 2676 | 2707 | 2686 | 2717 |
| DAP_Transfer read, 127 words, depth 8 | 1646 | 1547 | 1645 | 1548 |
| probe-rs read32, 4 KiB | 1657 | 1725 | 1548 | 1813 |

- Full-packet block reads barely move with the faster wire: 1% at 8 queued. Starting and stopping
  the timer stalls the core on the peripheral bus, and that used up most of what the faster wire
  saved.
- DAP_Transfer reads at 50 MHz are 6% slower, on gcc and clang alike, for AP and DP registers alike.
- probe-rs moves no more than its run-to-run spread. Its writes, which did not change, moved by up
  to 10% between the two dates at 64 and 256 bytes. A single-word round trip still costs 68 us.
- At 10 and 20 MHz, where reads do not use the timer, every case is within a few percent of
  2026-10-20.
- Stock is 1 to 3% below its own 2026-10-20 figures, which is how far the bench itself drifted. It
  fails at 50 MHz as before (53 of 480 cases), and probe-rs through stock failed at 30 MHz this time.

## Measured 2026-10-20: ours with gcc and clang against stock V3.172

`tools/throughput.py`, matrix `swd-1`, on the MSPM0L1306, one probe flashed in turn with each image
by the script itself; results in `results/throughput/2026-10-20-{gcc,clang,stock}.json`, which carry
identical hashes of the measuring code. Our images are mculink-verge `4a7ca13`. Retake with the
same three commands (the script's docstring) once the firmware is feature-complete and optimised.

Full-packet block commands through the AP, KiB/s (127-word reads, 126-word writes):

| asked | depth | gcc read | clang read | stock read | gcc write | clang write | stock write |
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

| asked | gcc read32 | clang | stock | gcc write32 | clang | stock |
| --- | --- | --- | --- | --- | --- | --- |
| 1 MHz | 82 | 80 | 76 | 82 | 82 | 77 |
| 5 MHz | 383 | 386 | 287 | 389 | 392 | 296 |
| 10 MHz | 707 | 698 | 491 | 739 | 721 | 520 |
| 20 MHz | 1127 | 1164 | 634 | 1195 | 1170 | 664 |
| 30 MHz | 1575 | 1591 | 661 | 1606 | 1777 | 733 |
| 50 MHz | 1657 | 1548 | failed | 2266 | 2283 | failed |

- Ours is ahead at every clock through probe-rs: 6 to 8% at 1 MHz, 1.4x at 10 MHz and 1.8x at
  20 MHz. Past 20 MHz asked stock gains little (reads 634 to 661 KiB/s at 30, writes 664 to 733),
  while ours keeps climbing to 50.
- A single-word round trip costs the same on all three, 68 to 69 us from 10 MHz up: that is the
  host and USB, not the probe.
- Stock at 50 MHz asked misreads acknowledges (3 or 6, neither valid) partway through runs whenever
  commands are pipelined or queued: 55 and 56 of 480 cases in two runs, all at depths 2 and 8,
  none at depth 1. probe-rs fails there too, and once of two runs at 30 MHz.
- gcc and clang are within a few percent of each other everywhere but one place: 1-word commands
  at 8 queued at 50 MHz, where gcc falls off the CPU 0 cliff (21,586 against 36,473 commands/s for
  a 1-word AP read through DAP_Transfer) and clang mostly does not. At 10 MHz both run about 51k.
- probe-rs's 4 KiB reads barely rise from 30 to 50 MHz on ours (1575 to 1657) while writes do (1606
  to 2266): something on the read path other than the wire binds there.
- What each firmware puts on the wire for each requested clock is in the next section.

## The wire, measured 2026-10-21: requested against delivered clock

`tools/wireclock.py`, matrix `wire-1`, the same images as the throughput above, the analyser at the
L1306's header (D6 SWDIO, D7 SWCLK, 100 MS/s). Results in
`results/wireclock/2026-10-21-{gcc,clang,stock}.json`, the exported captures and probe logs in
a directory outside this repository, which `wireclock.py analyse <dir> --result <json>`
reads again. clang was taken at three clocks, as the falsifier.

The 2026-10-20 results (`results/wireclock/2026-10-20-*.json`) are kept, and superseded by these:
their captures were unreliable. They carried spurious edges on SWDIO (a quarter of its changes on
ours at 10 MHz, against 0.6% here), misplaced where SWCLK crossed, and made one of ours' edges look
early at 10 MHz (mculink-verge's BACKLOG item 16). With the same images, that edge is on time.

Data bits, the median period over a transfer's data phase, in MHz (s: timed from SWDIO, see
below), and stock's fastest period anywhere in a transfer:

| asked | gcc read / write | clang | stock read / write | stock fastest, where |
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

- **Ours delivers the clock asked for, or the step under it.** The steps are 150 MHz over whole
  cycles: 11.54 is 13 cycles, 13.64 is 11, 18.75 is 8. At 50 MHz asked, reads run at 37.5 and writes
  at 50, which is what the emulator says: a read clock is four instructions. A timer that brought
  reads to 50 was built and removed again (mculink-verge's BUSES.md says why).
- **No period of ours is shorter than asked.** The fastest period anywhere in a transfer is within
  1 ns of the emulator's at every clock from 1 to 30 MHz. The figures `compare` prints run up to 3%
  over the step at 30 MHz, which is 1 ns at that period.
- **The emulator matches the wire.** `wireclock.py model --firmware <checkout> <core1 elf>
  <result>` sets every position beside mculink-verge's `tools/cycles.py` prediction, for every role
  the emulator has (its target never waits, so a WAIT transfer has no prediction). Every position of
  every kind agrees within 1 ns at every clock from 1 to 30 MHz, except one (next bullet). The
  2026-10-20 run's larger differences, up to 3.3 ns and an alternation in writes, are gone. clang
  matches gcc within 1 ns at every position at 10 and 20 MHz, WAIT retries included.
- **The turnaround before a write's data is longer than the model says**: 4 cycles from 1 to
  10 MHz (26.7 ns), 3 from 12 to 20, split over two positions from 14, then 1 at 25 and 2 at 30.
  It is a DIRSET store to the GPIO block, not an APB one. Longer is the safe direction; it costs a
  10 MHz write about 0.6% and is a gap in mculink-verge's `tools/m33.py`.
- **Stock tops out at 9.6 MHz for reads and 10.7 for writes, from 25 MHz asked.** The wire is the
  same from 25 to 50 MHz asked, period for period. Below that it moves in three steps (to 8, 10 to
  14, 16 to 20), and its data rate is always well under the request: 6.4 MHz at 10 asked.
- **Stock clocks its acknowledge faster than asked**: 11.6 MHz at 10 asked, 18.8 at 16 asked,
  2 to 3% over at 2 and 3 asked. From 25 asked its fastest period is the request, 53 ns (19.0 MHz).
- **Stock's misreads at 25 MHz asked and above are the probe, not the target.** The depth-2 kind
  (2,000 commands of eight DPIDR reads) misread 5, 4, 7 and 1 times at 25, 30, 40 and 50 MHz asked
  and never below. On the wire, every misread transfer carries OK and the right DPIDR. Every one
  follows a 3.24 us stall at position 8, the clock that launches the first acknowledge bit. The
  stalls that misread come at depth 2, when the next command arrives. After the stall, position 9,
  where the second acknowledge bit is read, lasts 40 to 50 ns against the usual 59. Stalls land at
  every position about equally; only position 8 ones misread, 17 of 79 from 25 MHz asked and 0 of
  553 at 20 and below. The reported acknowledge, 3, is OK read one bit late on the second bit, and
  the clocks that follow on the wire are the probe's protocol-error recovery, not data.
- **The L1306's reply arrives about 20 ns after the rising edge that launches it**, at its own
  pins (median, in 10 ns samples).

What the analyser can and cannot see here, which shaped the script:

- **Ours' clock is resolved pulse by pulse up to 30 MHz asked**, every command at every clock.
  From 40 MHz asked both phases of a read clock are two cycles, 13.3 ns, and the analyser loses
  them, so the data bits are timed from SWDIO: TAR holds 0x55555555, so every data bit changes the
  line, and a bit lasts a whole period rather than a phase. Stock is resolved at every clock.
- **SWCLK's highs read about 1.6 ns long and its lows 1.6 ns short**, against the generator's exact
  3 + 3 cycles at 25 MHz. Periods, rise to rise, carry no such offset. Phases in the result files
  are as the analyser saw them; this Logic 8's threshold is fixed.
- **The target's SWDIO edges cross the threshold about 10 ns apart by direction**, so its bits are
  timed rising to rising, and the analysis takes nothing from target bits it needs: the probe
  checked every word, and a transfer's length comes from the command's layout and its edge count.
- **SWCLK's edges put one-sample pulses on SWDIO**, 0.4 to 1.2% of SWDIO's changes on ours
  from 2 to 10 MHz and 3% on stock at 10, nearly all at a SWCLK edge. SWCLK carries none at 2 to
  10 MHz. The target saw none of them; every word written read back.

## Retaking, after the optimisation work

Same matrices, same commands, new images. A result compares only with one of the same matrix name
(`swd-1`, `wire-1`); change the cases and the name changes with them. Stock is the same S-record
every time, so its results double as a check that the bench itself has not moved.

1. Build gcc and clang from the tree to be measured. Keep both images of each build
   (`mculink_cjtag.elf` and `mculink_cjtag_core1.elf`, which `wireclock.py model` needs) and the
   stock S-record in one directory, `<dir>`, with a `SHA256SUMS`.
2. With no analyser recording running, throughput for each image:
   `uv run tools/throughput.py run --label gcc --image <dir>/gcc-<build>.elf`, the same for clang,
   and `--label stock --image <dir>/MCU-LINK_CMSIS-DAP_V3_172.s19 --note "LinkServer's ..."`.
3. With the analyser on D6 SWDIO and D7 SWCLK at the target:
   `uv run tools/wireclock.py run --label gcc --serial <serial> --image <dir>/gcc-<build>.elf
   <captures>/gcc`, the same for stock, and clang with `--clock 10
   A full sweep takes about a minute after the image is flashed.
4. Put the bench image back with `verge update --file <the image>`.
5. Set the dates side by side: `throughput.py compare` and `wireclock.py compare` take both dates'
   files, and label a repeated firmware with its date; `wireclock.py model --firmware <checkout>
   <dir>/gcc-<build>-core1.elf results/wireclock/<date>-gcc.json` checks the emulator again.
6. The results page: add the new date to `page/sets.json` with its result labels, then build and
   publish the page as `page/README.md` says. It opens on the newest date, and its set picker offers
   and switches between them.
