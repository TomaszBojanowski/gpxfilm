"""Sprites with alpha and drawing helpers shared by labels and frame composition."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

WHITE, MUTE = (255, 255, 255, 255), (230, 233, 235, 255)   # text colors: names and numbers, captions


class Sprite:
    """An image with an alpha channel (premultiplied), ready to be laid over quickly."""

    def __init__(self, img: Image.Image, pad: int = 0, w: float | None = None, h: float | None = None):
        arr = np.asarray(img.convert("RGBa"), np.float32)
        self.pre, self.a = np.ascontiguousarray(arr[..., :3]), np.ascontiguousarray(arr[..., 3] / 255.0)
        self.pad, self.w, self.h = pad, (img.width if w is None else w), (img.height if h is None else h)


def blit(dst: np.ndarray, sp: Sprite, x: float, y: float, op: float = 1.0) -> None:
    x, y = int(round(x)) - sp.pad, int(round(y)) - sp.pad
    h, w = sp.a.shape
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, dst.shape[1]), min(y + h, dst.shape[0])
    if x0 >= x1 or y0 >= y1 or op <= 0:
        return
    a = sp.a[y0 - y:y1 - y, x0 - x:x1 - x] * op
    reg = dst[y0:y1, x0:x1].astype(np.float32)
    reg *= (1 - a)[..., None]
    reg += sp.pre[y0 - y:y1 - y, x0 - x:x1 - x] * op
    dst[y0:y1, x0:x1] = np.clip(reg + 0.5, 0, 255).astype(np.uint8)


def shape_sprite(w: float, h: float, fn, ss: int = 4) -> Sprite:
    w, h = int(math.ceil(w)), int(math.ceil(h))
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    fn(ImageDraw.Draw(img), ss)
    return Sprite(img.resize((w, h), Image.LANCZOS))


def stroke_mask(size, pts, width: float, ss: int) -> np.ndarray:
    img = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    d = ImageDraw.Draw(img)
    p = [(float(x) * ss, float(y) * ss) for x, y in pts]
    d.line(p, fill=255, width=max(1, int(round(width * ss))), joint="curve")
    r = width * ss / 2
    for x, y in (p[0], p[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    return np.asarray(img.resize(size, Image.LANCZOS), np.float32) / 255
