"""Enough PNG decoding to compare two screenshots (P4.5 screenshot diff), with the standard library.

Headless Chromium writes 8-bit RGB or RGBA, non-interlaced PNGs; anything else is reported as
unsupported rather than guessed at.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(ValueError):
    pass


@dataclass(frozen=True)
class Image:
    width: int
    height: int
    channels: int
    pixels: bytes  # rows of width * channels bytes, unfiltered


def decode(data: bytes) -> Image:
    if not data.startswith(_SIGNATURE):
        raise PngError("not a PNG")
    pos, idat, header = 8, bytearray(), None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("no IHDR chunk")
    width, height, depth, color, _compression, _filter, interlace = header
    channels = {2: 3, 6: 4}.get(color)
    if depth != 8 or channels is None or interlace:
        raise PngError("only 8-bit RGB/RGBA, non-interlaced PNGs are supported")
    if width * height > 40_000_000:
        raise PngError("image too large")
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind, line = raw[start], bytearray(raw[start + 1 : start + 1 + stride])
        for x in range(stride):
            a = line[x - channels] if x >= channels else 0
            b = previous[x]
            c = previous[x - channels] if x >= channels else 0
            if kind == 1:
                line[x] = (line[x] + a) & 0xFF
            elif kind == 2:
                line[x] = (line[x] + b) & 0xFF
            elif kind == 3:
                line[x] = (line[x] + (a + b) // 2) & 0xFF
            elif kind == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif kind != 0:
                raise PngError(f"unknown filter type {kind}")
        out[y * stride : (y + 1) * stride] = line
        previous = line
    return Image(width, height, channels, bytes(out))


def changed_fraction(before: bytes, after: bytes, *, tolerance: int = 16) -> float:
    """The share of pixels that differ by more than ``tolerance`` in any channel (1.0 if sizes differ)."""
    a, b = decode(before), decode(after)
    if (a.width, a.height) != (b.width, b.height):
        return 1.0
    changed = 0
    for i in range(a.width * a.height):
        pa = a.pixels[i * a.channels : i * a.channels + 3]
        pb = b.pixels[i * b.channels : i * b.channels + 3]
        if any(abs(x - y) > tolerance for x, y in zip(pa, pb, strict=True)):
            changed += 1
    return changed / max(1, a.width * a.height)
