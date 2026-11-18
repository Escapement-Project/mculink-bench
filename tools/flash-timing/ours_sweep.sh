#!/usr/bin/env bash
# An MCU-Link Pro running Verge, through the probe-rs in Cargo.toml: JTAG, then each cJTAG format,
# both images, every clock, 5 runs. Run it alone, with nothing else using the bench:
#   ./ours_sweep.sh <serial> [formats...]
# <serial> is the Pro's USB serial; the formats are any of jtag, oscan1, oscan2, oscan5 and oscan6,
# all of them when left out. VERGE names the verge binary and TOOL flash-timing's, as a command on
# the PATH or an absolute path: `verge`, and target/release/flash-timing beside this script, when
# unset. The images and runs/ are beside this script too. Each cJTAG run connects in its format
# with --cjtag-format; nothing is left on the probe.
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "usage: $0 <serial> [formats...]: give the USB serial of the Pro to time" >&2
  exit 2
fi
SERIAL=$1
shift
cd "$(dirname "$0")"
VERGE=${VERGE:-verge}
TOOL=${TOOL:-target/release/flash-timing}
mkdir -p runs

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
