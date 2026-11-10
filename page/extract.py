"""Compact the bench's results into the data the page embeds, one set per date.

    uv run python -I extract.py data.json 2026-10-20 [later dates ...]
    uv run python -I extract.py --chart DATE VIEW ARM OUT.chart.json
    uv run python -I extract.py --landing ../../escapement-project.github.io/data/landing.json [--check]

The second form writes one of a view's probe-rs charts, `read32` or `write32`, in tickwave's
`tickwave-chart/1` (tickwave's CHARTS.md): the page's figures for the 4 KiB reads or writes, with
the verdicts, ranges, failures and series styles the page draws them with.

`sets.json` names each date's result files by label (`fnmatch` patterns over
`results/throughput/<date>-<label>.json`). A set splits into views, one per wire protocol and cJTAG
format, from each file's own settings. Within a view, a series is one probe and firmware: ours
built with a compiler, stock, or a reference probe. Repeat runs (`-r1`, `-r2`) of one series merge,
each point the median of its runs with their range kept. Runs under energy capture (`-capture<rate>`)
are series of their own. `results/wireclock/<date>-<label>.json` adds the SWD wire for gcc, clang
and stock. The page opens on the last date given.

A result from a sweep that checked its data carries, at each clock, how many rounds of a flash read
and a RAM pattern read back came out right. For sets taken before that, `stock_reads_right` in
`sets.json` records the fastest clock stock read right at, per wire, and the published note that
shows it.
"""
import fnmatch
import json
import os
import re
import statistics
import sys
from decimal import ROUND_CEILING, Decimal

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.dirname(os.path.abspath(__file__))
WIRE_LABELS = ("gcc", "clang", "stock")
LABEL = re.compile(r"^(?P<base>.+?)(?:-(?P<proto>swd|jtag|oscan\d))?(?:-capture(?P<rate>\d+))?(?:-r?(?P<run>\d+))?$")
TITLE = {"swd": "SWD", "jtag": "JTAG", "cjtag": "cJTAG"}


def series_of(label: str, record: dict) -> dict:
    """Who measured: the series a result belongs to, and the condition it ran under."""
    m = LABEL.match(label)
    base, rate = m["base"], m["rate"]
    fw = record["firmware"]
    if fw.get("ours"):
        compiler = base.split("-")[0]
        role, style = "ours", compiler if compiler in ("gcc", "clang") else "gcc"
        name = {"gcc": "GCC", "clang": "Clang"}.get(compiler, base)
        build = fw["ours"]["build"]
    elif base.startswith("stock"):
        role, style = "stock", "stock"
        version = (fw.get("dap_info") or {}).get("firmware version", "")
        name, build = f"Stock V{version}" if version else "Stock", f"V{version}"
    else:
        listed = fw.get("probe_rs_list") or {}
        role, style = "reference", "ref"
        name = listed.get("identifier") or (fw.get("dap_info") or {}).get("product") or base
        build = (fw.get("dap_info") or {}).get("firmware version") or ""
    sid = base + (f"-capture{rate}" if rate else "")
    condition = f"capture at {int(rate):,} pairs/s" if rate else None
    return {"id": sid, "name": name + (f", {condition}" if condition else ""), "role": role, "style": style,
            "condition": condition, "build": build}


def view_of(record: dict) -> tuple[str, str, str | None]:
    settings = record.get("settings") or {}
    protocol = settings.get("protocol") or "swd"
    fmt = settings.get("formats") if protocol == "cjtag" else None
    vid = f"{protocol}-{fmt}" if fmt else protocol
    return vid, protocol, fmt


def median_range(values: list[float]) -> tuple[float, float, float]:
    return statistics.median(values), min(values), max(values)


def merge_probe_rs(records: list[dict]) -> list[dict]:
    """Each point's median over the runs that measured it. `runs` of `of` says how many did: a
    point some run failed or never reached is not a result of the series, and the page shows it as
    failed with the other runs' value beside it."""
    points, failed = {}, {}
    for r in records:
        for p in (r.get("probe_rs") or {"points": []})["points"]:
            khz = int(p["speed_khz"])
            if p["arm"] == "failed":
                failed[khz] = failed.get(khz, 0) + 1
                continue
            points.setdefault((p["arm"], int(p["bytes"]), khz), []).append(p)
    out = []
    for (arm, size, khz), ps in sorted(points.items()):
        kib, kib_lo, kib_hi = median_range([p["bytes_per_second"] / 1024 for p in ps])
        us = statistics.median(p["median_us"] for p in ps)
        out.append({"khz": khz, "arm": arm, "bytes": size, "us": us, "kib": round(kib, 1),
                    "kib_lo": round(kib_lo, 1), "kib_hi": round(kib_hi, 1), "runs": len(ps), "of": len(records)})
    # A clock fails a series when no run completed it.
    done = {p["khz"] for p in out}
    out += [{"khz": khz, "arm": "failed", "runs": n, "of": len(records)} for khz, n in sorted(failed.items()) if khz not in done]
    return out


def merge_checks(records: list[dict]) -> list[dict] | None:
    """Each check's rounds right at each clock, summed over the runs; None unless every run
    checked. `runs` of `of` says how many runs checked that clock: a clock that failed to measure
    in a run has no checks there."""
    if any(not (r.get("probe_rs") or {}).get("checks") for r in records):
        return None
    merged = {}
    for r in records:
        for c in r["probe_rs"]["checks"]:
            m = merged.setdefault((c["check"], c["speed_khz"]), {"check": c["check"], "khz": c["speed_khz"],
                                                                  "right": 0, "rounds": 0, "runs": 0})
            m["right"] += c["right"]
            m["rounds"] += c["rounds"]
            m["runs"] += 1
    return [m | {"of": len(records)} for _, m in sorted(merged.items(), key=lambda kv: (kv[0][1], kv[0][0]))]


def merge_custom(records: list[dict]) -> tuple[dict, list, dict]:
    """Each case's median over the runs; a case any run failed has no result."""
    runs, errors = {}, {}
    for r in records:
        for c in r["custom_host"]:
            key = f"{c['command']}|{c['access']}|{c['words']}|{c['depth']}|{c['clock_mhz']}"
            runs.setdefault(key, [])
            if c["error"]:
                errors.setdefault(key, c["error"])
            else:
                runs[key].append((c["commands_per_s"], c["kib_per_s"]))
    # As decimal strings, commands a second whole and KiB/s to a tenth: the page charts them as they
    # are, and a JavaScript number would drop a trailing zero.
    cases = {}
    for key, values in runs.items():
        cases[key] = ([str(round(statistics.median(v[0] for v in values))), f"{statistics.median(v[1] for v in values):.1f}"]
                      if values and key not in errors else None)
    fails = [k for k, v in cases.items() if v is None]
    return cases, fails, {k: e for k, e in errors.items() if k in fails}


def throughput(date: str, patterns: list[str]) -> dict | None:
    folder = f"{REPO}/results/throughput"
    prefix = f"{date}-"
    labels = sorted(f[len(prefix):-5] for f in os.listdir(folder) if f.startswith(prefix) and f.endswith(".json"))
    labels = [lb for lb in labels if any(fnmatch.fnmatchcase(lb, p) for p in patterns)]
    groups: dict[tuple, dict] = {}
    for label in labels:
        record = json.load(open(f"{folder}/{prefix}{label}.json"))
        vid, protocol, fmt = view_of(record)
        info = series_of(label, record)
        group = groups.setdefault((vid, info["id"]), {"view": (vid, protocol, fmt), "info": info, "records": []})
        group["records"].append(record)
    if not groups:
        return None
    check_consistent(date, groups)
    views: dict[str, dict] = {}
    order = {"ours": 0, "stock": 1, "reference": 2}
    for (vid, _), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[1]["info"]["condition"] or "",
                                                              order[kv[1]["info"]["role"]],
                                                              {"gcc": 0, "clang": 1}.get(kv[1]["info"]["style"], 2),
                                                              kv[1]["info"]["id"])):
        _, protocol, fmt = g["view"]
        first = g["records"][0]
        view = views.setdefault(vid, {
            "id": vid, "protocol": protocol, "format": fmt,
            "title": TITLE.get(protocol, protocol) + (f" {fmt.replace('oscan', 'OScan')}" if fmt else ""),
            "series": [],
            "meta": {"matrix": first["matrix"], "chip": first["chip"],
                     "dpidr": (first.get("target") or {}).get("dpidr"),
                     "probe_rs": first["provenance"]["probe_rs"][1][:9], "repeats": first["settings"]["repeats"],
                     "run_seconds": first["settings"]["run_seconds"], "clocks": first["settings"]["clocks_mhz"],
                     "depths": first["settings"]["depths"]},
        })
        fw = first["firmware"]
        dap_info = fw.get("dap_info") or {}
        custom, fails, errors = merge_custom(g["records"])
        view["series"].append(g["info"] | {
            "product": dap_info.get("product") or (fw.get("probe_rs_list") or {}).get("identifier", ""),
            "serial": first["serial"],
            "packets": [dap_info["max packet count"], dap_info["max packet size"]] if dap_info else None,
            "taken": first["taken"], "runs": len(g["records"]),
            "probe_rs": merge_probe_rs(g["records"]),
            "checks": merge_checks(g["records"]),
            "custom": custom, "fails": fails, "errors": errors,
        })
    # References get their own colours in the order they appear, and runs under a condition their
    # own dash. Two series of one name tell their builds apart.
    for view in views.values():
        names = [s["name"] for s in view["series"]]
        for s in view["series"]:
            if names.count(s["name"]) > 1 and s["build"]:
                s["name"] = f"{s['name']} ({s['build']})"
        n, conditions = 0, {}
        for s in view["series"]:
            if s["role"] == "reference":
                s["style"], n = f"ref{n % 3}", n + 1
            if s["condition"]:
                s["cond_index"] = conditions.setdefault(s["condition"], len(conditions))
    ordered = sorted(views.values(), key=lambda v: ({"swd": 0, "jtag": 1, "cjtag": 2}.get(v["protocol"], 3), v["id"]))
    return {"views": ordered}


def check_consistent(date: str, groups: dict) -> None:
    """A view compares only results taken through one probe-rs revision and matrix, and a series
    merges only runs of one build on one unit."""
    by_view: dict[str, set] = {}
    for (vid, sid), g in groups.items():
        for r in g["records"]:
            by_view.setdefault(vid, set()).add((r["provenance"]["probe_rs"][1], r["matrix"]))
        builds = {(json.dumps(r["firmware"].get("ours"), sort_keys=True), json.dumps(r["firmware"].get("dap_info"), sort_keys=True),
                   json.dumps(r["firmware"].get("probe_rs_list"), sort_keys=True), r["serial"]) for r in g["records"]}
        if len(builds) > 1:
            raise ValueError(f"{date} {vid}: the runs of {sid} differ in firmware or unit")
    for vid, hosts in by_view.items():
        if len(hosts) > 1:
            raise ValueError(f"{date} {vid}: results from more than one probe-rs revision or matrix: {sorted(hosts)}")


def wire(date: str) -> tuple[dict, dict]:
    """The set's SWD wire per firmware, and its three wire-clock charts; both empty without it."""
    out, figures = {}, {}
    for label in WIRE_LABELS:
        path = f"{REPO}/results/wireclock/{date}-{label}.json"
        if not os.path.exists(path):
            continue
        r = json.load(open(path))
        rows = []
        for c in r["clocks"]:
            s = c.get("summary", {})
            row = {"mhz": c["clock_mhz"]}
            fig = figures.setdefault(label, {})[c["clock_mhz"]] = {}
            for d in ("read", "write"):
                if f"{d}_data_ns" in s:
                    row[d] = round(1000 / s[f"{d}_data_ns"], 3)
                    row[f"{d}_from"] = s[f"{d}_data_from"]
                    fig[d] = (1000 / s[f"{d}_data_ns"], s[f"{d}_data_from"])
            if "fastest" in s:
                row["fastest"] = round(1000 / s["fastest"]["period_ns"], 3)
                row["fastest_at"] = s["fastest"]["position"]
                fig["fastest"] = (1000 / s["fastest"]["period_ns"], None)
            pipelined = next((k for k in c["kinds"] if k["depth"] > 1), None)
            if pipelined:
                w = pipelined["wire"]
                row["commands"] = pipelined["host"]["commands"]
                row["failed"] = len(pipelined["host"]["failures"])
                row["stalls"] = sum(w.get("stalls_by_position", {}).values())
                row["stalls8"] = w.get("stalls_by_position", {}).get("8", 0)
                row["misreads8"] = sum(1 for m in w.get("misreads", []) if m.get("stall_position") == 8)
                row["misreads_ok"] = sum(1 for m in w.get("misreads", []) if m.get("wire_ack") == "OK")
            delay = next((k["wire"]["target_delay_ns"]["median"] for k in c["kinds"]
                          if "target_delay_ns" in k["wire"]), None)
            if delay is not None:
                row["delay"] = round(delay)
            rows.append(row)
        entry = {"rows": rows, "taken": r["taken"], "matrix": r["matrix"]}
        # The transfer around the acknowledge, as usual and as the last misread at the highest clock
        # that had one: SWCLK drawn to scale from the per-position means.
        misread = [(c, m) for c in r["clocks"] for k in c.get("kinds", []) if k["depth"] > 1
                   for m in k["wire"].get("misreads", []) if "period_ns" in m]
        if misread:
            c, m = max(misread, key=lambda cm: cm[0]["clock_mhz"])
            kind = next(k for k in c["kinds"] if k["depth"] > 1)
            usual = kind["wire"]["roles"][m["role"]]
            span = range(5, 14)
            entry["trace"] = {
                "mhz": c["clock_mhz"], "positions": list(span),
                "usual": [[round(usual["high_ns"]["mean"][j], 1), round(usual["low_ns"]["mean"][j], 1)] for j in span],
                "misread": [[m["high_ns"][j], m["low_ns"][j]] for j in span],
                "host_ack": m["host_ack"], "wire_ack": m["wire_ack"],
            }
        out[label] = entry
    return out, wire_charts(date, figures) if figures else {}


WIRE_NAMES = {"gcc": "GCC", "clang": "Clang", "stock": "Stock"}
WIRE_FIGURES = {"read": "Read data bits", "write": "Write data bits", "fastest": "Peak SWD rate"}


def wire_charts(date: str, figures: dict) -> dict:
    """The clock on the wire against the clock asked for, log against log, in MHz to the hundredth
    as the page shows it; a figure timed from SWDIO edges, where SWCLK was too fast to resolve,
    takes a ring."""
    clocks = sorted({m for by in figures.values() for m in by})
    charts = {}
    for key, title in WIRE_FIGURES.items():
        series = []
        for label in WIRE_LABELS:
            if label not in figures:
                continue
            points = []
            for m in clocks:
                f = figures[label].get(m, {}).get(key)
                points.append(None if f is None else {"value": f"{f[0]:.2f}"}
                              | ({"ring": True, "note": "from SWDIO"} if f[1] == "swdio" else {}))
            look = look_of({"style": label, "condition": None})
            series.append(series_head({"id": label, "name": WIRE_NAMES[label]}, look) | {"points": points})
        charts[key] = {"format": "tickwave-chart/1",
                       "source": f"mculink-bench results/wireclock/{date}: SWD, the analyser at 100 MS/s at the target; "
                                 f"{title.lower()}, the median over each clock's transfers",
                       "x": clock_axis("SWD clock asked for", [dec(m) for m in clocks]),
                       "y": {"name": title, "unit": "MHz", "scale": "log"},
                       "reference": "y=x", "series": series}
    return charts


def build_data(dates: list[str]) -> dict:
    manifest = json.load(open(f"{PAGE}/sets.json"))
    with open(f"{PAGE}/reference/jlink-2026-10-22.json") as f:
        reference = json.load(f)
    reference["look"] = JLINK_LOOK
    sets = {}
    for date in dates:
        if date not in manifest:
            raise ValueError(f"{date} is not in page/sets.json")
        t = throughput(date, manifest[date]["labels"])
        if t is None:
            raise ValueError(f"no throughput results for {date}")
        t["wire"], t["wire_charts"] = wire(date)
        t["context"] = manifest[date].get("context")
        t["stock_reads_right"] = manifest[date].get("stock_reads_right") or {}
        for protocol, entry in t["stock_reads_right"].items():
            if not os.path.exists(f"{REPO}/{entry['evidence']}"):
                raise ValueError(f"{date} {protocol}: no {entry['evidence']}, the evidence for stock's reads")
        for view in t["views"]:
            for s in view["series"]:
                s["look"] = look_of(s)
            view["charts"] = view_charts(date, view, t["stock_reads_right"].get(view["protocol"]), reference)
        sets[date] = t
    reference["chart"] = jlink_chart(reference)
    return {"sets": sets, "order": dates, "references": {"jlink": reference}}


# The charts, in tickwave's `tickwave-chart/1` (tickwave's CHARTS.md): every value a decimal string
# with the digits the page shows, and the verdicts, failures and ranges the page draws.

# The page's encoding, by series style: colour token, line pattern and marker. A run under capture
# keeps its build's pattern, the second condition of a view takes the long dash.
STYLE = {"gcc": ("verge", "second-build", "square"), "clang": ("verge", "solid", "circle"),
         "stock": ("nxp", "solid", "circle"), "ref0": ("context", "reference", "triangle-up"),
         "ref1": ("context", "reference", "triangle-down"), "ref2": ("context", "reference", "square")}
JLINK_LOOK = {"colour": "context", "pattern": "reference", "marker": "diamond", "capture": False}
UNKNOWN = object()


def look_of(s: dict) -> dict:
    colour, pattern, marker = STYLE[s["style"]]
    if s["condition"] and s.get("cond_index", 0) % 2:
        pattern = "capture-second"
    return {"colour": colour, "pattern": pattern, "marker": marker, "capture": bool(s["condition"])}


def dec(x) -> str:
    """A decimal as plain text, without trailing zeros or an exponent: 50, 37.5, 0.8."""
    return format((x if isinstance(x, Decimal) else Decimal(str(x))).normalize(), "f")


def mhz_text(khz: float) -> str:
    return dec(Decimal(int(khz)) / 1000)


def clock_axis(name: str, mhz: list[str]) -> dict:
    # The page pads the axis by 0.8 below the first clock and 1.25 above the last.
    return {"name": name, "unit": "MHz", "scale": "log", "values": mhz,
            "min": dec(Decimal(mhz[0]) * Decimal("0.8")), "max": dec(Decimal(mhz[-1]) * Decimal("1.25"))}


def series_head(s: dict, look: dict) -> dict:
    return ({"id": s["id"], "name": s["name"], "short": s["name"]} | {k: look[k] for k in ("colour", "pattern", "marker")}
            | ({"capture": True} if look["capture"] else {}))


def right_up_to(s: dict, recorded: dict | None):
    """The template's rightUpTo: the clock up to which a series read right, None if it read wrong
    at the slowest, UNKNOWN where nothing says."""
    checks = s.get("checks")
    if not checks:
        if s["role"] != "stock" or not recorded or "up_to_khz" not in recorded:
            return UNKNOWN
        return recorded["up_to_khz"]
    kinds = {c["check"] for c in checks}
    up_to = None
    for khz in sorted({c["khz"] for c in checks}):
        rows = [next((c for c in checks if c["khz"] == khz and c["check"] == k), None) for k in kinds]
        if any(c is None or c["runs"] != c["of"] for c in rows):
            continue
        if any(c["right"] != c["rounds"] for c in rows):
            break
        up_to = khz
    return up_to


def wrong_at(s: dict, khz: int, recorded: dict | None) -> bool:
    """The template's wrongClocks, at one clock."""
    checks = s.get("checks")
    if checks:
        return any(c["khz"] == khz and c["runs"] == c["of"] and c["right"] != c["rounds"] for c in checks)
    up_to = right_up_to(s, recorded)
    return up_to is None or (up_to is not UNKNOWN and khz > up_to)


def jlink_series(reference: dict) -> dict:
    """The J-Link as the template's JLINK: a reference series, shown only when ticked."""
    return {"id": "jlink", "name": f"J-Link Ultra, {reference['date']}", "role": "reference", "condition": None,
            "external": True, "checks": None, "look": JLINK_LOOK,
            "probe_rs": [{"khz": int(p["mhz"] * 1000), "arm": arm, "bytes": 4096, "kib": p[key], "runs": 1, "of": 1}
                         for p in reference["probe_rs"] for arm, key in (("read32", "read_kib_s"), ("write32", "write_kib_s"))]}


# A probe-rs chart: the arm, its transfer size, the figure, the y axis and what a figure is.
ARMS = {"read": ("read32", 4096, "kib", "4 KiB reads", "KiB/s"),
        "write": ("write32", 4096, "kib", "4 KiB writes", "KiB/s"),
        "rt": ("rt", 4, "us", "Single-word read round trip", "µs")}


def probe_rs_chart(date: str, view: dict, which: str, recorded: dict | None, series: list[dict]) -> dict:
    """One of a view's probe-rs charts as the page draws it: its clocks are those any series read
    4 KiB at or failed at, and a point the series' median over the runs that completed it."""
    arm, size, field, title, unit = ARMS[which]
    part = lambda s, k: next((p for p in s["probe_rs"] if p["arm"] == arm and p["bytes"] == size and p["khz"] == k), None)
    complete = lambda p: p is not None and p["runs"] == p.get("of", p["runs"])
    clocks = sorted({p["khz"] for s in series for p in s["probe_rs"]
                     if (p["arm"] == "read32" and p["bytes"] == 4096) or p["arm"] == "failed"})
    out = []
    for s in series:
        points = []
        for k in clocks:
            p = part(s, k)
            failed = any(q["khz"] == k and (q["arm"] == "failed" or not complete(q)) for q in s["probe_rs"])
            wrong = {"verdict": "wrong"} if wrong_at(s, k, recorded) else {}
            if complete(p):
                spread = ({"range": [f"{p['kib_lo']:.1f}", f"{p['kib_hi']:.1f}"]}
                          if field == "kib" and p["runs"] > 1 and p.get("kib_lo") is not None and p["kib_lo"] != p["kib_hi"] else {})
                points.append({"value": f"{p[field]:.1f}"} | spread | wrong)
            elif p is not None:
                points.append({"partial": {"failed": p["of"] - p["runs"], "of": p["of"], "value": f"{p[field]:.1f}"}} | wrong)
            elif failed:
                points.append({"failed": True})
            else:
                points.append(None)
        out.append(series_head(s, s["look"]) | ({"hidden": True} if s["condition"] or s.get("external") else {})
                   | ({"fails": "mark"} if s["role"] == "ours" else {}) | {"points": points})
    each = "each run's figure the median of {}, attach excluded, and a point the median of the runs"
    return {
        "format": "tickwave-chart/1",
        "source": f"mculink-bench results/throughput/{date}: {view['title']}, probe-rs, {title.lower()} on the "
                  f"{view['meta']['chip']}; " + each.format(view["meta"]["repeats"]),
        "x": clock_axis(f"{TITLE.get(view['protocol'], view['protocol'])} clock asked for", [mhz_text(k) for k in clocks]),
        "y": {"name": title, "unit": unit, "scale": "linear"},
        "series": out,
    }


def jlink_here(view: dict, reference: dict) -> bool:
    """The J-Link ran through one probe-rs revision; it joins only a view measured through it."""
    return view["protocol"] == "swd" and view["meta"]["probe_rs"] in reference["host_tool"]


def view_charts(date: str, view: dict, recorded: dict | None, reference: dict) -> dict:
    with_jlink = view["series"] + ([jlink_series(reference)] if jlink_here(view, reference) else [])
    return {"read": probe_rs_chart(date, view, "read", recorded, with_jlink),
            "write": probe_rs_chart(date, view, "write", recorded, with_jlink),
            "rt": probe_rs_chart(date, view, "rt", recorded, view["series"])}


def jlink_chart(reference: dict) -> dict:
    rows = reference["jlinkexe"]
    series = [({"id": f"jlink-{d}", "name": name, "short": name, "colour": "context", "pattern": pattern, "marker": "diamond"}
               | {"points": [{"value": dec(p[f"{d}_kbyte_s"])} for p in rows]})
              for d, name, pattern in (("read", "TestRSpeed, read", "solid"), ("write", "TestWSpeed, write", "second-build"))]
    return {"format": "tickwave-chart/1",
            "source": f"page/reference/jlink-2026-10-22.json: {reference['segger_tool']}, {reference['firmware']}",
            "x": clock_axis("SWD clock asked for", [dec(p["mhz"]) for p in rows]),
            "y": {"name": "JLinkExe TestRSpeed and TestWSpeed", "unit": "KByte/s", "scale": "linear"},
            "series": series}


# The site's figures (its data/landing.json): one set's SWD 4 KiB writes, as the headline on the
# landing page and Verge's page, and Table 1 and Figure 1 on Verge's page. Per series: the landing's
# id, the page's style, Table 1's name, the chart's name and its short name.
LANDING = {"date": "2026-11-04", "view": "swd"}
LANDING_SERIES = [("clang", "clang", "Clang build", "Verge, Clang build", "Verge"),
                  ("stock", "stock", "Stock V3.174", "NXP's firmware V3.174", "NXP"),
                  ("gcc", "gcc", "GCC build", "Verge, GCC build", "GCC")]
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def landing_data(data: dict) -> dict:
    date, vid = LANDING["date"], LANDING["view"]
    st = data["sets"][date]
    view = next(v for v in st["views"] if v["id"] == vid)
    recorded = st["stock_reads_right"].get(view["protocol"])
    page = {lid: next(s for s in view["series"] if s["style"] == style and not s["condition"])
            for lid, style, *_ in LANDING_SERIES}
    names = {lid: (table, chart, short) for lid, _, table, chart, short in LANDING_SERIES}
    write = view["charts"]["write"]
    clocks = [int(Decimal(m) * 1000) for m in write["x"]["values"]]
    point = lambda s, k: next((p for p in s["probe_rs"] if p["arm"] == "write32" and p["bytes"] == 4096 and p["khz"] == k), None)
    failed_at = lambda s, k: next((p for p in s["probe_rs"] if p["arm"] == "failed" and p["khz"] == k), None)
    complete = lambda p: p is not None and p["runs"] == p["of"]

    def peak(series: list[dict]):
        """The page's peakWrite: the fastest complete 4 KiB write at a clock the series reads right at."""
        best = None
        for s in series:
            up = right_up_to(s, recorded)
            for p in s["probe_rs"]:
                if up is not UNKNOWN and (up is None or p["khz"] > up):
                    continue
                if complete(p) and p["arm"] == "write32" and p["bytes"] == 4096 and (best is None or p["kib"] > best[1]["kib"]):
                    best = (s, p)
        return best

    ours = peak([s for s in view["series"] if s["role"] == "ours" and not s["condition"]])
    theirs = peak([page["stock"]])
    ours_id = next(lid for lid, s in page.items() if s is ours[0])
    stock_up = right_up_to(page["stock"], recorded)

    # Table 1: each series' figure at each clock as the page prints it.
    table, notes = {}, []
    for lid, s in page.items():
        cells = []
        for k in clocks:
            p = point(s, k)
            if p is not None:
                cells.append(f"{p['kib']:,.1f}")
                if not complete(p):
                    ran, failed = p["runs"], p["of"] - p["runs"]
                    notes.append(f"{names[lid][2]}'s figure at {mhz_text(k)} MHz is from {WORDS[ran]} run{'s' if ran > 1 else ''}: "
                                 f"the other{'s' if failed > 1 else ''} failed.")
            else:
                cells.append("failed" if failed_at(s, k) else "–")
        table[lid] = cells
    # Figure 1's caption: past the clock stock reads right up to, how its runs failed.
    stock = page["stock"]
    fails = []
    for k in clocks:
        p, f = point(stock, k), failed_at(stock, k)
        if f is not None and p is None:
            fails.append(f"{'both' if f['runs'] == f['of'] == 2 else 'all ' + WORDS[f['of']] if f['runs'] == f['of'] else WORDS[f['runs']] + ' of ' + WORDS[f['of']]} at {mhz_text(k)} MHz")
        elif p is not None and not complete(p):
            fails.append(f"{WORDS[p['of'] - p['runs']]} of {WORDS[p['of']]} at {mhz_text(k)} MHz")
    last = max(k for k in clocks if (p := point(stock, k)) is not None and complete(p))
    figure_note = (f"Past {mhz_text(stock_up)} MHz NXP's firmware reads wrong"
                   + (f", and its runs failed: {', '.join(fails)}" if fails else "") + f". Its line ends at {mhz_text(last)} MHz.")

    runs = page[ours_id]["runs"]
    source = (f"mculink-bench results/throughput/{date}: {view['title']}, probe-rs 4 KiB writes on an {view['meta']['chip']}, "
              f"the median of {WORDS[runs]} runs; stock reads right up to {mhz_text(stock_up)} MHz")
    # Figure 1: the view's write chart, its series under the landing's names, without the ranges.
    by_id = {s["id"]: s for s in write["series"]}
    series = []
    for lid, *_ in LANDING_SERIES:
        s = by_id[page[lid]["id"]]
        head = {k: v for k, v in s.items() if k not in ("id", "name", "short", "points")}
        series.append({"id": lid, "name": names[lid][1], "short": names[lid][2]} | head
                      | ({"hidden": True} if lid != ours_id and page[lid]["role"] == "ours" else {})
                      | {"points": [None if q is None else {k: v for k, v in q.items() if k != "range"} for q in s["points"]]})
    top = max(Decimal(q["value"]) for s in series for q in s["points"] if q and "value" in q)
    step = Decimal(500)
    figure1 = {"format": "tickwave-chart/1", "source": source,
               "x": {k: v for k, v in write["x"].items() if k not in ("min", "max")},
               "y": {"name": "4 KiB writes", "unit": "KiB/s", "scale": "linear",
                     "max": dec((top / step).to_integral_value(rounding=ROUND_CEILING) * step), "step": dec(step)},
               "series": series,
               "limits": [{"x": mhz_text(stock_up), "series": "stock",
                           "label": f"NXP's firmware reads right up to {mhz_text(stock_up)} MHz"}]}
    rows = []
    for lid, *_ in [LANDING_SERIES[0], LANDING_SERIES[2], LANDING_SERIES[1]]:
        s, up = page[lid], right_up_to(page[lid], recorded)
        rows.append({"id": lid, "name": names[lid][0], "role": s["role"], "table": table[lid]}
                    | ({"rightUpTo": int(up) // 1000 if up is not None else 0} if up is not UNKNOWN else {}))
    return {"source": source, "date": date, "clocks": [int(k) // 1000 if k % 1000 == 0 else k / 1000 for k in clocks],
            "series": rows, "tableNote": " ".join(notes), "figureNote": figure_note,
            "headline": {"ratio": f"{ours[1]['kib'] / theirs[1]['kib']:.2f}", "ours": f"{round(ours[1]['kib']):,}",
                         "oursAt": ours[1]["khz"] // 1000, "oursName": names[ours_id][0],
                         "stock": f"{round(theirs[1]['kib']):,}", "stockAt": theirs[1]["khz"] // 1000},
            "figure1": figure1}


def main() -> None:
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--landing":
        out = sys.argv[2]
        text = json.dumps(landing_data(build_data([LANDING["date"]])), indent=2, ensure_ascii=False) + "\n"
        if sys.argv[3:] == ["--check"]:
            if not os.path.exists(out) or open(out).read() != text:
                sys.exit(f"stale: {out} differs from extract.py --landing")
            print(f"current: {out}")
            return
        with open(out, "w") as f:
            f.write(text)
        print(f"wrote {out}")
        return
    if len(sys.argv) == 6 and sys.argv[1] == "--chart":
        _, _, date, vid, which, out = sys.argv
        views = build_data([date])["sets"][date]["views"]
        view = next((v for v in views if v["id"] == vid), None)
        if view is None or which not in view["charts"]:
            sys.exit(f"{date}: views {[v['id'] for v in views]}, charts {list(ARMS)}")
        chart = view["charts"][which]
        with open(out, "w") as f:
            f.write(json.dumps(chart, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out}: {len(chart['series'])} series at {len(chart['x']['values'])} clocks")
        return
    if len(sys.argv) < 3:
        sys.exit("usage: extract.py OUTPUT.json DATE [DATE ...] | extract.py --chart DATE VIEW read|write|rt OUT.chart.json")
    data = build_data(sys.argv[2:])
    with open(sys.argv[1], "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print({d: {v["id"]: [x["id"] for x in v["series"]] for v in s["views"]} for d, s in data["sets"].items()})


if __name__ == "__main__":
    main()
