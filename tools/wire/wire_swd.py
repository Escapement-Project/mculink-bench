#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""SWD transfers decoded from a wire export, with each phase's clock, grouped by the clock asked for.

    wire_swd.py EXPORT_DIR [--clk 3_0] [--dio 3_1] [--sessions-ms 50] [--labels 50,37.5,30,10]
    wire_swd.py EXPORT_DIR --annotate REC.tickwave [--tickwave BIN] [--labels ...] [--examples 3]

With --annotate, writes tickwave's sidecar REC.wire_swd.jsonl beside the recording instead of
printing (see annotate() for what it holds); SWCLK and SWDIO must be the recording's names.

SWDIO is read 1 ns before each SWCLK rise: the host sets a bit before the rise that clocks it, and
the target's bit, launched after one rise, has settled before the next (the L1306 drives about
17 ns after the rise). A request is a start bit, parity over APnDP, RnW and A[3:2], a stop bit and
a park bit; then a turnaround, three acknowledge bits, and for an OK read 32 data bits, parity and a
turnaround, for an OK write a turnaround, 32 data bits and parity.

Groups: with --sessions-ms, the capture splits where SWCLK stops that long (one `verge read` per
clock), labelled in order by --labels; otherwise transfers group by their data-bit period, rounded
to a tenth of a cycle. For each group: transfers by acknowledge, data parity failures, and the
clock of each phase in MHz: each transfer's mean period across the phase, then the median over the
group, so a transfer with a pause in it does not move the figure. A phase's mean is exact to 1 ns
over its length, which the analyser's 1 ns sampling would not give period by period. Data is
positions 13 to 43 (rises 13 to 44): position 11 or 12 holds the probe's acknowledge check in a
read and the turnaround in a write, and position 44 the parity bit's setup in a write. Also the
fastest single position, the median over the group's transfers of one kind. decode() says how
transfers are framed."""

import argparse
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from saleae import edges

OK, WAIT, FAULT = (1, 0, 0), (0, 1, 0), (0, 0, 1)


def level_before(clk_rises: np.ndarray, dio: Path) -> np.ndarray:
    initial, t = edges(dio)
    n = np.searchsorted(np.asarray(t), clk_rises - 1e-9, side="right")
    return (initial ^ (n & 1)).astype(np.int8)


def lsb_first(value: int, bits: int) -> bytes:
    return bytes(48 + ((value >> k) & 1) for k in range(bits))


# Sequences SWD sends that are not transfers, LSB first, from ADIv5.2 (B5.3): (pattern, bits
# masked before it, bits masked after it, whether it only counts straight after a line reset).
# The selection alert is followed by four low cycles and the activation code, 0x1A for SWD, which
# match too often to look for on their own. The 16-bit switches also occur inside data words by
# chance, about once in 65,536 positions, but the host always sends them after a line reset.
NOT_TRANSFERS = [
    (lsb_first(0x19BC0EA2E3DDAFE986852D956209F392, 128), 8, 12, False),  # selection alert
    (lsb_first(0x33BBBBBA, 31), 5, 0, False),  # JTAG-to-dormant
    (lsb_first(0xE3BC, 16), 0, 0, True),  # SWD-to-dormant
    (lsb_first(0xE79E, 16), 0, 0, True),  # JTAG-to-SWD
]


def decode(t: np.ndarray, b: np.ndarray):
    """Yields (kind, ack, start index, parity ok) for every transfer: "read" and "write" for an OK
    acknowledge, "other" for any other, with parity checked for OK reads and writes.

    A request is a start bit, APnDP, RnW, A[2:3], their parity, a stop bit and a park bit. The
    host may pause anywhere in it (NXP's firmware does, for about 6 us). Two things that are not
    transfers can look like one: the end of a line reset (50 or more cycles high), and the wake
    and switch sequences in NOT_TRANSFERS. A request may not start on the 50th one of a run or
    later, nor overlap a masked sequence in its request and acknowledge."""
    n = len(b) - 46
    if n <= 0:
        return
    bi = b.astype(np.int16)
    cand = np.flatnonzero((bi[:n] == 1) & (bi[6:n + 6] == 0) & (bi[7:n + 7] == 1)
                          & ((bi[1:n + 1] ^ bi[2:n + 2] ^ bi[3:n + 3] ^ bi[4:n + 4]) == bi[5:n + 5]))
    # How many ones run up to and including each candidate's start bit.
    edges_ = np.flatnonzero(np.diff(np.concatenate([[0], bi, [0]])))
    run_start, run_end = edges_[0::2], edges_[1::2]
    first_one = run_start[np.searchsorted(run_start, cand, side="right") - 1]
    cand = cand[cand - first_one < 49]
    after_reset = set(run_end[run_end - run_start >= 50].tolist())  # the bit after a line reset
    # Masked sequences, as a count of masked bits up to each index.
    text = (b + 48).astype(np.uint8).tobytes()
    masked = np.zeros(len(b) + 1, dtype=np.int32)
    for pattern, before, after, needs_reset in NOT_TRANSFERS:
        at = text.find(pattern)
        while at >= 0:
            if not needs_reset or at in after_reset:
                masked[max(0, at - before)] += 1
                masked[min(len(b), at + len(pattern) + after)] -= 1
            at = text.find(pattern, at + 1)
    covered = np.concatenate([[0], np.cumsum(np.cumsum(masked)[:-1] > 0)])  # masked bits in b[:k]
    cand = cand[covered[cand + 12] - covered[cand] == 0]
    free = 0
    for i in cand.tolist():
        if i < free:
            continue
        ack = (int(b[i + 9]), int(b[i + 10]), int(b[i + 11]))
        if ack == OK and b[i + 2] == 1:
            yield "read", ack, i, (int(bi[i + 12:i + 44].sum()) & 1) == b[i + 44]
            free = i + 46  # rise 45 is the turnaround after parity
        elif ack == OK:
            yield "write", ack, i, (int(bi[i + 13:i + 45].sum()) & 1) == b[i + 45]
            free = i + 46
        else:
            yield "other", ack, i, True
            free = i + 13  # rise 12 is the turnaround after the acknowledge


def mhz(period_s: float) -> float:
    return 1e-6 / period_s if period_s > 0 else float("nan")


PHASES = {"request": (0, 7), "acknowledge": (8, 11), "data": (13, 44)}


def phase_mhz(t: np.ndarray, starts: np.ndarray, phase: str) -> float:
    """Median over transfers of each transfer's mean period across the phase, in MHz."""
    a, z = PHASES[phase]
    return mhz(float(np.median((t[starts + z] - t[starts + a]) / (z - a))))


def summarise(name: str, t: np.ndarray, found: list) -> None:
    acks = Counter(a for _, a, _, _ in found)
    bad = sum(1 for k, _, _, p in found if not p)
    reads = np.array([i for k, _, i, _ in found if k == "read" and i + 47 < len(t)], dtype=np.int64)
    writes = np.array([i for k, _, i, _ in found if k == "write" and i + 47 < len(t)], dtype=np.int64)
    name_ack = {OK: "OK", WAIT: "WAIT", FAULT: "FAULT"}
    ack_text = ", ".join(f"{name_ack.get(a, a)} {c}" for a, c in acks.most_common())
    line = f"  {name}: {len(found)} transfers ({ack_text}), parity failures {bad}"
    starts = np.concatenate([reads, writes])
    if len(starts):
        line += (f"\n      request {phase_mhz(t, starts, 'request'):6.2f},"
                 f" acknowledge {phase_mhz(t, starts, 'acknowledge'):6.2f}")
    fastest = []
    for kind, idx in (("read", reads), ("write", writes)):
        if not len(idx):
            continue
        line += f", {kind} data {phase_mhz(t, idx, 'data'):6.2f}"
        per = np.median(np.diff(t[idx[:, None] + np.arange(46)[None, :]], axis=1), axis=0)
        at = int(np.argmin(per))
        fastest.append((kind, at, float(per[at])))
    if fastest:
        kind, at, p = min(fastest, key=lambda x: x[2])
        line += f" MHz; fastest {mhz(p):6.2f} MHz, {kind} position {at}"
    print(line)


CYCLE = 1 / 150e6
ACK_KIND = {WAIT: "wait", FAULT: "fault", (1, 1, 1): "no-ack"}


def request_text(b: np.ndarray, i: int) -> str:
    """'read AP 0x0C' from the request's bits (LSB first: APnDP, RnW, A2, A3)."""
    port = "AP" if b[i + 1] else "DP"
    rw = "read" if b[i + 2] else "write"
    return f"{rw} {port} 0x{(int(b[i + 3]) | int(b[i + 4]) << 1) * 4:02X}"


def word(b: np.ndarray, first: int) -> int:
    return sum(int(b[first + k]) << k for k in range(32))


def annotate(args, t: np.ndarray, b: np.ndarray) -> int:
    """The sidecar, in this order (a view steps through the first kind):

    session: each burst between pauses of --sessions-ms (default 20), labelled by --labels in turn
      (whole labels separated by |, one a session, or an error), with its transfers,
      acknowledges and data clocks.
    read, write: one example transfer per (kind, typical data period in cycles, the median of
      periods 13 to 43), the group's most common request, preferring one without a pause and then
      one whose data periods are all the typical one. Examples go in time order, each followed by
      its phases, spans with an SWCLK measure: request (rises 0-7), ack (8-11, turnaround and
      acknowledge), the longer of periods 11 and 12 as ack-check in a read (where the probe checks
      the acknowledge; Verge's is 12, NXP's 11) or turnaround in a write, and data (a read's
      12-44, a write's 13-45: the 32 bits and parity). The measure's fences leave a long period
      out of the data's rate. Then stretch: a data period a cycle or more longer than the typical,
      in the example if it has one, else in the group's first transfer that does, the label
      carrying how many transfers of the group have one.
    wait, fault, no-ack (the line left high), bad-ack (any other pattern): the first --examples
      transfers of each, the label carrying the count; parity: the same for read and write data
      parity failures.

    Ids are kind/start-tick, ticks by tickwave's rule."""
    from sidecar import Sidecar

    sc = Sidecar(args.tickwave, args.annotate, "wire_swd", Path(__file__), ["SWCLK", "SWDIO"])
    span = lambda a, z: sc.span(t, a, z)
    ns = lambda p: f"{p * 1e9:.0f} ns"
    out: list[dict] = []
    found = [f for f in decode(t, b) if f[2] + 47 < len(t)]
    # Sessions.
    cut = np.flatnonzero(np.diff(t) > args.sessions_ms * 1e-3) + 1
    bounds = [(s, e) for s, e in zip(np.r_[0, cut], np.r_[cut, len(t)]) if e - s >= 100]
    labels = args.labels.split("|") if args.labels else []
    if labels and len(labels) != len(bounds):
        raise SystemExit(f"{len(bounds)} sessions, {len(labels)} labels")
    starts = np.array([i for _, _, i, _ in found], dtype=np.int64)
    for k, (s, e) in enumerate(bounds):
        lo, hi = np.searchsorted(starts, [s, e])
        part = found[lo:hi]
        acks = Counter({OK: "OK", WAIT: "WAIT", FAULT: "FAULT"}.get(a, "no acknowledge") for _, a, _, _ in part)
        ack_text = "all OK" if set(acks) <= {"OK"} else ", ".join(f"{n:,} {a}" for a, n in acks.most_common())
        clocks = []
        for kind in ("read", "write"):
            idx = np.array([i for kd, _, i, _ in part if kd == kind], dtype=np.int64)
            if len(idx):
                clocks.append(f"{kind} data {phase_mhz(t, idx, 'data'):.1f} MHz")
        head = f"{labels[k]}: " if labels else ""
        out.append(sc.note("session", span(s, e - 1), f"{head}{len(part):,} transfers, {ack_text}"
                           + (f"; {', '.join(clocks)}" if clocks else "")))
    # Example transfers, their phases and stretched data periods, in time order.
    blocks: list[list[dict]] = []
    for kind in ("read", "write"):
        idx = np.array([i for kd, _, i, _ in found if kd == kind], dtype=np.int64)
        groups: dict[int, list] = defaultdict(list)
        stretched: dict[int, list] = defaultdict(list)
        for c0 in range(0, len(idx), 1 << 20):
            chunk = idx[c0:c0 + (1 << 20)]
            per = np.diff(t[chunk[:, None] + np.arange(46)[None, :]], axis=1)
            data = np.rint(per[:, 13:44] / CYCLE).astype(np.int32)
            typical = np.median(data, axis=1).astype(np.int32)
            whole = per.max(axis=1) < 4 * per[:, 13:44].max(axis=1)
            steady = (data == typical[:, None]).all(axis=1)
            longer = data > typical[:, None]
            for j in range(len(chunk)):
                groups[int(typical[j])].append((int(chunk[j]), bool(whole[j]), bool(steady[j])))
                if longer[j].any():
                    stretched[int(typical[j])].append((int(chunk[j]), int(np.argmax(longer[j]))))
        for cyc, members in groups.items():
            if len(members) < 2:
                continue
            common = Counter(request_text(b, m[0]) for m in members).most_common(1)[0][0]
            ranked = sorted(members, key=lambda m: (request_text(b, m[0]) != common, not m[1], not m[2]))
            i = ranked[0][0]
            p = cyc * CYCLE
            if kind == "read":
                text = f"{request_text(b, i)}: OK, 0x{word(b, i + 12):08X}"
            else:
                text = f"{request_text(b, i)} 0x{word(b, i + 13):08X}: OK"
            block = [sc.note(kind, span(i, i + 45), f"{text[0].upper()}{text[1:]} (one of {len(members):,}"
                                                      f" whose data periods are mostly {ns(p)})"),
                     sc.note("request", span(i, i + 7), "Request", "SWCLK"),
                     sc.note("ack", span(i + 8, i + 11), "Turnaround and acknowledge", "SWCLK")]
            # The longer of periods 11 and 12: where the probe checks the acknowledge before a
            # read's data (Verge's is 12, NXP's 11), and where a write turns the line around.
            at = 11 if t[i + 12] - t[i + 11] > t[i + 13] - t[i + 12] else 12
            pause = t[at + i + 1] - t[at + i]
            def data_label(a: int, z: int) -> str:
                # Two clocks inside one word: the measure's fences keep both, so say so.
                q = np.sort(np.diff(t[a:z + 1]))
                q1, q3 = q[(len(q) - 1) // 4], q[-(-3 * (len(q) - 1) // 4)]
                iqr = max(q3 - q1, 1e-9)
                kept = q[(q >= q1 - 1.5 * iqr) & (q <= q3 + 1.5 * iqr)]
                if kept[-1] > 1.5 * kept[0]:
                    return f"Data and parity; the clock changes mid-word, {ns(kept[-1])} to {ns(kept[0])} periods"
                return "Data and parity"

            if kind == "read":
                block += [sc.note("data", span(i + 12, i + 44), data_label(i + 12, i + 44), "SWCLK"),
                          sc.note("ack-check", span(i + at, i + at + 1), f"Acknowledge check, {ns(pause)}", "SWCLK", "period")]
            else:
                block += [sc.note("turnaround", span(i + at, i + at + 1), f"Turnaround, {ns(pause)}", "SWCLK", "period"),
                          sc.note("data", span(i + 13, i + 45), data_label(i + 13, i + 45), "SWCLK")]
            if stretched.get(cyc):
                j, pos = next((s for s in stretched[cyc] if s[0] == i), stretched[cyc][0])
                a = j + 13 + pos
                block.append(sc.note("stretch", span(a, a + 1),
                                     f"A {kind}-data period of {ns(t[a + 1] - t[a])} among mostly {ns(p)} ones; "
                                     f"{len(stretched[cyc]):,} of {len(members):,} {kind}s have one", "SWCLK", "period"))
            blocks.append(block)
    for block in sorted(blocks, key=lambda bl: bl[0]["start"]):
        out.extend(block)
    # Anomalies: the first few of each, with the count.
    others = [f for f in found if f[0] == "other"]
    by_ack: dict[str, list] = defaultdict(list)
    for _, ack, i, _ in others:
        by_ack[ACK_KIND.get(ack, "bad-ack")].append((i, ack))
    for kind, items in by_ack.items():
        for n, (i, ack) in enumerate(items[:args.examples], 1):
            what = {"wait": "WAIT", "fault": "FAULT", "no-ack": "No acknowledge"}.get(
                kind, "Invalid acknowledge " + "".join(map(str, ack)))
            out.append(sc.note(kind, span(i, i + 11), f"{what} to a {request_text(b, i)} ({n} of {len(items):,})"))
    bad = [(kd, i) for kd, _, i, ok in found if not ok]
    for n, (kd, i) in enumerate(bad[:args.examples], 1):
        a = i + 12 if kd == "read" else i + 13
        out.append(sc.note("parity", span(a, a + 32), f"{kd.capitalize()} data parity wrong ({n} of {len(bad):,})"))
    path = sc.write(out)
    print(f"{path.name}: {len(out)} annotations, {len(found):,} transfers, "
          f"{len(others):,} not OK, {len(bad):,} parity failures")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", type=Path)
    parser.add_argument("--clk", default="3_0")
    parser.add_argument("--dio", default="3_1")
    parser.add_argument("--sessions-ms", type=float, default=0.0)
    parser.add_argument("--labels", default="")
    parser.add_argument("--annotate", type=Path)
    parser.add_argument("--tickwave", default="tickwave")
    parser.add_argument("--examples", type=int, default=3)
    args = parser.parse_args()
    initial, times = edges(args.dir / f"digital_{args.clk}.bin")
    t = np.asarray(times[(0 if initial == 0 else 1)::2])
    b = level_before(t, args.dir / f"digital_{args.dio}.bin")
    if args.annotate:
        if not args.sessions_ms:
            args.sessions_ms = 20.0
        return annotate(args, t, b)
    print(f"{args.dir.name}: {len(t)} SWCLK rises")
    if args.sessions_ms > 0:
        cut = np.flatnonzero(np.diff(t) > args.sessions_ms * 1e-3) + 1
        bounds = [(s, e) for s, e in zip(np.r_[0, cut], np.r_[cut, len(t)]) if e - s >= 100]
        labels = args.labels.split(",") if args.labels else []
        for k, (s, e) in enumerate(bounds):
            name = f"{labels[k]} MHz asked" if k < len(labels) else f"session {k}"
            found = list(decode(t[s:e], b[s:e]))
            summarise(name, t[s:e], found)
        return 0
    found = list(decode(t, b))
    groups = defaultdict(list)
    for f in found:
        kind, _, i, _ = f
        if kind not in ("read", "write"):
            continue
        key = round((t[i + 44] - t[i + 13]) / 31 * 150e6, 1)
        groups[(kind, key)].append(f)
    others = Counter(a for k, a, _, _ in found if k == "other")
    print(f"  not OK: {dict(others)}")
    totals = Counter()
    for (kind, _), fs in groups.items():
        totals[kind] += len(fs)
    print(f"  {'kind':5} {'cycles':>6} {'transfers':>9} {'request':>8} {'ack':>8} {'data':>8} {'fastest':>8}  MHz")
    for (kind, key), fs in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
        if len(fs) < 0.005 * totals[kind]:
            continue
        idx = np.array([i for _, _, i, _ in fs if i + 47 < len(t)], dtype=np.int64)
        bad = sum(1 for _, _, _, ok in fs if not ok)
        per = np.median(np.diff(t[idx[:, None] + np.arange(46)[None, :]], axis=1), axis=0)
        at = int(np.argmin(per))
        print(f"  {kind:5} {key:6.1f} {len(fs):9d} {phase_mhz(t, idx, 'request'):8.2f}"
              f" {phase_mhz(t, idx, 'acknowledge'):8.2f} {phase_mhz(t, idx, 'data'):8.2f}"
              f" {mhz(float(per[at])):8.2f}  at {at}" + (f", {bad} parity failures" if bad else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
