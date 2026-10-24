#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["libusb1", "pyusb", "rich"]
# ///
"""Measure USB and CPU0-to-CPU1 command stages with the diagnostics firmware build."""

import argparse
import datetime
import json
import os
import struct
import time

import cmsis_dap as dap
import throughput as tp

TIMING_COMMAND = 0x93
STAGE_COMMAND = 0x8C
COUNTERS = (
    "clock_hz",
    "rx_count",
    "rx_callback_cycles",
    "rx_stage_copy_cycles",
    "tx_stage_copy_cycles",
    "rx_publish_cycles",
    "mailbox1_signal_cycles",
    "wake_to_dispatch_cycles",
    "dispatch_cycles",
    "request_copy_cycles",
    "dap_execute_cycles",
    "response_copy_cycles",
    "reply_publish_cycles",
    "tx_count",
    "tx_start_cycles",
    "tx_in_flight_cycles",
)


def read_counters(probe: dap.Probe) -> dict:
    reply = probe.command(bytes([TIMING_COMMAND]))
    if len(reply) < 3 + len(COUNTERS) * 4 or reply[1] != 0 or reply[2] != 1:
        raise RuntimeError(f"timing command answered {reply[:8].hex()}")
    return dict(zip(COUNTERS, struct.unpack_from(f"<{len(COUNTERS)}I", reply, 3)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="probe serial; omitted from the result file")
    parser.add_argument("--clock-mhz", type=int, default=50)
    parser.add_argument("--commands", type=int, default=3000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    probe = tp.open_probe(args.serial)
    identity = tp.identify(probe)
    firmware = identity["ours"]
    if firmware is None or not firmware["build"].endswith("+timing"):
        raise SystemExit("flash a DAP_TIMING_DIAGNOSTICS build first")

    dpidr = tp.attach(probe, args.clock_mhz * 1_000_000, True)
    if dpidr != dap.MSPM0_DPIDR:
        raise SystemExit(f"expected MSPM0 DPIDR {dap.MSPM0_DPIDR:#x}, got {dpidr:#x}")
    dap.power_up_and_halt(probe, 0)

    results = []
    try:
        for stage in (0, 1):
            reply = probe.command(bytes([STAGE_COMMAND, stage]))
            if reply[1:3] != bytes([0, stage]):
                raise RuntimeError(f"stage command answered {reply[:3].hex()}")

            for command in ("block", "transfer"):
                for words in (1, 8, 127):
                    case = {"clock_mhz": args.clock_mhz, "command": command,
                            "access": "dp-read", "words": words, "depth": 1}
                    request = tp.payload(command, "dp-read", words)
                    tp.check_case(probe, case, request, dpidr, [])
                    read_counters(probe)

                    for depth in (1, 2, 8):
                        case["depth"] = depth
                        for repeat in range(args.repeats):
                            read_counters(probe)
                            run, probe = tp.timed(probe, args.serial, case, request, args.commands)
                            if run.error or run.done != args.commands:
                                raise RuntimeError(
                                    f"{stage=} {command=} {words=} {depth=} run {repeat}: {run.error}; "
                                    f"{run.done}/{args.commands} commands"
                                )
                            counters = read_counters(probe)
                            request_count = max(counters["rx_count"], 1)
                            response_count = max(counters["tx_count"], 1)
                            results.append({
                                "stage": stage,
                                "command": command,
                                "words": words,
                                "depth": depth,
                                "repeat": repeat,
                                "host_seconds": run.seconds,
                                "host_cpu_seconds": run.cpu,
                                "commands": run.done,
                                "counters": counters,
                                "cycles_per_command": {
                                    key: value / (response_count if key.startswith("tx_") or
                                                  key == "tx_stage_copy_cycles" or
                                                  key == "reply_publish_cycles" or
                                                  key == "response_copy_cycles" else request_count)
                                    for key, value in counters.items() if key.endswith("_cycles")
                                },
                            })
                            print(f"stage {stage} {command:8} {words:3} words depth {depth} "
                                  f"run {repeat + 1}: {run.done / run.seconds:.0f} commands/s")
    finally:
        probe.release()

    record = {
        "taken": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "firmware": firmware,
        "target_dpidr": f"0x{dpidr:08x}",
        "clock_mhz": args.clock_mhz,
        "commands_per_run": args.commands,
        "repeats": args.repeats,
        "results": results,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as output:
        json.dump(record, output, indent=1)
        output.write("\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
