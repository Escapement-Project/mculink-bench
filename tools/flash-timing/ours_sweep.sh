#!/usr/bin/env bash
# Our probe (the Pro) through probe-rs 17beb8e80: JTAG, then each cJTAG format, both images, every
# clock, 5 runs (BACKLOG 70). Run as one bench slot:
#   ./ours_sweep.sh [formats...]
# Each cJTAG run holds the target to its format with --cjtag-format; nothing is left on the probe.
set -euo pipefail
cd "$(dirname "$0")"
SERIAL=CJ8C2F60A3
VERGE=${VERGE:-verge}
TOOL=${TOOL:-target/release/flash-timing}

"$VERGE" --serial "$SERIAL" power | sed 's/^/supply: /'
for format in "${@:-jtag oscan1 oscan2 oscan5 oscan6}"; do
  for f in $format; do
    if [ "$f" = jtag ]; then
      held=(--protocol jtag)
    else
      held=(--protocol cjtag --cjtag-format "$f")
    fi
    "$TOOL" --probe "1fc9:0143:$SERIAL" "${held[@]}" --repeats 5 \
      --image itm_counter.hex --image itm_counter_fill344k.hex \
      --label "ours-probe-rs-$f" --json "runs/ours-probe-rs-$f.json" 2>&1 | tee "runs/ours-probe-rs-$f.log" \
      | tr '\r' '\n' | rg 'kHz: [0-9]+/[0-9]+ ok|median' || true
  done
done
