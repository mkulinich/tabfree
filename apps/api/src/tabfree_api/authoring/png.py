"""Enough PNG decoding to compare two screenshots (P4.5 screenshot diff), with the standard library.

Headless Chromium writes 8-bit RGB or RGBA, non-interlaced PNGs; Tableau's image exports may also be
8-bit grayscale or palette images. Anything else is reported as unsupported rather than guessed at.
:func:`similarity` compares images of different sizes (a Tableau export and a TabFree render).
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
    pos, idat, header, palette = 8, bytearray(), None, b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = bytes(chunk)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("no IHDR chunk")
    width, height, depth, color, _compression, _filter, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if depth != 8 or channels is None or interlace:
        raise PngError("only 8-bit gray, palette, RGB or RGBA non-interlaced PNGs are supported")
    if color == 3 and not palette:
        raise PngError("palette image without a PLTE chunk")
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
    if color in (0, 3, 4):  # expand to RGB so callers see one layout
        rgb = bytearray(width * height * 3)
        for i in range(width * height):
            if color == 3:
                index = out[i] * 3
                rgb[i * 3 : i * 3 + 3] = palette[index : index + 3].ljust(3, b"\0")
            else:
                g = out[i * channels]
                rgb[i * 3 : i * 3 + 3] = bytes((g, g, g))
        return Image(width, height, 3, bytes(rgb))
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


def _grid(image: Image, cols: int, rows: int) -> list[float]:
    """Mean luminance (0-1) of each cell of a cols x rows grid over the image (alpha over white).

    Each cell covers at least one pixel, so images smaller than the grid still fill it; large cells
    are sampled (at most 8 x 8 points), which keeps big screenshots fast.
    """
    out: list[float] = []
    ch = image.channels
    for r in range(rows):
        y0 = r * image.height // rows
        y1 = max(y0 + 1, (r + 1) * image.height // rows)
        ystep = max(1, (y1 - y0) // 8)
        for c in range(cols):
            x0 = c * image.width // cols
            x1 = max(x0 + 1, (c + 1) * image.width // cols)
            xstep = max(1, (x1 - x0) // 8)
            total, n = 0.0, 0
            for y in range(y0, min(y1, image.height), ystep):
                base = y * image.width
                for x in range(x0, min(x1, image.width), xstep):
                    i = (base + x) * ch
                    r_, g, b = image.pixels[i], image.pixels[i + 1], image.pixels[i + 2]
                    lum = (0.299 * r_ + 0.587 * g + 0.114 * b) / 255
                    if ch == 4:
                        alpha = image.pixels[i + 3] / 255
                        lum = lum * alpha + (1 - alpha)
                    total += lum
                    n += 1
            out.append(total / n if n else 1.0)
    return out


def similarity(reference: bytes, candidate: bytes, *, cols: int = 48, rows: int = 32) -> float:
    """How alike two images look at layout scale, from 0 to 1, whatever their sizes.

    Both are reduced to a grid of mean luminance. The score averages how close the cells are
    (1 - mean absolute difference) and how well the patterns correlate (where things are), so a
    blank render scores low against a busy dashboard even though most pixels are white in both.
    It is a coarse visual-parity signal for triage, not a pixel comparison.
    """
    a, b = _grid(decode(reference), cols, rows), _grid(decode(candidate), cols, rows)
    n = len(a)
    closeness = 1 - sum(abs(x - y) for x, y in zip(a, b, strict=True)) / n
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a < 1e-9 and var_b < 1e-9:
        correlation = 1.0
    elif var_a < 1e-9 or var_b < 1e-9:
        correlation = 0.0
    else:
        correlation = max(0.0, cov / (var_a * var_b) ** 0.5)
    return round(0.5 * closeness + 0.5 * correlation, 4)
