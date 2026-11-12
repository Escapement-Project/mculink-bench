"""Build the standalone measurements page from committed result files.

    build.py [DATE ...]   # write out/index.html for these sets, or for every set in sets.json
    build.py --check      # fail if out/index.html differs from a fresh build of every set

The page shows the project's shared header and footer and takes its base styles and header
scripts from the site: copies of the site's files, inlined, so the page also works from disk.

A set with a wire fragment, wire/<DATE>.html, also gets its section on the wire: prose whose
figures open the wire recordings they come from, through tickwave. The build checks every
reference in a fragment against the recordings' sidecars, in a checkout of mculink-recordings
(--recordings, beside this repository by default). A set whose sets.json entry names
`wire_charts` shows those charts from the recordings, each point's recording and annotation checked
the same way.
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
from html.parser import HTMLParser

PAGE = pathlib.Path(__file__).resolve().parent
ROOT = PAGE.parent
sys.path.insert(0, str(PAGE))
from extract import build_data

# Where the published page finds the recordings and tickwave's elements. The recordings repository
# serves <date>/<name>.tickwave beside each recording's sidecar; tickwave's site serves a pinned
# version of tickwave.js, which loads its worker and decoder from beside itself.
RECORDINGS_URL = "https://escapement-project.github.io/mculink-recordings/"
TICKWAVE_URL = "https://escapement-project.github.io/tickwave/v0.1.0/"


def sidecar_ids(recordings: pathlib.Path, date: str, name: str) -> set[str]:
    """Every annotation id a page may cite in a recording: the hand-written file's own, and each
    included generator file's, as <source>/<id>. Every file must name the same recording. The
    full check, `tickwave annotations check`, runs where the recordings are."""
    toml = recordings / date / f"{name}.annotations.toml"
    if not toml.is_file():
        raise SystemExit(f"wire/{date}.html: no sidecar {toml}")
    side = tomllib.loads(toml.read_text())
    ids = {a["id"] for a in side.get("annotation", [])}
    for inc in side.get("include", []):
        lines = (toml.parent / inc).read_text().splitlines()
        head = json.loads(lines[0])
        if head.get("recording_id") != side.get("recording_id"):
            raise SystemExit(f"{inc}: recording_id differs from {toml.name}'s")
        ids |= {f"{head['source']}/{json.loads(line)['id']}" for line in lines[1:] if line.strip()}
    return ids


class Fragment(HTMLParser):
    """A wire fragment's parts, each a <section class="wire-view" data-view="...">, with the views
    and references inside it, and every element where it should not be."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[tuple[str | None, dict[str, str], list[tuple[str, str, int]]]] = []
        self.problems: list[str] = []
        self.depth = 0  # sections open inside the current part, the part's own included

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a, line = dict(attrs), self.getpos()[0]
        if tag == "section" and "wire-view" in (a.get("class") or "").split():
            if self.depth:
                self.problems.append(f"line {line}: a part inside a part")
            self.parts.append((a.get("data-view"), {}, []))
            self.depth = 1
        elif tag == "section" and self.depth:
            self.depth += 1
        elif tag.startswith("tickwave-") and not self.depth:
            self.problems.append(f"line {line}: <{tag}> outside a part")
        elif tag == "tickwave-view":
            views = self.parts[-1][1]
            if set(a) & {"src", "annotations"} or not a.get("id") or not a.get("data-recording"):
                self.problems.append(f"line {line}: a view takes id and data-recording, not src or annotations")
            elif a["id"] in views:
                self.problems.append(f"line {line}: a second view {a['id']} in its part")
            else:
                views[a["id"]] = a["data-recording"]
        elif tag == "tickwave-ref":
            if set(a) & {"src", "annotations"} or not a.get("for") or not a.get("annotation"):
                self.problems.append(f"line {line}: a reference takes for and annotation, not src or annotations")
            else:
                self.parts[-1][2].append((a["for"], a["annotation"], line))
        elif tag.startswith("tickwave-"):
            self.problems.append(f"line {line}: <{tag}> is not used in a fragment")

    def handle_endtag(self, tag: str) -> None:
        if tag == "section" and self.depth:
            self.depth -= 1


def wire_fragment(date: str, view_ids: list[str], recordings: pathlib.Path, recordings_url: str) -> str | None:
    """The set's wire fragment, every reference checked, recordings resolved to URLs; None
    without one.

    A part shows with its wire view. In it, a view names its recording, <tickwave-view id="v"
    data-recording="NAME">, and a reference names a view of its own part and an annotation of that
    recording, <tickwave-ref for="v" annotation="SOURCE/ID">."""
    path = PAGE / "wire" / f"{date}.html"
    if not path.is_file():
        return None
    text = path.read_text()
    frag = Fragment()
    frag.feed(text)
    frag.close()
    problems = list(frag.problems)
    if not frag.parts:
        problems.append('no <section class="wire-view" data-view="..."> parts')
    seen = set()
    for view, views, refs in frag.parts:
        if view not in view_ids:
            problems.append(f"part for view {view!r}: the set's views are {', '.join(view_ids)}")
        elif view in seen:
            problems.append(f"a second part for view {view}")
        seen.add(view)
        for to, ann, line in refs:
            if to not in views:
                problems.append(f"line {line}: reference to {ann} names view {to}, not in its part")
            elif ann not in sidecar_ids(recordings, date, views[to]):
                problems.append(f"line {line}: {views[to]} has no annotation {ann}")
    if problems:
        raise SystemExit(f"wire/{date}.html:\n  " + "\n  ".join(problems))
    base = f"{recordings_url}{date}/"
    return re.sub(r'data-recording="([^"]+)"', lambda m: (
        f'src="{htmllib.escape(base + m.group(1))}.tickwave" annotations="{htmllib.escape(base + m.group(1))}.annotations.toml"'), text)


def wire_charts(date: str, names: list[str], recordings: pathlib.Path, recordings_url: str) -> list[str]:
    """The set's wire-clock charts in the recordings, as URLs, each point's evidence checked: its
    recording's sidecar has the annotation (tools/wire/wire_chart.py writes them)."""
    problems, urls = [], []
    for name in names:
        path = recordings / date / f"{name}.chart.json"
        if not path.is_file():
            problems.append(f"no {path}")
            continue
        chart = json.loads(path.read_text())
        if chart.get("format") != "tickwave-chart/1":
            problems.append(f"{name}: format {chart.get('format')!r}")
        for series in chart.get("series", []):
            for point in series.get("points", []):
                e = (point or {}).get("evidence")
                if not e:
                    continue
                # The sidecars, not the recordings: a build checks out only the small files, and
                # the recordings repository checks its recordings against them.
                rec = e["recording"].removesuffix(".tickwave")
                if not (recordings / date / f"{rec}.annotations.toml").is_file():
                    problems.append(f"{name}: no sidecar for {e['recording']}")
                elif e["annotation"] not in sidecar_ids(recordings, date, rec):
                    problems.append(f"{name}: {rec} has no annotation {e['annotation']}")
        urls.append(f"{recordings_url}{date}/{name}.chart.json")
    if problems:
        raise SystemExit(f"sets.json {date} wire_charts:\n  " + "\n  ".join(problems))
    return urls


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dates", nargs="*", help="sets to include; every set in sets.json without")
    ap.add_argument("--check", action="store_true", help="compare out/index.html with a fresh build instead of writing it")
    ap.add_argument("--recordings", type=pathlib.Path, default=ROOT.parent / "mculink-recordings",
                    help="a checkout of mculink-recordings, for the wire fragments' references")
    ap.add_argument("--recordings-url", default=RECORDINGS_URL, help="for a local preview")
    ap.add_argument("--tickwave-url", default=TICKWAVE_URL, help="for a local preview")
    args = ap.parse_args()
    if args.check and (args.recordings_url != RECORDINGS_URL or args.tickwave_url != TICKWAVE_URL):
        raise SystemExit("--check builds the published page: leave the URLs as they are")
    if args.check and args.dates:
        raise SystemExit("--check builds every set: give no dates")
    # Without dates, every set in sets.json, oldest first.
    dates = args.dates or sorted(json.loads((PAGE / "sets.json").read_text()))
    data = build_data(dates)
    manifest = json.loads((PAGE / "sets.json").read_text())
    for d in dates:
        if manifest[d].get("wire_charts"):
            data["sets"][d]["recorded_wire"] = wire_charts(d, manifest[d]["wire_charts"], args.recordings, args.recordings_url)
    template = (PAGE / "template.html").read_text()
    if template.count("__DATA__") != 1:
        raise SystemExit("template must contain exactly one __DATA__ placeholder")
    for placeholder in ("__TOKENS__", "__BASE__", "__HEADER__", "__FOOTER__", "__FACEJS__", "__HEADERJS__", "__ICON__", "__WIRE__", "__TICKWAVE__"):
        if template.count(placeholder) != 1:
            raise SystemExit(f"template must contain exactly one {placeholder} placeholder")
    # Copies of the site's files (see README.md), inlined so the page carries them when opened from
    # disk too: the design tokens, whose fonts load from fonts/ beside the page; the shared base
    # sheet; the header and footer every page of the project shows, and the group of Verge's pages the
    # header shows on them; the day and night switch and the header's script; and the project's mark
    # and Verge's favicon. out/ is the published page and nothing
    # else: the site serves the folder as it is.
    copy = {name: (PAGE / name).read_text() for name in ("tokens.css", "base.css", "header.html", "header-verge.html",
                                                          "footer.html", "face.js", "header.js")}
    for name, closing in (("tokens.css", "</style"), ("base.css", "</style"), ("face.js", "</script"), ("header.js", "</script")):
        if closing in copy[name].lower():
            raise SystemExit(f"{name} holds {closing}>, which would end the element it is inlined in")
    mark = (PAGE / "dial.svg").read_text().strip().replace("<svg ", '<svg aria-hidden="true" fill="currentColor" ', 1)
    icon = "data:image/x-icon;base64," + base64.b64encode((PAGE / "favicon-verge.ico").read_bytes()).decode()
    # The header's links are root-relative: the site serves this page, and opened from disk its
    # script points them at the published site. The page is one of Verge's, so Verge's link gives way
    # to Verge's group (the site's design/README.md), with Verge marked and this page's entry as the
    # page shown.
    header, group = copy["header.html"].strip(), copy["header-verge.html"].strip()
    link = '<a href="{{base}}/verge/" data-page="verge">Verge</a>'
    for name, text, marker, count in (("header.html", header, "{{mark}}", 1), ("header.html", header, "{{base}}", None),
                                      ("header.html", header, link, 1), ("header-verge.html", group, 'data-page="verge"', 1),
                                      ("header-verge.html", group, 'data-page="verge/measurements"', 1)):
        if text.count(marker) == 0 or (count and text.count(marker) != count):
            raise SystemExit(f"{name}: expected {count or 'some'} {marker}; the site's header changed shape")
    header = (header.replace(link, group).replace("{{mark}}", mark).replace("{{base}}", "")
              .replace('data-page="verge"', 'data-page="verge" aria-current="true"')
              .replace('data-page="verge/measurements"', 'data-page="verge/measurements" aria-current="page"'))
    # Each set's wire section as an inert template; the page shows the part for the view shown.
    wire = "".join(f'<template id="wire-{d}">\n{frag}</template>\n' for d in dates
                   if (frag := wire_fragment(d, [v["id"] for v in data["sets"][d]["views"]], args.recordings,
                                             args.recordings_url)) is not None)
    wire = f'<div id="on-the-wire" data-tickwave="{htmllib.escape(args.tickwave_url)}"></div>\n{wire}'
    html = (template.replace("__TOKENS__", copy["tokens.css"]).replace("__BASE__", copy["base.css"])
            .replace("__HEADER__", header).replace("__FOOTER__", copy["footer.html"].strip())
            .replace("__FACEJS__", copy["face.js"]).replace("__HEADERJS__", copy["header.js"])
            .replace("__ICON__", icon).replace("__TICKWAVE__", htmllib.escape(args.tickwave_url)).replace("__WIRE__", wire).replace("__DATA__", json.dumps(data, separators=(",", ":"))))
    output = PAGE / "out" / "index.html"
    if args.check:
        # The page is what readers get: its copies of the site's files can be current while the
        # page still carries old ones, until it is rebuilt.
        if not output.is_file() or output.read_text() != html:
            raise SystemExit(f"stale: {output.relative_to(ROOT)} differs from a fresh build; run page/build.py")
        print(f"current: {output.relative_to(ROOT)}")
        return
    output.write_text(html, newline="\n")
    print(f"wrote {output.relative_to(ROOT)} for {', '.join(dates)}")


if __name__ == "__main__":
    main()
