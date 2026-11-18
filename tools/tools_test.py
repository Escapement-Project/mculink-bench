#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["libusb1", "numpy", "pyusb", "rich"]
# ///
"""The tools' host side against a fake probe, analyser and probe-rs. Nothing here opens a USB device.

    uv run tools/tools_test.py
"""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cmsis_dap as dap  # noqa: E402
import path_timing  # noqa: E402
import throughput  # noqa: E402
import wireclock  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERGE = "Verge CMSIS-DAP"
STOCK = "MCU-LINK (r0FB) CMSIS-DAP V3.174"


def identify_reply(format: int, build: str) -> bytes:
    """Vendor 0x82's reply: magic, format, flags, the build string's length and the build."""
    return bytes([0x82]) + b"CJTAG" + bytes([format, 0x2C, len(build)]) + build.encode()


class FakeProbe:
    """A probe whose target answers every transfer OK. Reads return the DPIDR from the DP's
    register 0 and all ones elsewhere, which reads as powered up and halted."""

    def __init__(self, product: str = VERGE, identify: bytes | None = None, dpidr: int = 0x0BE12477):
        self.serial = "CJ00000000"
        self.product = product
        self.vendor = "Escapement Project"
        self.identify = identify
        self.dpidr = dpidr
        self.sent: list[bytes] = []

    def command(self, request: bytes, timeout: int = 2000) -> bytes:
        self.sent.append(bytes(request))
        if request[0] == 0x82:
            return self.identify
        if request[0] == dap.DAP_TRANSFER:
            count, k, data = request[2], 3, b""
            for _ in range(count):
                if request[k] & 0x02:
                    data += dap.le(self.dpidr if request[k] & 0x0F == 0x02 else 0xFFFF_FFFF)
                    k += 1
                else:
                    k += 5
            return bytes([request[0], count, 1]) + data
        return bytes([request[0], 0])

    def info(self, field: int) -> bytes:
        return b""

    def connect(self, port: int = 1) -> int:
        self.sent.append(bytes([dap.DAP_CONNECT, port]))
        return port

    def swj_clock(self, hz: int) -> int:
        return 0

    def swj_sequence(self, bits: int, data: bytes) -> int:
        return 0

    def release(self) -> None:
        pass

    def count(self, command: int) -> int:
        return sum(1 for r in self.sent if r[0] == command)


class FakeAnalyser:
    def __init__(self, url: str):
        pass

    def devices(self) -> list[dict]:
        return [{"deviceType": "Logic8", "isSimulation": False}]

    def capture(self, channels: list[int]) -> int:
        raise RuntimeError("no analyser in this test")


def wireclock_args(out: str, **changes) -> argparse.Namespace:
    args = dict(label="test", out=out, force=False, image=None, verge="verge", serial="CJ00000000",
                chip="MSPM0L1306", clock=[10.0], note="", keep=None, analyser=wireclock.ANALYSER, swdio=6, swclk=7)
    return argparse.Namespace(**{**args, **changes})


def throughput_args(out: str, **changes) -> argparse.Namespace:
    args = dict(label="test", out=out, force=False, image=None, verge="verge", serial="CJ00000000",
                usb_id=throughput.USB_ID, protocol="jtag", chip=None, cjtag_format=None, note="", quick=True,
                run_seconds=0.01, no_probe_rs=False, stage=None)
    return argparse.Namespace(**{**args, **changes})


class Quiet(unittest.TestCase):
    """Runs each test in a scratch directory, with the tools' printing kept out of the report."""

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.out = os.path.join(self.scratch.name, "result.json")
        stack = contextlib.ExitStack()
        self.stdout = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stderr = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.addCleanup(stack.close)


class Attach(Quiet):
    """Every tool that attaches hands throughput.attach the identify record, whatever the firmware."""

    def wireclock(self, probe: FakeProbe) -> dict:
        with mock.patch.object(throughput, "open_probe", lambda serial: probe), \
                mock.patch.object(wireclock, "Analyser", FakeAnalyser):
            self.assertEqual(wireclock.cmd_run(wireclock_args(self.out)), 0)
        return json.load(open(self.out))

    def test_wireclock_verge(self):
        probe = FakeProbe(identify=identify_reply(8, "v0.3.0"))
        record = self.wireclock(probe)
        # The clock's attach came back, and the capture after it is what failed.
        self.assertEqual(record["clocks"], [{"clock_mhz": 10.0, "error": "no analyser in this test"}])
        self.assertEqual(probe.count(dap.DAP_CONNECT), 3)
        self.assertEqual(probe.count(dap.DAP_VENDOR_CJTAG_CONTROL), 0)

    def test_wireclock_before_format_7(self):
        probe = FakeProbe(identify=identify_reply(6, "abc1234"))
        self.wireclock(probe)
        self.assertEqual(probe.count(dap.DAP_VENDOR_CJTAG_CONTROL), probe.count(dap.DAP_CONNECT))

    def test_wireclock_stock(self):
        probe = FakeProbe(product=STOCK)
        record = self.wireclock(probe)
        self.assertIsNone(record["firmware"]["ours"])
        self.assertEqual(record["clocks"], [{"clock_mhz": 10.0, "error": "no analyser in this test"}])
        self.assertEqual(probe.count(0x82) + probe.count(dap.DAP_VENDOR_CJTAG_CONTROL), 0)

    def test_path_timing(self):
        probe = FakeProbe(identify=identify_reply(8, "v0.3.0+timing"))
        argv = ["path_timing.py", "--serial", probe.serial, "--out", self.out]
        with mock.patch.object(throughput, "open_probe", lambda serial: probe), mock.patch.object(sys, "argv", argv):
            # The fake target is no MSPM0, which path_timing finds out only after attaching.
            with self.assertRaisesRegex(SystemExit, "expected MSPM0 DPIDR"):
                path_timing.main()


class BeforeTheProbe(Quiet):
    """What can fail without the probe fails before the probe is flashed or opened."""

    def setUp(self):
        super().setUp()
        for name in ("flash_and_follow", "open_probe"):
            patch = mock.patch.object(throughput, name, side_effect=AssertionError(f"{name} ran"))
            patch.start()
            self.addCleanup(patch.stop)

    def test_no_analyser(self):
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        with mock.patch.object(wireclock.urllib.request, "urlopen", side_effect=refused):
            with self.assertRaisesRegex(SystemExit, "no Logic 2 MCP server at"):
                wireclock.cmd_run(wireclock_args(self.out, image="verge.elf"))

    def test_swd_throughput_does_not_build(self):
        failed = subprocess.CompletedProcess([], 101)
        with mock.patch.object(throughput.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(SystemExit, "did not build"):
                throughput.cmd_run(throughput_args(self.out, image="verge.elf", protocol="swd"))

    def test_cjtag_format_needs_cjtag(self):
        argv = ["throughput.py", "run", "--label", "x", "--serial", "S", "--image", "verge.elf",
                "--cjtag-format", "oscan2"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(throughput, "swd_throughput") as build:
            with self.assertRaises(SystemExit) as refused:
                throughput.main()
        self.assertEqual((refused.exception.code, build.call_count), (2, 0))

    def test_usb_id(self):
        for text in ("zz", "1fc9", "1fc9:zz"):
            with self.subTest(text), mock.patch.object(sys, "argv", ["throughput.py", "run", "--label", "x",
                                                                     "--serial", "S", "--usb-id", text]):
                with self.assertRaises(SystemExit) as refused:
                    throughput.main()
                self.assertEqual(refused.exception.code, 2)
                self.assertIn(f"--usb-id: '{text}': give VID:PID in hex", self.stderr.getvalue())
        self.assertEqual(throughput.usb_id("1FC9:0143"), (0x1FC9, 0x0143))


class ProbeRs(Quiet):
    """The probe-rs half, with swd-throughput replaced by a stand-in that exits `code`."""

    def run_with(self, code: int, **changes) -> tuple[int, dict]:
        def run(args, **kwargs):
            if args[0] == "swd-throughput" and code == 0:
                csv = args[args.index("--csv") + 1]
                with open(csv, "w") as f:
                    f.write("speed_khz,probe_khz,arm,bytes,median_us,bytes_per_second\n1000,1000,rt,4,90,44000\n")
            return subprocess.CompletedProcess(args, code, stdout="")

        probe = FakeProbe(identify=identify_reply(8, "v0.3.0"))
        with mock.patch.object(throughput, "swd_throughput", return_value="swd-throughput"), \
                mock.patch.object(throughput.subprocess, "run", side_effect=run), \
                mock.patch.object(throughput, "open_probe", lambda serial: probe), \
                mock.patch.object(throughput, "provenance", return_value={}), \
                mock.patch.object(throughput, "SWD_THROUGHPUT", self.scratch.name):
            os.makedirs(os.path.join(self.scratch.name, "target"), exist_ok=True)
            status = throughput.cmd_run(throughput_args(self.out, force=True, **changes))
        return status, json.load(open(self.out))

    def test_exit_status(self):
        status, record = self.run_with(0)
        self.assertEqual((status, record["probe_rs"]["exit"], len(record["probe_rs"]["points"])), (0, 0, 1))
        status, record = self.run_with(1)
        self.assertEqual((status, record["probe_rs"]["exit"]), (1, 1))

    def test_every_case_failed(self):
        cases = [{"error": "answered 0 of 1 words, ack 2"}] * 3
        with mock.patch.object(throughput, "custom_host", return_value=(cases, None)):
            status, record = self.run_with(0, protocol="swd")
        self.assertEqual((status, len(record["custom_host"])), (1, 3))


class Compare(Quiet):
    """compare on committed results: what failed, what was never measured, and units kept apart."""

    def compare(self, *files: str, clock: list[float] | None = None, host: str = "all") -> str:
        args = argparse.Namespace(files=[os.path.join(REPO, "results", "throughput", f) for f in files], host=host,
                                  metric="kib", clock=clock, depth=[8])
        self.assertEqual(throughput.cmd_compare(args), 0)
        return self.stdout.getvalue()

    def rows(self, text: str, start: str) -> list[list[str]]:
        return [[c.strip() for c in line.strip("|").split("|")] for line in text.splitlines() if line.startswith(start)]

    def test_failed_against_never_measured(self):
        text = self.compare("2026-10-25-gcc.json", "2026-10-25-stock.json", clock=[20, 30])
        # Stock's custom host failed every case at 20 MHz. Its probe-rs sweep failed at 30 MHz and
        # has nothing at 20.
        self.assertEqual({r[-1] for r in self.rows(text, "| block | ap-read | 8 | 20 |")}, {"failed"})
        self.assertEqual({r[-1] for r in self.rows(text, "| read32 | 4096 | 30 |")}, {"failed"})
        self.assertEqual({r[-1] for r in self.rows(text, "| read32 | 4096 | 20 |")}, {"--"})

    def test_tables_by_unit(self):
        text = self.compare("2026-11-04-gcc-a0e17c8-jtag-r1.json", "2026-11-04-gcc-a0e17c8-jtag-r2.json", clock=[10])
        self.assertNotIn("cmsis_dap.py", text)  # no custom host over JTAG
        self.assertEqual(self.rows(text, "| rt"), [])
        self.assertEqual(len(self.rows(text, "| 10 |")), 1)
        self.assertIn("us a single-word read", text)


class Commands(unittest.TestCase):
    """cmsis_dap.Probe.command's three ways for a reply to be wrong."""

    def answer(self, request: bytes, reply: bytes) -> bytes:
        endpoint = mock.Mock()
        endpoint.read.return_value = reply
        return dap.Probe(None, None, endpoint, endpoint, "S", "", "").command(request)

    def test_replies(self):
        with self.assertRaisesRegex(dap.UnknownCommand, "0x89: the firmware does not have this command"):
            self.answer(bytes([0x89]), bytes([0xFF]))
        with self.assertRaisesRegex(RuntimeError, "0x89: no reply"):
            self.answer(bytes([0x89]), b"")
        with self.assertRaisesRegex(RuntimeError, "out of step") as raised:
            self.answer(bytes([0x89]), bytes([0x05, 0]))
        self.assertNotIsInstance(raised.exception, dap.UnknownCommand)
        self.assertEqual(self.answer(bytes([0x89]), bytes([0x89, 0])), bytes([0x89, 0]))


class Wireclock(Quiet):
    def test_positions_without_the_clock(self):
        args = argparse.Namespace(file=os.path.join(REPO, "results", "wireclock", "2026-10-21-gcc.json"), clock=7.0)
        with self.assertRaisesRegex(SystemExit, "no clock 7 MHz in .*; it has 1, 2, 3, 5, 8, 10, 12"):
            wireclock.cmd_positions(args)

    def test_analyse_without_captures(self):
        args = argparse.Namespace(dir=self.scratch.name, result=None, verbose=False, swdio=6, swclk=7)
        with self.assertRaisesRegex(SystemExit, "no <clock>MHz directories"):
            wireclock.cmd_analyse(args)
        args.dir = os.path.join(self.scratch.name, "missing")
        with self.assertRaisesRegex(SystemExit, "is not a directory"):
            wireclock.cmd_analyse(args)


if __name__ == "__main__":
    unittest.main()
