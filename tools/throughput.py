#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["libusb1", "pyusb", "rich"]
# ///
r"""Throughput of the attached probe, the same cases every time, kept in results/throughput/.

    uv run tools/throughput.py run --label gcc --serial <serial> --image ../mculink-verge/build/mculink_cjtag.elf
    uv run tools/throughput.py run --label stock --serial <serial> \
        --image <LinkServer>/MCU-LINK/probe_firmware/MCU-LINK_CMSIS-DAP_V3_172.s19
    uv run tools/throughput.py run --label now --serial <serial>    # whatever the probe runs
    uv run tools/throughput.py run --label gcc-oscan5 --serial <serial> --protocol cjtag --cjtag-format oscan5
    uv run tools/throughput.py compare results/throughput/2026-10-20-*.json --clock 50

Over SWD, two hosts measure each probe. tools/cmsis_dap.py sends one command over and over and
times it: the commands the emulator models (DAP_TransferBlock and DAP_Transfer on the AP's DRW,
reads and writes, one word to a full packet), the same on DP registers, which no target slows, at
depths 1, 2 and 8 and at five clocks. tools/swd-throughput times probe-rs's memory reads and writes
end to end. Over JTAG and cJTAG only probe-rs measures.

A figure counts only if the data is right, and probe-rs does not check what it reads. At every clock
it measured, tools/swd-throughput also writes a pattern to the RAM under test and reads it back 16
times, ten rounds, and reads the target's first 64 KiB of flash ten times against a reference read
twice at the slowest clock. The flash check runs only on flash with varied content; erased flash
reads the same whatever word a misread returns. A result records how many rounds of each came back
right, and why a check did not run.

MATRICES names each protocol's case set. A change to the cases changes the name, and compare
refuses to set results from two matrices side by side.

The SWD target is an MSPM0L1306, held out of deep sleep and halted. The AP cases read and write the
first 1 KiB of its SRAM, which the AHB-AP's address wraps within, and the page is put back. The
JTAG and cJTAG target is a CC1312R1, and probe-rs works on SRAM from 0x2000_8000, clear of the
trace source's control block at its start (mculink-verge tools/swo_target).

`--image` flashes the probe through verge (`--verge` names its binary) and records verge's version.
`--cjtag-format` picks the scan format a cJTAG run connects in, OScan1 when left out. Nothing
checks that the target has it.
"""

import argparse
import csv
import datetime
import hashlib
import io
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cmsis_dap as dap  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "results", "throughput")
SWD_THROUGHPUT = os.path.join(REPO, "tools", "swd-throughput")

MATRICES = {"swd": "swd-1", "jtag": "jtag-1", "cjtag": "cjtag-1"}
# Where probe-rs reads and writes, by protocol: the start of the MSPM0L1306's SRAM, and the
# CC1312R1's past the trace source's control block and data.
PROBE_RS_BASE = {"swd": 0x2000_0000, "jtag": 0x2000_8000, "cjtag": 0x2000_8000}
CLOCKS_MHZ = (1, 5, 10, 20, 50)
DEPTHS = (1, 2, 8)
REPEATS = 3
RUN_SECONDS = 0.25
RUN_COMMANDS = (50, 50_000)

# Request byte, and whether the transfer carries a data word. ABORT written with zero sets nothing.
ACCESS = {
    "ap-read": (0x0F, False),
    "ap-write": (0x0D, True),
    "dp-read": (0x02, False),
    "dp-write": (0x00, True),
}
# Up to a full packet each way: a block read's 127 words make a 512-byte reply, a block write's 126
# a 509-byte request, and DAP_Transfer's five bytes a write stop it at 101.
WORDS = {
    ("block", False): (1, 8, 32, 127),
    ("block", True): (1, 8, 32, 126),
    ("transfer", False): (1, 8, 32, 127),
    ("transfer", True): (1, 8, 32, 101),
}

PAGE = 0x2000_0000
PAGE_WORDS = 256
PATTERN = 0xA5A5_5A5A
AP_TAR_WRITE = 0x05
DP_SELECT_WRITE = 0x08
DAP_TRANSFER_CONFIGURE = 0x04
DAP_SWD_CONFIGURE = 0x13
DAP_VENDOR_IDENTIFY = 0x82
# The probe's VID and PID; `run --usb-id` sets them for firmware that enumerates as another device.
USB_ID = (0x1FC9, 0x0143)

le = dap.le


def cases(quick: bool = False) -> list[dict]:
    """The matrix, or with `quick` a few of its cases for trying the script out."""
    out = []
    for mhz in (10,) if quick else CLOCKS_MHZ:
        for command in ("block", "transfer"):
            for access, (_, write) in ACCESS.items():
                for words in WORDS[(command, write)][1:3] if quick else WORDS[(command, write)]:
                    for depth in (1, 8) if quick else DEPTHS:
                        out.append({"clock_mhz": mhz, "command": command, "access": access,
                                    "words": words, "depth": depth})
    return out


def payload(command: str, access: str, words: int) -> bytes:
    request, write = ACCESS[access]
    data = le(PATTERN if access == "ap-write" else 0) if write else b""
    if command == "block":
        return bytes([dap.DAP_TRANSFER_BLOCK, 0]) + words.to_bytes(2, "little") + bytes([request]) + data * words
    return bytes([dap.DAP_TRANSFER, 0, words]) + (bytes([request]) + data) * words


def reply_words(command: str, reply: bytes, words: int) -> list[int]:
    start = 4 if command == "block" else 3
    return [int.from_bytes(reply[start + 4 * i : start + 4 * i + 4], "little") for i in range(words)]


def open_probe(serial: str) -> dap.Probe:
    for _ in range(50):
        found = dap.find(serial, *USB_ID)
        if found:
            found[0].drain()
            return found[0]
        time.sleep(0.2)
    raise SystemExit(f"no probe with serial {serial}")


def identify(probe: dap.Probe) -> dict:
    """What the probe says it is: every DAP_Info field, and our firmware's build if it is ours."""
    info = {}
    for field, (name, kind) in dap.INFO_FIELDS.items():
        try:
            raw = probe.info(field)
        except Exception as error:  # a field the firmware does not answer is a result
            info[name] = f"error: {error}"
            continue
        if kind == "str":
            info[name] = raw.rstrip(b"\x00").decode(errors="replace")
        elif kind in ("byte", "short", "word"):
            info[name] = int.from_bytes(raw, "little") if raw else None
        else:
            info[name] = raw.hex()
    # Vendor IDs are each firmware's own: on C DAPLink, 0x82 is UART_SetConfiguration. Ask only a
    # probe whose USB product names Verge, as ours does. The VID:PID is no test:
    # stock MCU-Link shares it.
    ours = None
    if "Verge" in probe.product:
        body = probe.command(bytes([DAP_VENDOR_IDENTIFY]))[1:]
        if body[:5] == b"CJTAG":
            ours = {"format": body[5], "flags": body[6], "build": body[8 : 8 + body[7]].decode(errors="replace")}
    return {"dap_info": info, "ours": ours}


def attach(probe: dap.Probe, hz: int, ours: dict | None) -> int:
    """Connect at `hz` with a line reset, as a host does when it changes the clock, and power up.

    Our firmware measures the target's read latency on the first transfer after a line reset at a
    clock where its default leaves no margin, so the reset belongs to every clock change.
    """
    if ours is not None and ours["format"] < 7:
        # Before identify format 7, 0x83 mode 0 stops the cJTAG framing a session left on. From
        # format 7 DAP_Connect stops it, and 0x83 is gone.
        probe.command(bytes([dap.DAP_VENDOR_CJTAG_CONTROL, 0]))
    if probe.connect(1) != 1:
        raise RuntimeError("DAP_Connect refused SWD")
    probe.command(bytes([DAP_TRANSFER_CONFIGURE, 0]) + (100).to_bytes(2, "little") + (0).to_bytes(2, "little"))
    probe.command(bytes([DAP_SWD_CONFIGURE, 0x00]))
    probe.swj_clock(hz)
    dpidr = dap.swd_reset(probe)
    probe.command(bytes([dap.DAP_TRANSFER, 0, 2, 0x00]) + le(0x1E) + bytes([0x04]) + le(0x5000_0000))
    dap.wait_powered(probe, 0)
    if dpidr == dap.MSPM0_DPIDR:
        probe.command(bytes([dap.DAP_TRANSFER, 0, 3, DP_SELECT_WRITE]) + le(dap.MSPM0_PWR_AP_SELECT)
                      + bytes([0x01]) + le(dap.MSPM0_DPREC0_DEBUG) + bytes([DP_SELECT_WRITE]) + le(0))
    else:
        probe.command(bytes([dap.DAP_TRANSFER, 0, 1, DP_SELECT_WRITE]) + le(0))
    return dpidr


def set_tar(probe: dap.Probe, address: int) -> None:
    reply = probe.command(bytes([dap.DAP_TRANSFER, 0, 1, AP_TAR_WRITE]) + le(address))
    if reply[1] != 1 or reply[2] != 1:
        raise RuntimeError(f"TAR write answered count {reply[1]} ack {reply[2]}")


def page_io(probe: dap.Probe, write: list[int] | None = None) -> list[int]:
    """Read the whole SRAM page, or write `write` over it, in block commands from its start."""
    set_tar(probe, PAGE)
    words = []
    left = PAGE_WORDS
    while left:
        n = min(left, 126 if write is not None else 127)
        if write is None:
            reply = probe.command(bytes([dap.DAP_TRANSFER_BLOCK, 0]) + n.to_bytes(2, "little") + bytes([0x0F]))
            words += reply_words("block", reply, n)
        else:
            chunk = write[PAGE_WORDS - left : PAGE_WORDS - left + n]
            reply = probe.command(bytes([dap.DAP_TRANSFER_BLOCK, 0]) + n.to_bytes(2, "little") + bytes([0x0D])
                                  + b"".join(le(w) for w in chunk))
        if int.from_bytes(reply[1:3], "little") != n or reply[3] != 1:
            raise RuntimeError(f"page block answered {reply[:4].hex()}")
        left -= n
    return words


def check_case(probe: dap.Probe, case: dict, request: bytes, dpidr: int, page: list[int]) -> None:
    """One command of the case, with its data checked, before any of it is timed."""
    command, access, words = case["command"], case["access"], case["words"]
    if access.startswith("ap"):
        set_tar(probe, PAGE)
    reply = probe.command(request)
    count, ack = dap.transfer_counts(request, reply)
    if count != words or ack != 1:
        raise RuntimeError(f"answered {count} of {words} words, ack {ack}")
    if access == "ap-read" and reply_words(command, reply, words) != page[:words]:
        raise RuntimeError("SRAM read back different words")
    if access == "dp-read" and set(reply_words(command, reply, words)) != {dpidr}:
        raise RuntimeError("DPIDR read back different words")
    if access == "ap-write":
        set_tar(probe, PAGE)
        back = probe.command(bytes([dap.DAP_TRANSFER_BLOCK, 0]) + words.to_bytes(2, "little") + bytes([0x0F]))
        if reply_words("block", back, words) != [PATTERN] * words:
            raise RuntimeError("SRAM did not hold the words written")


def timed(probe: dap.Probe, serial: str, case: dict, request: bytes, commands: int) -> tuple[dap.Run, dap.Probe]:
    """One timed run. A queued run releases the probe, so the one to go on with comes back too."""
    if case["access"].startswith("ap"):
        set_tar(probe, PAGE)
    if case["depth"] > 2:
        run = dap.queued_blocks(probe, request, commands, case["depth"], case["words"])
        return run, open_probe(serial)
    return dap.pipelined_blocks(probe, request, commands, case["depth"], case["words"]), probe


def custom_host(probe: dap.Probe, serial: str, ours: dict | None, quick: bool, run_seconds: float, progress: Progress,
                console: Console) -> tuple[list, dict]:
    todo = cases(quick)
    overall = progress.add_task("cmsis_dap.py cases", total=len(todo))
    repeats = progress.add_task("case", total=REPEATS)
    limit = min(probe.out_ep.wMaxPacketSize, probe.in_ep.wMaxPacketSize)
    dpidr = attach(probe, 1_000_000, ours)
    dap.power_up_and_halt(probe, 0)
    page = page_io(probe)
    target = {"dpidr": f"0x{dpidr:08x}"}
    results = []
    clock = None
    clock_errors = {}
    page_dirty = False

    def reattach(probe: dap.Probe) -> dap.Probe:
        """A fresh attach at the current clock. A clock that no longer attaches fails its other cases."""
        probe.release()
        probe = open_probe(serial)
        try:
            attach(probe, clock * 1_000_000, ours)
        except RuntimeError as error:
            clock_errors[clock] = f"could not attach again at {clock} MHz: {error}"
        return probe

    try:
        for case in todo:
            if case["clock_mhz"] in clock_errors:
                results.append(dict(case, runs=[], error=clock_errors[case["clock_mhz"]]))
                progress.advance(overall)
                continue
            if case["clock_mhz"] != clock:
                clock = case["clock_mhz"]
                try:
                    attach(probe, clock * 1_000_000, ours)
                    if page_dirty:
                        # A clock that failed its restore left the page changed; put it back here.
                        page_io(probe, page)
                        page_dirty = False
                except RuntimeError as error:
                    # One unsupported clock must not discard the completed lower-clock cases.
                    message = f"could not attach at {clock} MHz: {error}"
                    clock_errors[clock] = message
                    probe.release()
                    probe = open_probe(serial)
                    results.append(dict(case, runs=[], error=message))
                    progress.advance(overall)
                    continue
            request = payload(case["command"], case["access"], case["words"])
            label = f"{case['clock_mhz']:>2} MHz {case['command']:<8} {case['access']:<8} {case['words']:>3} words depth {case['depth']}"
            progress.update(repeats, completed=0, description=label)
            entry = dict(case, runs=[], error=None)
            reply_bytes = (4 if case["command"] == "block" else 3) + (0 if ACCESS[case["access"]][1] else 4 * case["words"])
            if len(request) > limit or reply_bytes > limit:
                entry["error"] = f"does not fit a {limit}-byte packet"
            else:
                try:
                    check_case(probe, case, request, dpidr, page)
                    calibration, probe = timed(probe, serial, case, request, 20)
                    if calibration.error:
                        raise RuntimeError(calibration.error)
                    per = calibration.seconds / calibration.done
                    commands = max(RUN_COMMANDS[0], min(RUN_COMMANDS[1], int(run_seconds / per)))
                    entry["commands_per_run"] = commands
                    for _ in range(REPEATS):
                        run, probe = timed(probe, serial, case, request, commands)
                        entry["runs"].append(vars(run))
                        progress.advance(repeats)
                        if run.error:
                            raise RuntimeError(run.error)
                except Exception as error:  # the case fails; the matrix goes on from a fresh attach
                    entry["error"] = str(error)
                    probe = reattach(probe)
                if case["access"] == "ap-write":
                    # The reads that follow check against the page as it was. A probe that misreads
                    # acknowledges at this clock can fail the restore too: retry it from a fresh
                    # attach, and if it still fails, the read cases record the mismatch.
                    page_dirty = True
                    for _ in range(3):
                        if clock in clock_errors:
                            break
                        try:
                            page_io(probe, page)
                            page_dirty = False
                            break
                        except Exception as error:
                            console.print(f"{label}: [red]restoring the test page: {error}[/red]")
                            probe = reattach(probe)
            if entry["runs"] and entry["error"] is None:
                rates = [r["done"] / r["seconds"] for r in entry["runs"]]
                entry["commands_per_s"] = statistics.median(rates)
                entry["kib_per_s"] = entry["commands_per_s"] * case["words"] * 4 / 1024
                console.print(f"{label}: {entry['commands_per_s']:>8.0f} commands/s {entry['kib_per_s']:>8.1f} KiB/s")
            else:
                console.print(f"{label}: [red]{entry['error']}[/red]")
            results.append(entry)
            progress.advance(overall)
    finally:
        attach(probe, 1_000_000, ours)
        page_io(probe, page)
        probe.release()
    progress.remove_task(repeats)
    return results, target


def swd_throughput() -> str:
    """tools/swd-throughput, built; its path."""
    build = subprocess.run(["cargo", "build", "--release", "--quiet", "--manifest-path",
                            os.path.join(SWD_THROUGHPUT, "Cargo.toml")])
    if build.returncode != 0:
        raise SystemExit("tools/swd-throughput did not build (cargo's error is above). It needs verge-probe "
                         "cloned beside this repository; --no-probe-rs leaves the probe-rs half out")
    return os.path.join(SWD_THROUGHPUT, "target", "release", "swd-throughput")


def listed(serial: str) -> dict | None:
    """What probe-rs lists for the probe with --usb-id's VID:PID and this serial: for one
    cmsis_dap.py cannot open, such as an XDS110 configured as CMSIS-DAP v1 over HID. None if
    probe-rs does not see it. probe_rs() opens the same VID:PID:serial, so the probe identified is
    the probe measured."""
    run = subprocess.run([swd_throughput(), "--list"], capture_output=True, text=True, check=True)
    found = []
    for line in run.stdout.splitlines():
        selector, rest = line.split("\t", 1)
        identifier, transport = rest.rsplit("\t", 1)
        vid, pid, listed_serial = selector.split(":", 2)
        if (int(vid, 16), int(pid, 16)) == USB_ID and listed_serial == serial:
            found.append({"selector": selector, "identifier": identifier, "transport": transport})
    if len(found) > 1:
        raise SystemExit(f"probe-rs lists {len(found)} probes as {found[0]['selector']}; unplug all but one")
    return found[0] if found else None


def probe_rs(binary: str, serial: str, chip: str, protocol: str, cjtag_format: str | None, quick: bool,
             console: Console) -> dict:
    """probe-rs's timed sweep, and at every clock it measured, the rounds of each check that came
    back right. `checks` is None when the sweep failed or did not check; `checks_skipped` says why
    a check never ran."""
    csv_path = os.path.join(SWD_THROUGHPUT, "target", "last-sweep.csv")
    checks_path = os.path.join(SWD_THROUGHPUT, "target", "last-checks.csv")
    for stale in (csv_path, checks_path):
        if os.path.exists(stale):
            os.remove(stale)
    sweep = ["--speeds", "1000,10000", "--sizes", "64,1024", "--repeats", "3", "--checks", "2",
             "--check-bytes", "4096"] if quick else []
    if cjtag_format is not None:
        sweep += ["--cjtag-format", cjtag_format]
    run = subprocess.run([binary, "--probe", f"{USB_ID[0]:04x}:{USB_ID[1]:04x}:{serial}", "--chip", chip,
                          "--protocol", protocol, "--base", f"0x{PROBE_RS_BASE[protocol]:08x}",
                          "--csv", csv_path, "--checks-csv", checks_path, *sweep],
                         stdout=subprocess.PIPE, text=True)
    console.print(run.stdout, markup=False, highlight=False)
    rows, checks, skipped = [], None, {}
    if run.returncode == 0:
        for row in csv.DictReader(io.StringIO(open(csv_path).read())):
            rows.append({k: (v if k == "arm" else float(v)) for k, v in row.items()})
        if os.path.exists(checks_path):
            checks = []
            for row in csv.DictReader(io.StringIO(open(checks_path).read())):
                row = {k: (v if k in ("check", "error") else int(v)) for k, v in row.items()}
                if row["speed_khz"]:
                    checks.append(row)
                else:
                    skipped[row["check"]] = row["error"]
    return {"exit": run.returncode, "report": run.stdout, "points": rows, "checks": checks, "checks_skipped": skipped}


def provenance(elf: str | None, verge: str) -> dict:
    def out(*args: str) -> str:
        return subprocess.run(args, cwd=REPO, capture_output=True, text=True).stdout.strip()

    manifest = open(os.path.join(SWD_THROUGHPUT, "Cargo.toml")).read()
    # The code that took the measurement, by content, so a result names its source even when the
    # tree it ran from was not committed.
    measuring = ["tools/throughput.py", "tools/cmsis_dap.py",
                 "tools/swd-throughput/Cargo.toml", "tools/swd-throughput/Cargo.lock",
                 "tools/swd-throughput/src/main.rs"]
    record = {
        "repo": out("git", "describe", "--always", "--dirty", "--abbrev=12"),
        "sources": {p: hashlib.sha256(open(os.path.join(REPO, p), "rb").read()).hexdigest() for p in measuring},
        "probe_rs": re.search(r'probe-rs = \{ git = "([^"]+)", rev = "([0-9a-f]+)"', manifest).groups(),
        "host": {"platform": platform.platform(), "python": platform.python_version()},
    }
    try:
        import usb1

        version = usb1.getVersion()
        record["host"]["libusb"] = f"{version.major}.{version.minor}.{version.micro}"
    except Exception:  # the version is a note, not a requirement
        pass
    if elf:
        record["verge"] = subprocess.run([verge, "--version"], capture_output=True, text=True).stdout.strip()
        full = os.path.abspath(elf)
        record["image"] = {
            # Outside the repo a path names nothing a reader has; the hash identifies the image.
            "path": os.path.relpath(full, REPO) if os.path.commonpath([full, REPO]) == REPO else os.path.basename(full),
            "sha256": hashlib.sha256(open(elf, "rb").read()).hexdigest(),
        }
        if not elf.endswith(".s19"):
            record["image"]["compilers"] = sorted({line.split("]", 1)[1].strip() for line in out(
                "arm-none-eabi-readelf", "-p", ".comment", elf).splitlines() if "]" in line})
    return record


def flash(verge: str, elf: str, serial: str) -> None:
    flashed = subprocess.run([verge, "update", "--file", elf, "--serial", serial, "--yes"],
                             capture_output=True, text=True)
    if flashed.returncode != 0:
        raise SystemExit(f"verge did not bring the image up:\n{flashed.stdout}{flashed.stderr}")


def flash_and_follow(verge: str, image: str, serial: str) -> str:
    """Flash `image` onto the unit `serial`, and return the serial it has afterwards.

    Stock and ours give the same unit different serials. The unit is followed when it is the only
    serial that appeared, so other probes on the bench do not hide it.
    """
    before = {p.serial for p in dap.find(None, *USB_ID)} - {serial}
    flash(verge, image, serial)
    time.sleep(2)
    appeared = [p.serial for p in dap.find(None, *USB_ID) if p.serial not in before]
    if serial not in appeared and len(appeared) == 1:
        return appeared[0]
    return serial


def cmd_run(args) -> int:
    global USB_ID
    USB_ID = args.usb_id
    path = args.out or os.path.join(RESULTS, f"{datetime.date.today().isoformat()}-{args.label}.json")
    if os.path.exists(path) and not args.force:
        raise SystemExit(f"{path} exists; pass --force to replace it")
    # Built before the probe is touched: a build that fails after the custom host's half would
    # discard that half.
    binary = None if args.no_probe_rs else swd_throughput()
    if args.image:
        args.serial = flash_and_follow(args.verge, args.image, args.serial)
    # A probe with no CMSIS-DAP v2 bulk interface runs probe-rs's half only, identified by probe-rs.
    # An MCU-Link always has one, ours or stock, so failing to open it is a fault, not a reference.
    seen = None
    if not args.image and USB_ID != (0x1FC9, 0x0143) and not dap.find(args.serial, *USB_ID):
        seen = listed(args.serial)
    if seen is not None:
        if args.protocol == "swd":
            raise SystemExit(f"{args.serial} has no CMSIS-DAP v2 bulk interface ({seen['transport']}): the custom "
                             "host needs one; JTAG and cJTAG run through probe-rs alone")
        probe, firmware = None, {"dap_info": None, "ours": None, "probe_rs_list": seen}
    else:
        probe = open_probe(args.serial)
        firmware = identify(probe)
    chip = args.chip or ("MSPM0L1306" if args.protocol == "swd" else "CC1312R")
    matrix = MATRICES[args.protocol]
    console = Console(stderr=True)
    record = {
        "matrix": f"{matrix}-quick" if args.quick else matrix, "label": args.label, "note": args.note,
        "taken": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "serial": args.serial, "usb_id": f"{USB_ID[0]:04x}:{USB_ID[1]:04x}", "chip": chip, "firmware": firmware,
        "provenance": provenance(args.image, args.verge),
        "settings": {"protocol": args.protocol, "cjtag_format": args.cjtag_format, "clocks_mhz": CLOCKS_MHZ, "depths": DEPTHS,
                     "repeats": REPEATS, "run_seconds": args.run_seconds},
    }
    if args.protocol == "swd":
        with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeElapsedColumn(),
                      console=console) as progress:
            record["custom_host"], record["target"] = custom_host(probe, args.serial, firmware["ours"],
                                                                  args.quick, args.run_seconds, progress, console)
    else:
        if probe is not None:
            probe.release()
        record["custom_host"], record["target"] = [], None
    record["probe_rs"] = None
    if binary is not None:
        try:
            record["probe_rs"] = probe_rs(binary, args.serial, chip, args.protocol, args.cjtag_format, args.quick,
                                          console)
        except Exception as error:  # the custom host's half is kept whatever happens here
            record["probe_rs"] = {"exit": None, "error": str(error)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=1)
        f.write("\n")
    failed = sum(1 for c in record["custom_host"] if c["error"])
    if record["probe_rs"] is None:
        probe_rs_result = "skipped"
    elif record["probe_rs"].get("error"):
        probe_rs_result = record["probe_rs"]["error"]
    else:
        probe_rs_result = f"exit {record['probe_rs']['exit']}"
    print(f"wrote {os.path.relpath(path, REPO)}: {len(record['custom_host']) - failed} cases measured, {failed} failed,"
          f" probe-rs {probe_rs_result}")
    all_failed = record["custom_host"] and failed == len(record["custom_host"])
    return 1 if all_failed or record["probe_rs"] is not None and record["probe_rs"]["exit"] != 0 else 0


def cmd_compare(args) -> int:
    records = [json.load(open(p)) for p in args.files]
    matrices = {r["matrix"] for r in records}
    if len(matrices) != 1:
        raise SystemExit(f"results from different matrices: {sorted(matrices)}")
    # A retake measures the same labels again, so a label that repeats carries its date.
    labels = [r["label"] for r in records]
    labels = [f"{r['label']} {r['taken'][:10]}" if labels.count(r["label"]) > 1 else r["label"] for r in records]
    metric = "kib_per_s" if args.metric == "kib" else "commands_per_s"
    unit = "KiB/s" if args.metric == "kib" else "commands/s"

    def keep(mhz: float, depth: int | None = None) -> bool:
        return (not args.clock or mhz in args.clock) and (depth is None or not args.depth or depth in args.depth)

    def markdown(title: str, columns: list[str], table: dict, cell) -> None:
        """One table, or nothing when no result has a row for it. Each row is a key and a value per
        result: a figure, "failed" for a case the result ran and failed, or None for one it never ran."""
        if not table:
            return
        print(f"{title} (--: not in that result)\n")
        print("| " + " | ".join(columns + labels) + " |")
        print("| --- " * (len(columns) + len(labels)) + "|")
        for key in sorted(table):
            cells = ["--" if v is None else v if v == "failed" else cell(v) for v in table[key]]
            print("| " + " | ".join(f"{k:g}" if isinstance(k, float) else str(k) for k in key) + " | "
                  + " | ".join(cells) + " |")
        print()

    if args.host in ("all", "dap"):
        table = {}
        for i, r in enumerate(records):
            for c in r["custom_host"]:
                if keep(c["clock_mhz"], c["depth"]):
                    key = (c["command"], c["access"], c["depth"], c["clock_mhz"], c["words"])
                    table.setdefault(key, [None] * len(records))[i] = "failed" if c["error"] else c[metric]
        markdown(f"tools/cmsis_dap.py, {unit}", ["command", "access", "depth", "MHz", "words"], table,
                 lambda v: f"{v:.0f}" if v >= 100 else f"{v:.1f}")
    if args.host in ("all", "probe-rs"):
        rates, round_trips, failed = {}, {}, []
        for i, r in enumerate(records):
            points = (r["probe_rs"] or {}).get("points", [])
            # A clock probe-rs failed at fails every arm at it.
            failed.append({p["speed_khz"] / 1000 for p in points if p["arm"] == "failed"})
            for p in points:
                mhz = p["speed_khz"] / 1000
                if not keep(mhz) or p["arm"] == "failed":
                    continue
                if p["arm"] == "rt":
                    round_trips.setdefault((mhz,), [None] * len(records))[i] = p["median_us"]
                else:
                    rates.setdefault((p["arm"], int(p["bytes"]), mhz), [None] * len(records))[i] = (
                        p["bytes_per_second"] / 1024)
        for table in (rates, round_trips):
            for key, row in table.items():
                for i, value in enumerate(row):
                    if value is None and key[-1] in failed[i]:
                        row[i] = "failed"
        markdown("probe-rs through tools/swd-throughput, KiB/s", ["arm", "bytes", "MHz"], rates, lambda v: f"{v:.1f}")
        markdown("probe-rs through tools/swd-throughput, us a single-word read (rt)", ["MHz"], round_trips,
                 lambda v: f"{v:.1f}")
    return 0


def usb_id(text: str) -> tuple[int, int]:
    vid, colon, pid = text.partition(":")
    try:
        if colon:
            return int(vid, 16), int(pid, 16)
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(f"{text!r}: give VID:PID in hex, such as 1fc9:0143")


def main() -> int:
    summary, _, rest = __doc__.partition("\n")
    ap = argparse.ArgumentParser(description=summary, epilog=rest.strip("\n"),
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    run = sub.add_parser("run", help="measure the attached probe and write one result file")
    run.add_argument("--label", required=True, help="names the result: gcc, clang, stock")
    run.add_argument("--image", metavar="FILE",
                     help="flash this image first, ours or an S-record of NXP's firmware; leave out to measure what runs")
    run.add_argument("--verge", default="verge", help="the verge binary that flashes --image")
    run.add_argument("--serial", required=True, help="the probe to measure, as it is before --image flashes it")
    run.add_argument("--usb-id", type=usb_id, default=USB_ID, metavar="VID:PID",
                     help="VID:PID in hex, for a probe that does not enumerate as an MCU-Link")
    run.add_argument("--protocol", choices=tuple(MATRICES), default="swd")
    run.add_argument("--chip", help="probe-rs's name for the target; MSPM0L1306 over SWD, CC1312R otherwise")
    run.add_argument("--cjtag-format", choices=("oscan1", "oscan2", "oscan5", "oscan6"),
                     help="cJTAG: the scan format to connect in, OScan1 when left out")
    run.add_argument("--note", default="", help="free text kept with the result, such as the name of NXP's image")
    run.add_argument("--out", help="result path; default results/throughput/<date>-<label>.json")
    run.add_argument("--force", action="store_true", help="replace an existing result file")
    run.add_argument("--quick", action="store_true", help="a few cases, to try the script; not comparable")
    run.add_argument("--run-seconds", type=float, default=RUN_SECONDS,
                     help="each timed run's length; shorter for a wire recording of every case, not comparable")
    run.add_argument("--no-probe-rs", action="store_true", help="leave out the probe-rs half")
    run.set_defaults(run=cmd_run)
    compare = sub.add_parser("compare", help="set result files side by side as markdown tables")
    compare.add_argument("files", nargs="+")
    compare.add_argument("--host", choices=("all", "dap", "probe-rs"), default="all")
    compare.add_argument("--metric", choices=("kib", "commands"), default="kib")
    compare.add_argument("--clock", type=float, action="append", help="MHz asked; repeat for several")
    compare.add_argument("--depth", type=int, action="append")
    compare.set_defaults(run=cmd_compare)
    args = ap.parse_args()
    if args.run is cmd_run and args.cjtag_format is not None and args.protocol != "cjtag":
        run.error("--cjtag-format needs --protocol cjtag")
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
