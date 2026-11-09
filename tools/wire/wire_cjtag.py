#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""cJTAG frames on the wire: packets per frame and the frame's length, from TCKC alone.

    wire_cjtag.py EXPORT_DIR... [--channel 4_0] [--gap-us 5] [--long 5]

A frame ends with its TDO packet, and the probe waits out the target's TDO delay before the rise
that ends that packet, so the TDO packet's period is the long one: a period over --long core cycles
inside a burst closes a frame. Frames are then counted by packets (2 for OScan5 and OScan6 in
Shift, 3 for OScan1's frame and the frames outside Shift) and by length in core cycles of 150 MHz,
the time from the rise that ends one frame to the rise that ends the next. Frames that span a burst
boundary fall outside the counts, and so does anything of one packet: OScan2 and OScan6's TMS
frames, which have no TDO packet, and every packet at a clock slow enough that all its periods
are long. So the counts are the frames at packet clocks of --long cycles or less.
Also the packet clock: the median of the short periods.

    wire_cjtag.py EXPORT_DIR --annotate REC.tickwave [--tickwave BIN] [--share 0.01]

With --annotate, writes tickwave's sidecar REC.wire_cjtag.jsonl instead (TCKC and TMSC must be the
recording's names): per packet clock under --long cycles (the frame's fastest period), one example frame
for each (packets, length) that holds at least --share of that clock's frames, in time order,
labelled with its share and measuring its duration; inside each, its TDO packet, the long period that waits out the target's
TDO delay. Frames by the rule above; spans and ids as sidecar.py and wire_swd.py."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from saleae import rises


def annotate(args, d: Path) -> int:
    from sidecar import Sidecar

    sc = Sidecar(args.tickwave, args.annotate, "wire_cjtag", Path(__file__), ["TCKC", "TMSC"])
    r = rises(d / f"digital_{args.channel}.bin")
    c = np.rint(np.diff(r) * 150e6).astype(np.int64)
    inside = c < args.gap_us * 150
    ends = np.flatnonzero(inside & (c > args.long))
    if len(ends) < 2:
        raise SystemExit(f"{d.name}: no frames")
    a, z = ends[:-1], ends[1:]
    whole = np.add.reduceat((~inside).astype(np.int64), ends)[:-1] == 0
    a, z = a[whole], z[whole]
    packets = z - a
    keep = packets >= 2
    a, z, packets = a[keep], z[keep], packets[keep]
    # The frame runs from the rise after frame end a to the rise after frame end z: periods
    # a+1..z, of which z is the TDO packet's.
    cycles = np.rint((r[z + 1] - r[a + 1]) * 150e6).astype(np.int64)
    fastest = np.array([c[i + 1:j].min() for i, j in zip(a.tolist(), z.tolist())], dtype=np.int64)
    out = []
    ns = lambda cyc: f"{cyc / 0.15:.0f} ns"
    for clock in sorted(set(fastest.tolist())):
        if clock >= args.long:
            continue  # packets as long as the frame-ending threshold: frames cannot be told apart
        at = np.flatnonzero(fastest == clock)
        by = Counter(zip(packets[at].tolist(), cycles[at].tolist()))
        total = len(at)
        for (p, cyc), n in by.most_common():
            if n / total < args.share:
                continue
            # The example: the group's first frame whose sampled length is the nominal one, so the
            # label and the inset's duration agree.
            members = at[(packets[at] == p) & (cycles[at] == cyc)]
            sampled = np.rint((r[z[members] + 1] - r[a[members] + 1]) * 1e9)
            exact = np.flatnonzero(sampled == round(cyc / 0.15))
            k = members[exact[0]] if len(exact) else members[0]
            i, j = int(a[k]), int(z[k])
            out.append([sc.note("frame", sc.exact(r, i + 1, j + 1),
                                f"{p} packets in {ns(cyc)}: {n / total:.0%} of {total:,} frames whose"
                                f" shortest packet is {ns(clock)}", "TCKC", "duration"),
                        sc.note("tdo", sc.span(r, j, j + 1), f"TDO packet, {ns(c[j])}", "TCKC", "period")])
    flat = [x for block in sorted(out, key=lambda bl: bl[0]["start"]) for x in block]
    path = sc.write(flat)
    print(f"{path.name}: {len(flat)} annotations from {len(a):,} frames")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dirs", nargs="+", type=Path)
    parser.add_argument("--channel", default="4_0")
    parser.add_argument("--gap-us", type=float, default=5.0)
    parser.add_argument("--long", type=int, default=5)
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--annotate", type=Path)
    parser.add_argument("--tickwave", default="tickwave")
    parser.add_argument("--share", type=float, default=0.01)
    args = parser.parse_args()
    if args.annotate:
        if len(args.dirs) != 1:
            raise SystemExit("--annotate takes one export")
        return annotate(args, args.dirs[0])
    for d in args.dirs:
        r = rises(d / f"digital_{args.channel}.bin")
        c = np.rint(np.diff(r) * 150e6).astype(np.int64)
        inside = c < args.gap_us * 150
        short = c[inside & (c <= args.long)]
        ends = np.flatnonzero(inside & (c > args.long))  # period i ends at rise i + 1
        # Consecutive frame ends with no burst gap between them make one frame.
        a, z = ends[:-1], ends[1:]
        gap_between = np.add.reduceat((~inside).astype(np.int64), ends)[:-1] if len(ends) > 1 else np.zeros(0, np.int64)
        whole = gap_between == 0
        packets = (z - a)[whole]
        cycles = np.rint((r[z + 1] - r[a + 1]) * 150e6).astype(np.int64)[whole]
        # One-packet "frames" are the slower clocks, where every packet's period is long.
        keep = packets >= 2
        by = Counter(zip(packets[keep].tolist(), cycles[keep].tolist()))
        total = sum(by.values())
        print(f"{d.name}: packet clock median {np.median(short):.0f} cycles over {len(short)} short periods;"
              f" {total} frames")
        for (p, cyc), n in by.most_common(args.top):
            print(f"    {p} packets, {cyc:3d} cycles ({150 / cyc:5.2f} MHz a JTAG clock): {n / total:6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
