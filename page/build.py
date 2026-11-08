"""Build the standalone measurements page from committed result files."""
from __future__ import annotations

import base64
import json
import pathlib
import sys

PAGE = pathlib.Path(__file__).resolve().parent
ROOT = PAGE.parent
sys.path.insert(0, str(PAGE))
from extract import build_data


def main() -> None:
    # Without dates, every set in sets.json, oldest first.
    dates = sys.argv[1:] or sorted(json.loads((PAGE / "sets.json").read_text()))
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
    output.write_text(html)
    print(f"wrote {output.relative_to(ROOT)} for {', '.join(dates)}")


if __name__ == "__main__":
    main()
