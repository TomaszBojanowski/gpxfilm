"""Typefaces, text labels and their placement on the map."""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .draw import MUTE, Sprite
from .geo import LIMITS
from .i18n import N_, NC_, translator
from .net import CACHE, http_get

# Pillow lays text out with libraqm when it finds the FriBiDi library, otherwise with its basic engine (no kerning).
# GPXFILM_TEXT_LAYOUT=basic forces the basic engine, so the reference tests give the same frames on every machine.
LAYOUT = ImageFont.Layout.BASIC if os.environ.get("GPXFILM_TEXT_LAYOUT") == "basic" else None
NB = "\u00a0"
FILM_TEXTS = dict(dist=N_("distance"), up=N_("ascent"), time=N_("elapsed"), stop=N_("stop"), clock=N_("time"), ele=N_("elevation"),
                  spd=N_("speed"), hr=N_("heart rate"), slope=N_("slope"), moving=N_("moving"), top=N_("highest point"),
                  start=N_("Start"), finish=N_("Finish"), loop=N_("Start and finish"), credit=N_("Terrain: Mapterhorn. Map: © OpenStreetMap"))
MONTHS = (NC_("date", "January"), NC_("date", "February"), NC_("date", "March"), NC_("date", "April"), NC_("date", "May"),
          NC_("date", "June"), NC_("date", "July"), NC_("date", "August"), NC_("date", "September"), NC_("date", "October"),
          NC_("date", "November"), NC_("date", "December"))
DECIMAL = NC_("decimal separator", ".")


def film_texts(language: str) -> dict:
    """The texts drawn on the film, in its language (--language): words of the counters, labels and outro, the month names of
    the date under the title, the decimal separator and the default map credit."""
    t = translator(language)
    out = {k: t.gettext(v) for k, v in FILM_TEXTS.items()}
    out.update(months=[t.pgettext(*m) for m in MONTHS], dec=t.pgettext(*DECIMAL))
    return out


class Fonts:
    FAM = {"disp": ("Big Shoulders Display", 800), "semi": ("Figtree", 600), "reg": ("Figtree", 400)}

    def __init__(self):
        self.paths, self.cache = {}, {}
        for key, (fam, wgt) in self.FAM.items():
            p = CACHE / "fonts" / f"{fam.replace(' ', '')}-{wgt}.ttf"
            if not p.exists():
                try:
                    css = http_get(f"https://fonts.googleapis.com/css2?family={fam.replace(' ', '+')}:wght@{wgt}", tries=2).decode()
                    url = re.search(r"url\((https://[^)]+\.ttf)\)", css).group(1)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(http_get(url, tries=2))
                except Exception:  # noqa: BLE001 – without a network, use a system typeface
                    p = None
            if p is None and shutil.which("fc-match"):
                pat = "sans-serif:bold" if key != "reg" else "sans-serif"
                f = subprocess.run(["fc-match", "-f", "%{file}", pat], capture_output=True, text=True).stdout.strip()
                p = Path(f) if f else None
            if p is None:                             # macOS has no fc-match: system typefaces
                names = ("Arial.ttf", "Helvetica.ttc") if key == "reg" else ("Arial Bold.ttf", "Arial.ttf", "Helvetica.ttc")
                p = next((d / n for n in names for d in (Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"), Path("/Library/Fonts"))
                          if (d / n).exists()), None)
            self.paths[key] = p

    def get(self, key: str, size: float):
        k = (key, int(round(size)))
        if k not in self.cache:
            p = self.paths[key]
            self.cache[k] = ImageFont.truetype(str(p), k[1], layout_engine=LAYOUT) if p else ImageFont.load_default(k[1])
        return self.cache[k]


LABEL = {"style": "plain", "scale": 1.0}   # look of the map labels: plain, strong (thick outline) or badges (dark badge); size


def text_sprite(rows, K: float, align: str = "left", halo: bool = True, label: bool = False) -> Sprite:
    """rows: list of (text, font, RGBA color, row height in pixels). label=True marks a label on the map: it gets the chosen
    style and size."""
    style, px_, py_ = "plain", 0, 0
    if label:
        style, sc = LABEL["style"], LABEL["scale"]
        if sc != 1.0:
            rows = [(t, f.font_variant(size=max(6, int(round(f.size * sc)))), c, lh * sc) for t, f, c, lh in rows]
        if style != "plain":                         # gray second rows (elevation, time) turn light; background names keep their colors
            rows = [(t, f, (236, 239, 241, 255) if tuple(c) == MUTE else c, lh) for t, f, c, lh in rows]
        if style == "badges":
            px_, py_ = int(round(9 * K)), int(round(5 * K))
    widths = [f.getlength(t) for t, f, _, _ in rows]
    tw_ = int(math.ceil(max(widths))) + 2
    w, h = tw_ + 2 * px_, int(math.ceil(sum(r[3] for r in rows))) + 2 * py_
    pad = int(round(16 * K)) if halo else 2
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d, y = ImageDraw.Draw(img), float(pad + py_)
    if style == "badges":
        d.rounded_rectangle([pad, pad, pad + w - 1, pad + h - 1], radius=int(round(7 * K)), fill=(10, 13, 15, 180))
    stroke = max(1, int(round(1.7 * K))) if style == "strong" else 0
    for (t, f, c, lh), tw in zip(rows, widths):
        x = pad + px_ + (0 if align == "left" else (tw_ - tw) if align == "right" else (tw_ - tw) / 2)
        asc, desc = f.getmetrics()
        d.text((x, y + (lh - asc - desc) / 2), t, font=f, fill=c, stroke_width=stroke, stroke_fill=(8, 10, 12, 255))
        y += lh
    if halo:                                          # dark glow so the text is readable on the map
        a = img.getchannel("A")
        k1, k2 = (2.6, 2.2) if style == "strong" else (0.8, 0.0) if style == "badges" else (1.7, 1.5)
        sh = np.clip(np.asarray(a.filter(ImageFilter.GaussianBlur(5 * K)), np.float32) * k1
                     + np.asarray(a.filter(ImageFilter.GaussianBlur(1.6 * K)), np.float32) * k2, 0, 235).astype(np.uint8)
        z = Image.new("L", img.size, 0)
        img = Image.alpha_composite(Image.merge("RGBA", [z, z, z, Image.fromarray(sh)]), img)
    return Sprite(img, pad, w, h)


class Placer:
    """Finds a spot for a label near its point, away from the route and other labels (design units)."""
    DIRS = [("E", 1, 0, 0), ("W", -1, 0, 4), ("NE", .7, -.7, 6), ("SE", .7, .7, 6), ("NW", -.7, -.7, 9), ("SW", -.7, .7, 9),
            ("S", 0, 1, 14), ("N", 0, -1, 14)]

    def __init__(self, route: np.ndarray):
        self.route, self.rects, self.c = route, [], route.mean(0)

    def gap(self, r):
        dx = np.maximum(np.maximum(r[0] - self.route[:, 0], self.route[:, 0] - r[2]), 0)
        dy = np.maximum(np.maximum(r[1] - self.route[:, 1], self.route[:, 1] - r[3]), 0)
        return float(np.hypot(dx, dy).min())

    def free(self, r, pad=8):
        return all(r[2] + pad <= q[0] or r[0] - pad >= q[2] or r[3] + pad <= q[1] or r[1] - pad >= q[3] for q in self.rects)

    def place(self, ax, ay, w, h, dirs=None, min_gap=13.0, dists=(20, 30, 44, 62, 84, 110, 140), relaxes=(1.0, 0.5, 0.0)):
        out = np.array([ax, ay]) - self.c
        out /= max(np.hypot(*out), 1e-6)
        best = None
        for relax in relaxes:
            for dist in dists:
                for name, ux, uy, pen in self.DIRS:
                    if dirs and name not in dirs:
                        continue
                    x0 = ax + ux * dist if ux > 0 else ax + ux * dist - w if ux < 0 else ax - w / 2
                    y0 = ay + uy * dist if uy > 0 else ay + uy * dist - h if uy < 0 else ay - h / 2
                    r = (x0, y0, x0 + w, y0 + h)
                    if r[0] < LIMITS[0] or r[1] < LIMITS[1] or r[2] > LIMITS[2] or r[3] > LIMITS[3]:
                        continue
                    if not self.free(r) or self.gap(r) < min_gap * relax:
                        continue
                    score = dist + pen + 18 * (1 - (ux * out[0] + uy * out[1]) / max(math.hypot(ux, uy), 1e-6))
                    if best is None or score < best[0]:
                        best = (score, r, "left" if ux > 0 else "right" if ux < 0 else "center")
            if best:
                break
        if best is None:
            return None
        self.rects.append(best[1])
        return best[1], best[2]


def halves(name: str) -> list[str]:
    """The name in two lines broken at a space, as close in length as possible; a single word stays one line."""
    words = name.split()
    if len(words) < 2:
        return [name]
    return list(min(((" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))), key=lambda p: max(len(p[0]), len(p[1]))))


def wrap_name(name: str, maxc: int = 19) -> list[str]:
    """At most two lines, as close in length as possible."""
    if len(name) <= maxc or len(name.split()) < 2:
        return [name]
    cut = lambda t: t if len(t) <= maxc + 4 else t[:maxc + 3].rstrip() + "…"
    return [cut(t) for t in halves(name)]
