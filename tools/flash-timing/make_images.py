# /// script
# dependencies = ["intelhex"]
# ///
"""itm_counter.hex plus seeded random filler from 0x2000 to 0x56000 (the CCFG sector start)."""
import random
import sys
from intelhex import IntelHex

src, out = sys.argv[1], sys.argv[2]
image = IntelHex(src)
assert image.maxaddr() < 0x2000, hex(image.maxaddr())
rng = random.Random(70)  # the published images' seed: another seed makes another image
filler = rng.randbytes(0x56000 - 0x2000)
image.frombytes(filler, offset=0x2000)
assert image.maxaddr() == 0x55FFF
image.write_hex_file(out)
print(f"{out}: {len(image)} bytes, {image.minaddr():#x}..{image.maxaddr():#x}")
