# /// script
# dependencies = ["rich"]
# ///
"""Time J-Flash production programming (erase, program, verify) on the Flasher, per interface,
image and clock (BACKLOG 70). Run it alone, with nothing else using the bench:

    uv run --no-project --with rich python -I jflash_sweep.py --out runs/jflash.json

Uses the per-clock project copies in jflash/runs/ (sectors 0-42 only, never the CCFG sector). A
clock that fails once is recorded and the sweep moves on to the next clock."""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

HERE = Path(__file__).resolve().parent
SERIAL = "100000001"
COMPLETED = re.compile(r"successfully - Completed after ([0-9.]+) sec")


def run_once(project: Path, image: Path, log: Path) -> dict:
    log.unlink(missing_ok=True)
    start = time.monotonic()
    done = subprocess.run(["JFlashExe", f"-jflashlog{log}", f"-openprj{project}", f"-open{image}",
                           f"-usb{SERIAL}", "-auto", "-exit", "-hide"],
                          capture_output=True, text=True, timeout=300)
    wall = time.monotonic() - start
    text = log.read_text(errors="replace") if log.exists() else ""
    found = COMPLETED.search(text)
    ok = done.returncode == 0 and found is not None
    return {"exit": done.returncode, "wall_s": round(wall, 4),
            "jflash_s": float(found.group(1)) if found else None,
            "error": None if ok else (text.strip().splitlines()[-8:] or done.stderr.strip().splitlines()[-4:])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--interfaces", default="jtag,cjtag")
    parser.add_argument("--images", default="itm_counter.hex,itm_counter_fill344k.hex")
    parser.add_argument("--clocks", default="1000,4000,10000,20000,30000,50000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--projects", default="runs",
                        help="folder under jflash/ with the per-clock project copies (runs: CRC verify, "
                             "runs-readback: readback verify)")
    args = parser.parse_args()
    interfaces, images = args.interfaces.split(","), args.images.split(",")
    clocks = [int(c) for c in args.clocks.split(",")]
    logs = HERE / "jflash" / args.projects / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    console = Console(stderr=True)
    runs = []
    with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
                  console=console) as progress:
        sweep = progress.add_task("sweep", total=len(interfaces) * len(images) * len(clocks) * args.repeats)
        for interface in interfaces:
            for image in images:
                for clock in clocks:
                    project = HERE / "jflash" / args.projects / f"{interface}-{clock}.jflash"
                    for repeat in range(args.repeats):
                        progress.update(sweep, description=f"{interface} {image} {clock} kHz r{repeat}")
                        log = logs / f"{interface}-{Path(image).stem}-{clock}-r{repeat}.log"
                        result = run_once(project, HERE / image, log)
                        runs.append({"interface": interface, "image": image, "clock_khz": clock, "repeat": repeat,
                                     **result})
                        progress.advance(sweep)
                        if result["error"]:
                            console.print(f"{interface} {image} {clock} kHz r{repeat}: failed: {result['error'][-1]}")
                            progress.advance(sweep, args.repeats - repeat - 1)
                            break
                    good = [r for r in runs if r["interface"] == interface and r["image"] == image
                            and r["clock_khz"] == clock and not r["error"]]
                    if good:
                        times = sorted(r["jflash_s"] for r in good)
                        walls = sorted(r["wall_s"] for r in good)
                        console.print(f"{interface} {image} {clock} kHz: {len(good)} ok, J-Flash median "
                                      f"{times[len(times) // 2]:.3f} s ({times[0]:.3f}-{times[-1]:.3f}), "
                                      f"process median {walls[len(walls) // 2]:.3f} s")
    Path(args.out).write_text(json.dumps({"host": "J-Flash V9.92 on the PC", "probe": f"Flasher ARM {SERIAL}",
                                          "runs": runs}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
