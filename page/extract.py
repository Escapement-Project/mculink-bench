"""Compact the bench's results into the data the page embeds, one set per date.

    uv run python -I extract.py data.json 2026-10-20 [later dates ...]
    uv run python -I extract.py --chart DATE VIEW ARM OUT.chart.json

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
from decimal import Decimal

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
    cases = {}
    for key, values in runs.items():
        cases[key] = ([round(statistics.median(v[0] for v in values)), round(statistics.median(v[1] for v in values), 1)]
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


def wire(date: str) -> dict | None:
    out = {}
    for label in WIRE_LABELS:
        path = f"{REPO}/results/wireclock/{date}-{label}.json"
        if not os.path.exists(path):
            continue
        r = json.load(open(path))
        rows = []
        for c in r["clocks"]:
            s = c.get("summary", {})
            row = {"mhz": c["clock_mhz"]}
            for d in ("read", "write"):
                if f"{d}_data_ns" in s:
                    row[d] = round(1000 / s[f"{d}_data_ns"], 3)
                    row[f"{d}_from"] = s[f"{d}_data_from"]
            if "fastest" in s:
                row["fastest"] = round(1000 / s["fastest"]["period_ns"], 3)
                row["fastest_at"] = s["fastest"]["position"]
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
    return out or None


def build_data(dates: list[str]) -> dict:
    manifest = json.load(open(f"{PAGE}/sets.json"))
    sets = {}
    for date in dates:
        if date not in manifest:
            raise ValueError(f"{date} is not in page/sets.json")
        t = throughput(date, manifest[date]["labels"])
        if t is None:
            raise ValueError(f"no throughput results for {date}")
        t["wire"] = wire(date) or {}
        t["context"] = manifest[date].get("context")
        t["stock_reads_right"] = manifest[date].get("stock_reads_right") or {}
        for protocol, entry in t["stock_reads_right"].items():
            if not os.path.exists(f"{REPO}/{entry['evidence']}"):
                raise ValueError(f"{date} {protocol}: no {entry['evidence']}, the evidence for stock's reads")
        sets[date] = t
    with open(f"{PAGE}/reference/jlink-2026-10-22.json") as f:
        reference = json.load(f)
    return {"sets": sets, "order": dates, "references": {"jlink": reference}}


# The page's chart encoding, by series style: colour token, line pattern and marker.
STYLE = {"gcc": ("verge", "second-build", "square"), "clang": ("verge", "solid", "circle"),
         "stock": ("nxp", "solid", "circle"), "ref0": ("context", "reference", "triangle-up"),
         "ref1": ("context", "reference", "triangle-down"), "ref2": ("context", "reference", "square")}
UNKNOWN = object()


def mhz_text(khz: float) -> str:
    return str(Decimal(int(khz)) / 1000)


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


def probe_rs_chart(date: str, data: dict, vid: str, arm: str) -> dict:
    """One of a view's 4 KiB charts as the page draws it, in tickwave's `tickwave-chart/1`."""
    if arm not in ("read32", "write32"):
        raise ValueError(f"{arm}: the charts are read32 and write32")
    view = next((v for v in data["views"] if v["id"] == vid), None)
    if view is None:
        raise ValueError(f"{date} has no view {vid}: {[v['id'] for v in data['views']]}")
    recorded = data["stock_reads_right"].get(view["protocol"])
    part = lambda s, k: next((p for p in s["probe_rs"] if p["arm"] == arm and p["bytes"] == 4096 and p["khz"] == k), None)
    complete = lambda p: p is not None and p["runs"] == p["of"]
    clocks = sorted({p["khz"] for s in view["series"] for p in s["probe_rs"]
                     if (p["arm"] == "read32" and p["bytes"] == 4096) or p["arm"] == "failed"})
    series = []
    for s in view["series"]:
        colour, pattern, marker = STYLE[s["style"]]
        if s["condition"] and s.get("cond_index", 0) % 2:
            pattern = "capture-second"
        points = []
        for k in clocks:
            p = part(s, k)
            failed = any(q["khz"] == k and (q["arm"] == "failed" or not complete(q)) for q in s["probe_rs"])
            wrong = {"verdict": "wrong"} if wrong_at(s, k, recorded) else {}
            if complete(p):
                spread = {"range": [f"{p['kib_lo']:.1f}", f"{p['kib_hi']:.1f}"]} if p["runs"] > 1 and p["kib_lo"] != p["kib_hi"] else {}
                points.append({"value": f"{p['kib']:.1f}"} | spread | wrong)
            elif p is not None:
                points.append({"partial": {"failed": p["of"] - p["runs"], "of": p["of"], "value": f"{p['kib']:.1f}"}} | wrong)
            elif failed:
                points.append({"failed": True})
            else:
                points.append(None)
        series.append({"id": s["id"], "name": s["name"], "short": s["name"], "colour": colour, "pattern": pattern,
                       "marker": marker} | ({"capture": True} if s["condition"] else {})
                      | ({"hidden": True} if s["condition"] else {}) | ({"fails": "mark"} if s["role"] == "ours" else {})
                      | {"points": points})
    what = {"read32": "reads", "write32": "writes"}[arm]
    title = TITLE.get(view["protocol"], view["protocol"])
    return {
        "format": "tickwave-chart/1",
        "source": f"mculink-bench results/throughput/{date}: {view['title']}, probe-rs 4 KiB {what} on the "
                  f"{view['meta']['chip']}; each run's figure the median of {view['meta']['repeats']}, attach "
                  "excluded, and a point the median of the runs",
        # The page pads the axis by 0.8 below the first clock and 1.25 above the last.
        "x": {"name": f"{title} clock asked for", "unit": "MHz", "scale": "log", "values": [mhz_text(k) for k in clocks],
              "min": str((Decimal(mhz_text(clocks[0])) * Decimal("0.8")).normalize()),
              "max": str((Decimal(mhz_text(clocks[-1])) * Decimal("1.25")).normalize())},
        "y": {"name": f"4 KiB {what}", "unit": "KiB/s", "scale": "linear"},
        "series": series,
    }


def main() -> None:
    if len(sys.argv) == 6 and sys.argv[1] == "--chart":
        _, _, date, vid, arm, out = sys.argv
        chart = probe_rs_chart(date, build_data([date])["sets"][date], vid, arm)
        with open(out, "w") as f:
            f.write(json.dumps(chart, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {out}: {len(chart['series'])} series at {len(chart['x']['values'])} clocks")
        return
    if len(sys.argv) < 3:
        sys.exit("usage: extract.py OUTPUT.json DATE [DATE ...] | extract.py --chart DATE VIEW ARM OUT.chart.json")
    data = build_data(sys.argv[2:])
    with open(sys.argv[1], "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print({d: {v["id"]: [x["id"] for x in v["series"]] for v in s["views"]} for d, s in data["sets"].items()})


if __name__ == "__main__":
    main()
