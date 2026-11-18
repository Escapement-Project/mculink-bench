# /// script
# dependencies = ["intelhex"]
# ///
"""itm_counter.hex plus seeded random filler from 0x2000 to 0x56000 (the CCFG sector start)."""
import argparse
import random

from intelhex import IntelHex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("src", help="itm_counter.hex: mculink-verge's tools/swo_target build as Intel HEX")
parser.add_argument("out", help="the image to write, itm_counter_fill344k.hex")
args = parser.parse_args()
image = IntelHex(args.src)
assert image.maxaddr() < 0x2000, hex(image.maxaddr())
rng = random.Random(70)  # the published images' seed: another seed makes another image
filler = rng.randbytes(0x56000 - 0x2000)
image.frombytes(filler, offset=0x2000)
assert image.maxaddr() == 0x55FFF
image.write_hex_file(args.out)
print(f"{args.out}: {len(image)} bytes, {image.minaddr():#x}..{image.maxaddr():#x}")
