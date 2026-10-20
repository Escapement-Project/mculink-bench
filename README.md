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

## The wire, measured 2026-10-20: requested against delivered clock

`tools/wireclock.py`, matrix `wire-1`, the same images, the analyser at the L1306's header (D6
SWDIO, D7 SWCLK, 100 MS/s). Results in `results/wireclock/2026-10-20-{gcc,clang,stock}.json`, the
exported captures and probe logs kept outside this repository (1.1 GB), which
`wireclock.py analyse <dir> --result <json>` reads again. All three were captured by earlier
revisions of the script whose probe side is identical to the committed one, and analysed by the
committed one; each file names both by hash. clang was taken at three clocks, as the falsifier.
Retake all three after the firmware is feature-complete and optimised, with the throughput.

Data bits, the median period over a transfer's data phase, in MHz (s: timed from SWDIO, see
below), and stock's fastest period anywhere in a transfer:

| asked | gcc read / write | clang | stock read / write | stock fastest, where |
| --- | --- | --- | --- | --- |
| 1 | 1.00 / 1.00 | | 0.93 / 0.94 | 0.99, acknowledge |
| 2 | 2.00 / 2.00 | | 1.79 / 1.85 | 2.06, acknowledge |
| 3 | 3.00 / 3.00 | | 2.50 / 2.63 | 3.07, acknowledge |
| 5 | 5.00 / 5.00 | | 3.56 / 3.85 | 4.84, acknowledge |
| 8 | 7.89 / 7.89 | | 5.01 / 5.56 | 7.91, acknowledge |
| 10 | 10.00 / 10.00 | 10.00 / 10.00 | 6.38 / 7.14 | 11.63, acknowledge |
| 12 | 11.54 / 11.55 | | 6.37 / 7.14 | 11.61, acknowledge |
| 14 | 13.63 / 13.55 | | 6.40 / 7.14 | 11.71, acknowledge |
| 16 | 14.99 / 14.87 | | 9.06 / 9.37 | 18.80, acknowledge |
| 20 | 18.76 / 18.74 | 18.77 / 18.74 | 9.08 / 9.38 | 18.83, acknowledge |
| 25 | 24.98 / 24.89 | | 9.62 / 10.80 | 19.12, request |
| 30 | 29.63 s / 29.70 s | | 9.64 / 10.80 | 19.06, request |
| 40 | 37.50 s / 37.50 s | | 9.61 / 10.80 | 19.09, request |
| 50 | 37.50 s / 50.00 s | 37.50 s / 50.00 s | 9.63 / 10.80 | 19.16, request |

- **Ours delivers the clock asked for, or the step under it.** The steps are 150 MHz over whole
  cycles: 11.54 is 13 cycles, 13.64 is 11, 18.75 is 8. At 50 MHz asked, reads run at 37.5 and writes
  at 50, which is what the emulator says: a read clock is four instructions. mculink-verge's BACKLOG
  item 15 has the timer that would bring reads to 50.
- **One edge comes a cycle early at 10 MHz.** In `DAP_Transfer`'s first RDBUFF after a write,
  rising edge 1 arrives 6.2 ns early in gcc and 7.5 ns in clang, so that one period is 93.8 ns
  (10.7 MHz) against the 100 asked; the two periods around the edge sum to exactly 200. The block
  path's RDBUFF after a write is normal, and so is every other clock from 1 to 25 MHz. The emulator
  puts the edge on time, so this is a hardware cost it does not model, in the write path like the
  turnaround below. It is the one place a read of ours runs faster than asked, which the gate exists
  to prevent; mculink-verge's BACKLOG item 16. At 25 MHz readings run up to 3% over, where the
  analyser loses pulses.
- **The emulator matches the wire.** `wireclock.py model --firmware <checkout> <core1 elf>
  <result>` sets every position beside mculink-verge's `tools/cycles.py` prediction, for every role
  the emulator has (its target never waits, so a WAIT transfer has no prediction). Reads agree
  within 0.5 ns at every position at 1 to 5 and 10 MHz, within 1.7 ns at 8 and 12 to 20 MHz, and
  within 3.3 ns at 25, in every kind. Writes agree except one position (next bullet) and the alternating artifact below. clang matches gcc within
  1 ns at every position at 20 MHz. At 10 MHz it does too, but for the first two periods of a WAIT
  retry, compiled C on both (5 ns, a cycle), and the start of a write's data, where the artifact
  builds up (up to 4.5 ns).
- **The turnaround before a write's data is 4 cycles longer than the model says**, at every clock
  from 1 to 10 MHz (26.7 ns, measured 3.95 to 4.01 cycles). From 12 to 20 MHz it is about 3 cycles,
  split over two positions. It is a DIRSET store to the GPIO block, not an APB one. Longer is the
  safe direction; it costs a 10 MHz write about 0.6% and is a gap in mculink-verge's `tools/m33.py`.
- **Stock tops out at 9.6 MHz for reads and 10.8 for writes, from 25 MHz asked.** The wire is the
  same from 25 to 50 MHz asked, period for period. Below that it moves in three steps (to 8, 10 to
  14, 16 to 20), and its data rate is always well under the request: 6.4 MHz at 10 asked.
- **Stock clocks its acknowledge faster than asked**: 11.6 MHz at 10 asked, 18.8 at 16 asked,
  2 to 3% over at 2 and 3 asked. From 25 asked its fastest period is the request, 52 ns (19.1 MHz),
  with the acknowledge close behind at 59 ns.
- **Stock's misreads at 25 MHz asked and above are the probe, not the target.** The depth-2 kind
  (2,000 commands of eight DPIDR reads) misread 4, 1, 1 and 2 times at 25, 30, 40 and 50 MHz asked
  and never below. On the wire, every misread transfer carries OK and the right DPIDR. Every one
  follows a 3.24 us stall at position 8, the clock that launches the first acknowledge bit. The
  stalls that misread come at depth 2, when the next command arrives. After the stall, position 9,
  where the second acknowledge bit is read, lasts 40 to 50 ns against the usual 59. Stalls at that
  position that read correctly leave it at 50 to 60. Stalls land at every position about equally;
  only position 8 ones misread, 8 of 89 from 25 MHz asked and 0 of 575 at 20 and below. The
  reported acknowledge, 3, is OK read one bit late on the second bit, and the 34 clocks that
  follow on the wire are the probe's protocol-error recovery, not data.
- **The L1306's reply arrives about 20 ns after the rising edge that launches it**, at its own
  pins (median, in 10 ns samples).

What the analyser can and cannot see here, which shaped the script:

- **Pulses of 20 ns are always caught and 13 ns ones are not**, so ours' clock is resolved pulse by
  pulse up to 25 MHz (where about one pulse in 1,000 to 1,500 is lost) and not from 30. Above that
  the data bits are timed from SWDIO: TAR holds 0x55555555, so every data bit changes the line, and
  a bit lasts a whole period rather than a phase. Stock is resolved at every clock.
- **SWCLK's highs read 7.8 ns long and its lows 7.8 ns short**, against the generator's exact 3 + 3
  cycles at 25 MHz. Periods, rise to rise, carry no such offset: they are what met the model above.
  Phases in the result files are as the analyser saw them; this Logic 8's threshold is fixed.
- **The target's SWDIO edges cross the threshold up to 20 ns apart by direction**, so its bits are
  timed rising to rising, and the analysis takes nothing from target bits it needs: the probe
  checked every word, and a transfer's length comes from the command's layout and its edge count.
- **Each line's edges put one-sample pulses on the other where it is low.** On SWDIO at SWCLK's
  edges: a quarter of all SWDIO changes on ours at 10 MHz, a twentieth to a tenth on stock. On
  SWCLK, just before a rising edge in a write's data: ours at 2 to 8 MHz (10 to 22,211 a capture),
  stock at no clock up to 25. The target saw none of them; every word written read back. The level
  ours drives also moves where the analyser sees SWCLK rise, by up to 10 ns, so a write's data
  periods alternate long and short in pairs that sum exactly. Ours' fastest write period is the
  analyser's, not the probe's, so `compare` and the results page take the fastest period from reads.

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
