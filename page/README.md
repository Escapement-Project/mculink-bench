# The results page

Build the standalone page from the committed throughput and wire-clock results:

```sh
uv run python -I build.py 2026-10-20 2026-10-21 2026-10-24
```

The last date opens by default. Earlier dates remain available in the date selector. The generated
`index.html` is ready to serve as a static page. `extract.py` can also write the embedded data as
JSON:

```sh
uv run python -I extract.py /tmp/measurements.json 2026-10-20 2026-10-21 2026-10-24
```

Every comparison on the page holds the host fixed. Verge, stock and the J-Link Ultra are compared
through the same probe-rs revision and workload. A probe's own best host is compared only with
another probe's best host, such as our CMSIS-DAP host against SEGGER's DLL. probe-rs on one probe
against a vendor's own host on another says more about the hosts than the probes.

The headline shows peak 4 KiB probe-rs write throughput as a multiple of stock throughput. Its
detail gives both rates and requested clocks. The line below uses the same compiler build to
compare Verge with stock and J-Link Ultra at 10 MHz. Open “Test conditions and firmware details”
for the target state, run metadata and build IDs. The chart key identifies GCC, Clang and stock.

The J-Link data lives in `reference/jlink-2026-10-22.json`. Its probe-rs results use the same
revision, 4 KiB workload, target and 10-pin connection as the MCU-Link measurements. They support
comparison between probes; the runs were collected on different dates. Use the J-Link toggle
above the read chart to show or hide its probe-rs series at shared rates on both 4 KiB charts. The
J-Link section below graphs SEGGER's `TestRSpeed` and `TestWSpeed` results in their reported
KByte/s units. Those commands use a different workload. See the reference note for calculations
and target state.
