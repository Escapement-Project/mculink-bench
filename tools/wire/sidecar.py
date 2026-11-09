"""What the wire decoders' --annotate share: the recording's header, tickwave's tick rule and the
sidecar file (tickwave's ANNOTATIONS.md, version 1)."""
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np


class Sidecar:
    def __init__(self, tickwave: str, recording: Path, source: str, script: Path, channels: list[str]):
        info = json.loads(subprocess.run([tickwave, "info", str(recording), "--json"], capture_output=True,
                                         text=True, check=True).stdout)["header"]
        names = [c["name"] for c in info["channels"]]
        if not set(channels) <= set(names):
            raise SystemExit(f"{recording}: channels {names}, expected {channels}")
        self.recording, self.source, self.channels = recording, source, channels
        self.begin = info["source"]["begin_s"]
        self.num, self.den = info["timebase"]
        # The version covers everything that shapes the output: the decoder, this module and the
        # export reader beside it.
        digest = hashlib.sha256()
        for f in (script, Path(__file__), Path(__file__).with_name("saleae.py")):
            digest.update(f.read_bytes())
        self.head = {"format": "tickwave-annotations/1", "source": source,
                     "version": "sha256:" + digest.hexdigest()[:12], "recording_id": info["content_id"]}

    def tick(self, x: float) -> int:
        """tickwave's rule: round((t - begin) * rate)."""
        return int(round((x - self.begin) * self.num / self.den))

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

    def write(self, annotations: list[dict]) -> Path:
        seen, dup = set(), set()
        for a in annotations:
            (dup if a["id"] in seen else seen).add(a["id"])
        if dup:
            raise SystemExit(f"{self.recording.name}: duplicate ids {sorted(dup)[:5]}")
        out = self.recording.with_name(self.recording.name.removesuffix(".tickwave") + f".{self.source}.jsonl")
        out.write_text("".join(json.dumps(x) + "\n" for x in [self.head] + annotations), newline="\n")
        return out
