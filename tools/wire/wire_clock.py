#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""Clock periods in a wire export, in core cycles of 150 MHz, per recording.

    wire_clock.py EXPORT_DIR... [--channel 4_0] [--gap-us 5] [--top 8] [--sessions-ms 20]

For each export directory (a Saleae binary export), takes the clock channel's rising edges, keeps
the periods inside bursts (a gap longer than --gap-us ends a burst), and prints the most common
periods in core cycles with their share. With --sessions-ms, splits the capture where the clock
stops for that long and prints each session on its own line.

    wire_clock.py EXPORT_DIR --annotate REC.tickwave --name TCK [--tickwave BIN] [--labels a|b]

With --annotate, writes tickwave's sidecar REC.wire_clock.jsonl instead. Per session (a pause of
--sessions-ms, default 20, ends one), a session span labelled with its bursts, which group by
their median period, and each group's share of the periods (--labels prefixes each, separated by
|, one a session). Then for each group of at least --share, in order of period, a clock span over
up to 64 periods of its first run of 32 or more within twice its period, labelled with the group's
most common periods, with a rate measure on --name. Spans and ids as sidecar.py."""

import argparse
from collections import Counter
from pathlib import Path

import numpy as np

from saleae import rises


CYCLE_NS = 1e3 / 150


def histogram(r: np.ndarray, gap_s: float) -> Counter:
    p = np.diff(r)
    p = p[p < gap_s]
    cycles = np.rint(p * 150e6).astype(np.int64)
    values, counts = np.unique(cycles, return_counts=True)
    return Counter(dict(zip(values.tolist(), counts.tolist())))


def describe(h: Counter, top: int) -> str:
    total = sum(h.values())
    if not total:
        return "no periods"
    parts = [f"{c}:{n / total:5.1%}" for c, n in h.most_common(top)]
    return f"{total:10d} periods; cycles:share " + " ".join(parts)


def annotate(args, d: Path) -> int:
    from sidecar import Sidecar

    sc = Sidecar(args.tickwave, args.annotate, "wire_clock", Path(__file__), [args.name])
    r = rises(d / f"digital_{args.channel}.bin")
    gap = args.gap_us * 1e-6
    cut = np.flatnonzero(np.diff(r) > args.sessions_ms * 1e-3) + 1
    bounds = [(s, e) for s, e in zip(np.r_[0, cut], np.r_[cut, len(r)]) if e - s >= 100]
    labels = args.labels.split("|") if args.labels else []
    if labels and len(labels) != len(bounds):
        raise SystemExit(f"{len(bounds)} sessions, {len(labels)} labels")
    ns = lambda cyc: f"{cyc / 0.15:.0f} ns"
    out = []
    for k, (s, e) in enumerate(bounds):
        dt = np.diff(r[s:e])
        c = np.rint(dt * 150e6).astype(np.int64)
        breaks = np.flatnonzero(dt >= gap)
        bursts = [(b0, b1) for b0, b1 in zip(np.r_[0, breaks + 1], np.r_[breaks, len(c)]) if b1 - b0 >= 8]
        if not bursts:
            continue
        groups: dict[int, list] = {}
        for b0, b1 in bursts:
            groups.setdefault(int(np.median(c[b0:b1])), []).append((b0, b1))
        total = sum(b1 - b0 for b0, b1 in bursts)
        head = f"{labels[k]}: " if labels else ""
        shares = sorted(((sum(b1 - b0 for b0, b1 in g), m) for m, g in groups.items()), reverse=True)
        out.append(sc.note("session", sc.span(r, s, e - 1), f"{head}{args.name} bursts at "
                           + ", ".join(f"{ns(m)} {n / total:.0%}" for n, m in shares[:4] if n / total >= 0.01)
                           + " of the periods"))
        for n, m in sorted(shares, key=lambda x: x[1]):
            if n / total < args.share:
                continue
            g = groups[m]
            inside = np.concatenate([c[b0:b1] for b0, b1 in g])
            v, cnt = np.unique(inside, return_counts=True)
            top = sorted(zip(cnt.tolist(), v.tolist()), reverse=True)[:3]
            # The example: the first run of 32 or more consecutive periods within twice the
            # group's, inside one of its bursts (a burst admits periods up to --gap-us, so its
            # start can be sparse), else the group's longest burst.
            b0, z = None, None
            for g0, g1 in g:
                ok = np.r_[c[g0:g1] <= 2 * m, False]
                edges_ = np.flatnonzero(np.diff(np.r_[False, ok].astype(np.int8)))
                for a0, a1 in zip(edges_[0::2], edges_[1::2]):
                    if a1 - a0 >= 32:
                        b0, z = g0 + a0, g0 + min(a1, a0 + 64)
                        break
                if b0 is not None:
                    break
            if b0 is None:
                b0, b1 = max(g, key=lambda x: x[1] - x[0])
                z = min(b1, b0 + 64)
            out.append(sc.note("clock", sc.span(r, s + b0, s + z),
                               f"Bursts at {ns(m)}: " + ", ".join(f"{ns(vv)} {cc / len(inside):.0%}" for cc, vv in top)
                               + f" of their {len(inside):,} periods", args.name))
    path = sc.write(out)
    print(f"{path.name}: {len(out)} annotations, {len(bounds)} sessions")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dirs", nargs="+", type=Path)
    parser.add_argument("--channel", default="4_0", help="export name: port (from 1) _ channel")
    parser.add_argument("--gap-us", type=float, default=5.0)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--sessions-ms", type=float, default=0.0)
    parser.add_argument("--annotate", type=Path)
    parser.add_argument("--name", default="TCK")
    parser.add_argument("--tickwave", default="tickwave")
    parser.add_argument("--labels", default="")
    parser.add_argument("--share", type=float, default=0.02)
    args = parser.parse_args()
    if args.annotate:
        if len(args.dirs) != 1:
            raise SystemExit("--annotate takes one export")
        args.sessions_ms = args.sessions_ms or 20.0
        return annotate(args, args.dirs[0])
    for d in args.dirs:
        r = rises(d / f"digital_{args.channel}.bin")
        print(f"{d.name}: {len(r)} rises")
        if len(r) < 3:
            continue
        if args.sessions_ms <= 0:
            print("  " + describe(histogram(r, args.gap_us * 1e-6), args.top))
            continue
        cut = np.flatnonzero(np.diff(r) > args.sessions_ms * 1e-3) + 1
        for i, s in enumerate(np.split(r, cut)):
            if len(s) < 50:
                continue
            print(f"  {i:3d} at {s[0]:8.3f} s, {s[-1] - s[0]:7.3f} s: "
                  + describe(histogram(s, args.gap_us * 1e-6), args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
