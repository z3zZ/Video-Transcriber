"""Generate the app icon (transcriber/static/icon.ico) in the Klokd palette.

Pure numpy + zlib, no image library needed. Run: python scripts/make_icon.py
"""

import struct
import zlib
from pathlib import Path

import numpy as np

BASE = (0x0D, 0x0D, 0x0D)
BORDER = (0x2A, 0x2A, 0x2A)
TEXT = (0xF0, 0xF0, 0xF0)
SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4  # supersampling factor for anti-aliasing


def rounded_rect_mask(n, x0, y0, x1, y1, r):
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    cx = np.clip(xx, x0 + r, x1 - r)
    cy = np.clip(yy, y0 + r, y1 - r)
    return ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r) & (xx >= x0) & (xx <= x1) & (yy >= y0) & (yy <= y1)


def capsule_mask(n, x0, x1, y, half):
    """Horizontal line with round caps."""
    yy, xx = np.mgrid[0:n, 0:n] + 0.5
    cx = np.clip(xx, x0, x1)
    return (xx - cx) ** 2 + (yy - y) ** 2 <= half * half


def render(size):
    n = size * SS
    u = n / 32  # design on a 32-unit grid (same as the web favicon)
    rgba = np.zeros((n, n, 4), dtype=np.float32)

    outer = rounded_rect_mask(n, 0, 0, n, n, 7 * u)
    inner = rounded_rect_mask(n, 1.5 * u, 1.5 * u, n - 1.5 * u, n - 1.5 * u, 5.5 * u)
    rgba[outer] = (*BORDER, 255)
    rgba[inner] = (*BASE, 255)

    # Thicker strokes at tiny sizes so the lines stay visible.
    half = (1.6 if size <= 24 else 1.25) * u
    for y, x1 in ((11, 23), (16, 19), (21, 21)):
        rgba[capsule_mask(n, 9 * u, x1 * u, y * u, half) & inner] = (*TEXT, 255)

    # Downsample with premultiplied alpha.
    a = rgba[..., 3:4] / 255
    pre = np.concatenate([rgba[..., :3] * a, a * 255], axis=-1)
    small = pre.reshape(size, SS, size, SS, 4).mean(axis=(1, 3))
    alpha = small[..., 3:4]
    rgb = np.where(alpha > 0, small[..., :3] / np.maximum(alpha / 255, 1e-6), 0)
    return np.concatenate([rgb, alpha], axis=-1).round().clip(0, 255).astype(np.uint8)


def png_bytes(img):
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    images = [png_bytes(render(s)) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for size, data in zip(SIZES, images):
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    out = Path(__file__).resolve().parent.parent / "transcriber" / "static" / "icon.ico"
    out.write_bytes(header + entries + b"".join(images))
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
