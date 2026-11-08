# The results page

Build the standalone page from the committed throughput and wire-clock results, every set
`sets.json` names, or the dates given:

```sh
uv run python -I build.py
```

The last date opens by default. Earlier dates remain available in the date selector. The build
writes `out/index.html`. The `out/` folder holds the page and its fonts and nothing else, so it can
be served as it is. The project's site serves it at `/measurements/`, and the page's header links
back to the site. Opened from disk, the header links to the published site.

The page's colours, type and line patterns come from the project's design tokens. `tokens.css` and
`out/fonts/` are copies of the site repository's `static/tokens.css` and `static/fonts/`, and the
build inlines `tokens.css` into the page. To check the copies are current, run this from the site
repository (`escapement-project.github.io`):

```sh
uv run design/build.py --check ../mculink-bench/page/tokens.css ../mculink-bench/page/out/fonts/*
```

The page follows the reader's light or dark system setting until its switch is used, and then
keeps the choice in the browser. Verge is teal and NXP's firmware blue. A reference probe is grey,
told apart by its marker and name. Line patterns tell builds apart (Clang solid, GCC dashed) and
mark wrong reads: past the fastest clock a firmware reads right at, its line is dotted and its
points hollow. `extract.py` can also write the embedded data as
JSON:

```sh
uv run python -I extract.py measurements.json 2026-10-20 2026-10-21 2026-10-24 2026-10-25 2026-11-04
```

`sets.json` says which result files make up each date's set, by their labels. A set splits into one
view per wire: SWD, JTAG, and each cJTAG format. A view shows every probe measured on that wire:
ours built with GCC and with Clang, stock, and any reference probe whose results the set names. A
reference needs nothing more than a `throughput.py` result with its label in `sets.json`; a probe
with no CMSIS-DAP bulk interface, such as an XDS110 set up for HID, is measured through probe-rs
alone. Repeat runs of one probe merge, and the readout gives their range. Runs under energy capture
are series of their own, off until ticked in the key. The custom host's matrix, the stock failure
map and the wire-clock section appear on SWD, the only wire they cover.

Every comparison on the page holds the host fixed. Verge, stock and the reference probes are
compared through the same probe-rs revision and workload. A probe's own best host is compared only
with another probe's best host, such as our CMSIS-DAP host against SEGGER's DLL. probe-rs on one
probe against a vendor's own host on another says more about the hosts than the probes.

The headline shows our peak 4 KiB probe-rs write throughput as a multiple of stock's, on the wire
shown. Stock counts only up to the fastest clock it reads right at: faster, it misreads, reporting
errors or returning wrong data. The headline names that clock. Results from `throughput.py` say at
each clock they measured whether the data came back right, and the page applies the same limit to
any series that has them. For sets taken before those checks, `stock_reads_right` in `sets.json` gives stock's
fastest right clock per wire, with the published note that shows it. On a wire stock does not run,
the headline shows our peak instead. The line below compares the
peak's build with stock and every reference at 10 MHz. Open “Test conditions and firmware details”
for the target state, run metadata and build IDs.

The J-Link data lives in `reference/jlink-2026-10-22.json`. Its probe-rs results use the same
revision, 4 KiB workload, target and 10-pin connection as the 2026-10 MCU-Link measurements. They
appear as the series "J-Link Ultra · 2026-10-22", off until ticked, only in SWD views measured
through that probe-rs revision. The J-Link section below
graphs SEGGER's `TestRSpeed` and `TestWSpeed` results in their reported KByte/s units. Those
commands use a different workload. See the reference note for calculations and target state.
