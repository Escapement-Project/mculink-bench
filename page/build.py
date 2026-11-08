"""Build the standalone measurements page from committed result files.

    build.py [DATE ...]   # write out/index.html for these sets, or for every set in sets.json
    build.py --check      # fail if out/index.html differs from a fresh build of every set
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys

PAGE = pathlib.Path(__file__).resolve().parent
ROOT = PAGE.parent
sys.path.insert(0, str(PAGE))
from extract import build_data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dates", nargs="*", help="sets to include; every set in sets.json without")
    ap.add_argument("--check", action="store_true", help="compare out/index.html with a fresh build instead of writing it")
    args = ap.parse_args()
    if args.check and args.dates:
        raise SystemExit("--check builds every set: give no dates")
    # Without dates, every set in sets.json, oldest first.
    dates = args.dates or sorted(json.loads((PAGE / "sets.json").read_text()))
    data = build_data(dates)
    template = (PAGE / "template.html").read_text()
    if template.count("__DATA__") != 1:
        raise SystemExit("template must contain exactly one __DATA__ placeholder")
    for placeholder in ("__TOKENS__", "__MARK__", "__ICON__"):
        if template.count(placeholder) != 1:
            raise SystemExit(f"template must contain exactly one {placeholder} placeholder")
    # The design tokens, a copy of the site's static/tokens.css; their fonts load from fonts/ beside
    # the page. out/ is the published page and nothing else: the site serves the folder as it is.
    tokens = (PAGE / "tokens.css").read_text()
    # The project's mark and favicon, copies of the site's design/marks/dial.svg and
    # static/favicon.ico, inlined so the page carries them when opened from disk too.
    mark = (PAGE / "dial.svg").read_text().strip().replace("<svg ", '<svg aria-hidden="true" fill="currentColor" ', 1)
    icon = "data:image/x-icon;base64," + base64.b64encode((PAGE / "favicon.ico").read_bytes()).decode()
    html = (template.replace("__TOKENS__", tokens).replace("__MARK__", mark).replace("__ICON__", icon)
            .replace("__DATA__", json.dumps(data, separators=(",", ":"))))
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
