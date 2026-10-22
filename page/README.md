# The results page

The source of the "MCU-Link SWD Throughput" page, built from `results/throughput/<date>-<label>.json` and `results/wireclock/<date>-<label>.json`.

    uv run python -I extract.py data.json 2026-10-20 [later dates ...]   # one set per date
    # then replace __DATA__ in template.html with data.json's text, and publish the result

The page opens on the last date given and shows a switch in the title rail when there is more than
one. A retake (the repository's README, "Retaking") is a new date on the same command line.
