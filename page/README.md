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

The J-Link reference data lives in `reference/jlink-2026-10-22.json`. Its two probe-rs and SEGGER
measurement paths are shown separately from the MCU-Link and stock result sets. See the adjacent
reference note for the calculation and target-state caveat.
