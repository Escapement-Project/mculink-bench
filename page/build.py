"""Build the standalone measurements page from committed result files."""
from __future__ import annotations

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
    html = template.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    output = PAGE / "index.html"
    output.write_text(html)
    print(f"wrote {output.relative_to(ROOT)} for {', '.join(dates)}")


if __name__ == "__main__":
    main()
