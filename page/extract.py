"""Compact the bench's results into the data the page embeds, one set per date.

    uv run python -I extract.py data.json 2026-10-20 [later dates ...]

A set is every results/throughput/<date>-<label>.json and results/wireclock/<date>-<label>.json for
gcc, clang and stock that exists. The page opens on the last date given and switches between them.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS = ("gcc", "clang", "stock")
READ_ROLES = ("ap-read", "rdbuff", "dp-read")


def throughput(date: str) -> dict | None:
    out = {"firmwares": [], "custom": {}, "probe_rs": {}, "fails": {}}
    for label in LABELS:
        path = f"{REPO}/results/throughput/{date}-{label}.json"
        if not os.path.exists(path):
            continue
        r = json.load(open(path))
        fw = r["firmware"]
        out["firmwares"].append({
            "label": label,
            "build": fw["ours"]["build"] if fw["ours"] else "V" + fw["dap_info"]["firmware version"],
            "product": fw["dap_info"]["product"],
            "serial": r["serial"],
            "packets": [fw["dap_info"]["max packet count"], fw["dap_info"]["max packet size"]],
            "taken": r["taken"],
        })
        cases, fails = {}, []
        for c in r["custom_host"]:
            key = f"{c['command']}|{c['access']}|{c['words']}|{c['depth']}|{c['clock_mhz']}"
            if c["error"]:
                cases[key] = None
                fails.append(key)
            else:
                cases[key] = [round(c["commands_per_s"]), round(c["kib_per_s"], 1)]
        out["custom"][label] = cases
        out["fails"][label] = fails
        out["probe_rs"][label] = [
            {"khz": int(p["speed_khz"]), "arm": p["arm"], "bytes": int(p["bytes"]),
             "us": p["median_us"], "kib": round(p["bytes_per_second"] / 1024, 1)}
            for p in (r["probe_rs"] or {"points": []})["points"]
        ]
        if "meta" not in out:
            out["meta"] = {"matrix": r["matrix"], "chip": r["chip"], "dpidr": r["target"]["dpidr"],
                           "probe_rs": r["provenance"]["probe_rs"][1][:9], "repeats": r["settings"]["repeats"],
                           "run_seconds": r["settings"]["run_seconds"], "clocks": r["settings"]["clocks_mhz"],
                           "depths": r["settings"]["depths"]}
    return out if out["firmwares"] else None


def fastest_read(clock: dict) -> dict | None:
    """The shortest mean period over the read roles of every resolved kind: where the analyser's view
    of SWCLK is clean for all three firmwares."""
    best = None
    for k in clock["kinds"]:
        if not k["wire"].get("resolved"):
            continue
        for role, r in k["wire"]["roles"].items():
            if not role.startswith(READ_ROLES):
                continue
            periods = r["period_ns"]["mean"]
            j = min(range(len(periods)), key=periods.__getitem__)
            if best is None or periods[j] < best["ns"]:
                best = {"ns": round(periods[j], 2), "position": j, "role": role, "kind": k["name"]}
    return best


def wire(date: str) -> dict | None:
    out = {}
    for label in LABELS:
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
            f = fastest_read(c)
            if f:
                row["fastest"] = round(1000 / f["ns"], 3)
                row["fastest_at"] = f["position"]
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


sets = {}
for date in sys.argv[2:]:
    t, w = throughput(date), wire(date)
    if t is None:
        sys.exit(f"no throughput results for {date}")
    t["wire"] = w or {}
    sets[date] = t
json.dump({"sets": sets, "order": sys.argv[2:]}, open(sys.argv[1], "w"), separators=(",", ":"))
print({d: {"firmwares": len(s["firmwares"]), "wire": list(s["wire"])} for d, s in sets.items()})
