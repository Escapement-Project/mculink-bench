"""Build the standalone measurements page from committed result files.

    build.py [DATE ...]   # write out/index.html for these sets, or for every set in sets.json
    build.py --check      # fail if out/index.html differs from a fresh build of every set

A set with a wire fragment, wire/<DATE>.html, also gets its section on the wire: prose whose
figures open the wire recordings they come from, through chrono. The build checks every
reference in a fragment against the recordings' sidecars, in a checkout of mculink-recordings
(--recordings, beside this repository by default).
"""
from __future__ import annotations

import argparse
import base64
import html as htmllib
import json
import pathlib
import re
import sys
import tomllib

PAGE = pathlib.Path(__file__).resolve().parent
ROOT = PAGE.parent
sys.path.insert(0, str(PAGE))
from extract import build_data

# Where the published page finds the recordings and chrono's elements. The recordings repository
# serves <date>/<name>.chrono beside each recording's sidecar; chrono's site serves a pinned
# version of chrono.js, which loads its worker and decoder from beside itself.
RECORDINGS_URL = "https://escapement-project.github.io/mculink-recordings/"
CHRONO_URL = "https://escapement-project.github.io/chrono/v0.1.0/"


def sidecar_ids(recordings: pathlib.Path, date: str, name: str) -> set[str]:
    """Every annotation id a page may cite in a recording: the hand-written file's own, and each
    included generator file's, as <source>/<id>."""
    toml = recordings / date / f"{name}.annotations.toml"
    if not toml.is_file():
        raise SystemExit(f"wire/{date}.html: no sidecar {toml}")
    side = tomllib.loads(toml.read_text())
    ids = {a["id"] for a in side.get("annotation", [])}
    for inc in side.get("include", []):
        lines = (toml.parent / inc).read_text().splitlines()
        source = json.loads(lines[0])["source"]
        ids |= {f"{source}/{json.loads(line)['id']}" for line in lines[1:] if line.strip()}
    return ids


def wire_fragment(date: str, recordings: pathlib.Path, recordings_url: str) -> str | None:
    """The set's wire fragment, references checked, recordings resolved to URLs; None without one.

    In the fragment a view names its recording, <chrono-view id="v" data-recording="NAME">, and a
    reference names the view and the annotation, <chrono-ref for="v" annotation="SOURCE/ID">."""
    path = PAGE / "wire" / f"{date}.html"
    if not path.is_file():
        return None
    text = path.read_text()
    problems = []
    # One part a wire view, each a top-level <section class="wire-view" data-view="...">: a
    # reference names a view in its own part, which the page shows on its own.
    parts = re.split(r'(?=<section class="wire-view")', text)[1:]
    if not parts:
        problems.append('no <section class="wire-view" data-view="..."> parts')
    for part in parts:
        views = dict(re.findall(r'<chrono-view id="([^"]+)" data-recording="([^"]+)"', part))
        for view, ann in re.findall(r'<chrono-ref for="([^"]+)" annotation="([^"]+)"', part):
            if view not in views:
                problems.append(f"reference to {ann} names view {view}, not in its part")
            elif ann not in sidecar_ids(recordings, date, views[view]):
                problems.append(f"{views[view]} has no annotation {ann}")
    if problems:
        raise SystemExit(f"wire/{date}.html:\n  " + "\n  ".join(problems))
    base = f"{recordings_url}{date}/"
    return re.sub(r'data-recording="([^"]+)"', lambda m: (
        f'src="{htmllib.escape(base + m.group(1))}.chrono" annotations="{htmllib.escape(base + m.group(1))}.annotations.toml"'), text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dates", nargs="*", help="sets to include; every set in sets.json without")
    ap.add_argument("--check", action="store_true", help="compare out/index.html with a fresh build instead of writing it")
    ap.add_argument("--recordings", type=pathlib.Path, default=ROOT.parent / "mculink-recordings",
                    help="a checkout of mculink-recordings, for the wire fragments' references")
    ap.add_argument("--recordings-url", default=RECORDINGS_URL, help="for a local preview")
    ap.add_argument("--chrono-url", default=CHRONO_URL, help="for a local preview")
    args = ap.parse_args()
    if args.check and (args.recordings_url != RECORDINGS_URL or args.chrono_url != CHRONO_URL):
        raise SystemExit("--check builds the published page: leave the URLs as they are")
    if args.check and args.dates:
        raise SystemExit("--check builds every set: give no dates")
    # Without dates, every set in sets.json, oldest first.
    dates = args.dates or sorted(json.loads((PAGE / "sets.json").read_text()))
    data = build_data(dates)
    template = (PAGE / "template.html").read_text()
    if template.count("__DATA__") != 1:
        raise SystemExit("template must contain exactly one __DATA__ placeholder")
    for placeholder in ("__TOKENS__", "__MARK__", "__ICON__", "__WIRE__"):
        if template.count(placeholder) != 1:
            raise SystemExit(f"template must contain exactly one {placeholder} placeholder")
    # The design tokens, a copy of the site's static/tokens.css; their fonts load from fonts/ beside
    # the page. out/ is the published page and nothing else: the site serves the folder as it is.
    tokens = (PAGE / "tokens.css").read_text()
    # The project's mark and favicon, copies of the site's design/marks/dial.svg and
    # static/favicon.ico, inlined so the page carries them when opened from disk too.
    mark = (PAGE / "dial.svg").read_text().strip().replace("<svg ", '<svg aria-hidden="true" fill="currentColor" ', 1)
    icon = "data:image/x-icon;base64," + base64.b64encode((PAGE / "favicon.ico").read_bytes()).decode()
    # Each set's wire section as an inert template; the page shows the part for the view shown.
    wire = "".join(f'<template id="wire-{d}">\n{frag}</template>\n' for d in dates
                   if (frag := wire_fragment(d, args.recordings, args.recordings_url)) is not None)
    wire = f'<div id="on-the-wire" data-chrono="{htmllib.escape(args.chrono_url)}"></div>\n{wire}'
    html = (template.replace("__TOKENS__", tokens).replace("__MARK__", mark).replace("__ICON__", icon)
            .replace("__WIRE__", wire).replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    output = PAGE / "out" / "index.html"
    if args.check:
        # The page is what readers get: its copies of the tokens, mark and favicon can be current
        # while the page still carries old ones, until it is rebuilt.
        if not output.is_file() or output.read_text() != html:
            raise SystemExit(f"stale: {output.relative_to(ROOT)} differs from a fresh build; run page/build.py")
        print(f"current: {output.relative_to(ROOT)}")
        return
    output.write_text(html, newline="\n")
    print(f"wrote {output.relative_to(ROOT)} for {', '.join(dates)}")


if __name__ == "__main__":
    main()
