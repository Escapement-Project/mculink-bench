"""What the wire decoders' --annotate share: the recording's header, tickwave's tick rule and the
sidecar file (tickwave's ANNOTATIONS.md, version 1)."""
import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

# Who drives a shared line, as the decoders paint it (ANNOTATIONS.md, "Roles"). One table, so every
# generator declares a role alike; each file declares the roles it paints, in this order. By their
# places the probe takes the trace's colour and the target ink. Neither takes the site's rule colour
# (design/tokens.toml) and the turnaround dash of Verge's architecture figures, 2 px on and 1 off at
# tickwave's 1.5 px line.
ROLES = [
    {"name": "probe", "label": "probe drives"},
    {"name": "target", "label": "target drives"},
    {"name": "neither", "label": "neither drives", "colour": ["#738691", "#636e79"], "dash": [1.33, 0.67]},
]


class Sidecar:
    def __init__(self, tickwave: str, recording: Path, source: str, script: Path, channels: list[str]):
        info = json.loads(subprocess.run([tickwave, "info", str(recording), "--json"], capture_output=True,
                                         text=True, check=True).stdout)["header"]
        by_name = {c["name"]: c for c in info["channels"]}
        if not set(channels) <= set(by_name):
            raise SystemExit(f"{recording}: channels {list(by_name)}, expected {channels}")
        # One decode reads one source's times, so its channels must share a source and an offset.
        places = {(by_name[c]["source"], by_name[c].get("offset", 0)) for c in channels}
        if len(places) != 1:
            raise SystemExit(f"{recording}: channels {channels} differ in source or offset")
        (index, self.offset), = places
        src = info["sources"][index]
        self.recording, self.source, self.channels = recording, source, channels
        self.begin, self.origin = src["begin_s"], src["origin"]
        self.num, self.den = src["timebase"]
        f = Fraction(*info["timebase"]) / Fraction(self.num, self.den)
        if f.denominator != 1:
            raise SystemExit(f"{recording}: timebase {info['timebase']} is no multiple of the source's")
        self.factor = f.numerator
        # The version covers everything that shapes the output: the decoder, this module and the
        # export reader beside it.
        digest = hashlib.sha256()
        for f in (script, Path(__file__), Path(__file__).with_name("saleae.py")):
            digest.update(f.read_bytes())
        self.head = {"format": "tickwave-annotations/1", "source": source,
                     "version": "sha256:" + digest.hexdigest()[:12], "recording_id": info["content_id"]}

    def tick(self, x: float) -> int:
        """tickwave's rule: f * round((t - begin_s) * rate) + origin + offset."""
        return self.factor * int(round((x - self.begin) * self.num / self.den)) + self.origin + self.offset

    def span(self, t: np.ndarray, a: int, z: int) -> tuple[int, int, int]:
        """Rises t[a..z], padded at each end by half the shorter of the periods on either side of
        that rise, so a measure over the span takes exactly those rises and a pause outside it
        does not widen it. The third value is the first rise's tick, which names the annotation:
        an id that does not move when the padding does."""
        before = t[a + 1] - t[a] if a == 0 else min(t[a] - t[a - 1], t[a + 1] - t[a])
        after = t[z] - t[z - 1] if z + 1 >= len(t) else min(t[z + 1] - t[z], t[z] - t[z - 1])
        return self.tick(t[a] - before / 2), self.tick(t[z] + after / 2), self.tick(t[a])

    def exact(self, t: np.ndarray, a: int, z: int) -> tuple[int, int, int]:
        """Rises t[a..z] with no padding: from the first rise's tick to one past the last's, so a
        duration over the span runs from rise a to rise z, the falls outside left out."""
        return self.tick(t[a]), self.tick(t[z]) + 1, self.tick(t[a])

    def note(self, kind: str, se: tuple[int, int, int], label: str, measure: str | None = None,
             what: str = "rate") -> dict:
        a = {"id": f"{kind}/{se[2]}", "kind": kind, "start": se[0], "end": se[1],
             "channels": self.channels, "label": label}
        if measure:
            a["measure"] = {"channel": measure, "what": what}
        return a

    def paint(self, role: str, start: int, end: int, channel: str, at: int | None = None) -> dict:
        """`channel` driven by `role` from tick `start` to `end`, named by where it starts: `at`
        where the start is padded, else the start."""
        return {"id": f"drive/{start if at is None else at}", "kind": "drive", "start": start, "end": end,
                "channels": [channel], "paint": role}

    def write(self, annotations: list[dict]) -> Path:
        seen, dup = set(), set()
        for a in annotations:
            (dup if a["id"] in seen else seen).add(a["id"])
        if dup:
            raise SystemExit(f"{self.recording.name}: duplicate ids {sorted(dup)[:5]}")
        painted = {a["paint"] for a in annotations if "paint" in a}
        if painted - {r["name"] for r in ROLES}:
            raise SystemExit(f"{self.recording.name}: undeclared roles {sorted(painted - {r['name'] for r in ROLES})}")
        head = dict(self.head)
        if painted:
            head["role"] = [r for r in ROLES if r["name"] in painted]
        out = self.recording.with_name(self.recording.name.removesuffix(".tickwave") + f".{self.source}.jsonl")
        out.write_text("".join(json.dumps(x) + "\n" for x in [head] + annotations), newline="\n")
        return out
