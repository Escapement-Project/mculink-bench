# The results page

Build the standalone page from the committed throughput and wire-clock results, every set
`sets.json` names, or the dates given:

```sh
uv run python -I build.py
```

The last date opens by default. Earlier dates remain available in the set picker. The build
writes `out/index.html`. The `out/` folder holds the page and its fonts and nothing else, so it can
be served as it is. The project's site serves it at `/verge/measurements/`.

The page shows the project's shared header and footer, the same as every page of the project's
site, with Measurements marked as the page shown. Under the header's row of places, a row of the
page's own holds the wire and the measurement set; a new wire keeps the reader in the section they
were reading, or the nearest section above it the new wire shows. Opened from disk, the header's
links go to the published site.

The page's colours, type, line patterns, mark and favicon come from the project's design tokens,
and its header, footer and base styles from the site. These are copies of the site repository's
files:

| Copy | Of the site's |
|---|---|
| `tokens.css`, `out/fonts/` | `static/tokens.css`, `static/fonts/` |
| `dial.svg`, `favicon-verge.ico` | `design/marks/dial.svg`, `static/favicon-verge.ico` |
| `base.css` | `static/base.css`, the base every page shares |
| `header.html`, `header-verge.html`, `footer.html` | `design/header.html`, `design/header-verge.html`, `design/footer.html` |
| `face.js`, `header.js` | `static/js/face.js`, `static/js/header.js` |

The build inlines all but the fonts into the page, and fills the header in: the mark, links that
start at the site's root, and Verge's group in place of Verge's link, with Measurements marked as
this page. `template.html` holds only the
page's own markup, styles and script. To check the copies are current, run this from the site
repository (`escapement-project.github.io`):

```sh
uv run design/build.py --check ../mculink-bench/page/tokens.css ../mculink-bench/page/out/fonts/* \
    ../mculink-bench/page/dial.svg ../mculink-bench/page/favicon-verge.ico \
    ../mculink-bench/page/base.css ../mculink-bench/page/header.html ../mculink-bench/page/header-verge.html \
    ../mculink-bench/page/footer.html ../mculink-bench/page/face.js ../mculink-bench/page/header.js
```

The build inlines the copies, so the page carries them only once it is rebuilt. This fails while
`out/index.html` differs from a fresh build:

```sh
uv run python -I build.py --check
```

The page follows the reader's light or dark system setting until its switch is used, and then
keeps the choice in the browser. Verge is teal and NXP's firmware blue. A reference probe is grey,
told apart by its marker and name. Line patterns tell builds apart (Clang solid, GCC dashed) and
mark wrong reads: past the fastest clock a firmware reads right at, its line is dotted and its
points hollow.

tickwave's `<tickwave-chart>` draws the line charts, loaded from the version `build.py` pins.
`extract.py` writes each chart into the page's data in tickwave's chart format: every figure a
decimal string with the digits measured, with its verdict, failed and partial runs and range. The
page slices the custom host's matrix into charts the same way. The key above the charts says which
series they show. `extract.py` can also write the embedded data as JSON, or one view's chart
(`read`, `write` or `rt`):

```sh
uv run python -I extract.py measurements.json 2026-10-20 2026-10-21 2026-10-24 2026-10-25 2026-11-04
uv run python -I extract.py --chart 2026-11-04 swd read read.chart.json
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

The headline shows our peak 4 KiB probe-rs write throughput on the wire shown, in a window of lume.
After it, a sentence gives that peak as a multiple of stock's. Stock counts only up to the fastest clock it reads right at:
faster, it misreads, reporting errors or returning wrong data. The comparison names that clock.
Results from `throughput.py` say at each clock they measured whether the data came back right, and
the page applies the same limit to any series that has them. For sets taken before those checks,
`stock_reads_right` in `sets.json` gives stock's fastest right clock per wire, with the published
note that shows it. On a wire stock does not run, the comparison says so. The line below compares
the peak's build with stock and every reference at 10 MHz. Open “Test conditions and firmware
details” for the target state, run metadata and build IDs.

A set can have a section on the wire: prose whose figures open the analyser recordings they come
from. Its text is `wire/<date>.html`, one `<section class="wire-view" data-view="...">` for each
wire view it covers (`swd`, `jtag`, `cjtag-oscan1` and so on), shown with that view. A part names
its recordings, `<tickwave-view id="v" data-recording="gcc-swd-reads">`, and cites annotations in
them, `<tickwave-ref for="v" annotation="wire_swd/read/380567201">37.5 MHz</tickwave-ref>`. Hovering
or tapping a reference opens an inset of that stretch of wire; the recordings stay collapsed until
opened. tickwave draws them: the page loads its elements and its sheet, `tickwave.css`, from
tickwave's site at the version `build.py` pins, and the recordings from the `mculink-recordings`
repository's site, at `<date>/<name>.tickwave` beside each recording's annotations.

Each view holds its recording's title and facts, `<span slot="title">` and `<span slot="facts">`, as
tickwave gives them. tickwave's sheet lays them out before its script loads, so a row keeps its
height. Once a recording's head is in, tickwave warns in the console where the page's text differs
from the recording's.

The build checks every reference against the annotations in a checkout of `mculink-recordings`,
beside this repository by default (`--recordings DIR`). It fails on a reference that names an
annotation the recording does not have or a view outside its part, on a part for a view the set does
not have, and on sidecar files that name different recordings. Checking the sidecars against the
recordings themselves, `tickwave annotations check`, is the recordings repository's job. For a local
preview, `--recordings-url` and `--tickwave-url` point the page at local copies; `--check` refuses
them.

The J-Link data lives in `reference/jlink-2026-10-22.json`. Its probe-rs results use the same
revision, 4 KiB workload, target and 10-pin connection as the 2026-10 MCU-Link measurements. They
appear as the series "J-Link Ultra, 2026-10-22", off until ticked, only in SWD views measured
through that probe-rs revision. The J-Link section below
graphs SEGGER's `TestRSpeed` and `TestWSpeed` results in their reported KByte/s units. Those
commands use a different workload. See the reference note for calculations and target state.
