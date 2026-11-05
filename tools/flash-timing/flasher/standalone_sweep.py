# /// script
# dependencies = ["pyserial", "rich"]
# ///
"""Time the Flasher's own stand-alone programming, #AUTO over its virtual COM port, per interface,
image and clock (BACKLOG 70). Run it alone, with nothing else using the bench:

    uv run --no-project --with pyserial --with rich python -I standalone_sweep.py --out runs/standalone.json
    uv run ... standalone_sweep.py --ask '#STATUS'      one command, every reply line printed

For each combination J-Flash downloads the project (a per-clock copy in jflash/runs/, sectors 0-42
only, never the CCFG sector) and the image into the Flasher, then #AUTO runs --repeats times. The
Flasher reports Total, Erase, Prog and Verify itself; the host also times #AUTO to its final reply.
Commands end in CR; every command is answered #ACK (or #NACK), then #OK or #ERRxxx (SEGGER KB,
"Flasher - Remote control")."""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import serial
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

HERE = Path(__file__).resolve().parent
SERIAL = "100000001"
TIMES = re.compile(r"(Total|Erase|Prog|Verify) ([0-9.]+)s")


def tty() -> str:
    """The Flasher's CDC ACM port, found by its USB serial number."""
    for device in Path("/sys/bus/usb/devices").iterdir():
        number = device / "serial"
        if number.exists() and number.read_text().strip().lstrip("0") == SERIAL:
            for node in device.glob(f"{device.name}:1.0/tty/tty*"):
                return f"/dev/{node.name}"
    raise SystemExit(f"no virtual COM port for Flasher {SERIAL}")


def ask(command: str, timeout: float, echo=None) -> tuple[list[str], float]:
    """Send one command and read reply lines until a final one or the timeout."""
    lines, buffer = [], b""
    with serial.Serial(tty(), 9600, timeout=0.2) as port:
        port.reset_input_buffer()
        start = time.monotonic()
        port.write(command.encode() + b"\r")
        while time.monotonic() - start < timeout:
            buffer += port.read(256)
            *complete, buffer = re.split(rb"\r\n?|\n", buffer)
            for raw in complete:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                lines.append(line)
                if echo:
                    echo(line)
                # #AUTO sends #STATUS:... lines while it runs; only the #STATUS command ends on one.
                final = ("#OK", "#ERR", "#NACK", "#DONE", "#RESULT") + (("#STATUS:",) if command == "#STATUS" else ())
                if line.startswith(final):
                    return lines, time.monotonic() - start
    return lines + ["(timeout)"], time.monotonic() - start


def ready(timeout: float = 15.0) -> bool:
    """Wait for #STATUS:READY. Right after an #OK the Flasher can still refuse a command as busy
    (#NACK:ERR008)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines, _ = ask("#STATUS", 2.0)
        if lines and lines[-1] == "#STATUS:READY":
            return True
        time.sleep(0.2)
    return False


def auto() -> tuple[list[str], float]:
    """#AUTO once the Flasher is ready, again once if it still answers busy."""
    for _ in range(2):
        ready()
        lines, seconds = ask("#AUTO", 300.0)
        if not (lines and lines[-1].startswith("#NACK:ERR008")):
            break
        time.sleep(0.5)
    return lines, seconds


def download(project: Path, image: Path, log: Path) -> str | None:
    """Put the project and image on the Flasher through J-Flash. An error text, or None."""
    log.unlink(missing_ok=True)
    done = subprocess.run(["JFlashExe", f"-jflashlog{log}", f"-openprj{project}", f"-open{image}",
                           f"-usb{SERIAL}", "-download", "-exit", "-hide"],
                          capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        tail = log.read_text(errors="replace").strip().splitlines()[-6:] if log.exists() else []
        return f"J-Flash -download exit {done.returncode}: {tail}"
    return None


def save(out: str, runs: list[dict]) -> None:
    Path(out).write_text(json.dumps({"host": "Flasher ARM stand-alone, #AUTO over VCOM, J-Flash V9.92 -download",
                                     "probe": f"Flasher ARM {SERIAL}", "runs": runs}, indent=1) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out")
    parser.add_argument("--ask", help="send one command and print the replies")
    parser.add_argument("--interfaces", default="cjtag,jtag")
    parser.add_argument("--images", default="itm_counter.hex,itm_counter_fill344k.hex")
    parser.add_argument("--clocks", default="1000,4000,10000,20000,30000,50000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--projects", default="runs",
                        help="folder under jflash/ with the per-clock project copies (runs: CRC verify, "
                             "runs-readback: readback verify)")
    args = parser.parse_args()
    console = Console(stderr=True)
    if args.ask:
        lines, seconds = ask(args.ask, 30.0, echo=console.print)
        console.print(f"({seconds:.3f} s, port {tty()})")
        return 0 if lines and not lines[-1].startswith(("#ERR", "#NACK", "(timeout)")) else 1
    if not args.out:
        parser.error("--out, or --ask")
    interfaces, images = args.interfaces.split(","), args.images.split(",")
    clocks = [int(c) for c in args.clocks.split(",")]
    logs = HERE / "jflash" / args.projects / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    runs = []
    with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
                  console=console) as progress:
        sweep = progress.add_task("sweep", total=len(interfaces) * len(images) * len(clocks) * args.repeats)
        for interface in interfaces:
            for image in images:
                for clock in clocks:
                    progress.update(sweep, description=f"{interface} {image} {clock} kHz: download")
                    project = HERE / "jflash" / args.projects / f"{interface}-{clock}.jflash"
                    failed = download(project, HERE / image,
                                      logs / f"download-{interface}-{Path(image).stem}-{clock}.log")
                    if failed:
                        console.print(f"{interface} {image} {clock} kHz: {failed}")
                        runs.append({"interface": interface, "image": image, "clock_khz": clock, "repeat": None,
                                     "error": failed})
                        progress.advance(sweep, args.repeats)
                        continue
                    time.sleep(1.0)
                    for repeat in range(args.repeats):
                        progress.update(sweep, description=f"{interface} {image} {clock} kHz r{repeat}")
                        try:
                            lines, seconds = auto()
                        except serial.SerialException as error:
                            # The port went away (the Flasher replugged or restarted): the rest
                            # of the sweep would run blind, so it stops here with what it has.
                            runs.append({"interface": interface, "image": image, "clock_khz": clock,
                                         "repeat": repeat, "error": f"serial: {error}"})
                            save(args.out, runs)
                            raise SystemExit(f"{interface} {image} {clock} kHz r{repeat}: serial: {error}")
                        final = lines[-1] if lines else "(no reply)"
                        times = {k.lower(): float(v) for k, v in TIMES.findall(final)} if final.startswith("#OK") else {}
                        error = None if final.startswith("#OK") else final
                        runs.append({"interface": interface, "image": image, "clock_khz": clock, "repeat": repeat,
                                     "host_s": round(seconds, 4), "flasher": times, "replies": lines, "error": error})
                        progress.advance(sweep)
                        if error:
                            console.print(f"{interface} {image} {clock} kHz r{repeat}: {error}")
                            progress.advance(sweep, args.repeats - repeat - 1)
                            break
                    save(args.out, runs)
                    good = [r for r in runs if r["interface"] == interface and r["image"] == image
                            and r["clock_khz"] == clock and r["repeat"] is not None and not r["error"]]
                    if good:
                        total = sorted(r["flasher"].get("total", float("nan")) for r in good)
                        console.print(f"{interface} {image} {clock} kHz: {len(good)} ok, Flasher total median "
                                      f"{total[len(total) // 2]:.3f} s ({total[0]:.3f}-{total[-1]:.3f}); last "
                                      f"{good[-1]['replies'][-1]}")
    save(args.out, runs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
