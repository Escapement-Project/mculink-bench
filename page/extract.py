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


def throughput(date: str) -> dict | None:
    out = {"firmwares": [], "custom": {}, "probe_rs": {}, "fails": {}, "errors": {}}
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
        cases, fails, errors = {}, [], {}
        for c in r["custom_host"]:
            key = f"{c['command']}|{c['access']}|{c['words']}|{c['depth']}|{c['clock_mhz']}"
            if c["error"]:
                cases[key] = None
                fails.append(key)
                errors[key] = c["error"]
            else:
                cases[key] = [round(c["commands_per_s"]), round(c["kib_per_s"], 1)]
        out["custom"][label] = cases
        out["fails"][label] = fails
        out["errors"][label] = errors
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
    sets = {}
    for date in dates:
        t, w = throughput(date), wire(date)
        if t is None:
            raise ValueError(f"no throughput results for {date}")
        t["wire"] = w or {}
        if date == "2026-10-24":
            t["context"] = "The target main flash had been erased during the J-Link attach on 2026-10-22. No image was restored before this retake. The target stayed halted, and the harness changed only its SRAM test page."
        sets[date] = t
    reference_path = f"{os.path.dirname(__file__)}/reference/jlink-2026-10-22.json"
    with open(reference_path) as f:
        reference = json.load(f)
    return {"sets": sets, "order": dates, "references": {"jlink": reference}}


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: extract.py OUTPUT.json DATE [DATE ...]")
    data = build_data(sys.argv[2:])
    with open(sys.argv[1], "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print({d: {"firmwares": len(s["firmwares"]), "wire": list(s["wire"])}
           for d, s in data["sets"].items()})


if __name__ == "__main__":
    main()
