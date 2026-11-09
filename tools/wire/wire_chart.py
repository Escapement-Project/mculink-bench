#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Write a set's wire-clock charts, tickwave's `tickwave-chart/1`, from its session annotations.

    wire_chart.py DIR [--tickwave BIN] [--check]

DIR is one date's folder of mculink-recordings, such as mculink-recordings/2026-11-04: the charts
are written beside the recordings, so a point names its recording, sidecar and annotation by
relative URLs. CHARTS below says which recordings make which series. A series' point at a clock is
its session whose `asked` value is that clock, as `tickwave annotations check --json` resolves the
sidecar; a clock no session of the series asked for is null. One chart per clock the sessions'
values give: request, read data and write data, each against the clock asked for, log against
log. --check compares the files with a fresh run instead of writing them.
"""
import argparse
import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

DECIMAL = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?) (\S+)")

# Per date: the wire, what was recorded, and the series in drawing order. Ids, names, colours,
# patterns and markers follow the measurements page's series for the same runs. `points` adds to a
# series' point at a clock asked for: `failed` drops its value, `note` goes in the readout.
CHARTS = {
    "2026-11-04": {
        "wire": "SWD",
        "source": "mculink-recordings 2026-11-04: SWD, 64 KiB flash reads of an MSPM0L1306 through an "
                  "MCU-Link Pro, with analyser leads on the lines; a session's figure is the median over "
                  "its transfers of each one's mean period across the phase",
        "series": [
            {"recording": "gcc-swd-reads", "id": "gcc-a0e17c8", "name": "Verge, GCC build", "short": "Verge",
             "colour": "verge", "pattern": "second-build", "marker": "square"},
            {"recording": "gcc-swd-reads-capture100000", "id": "gcc-a0e17c8-capture100000",
             "name": "Verge, GCC build, capturing energy at 100,000 samples/s", "short": "Verge, capturing",
             "colour": "verge", "pattern": "second-build", "marker": "square", "capture": True},
            {"recording": "stock-swd-reads", "id": "stock-v3174", "name": "NXP's firmware V3.174", "short": "NXP",
             "colour": "nxp", "pattern": "solid", "marker": "circle",
             # Marked so the failure reads apart from a clock nobody recorded.
             "fails": "mark",
             # Under the leads it attached at 20 MHz asked only
             # long enough to FAULT, and its 25 and 50 MHz reads stopped with errors.
             "points": {"20": {"failed": True}, "25": {"note": "the read failed partway"},
                        "50": {"note": "the read failed partway"}}},
        ],
    },
}
QUANTITIES = (
    ("request", "request", "Request clock"),
    ("read", "read_data", "Read data clock"),
    ("write", "write_data", "Write data clock"),
)


def resolved(tickwave: str, folder: Path, name: str) -> list[dict]:
    r = subprocess.run([tickwave, "annotations", "check", "--json", str(folder / f"{name}.tickwave"),
                        str(folder / f"{name}.annotations.toml")], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"{name}: tickwave annotations check failed:\n{r.stderr.strip()}")
    return json.loads(r.stdout)


def megahertz(value: str, where: str) -> Decimal:
    m = DECIMAL.fullmatch(value)
    if not m or m[2] != "MHz":
        raise SystemExit(f"{where}: {value!r} is not a decimal in MHz")
    return Decimal(m[1])


def charts(folder: Path, tickwave: str) -> dict[str, dict]:
    spec = CHARTS.get(folder.name)
    if spec is None:
        raise SystemExit(f"no wire-clock charts for {folder.name}; known: {', '.join(CHARTS)}")
    # Each series' sessions by the clock asked for, as the decimal the sidecar writes.
    sessions = []
    for s in spec["series"]:
        by_clock = {}
        for a in resolved(tickwave, folder, s["recording"]):
            if a["kind"] != "session" or not (a["values"] or {}).get("asked"):
                continue
            clock = str(megahertz(a["values"]["asked"], a["id"]))
            if clock in by_clock:
                raise SystemExit(f"{s['recording']}: two sessions asked for {clock} MHz")
            by_clock[clock] = a
        unknown = set(s.get("points", {})) - set(by_clock)
        if unknown:
            raise SystemExit(f"{s['recording']}: no session asked for {sorted(unknown)} MHz")
        sessions.append(by_clock)
    clocks = sorted({c for by_clock in sessions for c in by_clock}, key=Decimal)
    out = {}
    for slug, key, title in QUANTITIES:
        series = []
        for s, by_clock in zip(spec["series"], sessions):
            points = []
            for c in clocks:
                a = by_clock.get(c)
                if a is None:
                    points.append(None)
                    continue
                extra = s.get("points", {}).get(c, {})
                # A session without this phase (no reads, say) has no figure for it, and did not fail.
                p = {}
                value = (a["values"] or {}).get(key)
                if extra.get("failed"):
                    p["failed"] = True
                elif value is not None:
                    p["value"] = str(megahertz(value, a["id"]))
                if "note" in extra:
                    p["note"] = extra["note"]
                p["evidence"] = {"recording": f"{s['recording']}.tickwave",
                                 "annotations": f"{s['recording']}.annotations.toml", "annotation": a["id"]}
                points.append(p)
            series.append({k: v for k, v in s.items() if k not in ("recording", "points")} | {"points": points})
        out[f"wire-clock-{slug}.chart.json"] = {
            "format": "tickwave-chart/1",
            "source": spec["source"],
            "x": {"name": f"{spec['wire']} clock asked for", "unit": "MHz", "scale": "log", "values": clocks},
            "y": {"name": title, "unit": "MHz", "scale": "log"},
            "reference": "y=x",
            "series": series,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--tickwave", default="tickwave")
    ap.add_argument("--check", action="store_true", help="compare with a fresh run instead of writing")
    args = ap.parse_args()
    folder = args.dir.resolve()
    stale = []
    for name, chart in charts(folder, args.tickwave).items():
        text = json.dumps(chart, indent=1, ensure_ascii=False) + "\n"
        path = folder / name
        if args.check:
            if not path.is_file() or path.read_text() != text:
                stale.append(name)
            continue
        path.write_text(text, newline="\n")
        print(f"wrote {path}")
    if stale:
        print(f"stale: {', '.join(stale)}; run wire_chart.py {args.dir}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
