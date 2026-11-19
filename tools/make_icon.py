"""Draw the plugin icon (.claude-plugin/icon.png): a white check mark on a teal rounded square. Standard library only.

    python tools/make_icon.py            # rewrites .claude-plugin/icon.png (1024 x 1024)
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIZE = 1024
BG = (15, 118, 110)       # teal
FG = (255, 255, 255)
RADIUS = 200
THICK = 62
# the two strokes of the check mark, as (x1, y1, x2, y2)
STROKES = [(290, 540, 450, 700), (450, 700, 760, 340)]


def dist_to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    dx, dy = x2 - x1, y2 - y1
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return ((px - (x1 + t * dx)) ** 2 + (py - (y1 + t * dy)) ** 2) ** 0.5


def inside_rounded_square(x: int, y: int) -> bool:
    cx = min(max(x, RADIUS), SIZE - 1 - RADIUS)
    cy = min(max(y, RADIUS), SIZE - 1 - RADIUS)
    return (x - cx) ** 2 + (y - cy) ** 2 <= RADIUS ** 2


def pixel(x: int, y: int) -> tuple[int, int, int, int]:
    if not inside_rounded_square(x, y):
        return (0, 0, 0, 0)
    if any(dist_to_segment(x, y, *s) <= THICK / 2 for s in STROKES):
        return (*FG, 255)
    return (*BG, 255)


def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png() -> bytes:
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)
        for x in range(SIZE):
            rows.extend(pixel(x, y))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / ".claude-plugin" / "icon.png"
    out.write_bytes(png())
    print(f"wrote {out} ({out.stat().st_size} bytes)", file=sys.stderr)
