"""Writing and reading the OpenEXR scanline format, for the lookup tables.

Only what the tables need: RGBA, 32-bit float, ZIPS compression, one part, no
tiles. There is no EXR library in this environment and pulling one in for four
files would cost more than the format does -- it is a header of named
attributes, a table of scanline offsets, and the scanlines.

The reader exists so the writer can be checked against itself rather than
against Fusion's opinion of the result.
"""
from __future__ import annotations

import struct
import zlib

import numpy as np

MAGIC = 20000630
FLOAT = 2                # the pixel type code for 32-bit float
NO_COMPRESSION, RLE, ZIPS, ZIP = 0, 1, 2, 3

# EXR keeps channels in alphabetical order, and readers rely on it.
ORDER = ("A", "B", "G", "R")


def _attr(name: str, kind: str, data: bytes) -> bytes:
    return (name.encode() + b"\x00" + kind.encode() + b"\x00"
            + struct.pack("<i", len(data)) + data)


def _reorder_and_predict(raw: bytes) -> bytes:
    """The two steps EXR applies before deflating, in its order.

    First the bytes are split into the even and odd halves -- which puts the
    high bytes of the floats together, and they are far more alike than
    neighbouring bytes of one float. Then each byte is stored as its difference
    from the one before. Together they turn floats that vary smoothly across a
    scanline into runs zlib can do something with.
    """
    a = np.frombuffer(raw, dtype=np.uint8)
    half = (len(a) + 1) // 2
    out = np.empty(len(a), dtype=np.uint8)
    out[:half] = a[0::2]
    out[half:] = a[1::2]
    # The difference is taken modulo 256, which is what the C code's
    # (d - p + 128 + 256) truncated to a byte comes to.
    diff = np.empty_like(out)
    diff[0] = out[0]
    diff[1:] = (out[1:].astype(np.int16) - out[:-1].astype(np.int16) + 128) % 256
    return diff.tobytes()


def _unpredict_and_unreorder(data: bytes) -> bytes:
    """The inverse, for reading back what was written."""
    d = np.frombuffer(data, dtype=np.uint8).astype(np.int16)
    out = np.empty(len(d), dtype=np.uint8)
    out[0] = d[0]
    # Each byte is the previous one plus the stored difference, all modulo 256.
    running = np.cumsum(d[1:] - 128) + int(d[0])
    out[1:] = np.mod(running, 256).astype(np.uint8)
    half = (len(out) + 1) // 2
    back = np.empty(len(out), dtype=np.uint8)
    back[0::2] = out[:half]
    back[1::2] = out[half:]
    return back.tobytes()


def write(path, channels: dict[str, np.ndarray]) -> None:
    """One RGBA float EXR. `channels` maps "R"/"G"/"B"/"A" to 2-D arrays."""
    height, width = next(iter(channels.values())).shape
    for name in ORDER:
        if name not in channels:
            raise ValueError(f"channel {name} is missing")
        if channels[name].shape != (height, width):
            raise ValueError(f"channel {name} is a different size")

    chlist = b""
    for name in ORDER:
        chlist += (name.encode() + b"\x00" + struct.pack("<i", FLOAT)
                   + b"\x00" + b"\x00" * 3 + struct.pack("<ii", 1, 1))
    chlist += b"\x00"

    header = struct.pack("<ii", MAGIC, 2)
    header += _attr("channels", "chlist", chlist)
    header += _attr("compression", "compression", bytes([ZIPS]))
    box = struct.pack("<4i", 0, 0, width - 1, height - 1)
    header += _attr("dataWindow", "box2i", box)
    header += _attr("displayWindow", "box2i", box)
    header += _attr("lineOrder", "lineOrder", bytes([0]))
    header += _attr("pixelAspectRatio", "float", struct.pack("<f", 1.0))
    header += _attr("screenWindowCenter", "v2f", struct.pack("<ff", 0.0, 0.0))
    header += _attr("screenWindowWidth", "float", struct.pack("<f", 1.0))
    header += b"\x00"

    rows = [np.ascontiguousarray(channels[n], dtype="<f4") for n in ORDER]
    blocks = []
    for y in range(height):
        raw = b"".join(r[y].tobytes() for r in rows)
        packed = zlib.compress(_reorder_and_predict(raw), 6)
        # EXR stores whichever is smaller, and says so by the size alone.
        blocks.append(raw if len(packed) >= len(raw) else packed)

    # The offset table sits between the header and the scanlines, so its own
    # size has to be counted before any offset can be worked out.
    start = len(header) + 8 * height
    offsets, at = [], start
    for block in blocks:
        offsets.append(at)
        at += 8 + len(block)

    with open(path, "wb") as f:
        f.write(header)
        f.write(b"".join(struct.pack("<Q", o) for o in offsets))
        for y, block in enumerate(blocks):
            f.write(struct.pack("<ii", y, len(block)))
            f.write(block)


def read(path) -> dict[str, np.ndarray]:
    """Back again, for checking. Handles only what `write` produces."""
    with open(path, "rb") as f:
        data = f.read()

    magic, version = struct.unpack_from("<ii", data, 0)
    if magic != MAGIC:
        raise ValueError("not an EXR")
    at = 8
    attrs, names = {}, []
    while data[at] != 0:
        end = data.index(b"\x00", at)
        name = data[at:end].decode(); at = end + 1
        end = data.index(b"\x00", at)
        kind = data[at:end].decode(); at = end + 1
        size = struct.unpack_from("<i", data, at)[0]; at += 4
        attrs[name] = data[at:at + size]; at += size
    at += 1

    raw = attrs["channels"]
    i = 0
    while i < len(raw) - 1:
        end = raw.index(b"\x00", i)
        names.append(raw[i:end].decode())
        i = end + 1 + 16
    x0, y0, x1, y1 = struct.unpack("<4i", attrs["dataWindow"])
    width, height = x1 - x0 + 1, y1 - y0 + 1
    compression = attrs["compression"][0]

    at += 8 * height              # skip the offset table; the blocks follow
    out = {n: np.empty((height, width), dtype=np.float32) for n in names}
    row_bytes = width * 4
    for _ in range(height):
        y, size = struct.unpack_from("<ii", data, at); at += 8
        block = data[at:at + size]; at += size
        if compression == ZIPS and size != row_bytes * len(names):
            block = _unpredict_and_unreorder(zlib.decompress(block))
        for index, name in enumerate(names):
            piece = block[index * row_bytes:(index + 1) * row_bytes]
            out[name][y] = np.frombuffer(piece, dtype="<f4")
    return out
