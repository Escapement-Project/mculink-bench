"""Read a digital channel of Saleae Logic 2's binary export, version 1 with one chunk, as its
headless server writes it: `<SALEAE>`, u32 version, u32 type, u64 chunks, then the chunk's u32
initial level, f64 sample rate, f64 begin, f64 end, u64 count and count f64 transition times
(seconds). Read by memory map, so a multi-gigabyte channel costs little memory."""

import struct
from pathlib import Path

import numpy as np


def edges(path: Path) -> tuple[int, np.ndarray]:
    """Initial level and the transition times (s) of one digital channel."""
    with open(path, "rb") as f:
        head = f.read(60)
    if head[:8] != b"<SALEAE>":
        raise ValueError(f"{path}: not a Saleae export")
    version, kind, chunks = struct.unpack_from("<iiq", head, 8)
    if version != 1 or kind != 0 or chunks != 1:
        raise ValueError(f"{path}: version {version}, kind {kind}, {chunks} chunks")
    initial, _rate, _begin, _end, count = struct.unpack_from("<Idddq", head, 24)
    if count == 0:
        return initial, np.zeros(0)
    return initial, np.memmap(path, dtype="<f8", mode="r", offset=60, shape=(count,))


def rises(path: Path) -> np.ndarray:
    initial, times = edges(path)
    return np.asarray(times[(0 if initial == 0 else 1)::2])
