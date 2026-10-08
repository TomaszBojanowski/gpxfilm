"""The film timeline (intro, stops, photos, outro) and encoding with ffmpeg."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
from scipy import ndimage as ndi

from .draw import MUTE, WHITE, Sprite, blit, shape_sprite
from .geo import Layout, Track
from .i18n import _, ngettext, ui
from .net import CACHE, http_get
from .osm import NE_URL, country_rings, fetch_peaks, fetch_places, find_country, pick_name
from .terrain import relief
from .text import Fonts, halves, text_sprite, wrap_name
from .util import clamp, log, to_u8

try:                                     # HEIC photos from a phone, if the pillow-heif package is installed
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass
T_IN, T_OUT = 1.0, 3.0                   # seconds of stillness at the start and at the end


def intro_camera(tr: Track, lay: Layout, lang: str) -> SimpleNamespace | None:
    """The camera flight of the intro without drawing anything: the country, the scales at both ends and the path of the
    camera. None when the route takes up too much of its country for a flight."""
    S1, P = lay.S, tr.proj
    c1 = np.array([float(v) for v in lay.meters(960, 540)])
    ps = np.array([tr.cx[0], tr.cy[0]])
    p = CACHE / "ne_50m_admin_0_countries.geojson"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(http_get(NE_URL))
    feats = json.loads(p.read_text(encoding="utf-8"))["features"]
    to_m = lambda r: np.c_[P.fwd(np.clip(r[:, 1], -84, 84), r[:, 0])]
    feat, ring = find_country(feats, *(float(v) for v in P.inv(ps[0], ps[1])))
    parts, name, said, bc = [], None, None, None
    if feat is not None:                              # parts of the country near the main one (no overseas territories)
        main_ = to_m(ring)
        bc, diag = (main_.min(0) + main_.max(0)) / 2, float(np.hypot(*np.ptp(main_, axis=0)))
        parts = [m for m in map(to_m, country_rings(feat)) if np.hypot(*((m.min(0) + m.max(0)) / 2 - bc)) < 1.2 * diag]
        lo, hi = np.min([m.min(0) for m in parts], 0), np.max([m.max(0) for m in parts], 0)
        S0 = min(1040 / (hi[0] - lo[0]), 900 / (hi[1] - lo[1]))
        c0 = (lo + hi) / 2 - np.array([340 / S0, 0])          # the country in the right part of the frame, beside the title
        pr_ = feat["properties"]
        name = pr_.get("NAME_PL" if lang == "pl" else "NAME_EN") or pr_.get("NAME")
        said = pr_.get("NAME_PL" if ui().language == "pl" else "NAME_EN") or pr_.get("NAME")   # in the language of the messages
    else:
        S0 = S1 / 256
        c0 = ps - np.array([340 / S0, 0])
    if S1 / S0 < 4:
        return None
    d0 = (c1 - c0) * S0                               # where the target is on the screen in the country view, relative to the frame center

    def cam(u):                                       # the scale grows exponentially, the target eases to the frame center over the whole flight
        S = S0 * (S1 / S0) ** u
        return S, c1 - d0 * (1 - u * u * (3 - 2 * u)) / S

    def spot(u: float, x: float, y: float):          # where a point of the map (in meters) is on the screen, in pixels
        S, c = cam(u)
        return (960 + (x - c[0]) * S) * lay.K, (540 + (y - c[1]) * S) * lay.K

    return SimpleNamespace(S0=S0, S1=S1, c0=c0, c1=c1, ps=ps, K=lay.K, cam=cam, spot=spot, start=lambda u: spot(u, ps[0], ps[1]),
                           feats=feats, feat=feat, parts=parts, name=name, said=said, bc=bc, to_m=to_m)


def make_intro(tr: Track, lay: Layout, wide8: np.ndarray, sv: np.ndarray, fonts: "Fonts", cm: SimpleNamespace):
    """The camera flight from the view of the whole country to the map of the region along the camera path cm. Returns a
    function frame(u) for u in 0..1."""
    W, H, K, S1, P = lay.W, lay.H, lay.K, lay.S, tr.proj
    S0, c0, c1, ps, feat, parts, name, bc, cam = cm.S0, cm.c0, cm.c1, cm.ps, cm.feat, cm.parts, cm.name, cm.bc, cm.cam
    rings_m = [cm.to_m(r) for f in cm.feats for r in country_rings(f)]

    def level(S, c, w, h, strength, label):
        lv = Layout.view(w, h, S, 960 - S * c[0], 540 - S * c[1])
        img, _dem = relief(P, lv, {"elements": []}, contours=lv.mpp < 60, quiet=True)
        if strength > 0:
            ss, kq = 2, lv.K
            pix = lambda m: [(float(a) * kq * ss, float(b) * kq * ss) for a, b in zip(*lv.des(m[:, 0], m[:, 1]))]
            mk = Image.new("L", (w * ss, h * ss), 0 if parts else 255)
            bl = Image.new("L", (w * ss, h * ss), 0)
            md, bd = ImageDraw.Draw(mk), ImageDraw.Draw(bl)
            (xa, xb), (ya, yb) = lv.meters([0, 1920], [0, 1080])
            for m in rings_m:                         # borders of all countries in the frame
                if m[:, 0].max() < xa or m[:, 0].min() > xb or m[:, 1].max() < ya or m[:, 1].min() > yb:
                    continue
                bd.line(pix(m) + pix(m[:1]), fill=105, width=max(1, int(round(kq * ss))))
            for m in parts:                           # the highlighted country
                md.polygon(pix(m), fill=255)
                bd.line(pix(m) + pix(m[:1]), fill=255, width=max(2, int(round(2.4 * kq * ss))), joint="curve")
            inside = np.asarray(mk.resize((w, h), Image.BOX), np.float32)[..., None] / 255
            img *= 1 - strength * 0.5 * (1 - inside)
            ba = np.asarray(bl.resize((w, h), Image.BOX), np.float32)[..., None] / 255 * 0.85 * strength
            img = img * (1 - ba) + ba
        im8 = to_u8(img)
        if label and name:                            # the country name, away from the place marker
            lx, ly = feat["properties"].get("LABEL_X"), feat["properties"].get("LABEL_Y")
            qx, qy = (float(v) for v in (lv.des(*P.fwd(ly, lx)) if lx is not None else lv.des(*bc)))
            sx_, sy_ = (float(v) for v in lv.des(ps[0], ps[1]))
            if math.hypot(qx - sx_, qy - sy_) < 150:
                qy = sy_ + (170 if sy_ < 540 else -170)
            sp = text_sprite([(name, fonts.get("disp", 58 * K), (255, 255, 255, 235), 62 * K)], K, "center")
            blit(im8, sp, clamp(qx * K - sp.w / 2, 700 * K, W - sp.w - 64 * K), qy * K - sp.h / 2)
        return Image.fromarray(im8)

    srcs = [(S1, c1, K, Image.fromarray(wide8))]      # sources, the most detailed first: (scale, center, density, image)
    nlev = max(0, math.ceil(math.log2(S1 / S0)) - 1)
    for k in range(1, nlev + 1):
        s = S1 / 2 ** k
        w_, h_ = (W, H) if k <= 2 else (max(W // 2, 960), max(H // 2, 540))
        log("  " + _("level {level}/{total}").format(level=k, total=nlev + 1))
        srcs.append((s, c1, w_ / 1920.0, level(s, c1, w_, h_, clamp(1 - math.log2(s / S0) / 4), False)))
    log("  " + (_("level {level}/{total}: country view ({country})") if cm.said else _("level {level}/{total}: country view"))
        .format(level=nlev + 1, total=nlev + 1, country=cm.said))
    srcs.append((S0, c0, K, level(S0, c0, W, H, 1.0, True)))
    svi = Image.fromarray(to_u8(np.repeat(sv[..., None], 3, 2)))

    def cover(src, S, c):                             # how many times larger the source is than the part needed
        s, cc, f, im = src
        return min(im.width / (2 * f * s) / (abs(c[0] - cc[0]) + 960 / S), im.height / (2 * f * s) / (abs(c[1] - cc[1]) + 540 / S))

    def sample(src, S, c):
        s, cc, f, im = src
        a = s * f / (S * K)
        return im.transform((W, H), Image.AFFINE, (a, 0, im.width / 2 + ((c[0] - cc[0]) - 960 / S) * s * f,
                                                   0, a, im.height / 2 + ((c[1] - cc[1]) - 540 / S) * s * f), resample=Image.BILINEAR)

    def frame(u: float) -> np.ndarray:
        S, c = cam(u)
        i = next((j for j, s_ in enumerate(srcs) if cover(s_, S, c) >= 0.999), len(srcs) - 1)
        im = sample(srcs[i], S, c)
        if i + 1 < len(srcs):                         # the new, more detailed source comes in smoothly
            wf = clamp(math.log2(max(cover(srcs[i], S, c), 1.0)) / 0.35)
            wf = wf * wf * (3 - 2 * wf)
            if wf < 1:
                im = Image.blend(sample(srcs[i + 1], S, c), im, wf)
        return np.array(ImageChops.multiply(im, svi))

    return frame


PAUSE_FIT = (60, 70, 1860, 1000)         # the circle of peaks lies inside this part of the frame (1920 × 1080 units) at the pause


def pause_at(cm: SimpleNamespace, radius: float) -> float | None:
    """Where on the flight (0..1) the camera stops over the region: the closest view in which the whole circle of the given
    radius around the start fits the frame, with the start where the camera has it at that moment. None when that view is
    too close to the country view or to the map of the route to be worth a stop."""
    us = np.linspace(0.0, 1.0, 2001)
    x0, y0, x1, y1 = PAUSE_FIT
    fits = []
    for u in us:
        r = radius * cm.S0 * (cm.S1 / cm.S0) ** u
        sx, sy = (v / cm.K for v in cm.start(u))
        fits.append(sx - r >= x0 and sx + r <= x1 and sy - r >= y0 and sy + r <= y1)
    fits = np.flatnonzero(fits)
    if not len(fits):
        return None
    k = int(fits[-1])                                 # the closest view: the start may leave the fit and come back on the way
    S = cm.S0 * (cm.S1 / cm.S0) ** us[k]
    return float(us[k]) if cm.S0 * 1.2 < S < cm.S1 * 0.8 else None


PLACE_GAP = 14                           # free room around a place of the intro and its name (design units)


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def around(qx: float, qy: float, w: float, h: float, d: float) -> list:
    """Spots for a w x h label at distance d from a dot at (qx, qy), as (left, top, align): on the right, on the left,
    above, below, then on the four slants."""
    e = d * 0.7
    return [(qx + d, qy - h / 2, "left"), (qx - d - w, qy - h / 2, "right"), (qx - w / 2, qy - d - h, "center"),
            (qx - w / 2, qy + d, "center"), (qx + e, qy - e - h, "left"), (qx + e, qy + e, "left"),
            (qx - e - w, qy - e - h, "right"), (qx - e - w, qy + e, "right")]


def plan_pause(A, tr: Track, cm: SimpleNamespace, sc: SimpleNamespace, n_zoom: int):
    """The pause over the region in the intro: where the camera stops, and what comes in there: the main places and the
    highest named peaks within the radius around the start, with their labels. Each item is (slot, mark, mark x, mark y,
    label, label x, label y) in pixels; the places share the first slot and come in together, the peaks follow one slot
    each. (None, []) without the pause.

    The layout goes in this order: the most important place (its room is kept for it, unless its dot is outside the frame
    or under the title), the peaks from the highest (a label above its mark, below, on the right or on the left, whichever
    is free first; the highest peak shows in any case, unless its mark is outside the frame or under the title), then the
    other places where there is room."""
    if cm is None or (A.intro_peaks <= 0 and A.intro_places <= 0) or A.no_osm or n_zoom < 2:
        return None, []
    radius = A.intro_radius * 1000
    u_reg = pause_at(cm, radius)
    if u_reg is None:
        return None, []
    K, fonts = cm.K, sc.fonts
    sx, sy = (v / K for v in cm.start(u_reg))
    title_w = max(sc.title_sp.w, sc.sub_sp.w if sc.sub_sp is not None else 0) / K
    title = (0, 0, 96 + title_w + 50, sc.sub_y + 80)
    taken = [title, (sx - 30, sy - 30, sx + 30, sy + 30)]
    hit = lambda r, pad=0: any(r[0] - pad < o[2] and r[2] + pad > o[0] and r[1] - pad < o[3] and r[3] + pad > o[1] for o in taken)
    in_frame = lambda b: b[0] > 26 and b[2] < 1894 and b[1] > 20 and b[3] < 1060
    name_font = fonts.get("semi", 20 * K)
    skip = [s_.lower() for s_ in A.skip]

    def named(found):                                 # the name in the film's language, unless --skip drops it; then --rename
        for *rest, tags in found:
            name = pick_name(tags, sc.prefs)
            if name and not any(s_ in name.lower() for s_ in skip):
                yield (*rest, sc.rename.get(name, name))

    cand_pk = list(named(fetch_peaks(tr, radius))) if A.intro_peaks > 0 else []
    cand_pl = [(*(v / K for v in cm.spot(u_reg, x, y)), nm) for x, y, nm in named(fetch_places(tr, radius))] if A.intro_places > 0 else []
    dot = shape_sprite(14 * K, 14 * K, lambda d, s: d.ellipse([1.5 * K * s, 1.5 * K * s, 12.5 * K * s, 12.5 * K * s], fill=WHITE,
                                                             outline=(14, 17, 19, 255), width=max(1, int(2 * K * s))))
    places, peaks = [], []

    def add_place(qx, qy, lines, box, align):
        taken.extend([(qx - 7, qy - 7, qx + 7, qy + 7), box])
        places.append((dot, qx * K - dot.w / 2, qy * K - dot.h / 2,
                       text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in lines], K, align, label=True), box[0] * K, box[1] * K))

    # 1. the most important place: the best of several spots around its dot, the long name maybe on two lines
    if cand_pl and A.intro_places > 0:
        qx, qy, nm = cand_pl[0]
        mark = (qx - 7, qy - 7, qx + 7, qy + 7)
        if in_frame(mark) and not _overlap(mark, title):
            marks = [(px_ - 16, py_ - 6, px_ + 16, py_ + 26) for px_, py_ in
                     ((v / K for v in cm.spot(u_reg, x, y)) for _e, x, y, _n in cand_pk[:A.intro_peaks])]
            best = None
            variants = ([(nm,)] if len(nm) <= 19 else []) + [tuple(halves(nm))]    # one line like other names, or two, never cut
            for lines in dict.fromkeys(variants):
                probe = text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in lines], K, label=True)
                w, h = probe.w / K, probe.h / K
                for k_d, d in enumerate((12, 26)):
                    for k_s, (lx, ly, align) in enumerate(around(qx, qy, w, h, d)):
                        box = (lx, ly, lx + w, ly + h)
                        pad = (box[0] - PLACE_GAP, box[1] - PLACE_GAP, box[2] + PLACE_GAP, box[3] + PLACE_GAP)
                        cost = (not in_frame(box), sum(_overlap(box, o) for o in taken + marks),      # covering nothing comes first,
                                sum(_overlap(pad, o) for o in taken), sum(1 for m_ in marks if _overlap(pad, m_)),   # then the gaps
                                len(lines), k_d, k_s)
                        if best is None or cost < best[0]:
                            best = (cost, lines, box, align)
            if best and not best[0][0]:
                add_place(qx, qy, best[1], best[2], best[3])
    # 2. the peaks from the highest: a label above the mark, below it, on the right or on the left, the first that is free
    for k, (ele, x, y, nm) in enumerate(cand_pk):
        if len(peaks) >= A.intro_peaks:
            break
        qx, qy = (v / K for v in cm.spot(u_reg, x, y))
        mark = (qx - 16, qy - 6, qx + 16, qy + 26)
        sure = k == 0 and in_frame(mark) and not _overlap(mark, title)    # the highest peak always shows
        if hit(mark) and not sure:
            continue
        sp = text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in wrap_name(nm)] + [(str(int(round(ele))), fonts.get("reg", 20 * K), MUTE, 24 * K)],
                         K, "center", label=True)
        w, h = sp.w / K, sp.h / K
        lx = clamp(qx - w / 2, 26, 1894 - w)          # above or below, the label stays in the frame even under a mark near its edge
        boxes = [(b_[0] - 6, b_[1] - 4, b_[2] + 6, b_[3] + 4) for b_ in
                 ((lx, qy - 8 - h, lx + w, qy - 8), (lx, qy + 26, lx + w, qy + 26 + h),
                  (qx + 20, qy + 10 - h / 2, qx + 20 + w, qy + 10 + h / 2), (qx - 20 - w, qy + 10 - h / 2, qx - 20, qy + 10 + h / 2))]
        fits = lambda b_: b_[0] >= 20 and b_[2] <= 1900 and b_[1] > 20 and b_[3] < 1060
        free = [b_ for b_ in boxes if fits(b_) and not hit(b_)]
        if not free and sure:
            free = sorted((b_ for b_ in boxes if fits(b_)), key=lambda b_: sum(_overlap(b_, o) for o in taken))[:1]
        if free:
            box = free[0]
            taken.extend([mark, box])
            peaks.append((sc.tri, qx * K - sc.tri.w / 2, qy * K - 2 * K, sp, (box[0] + 6) * K, (box[1] + 4) * K))
    # 3. the other places, in the room that is left: the name beside the dot, above it, below it or on a slant
    for qx, qy, nm in cand_pl[1:] if places or not cand_pl else cand_pl:
        if len(places) >= A.intro_places:
            break
        mark = (qx - 7, qy - 7, qx + 7, qy + 7)
        lines = wrap_name(nm)
        probe = text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in lines], K, label=True)
        w, h = probe.w / K, probe.h / K
        if hit(mark, PLACE_GAP):
            continue
        for lx, ly, align in around(qx, qy, w, h, 12):
            box = (lx, ly, lx + w, ly + h)
            if in_frame(box) and not hit(box, PLACE_GAP):
                add_place(qx, qy, lines, box, align)
                break
    if not peaks and not places:
        return None, []
    if places:
        log(ngettext("Place in the intro: the most important one within {radius} km of the start",
                     "Places in the intro: the {n} most important within {radius} km of the start", len(places))
            .format(n=len(places), radius=f"{A.intro_radius:g}"))
    if peaks:
        log(ngettext("Peak in the intro: the highest one within {radius} km of the start",
                     "Peaks in the intro: the {n} highest within {radius} km of the start", len(peaks))
            .format(n=len(peaks), radius=f"{A.intro_radius:g}"))
    first = 1 if places else 0
    return u_reg, [(0, *it) for it in places] + [(first + k, *it) for k, it in enumerate(peaks)]


def slots(items: list) -> int:
    """How many turns the items of a show take: items of one slot come in together."""
    return max((it[0] for it in items), default=-1) + 1


def peaks_in(n: int, region: bool, fps: int) -> int:
    """The first frame of a peak show with n peaks in which every peak is fully in: they come in one by one, 0.22 s apart,
    each in 0.4 s, from 0.3 s into the pause over the region or 0.2 s after the arrival."""
    return math.ceil(((0.3 if region else 0.2) + 0.4 + 0.22 * (n - 1)) * fps - 1e-6)


def peak_show(n: int, hold: float | None, region: bool, fps: int) -> int:
    """Frames of a peak show of the intro with n peaks: they come in, all stay fully visible for hold seconds, and then
    fade (in 0.35 s over the region; after the arrival the intro dissolves into the map). Without hold the length is the
    one from before the option: about 1.3 s of all peaks over the region and 0.8 s after the arrival."""
    if n <= 0:
        return 0
    if hold is None:
        return int(((2.35 if region else 1.4) + 0.22 * (n - 1)) * fps)
    return peaks_in(n, region, fps) + int(hold * fps + 0.5) + (math.ceil(0.35 * fps) if region else 0)


def intro_seconds(A, pause: tuple, intro_pk: list) -> float:
    """How long the peaks of the intro hold the camera: the pause over the region and the peaks after its arrival."""
    u_reg, reg_pk = pause
    if A.intro_label_duration is None:                       # as before the option, so these reports stay the same
        return ((2.35 + 0.22 * (slots(reg_pk) - 1)) if u_reg is not None else 0.0) + ((1.4 + 0.22 * (len(intro_pk) - 1)) if intro_pk else 0.0)
    n_reg = peak_show(slots(reg_pk), A.intro_label_duration, True, A.fps) if u_reg is not None else 0
    return (n_reg + peak_show(len(intro_pk), A.intro_label_duration, False, A.fps)) / A.fps


def read_photo(path: Path, tz, shift: float):
    """The time the photo was taken (with its time zone) and its GPS position, if the file stores them."""
    try:
        with Image.open(path) as im:
            ex = im.getexif()
    except Exception:  # noqa: BLE001 – a file we cannot open is simply skipped
        return None, None
    ifd = ex.get_ifd(0x8769)
    raw, when, gps = ifd.get(0x9003) or ifd.get(0x9004) or ex.get(0x0132), None, None
    if raw:
        try:
            when = dt.datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
            off = str(ifd.get(0x9011) or "").strip()
            if re.fullmatch(r"[+-]\d\d:\d\d", off):       # the camera stored its offset from UTC
                when = when.replace(tzinfo=dt.timezone((1 if off[0] == "+" else -1) * dt.timedelta(hours=int(off[1:3]), minutes=int(off[4:6]))))
            else:
                when = when.replace(tzinfo=tz) if tz else when.astimezone()
            when += dt.timedelta(seconds=shift)
        except ValueError:
            when = None
    g = ex.get_ifd(0x8825)
    if g and 2 in g and 4 in g:
        try:
            dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            gps = (dms(g[2]) * (-1 if g.get(1) == "S" else 1), dms(g[4]) * (-1 if g.get(3) == "W" else 1))
        except Exception:  # noqa: BLE001
            gps = None
    return when, gps


def ease(u: float, a: float = 0.08) -> float:
    if u <= 0:
        return 0.0
    if u >= 1:
        return 1.0
    vm = 1 / (1 - a)
    if u < a:
        return vm * u * u / (2 * a)
    if u > 1 - a:
        return 1 - vm * (1 - u) ** 2 / (2 * a)
    return vm * (u - a / 2)


def pick_encoder(ff: str, want: str | None, crf: int):
    encs = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    table = [("libx264", ["-preset", "medium", "-crf", str(crf)]),
             ("h264_videotoolbox", ["-b:v", "45M", "-maxrate", "60M"]),
             ("libopenh264", ["-b:v", "45M", "-maxrate", "60M"]),
             ("libx265", ["-preset", "medium", "-crf", str(crf + 4), "-tag:v", "hvc1"]),
             ("libsvtav1", ["-preset", "7", "-crf", str(crf + 12)])]
    for name, opts in table:
        if (want in (None, name)) and re.search(rf"\b{name}\b", encs):
            if name != "libx264":
                log(_("Warning: ffmpeg has no libx264, encoding with {encoder}.").format(encoder=name))
            return ["-c:v", name] + opts
    if want:
        return ["-c:v", want]
    sys.exit(_("ffmpeg has none of the encoders: libx264, h264_videotoolbox, libopenh264, libx265, libsvtav1."))


def encode(A, sc: SimpleNamespace, tr: Track, lay: Layout, wide8: np.ndarray | None, sv: np.ndarray, clean: np.ndarray | None, L: dict,
           acc: tuple, cm: SimpleNamespace | None = None, pause: tuple = (None, [])) -> None:
    """Render every frame of the film (intro, route with stops and photos, outro) and encode it with ffmpeg.
    With GPXFILM_FRAMES set to a directory, the frames of the named moments are saved there as PNG instead and no film is
    encoded (ffmpeg is not needed): region_all_in (the first frame of the pause over the region with every place and peak fully in),
    region_peaks (the last such frame, just before they fade), intro_arrived (the camera has arrived, before the peaks by
    the route come in), arrival_all_in (the first frame with all of these in), intro_last (the last intro frame before it
    dissolves into the map) and route_first (the first frame of the route). moments.json beside them gives the number of
    each of these frames and the number of all frames of the film.
    cm is the camera flight of the intro and pause the pause over the region planned along it (plan_pause)."""
    W, H, K = lay.W, lay.H, lay.K
    keep = Path(os.environ["GPXFILM_FRAMES"]) if os.environ.get("GPXFILM_FRAMES") else None
    render, events, badges, clock, time_at, f_km, f_m, f_hm, fonts, title_sp, sub_sp = \
        sc.render, sc.events, sc.badges, sc.clock, sc.time_at, sc.f_km, sc.f_m, sc.f_hm, sc.fonts, sc.title_sp, sc.sub_sp
    sub_y, credit_sp, static_marks, ring, o_red, o_col, rx0, ry0, rx1, ry1 = \
        sc.sub_y, sc.credit_sp, sc.static_marks, sc.ring, sc.o_red, sc.o_col, sc.rx0, sc.ry0, sc.rx1, sc.ry1
    n_xf = int(0.4 * A.fps)                           # cross-fade between photos from one place
    ff = shutil.which("ffmpeg") or "ffmpeg"
    if keep is None and not shutil.which("ffmpeg"):
        sys.exit(_("ffmpeg was not found (Fedora: sudo dnf install ffmpeg, macOS: brew install ffmpeg)."))
    out = A.output or Path(A.gpx.stem + ".mp4")
    cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(A.fps), "-i", "-",
           "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p", *(pick_encoder(ff, A.encoder, A.crf) if keep is None else []),
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
           "-r", str(A.fps), "-movflags", "+faststart", str(out)]
    intro = None
    if wide8 is not None:
        if cm is None:
            log(_("The route takes up too much of its country – skipping the camera flight."))
        else:
            log(_("Preparing the camera flight from the country view…"))
            intro = make_intro(tr, lay, wide8, sv, fonts, cm)
    T0 = 0.4 if intro else T_IN
    n_hold, n_zoom, n_x = (int(2.4 * A.fps), max(1, int(A.intro_duration * A.fps)), int(1.0 * A.fps)) if intro else (0, 0, 0)
    n_main = int(round((T0 + A.route_duration + T_OUT) * A.fps))
    n_stop, n_ph = max(2, int(1.4 * A.fps)), (int(0.35 * A.fps), int(A.photo_duration * A.fps), int(0.3 * A.fps))
    n_out = (int(0.8 * A.fps), int(A.outro_duration * A.fps)) if A.outro else (0, 0)
    u_reg, reg_pk = pause if intro else (None, [])
    intro_pk, tri = (sc.intro_pk if intro else []), sc.tri
    n_reg = peak_show(slots(reg_pk), A.intro_label_duration, True, A.fps) if u_reg is not None else 0   # the pause over the region
    n_pk = peak_show(len(intro_pk), A.intro_label_duration, False, A.fps)                     # the camera stands still, the peaks come in one by one
    N = n_hold + n_zoom + n_reg + n_pk + n_x + n_main + sum(n_stop if e[2] == "stop" else n_ph[0] + len(e[3]) * n_ph[1] + (len(e[3]) - 1) * n_xf + n_ph[2] for e in events) + sum(n_out)
    if A.music:                                      # background music: looped, fading in, and fading out at the end
        dur, i_ = N / A.fps, cmd.index("-vf")
        cmd[i_:i_] = ["-stream_loop", "-1", "-i", str(A.music)]
        cmd[-1:-1] = ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k",
                      "-af", f"afade=t=in:d=1.5,afade=t=out:st={max(dur - 3, 0):.2f}:d=3", "-t", f"{dur:.3f}"]
    if keep is None:
        log(ngettext("Encoding {n} frame {size} at {fps} fps → {file}", "Encoding {n} frames {size} at {fps} fps → {file}", N)
            .format(n=N, size=f"{W}x{H}", fps=A.fps, file=out))
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    else:
        log(_("Saving the selected frames of {n} in {folder}").format(n=N, folder=keep))
        keep.mkdir(parents=True, exist_ok=True)
    t_start, done, marks = time.time(), 0, {}

    def put(fr: np.ndarray, moment: str | None = None) -> None:
        nonlocal done
        if keep is None:
            enc.stdin.write(fr.tobytes())
        elif moment:
            Image.fromarray(fr).save(keep / f"{moment}.png")
            marks[moment] = done
        if done % max(1, N // 25) == 0:
            log(f"  {100 * done // N:3d}%  ({time.time() - t_start:.0f} s)")
        done += 1

    def show_stop(v: int, p: float) -> None:          # the marker waits, the clock runs through the time of the stop
        sp_, bx, by = badges[v]
        for j in range(n_stop):
            u = j / (n_stop - 1)
            fr = render(p, float(tr.tin[v] + (tr.tout[v] - tr.tin[v]) * u * u * (3 - 2 * u)))
            blit(fr, sp_, bx, by, clamp(min((j + 1) / (0.2 * A.fps), (n_stop - j) / (0.25 * A.fps))))
            put(fr)

    def photo_card(ph: dict):                         # the photo in a white border with a shadow, the time and the kilometer below it
        try:
            im = ImageOps.exif_transpose(Image.open(ph["path"])).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log("  " + _("could not open {file}: {error}").format(file=ph["path"].name, error=e))
            im = Image.new("RGB", (16, 9), (40, 40, 40))
        sc = min(0.66 * W / im.width, 0.74 * H / im.height)
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
        b, m = int(7 * K), int(40 * K)
        card = Image.new("RGBA", (im.width + 2 * (b + m), im.height + 2 * (b + m)), (0, 0, 0, 0))
        sh = Image.new("L", card.size, 0)
        ImageDraw.Draw(sh).rectangle([m, m + int(8 * K), card.width - m, card.height - m + int(8 * K)], fill=170)
        card.putalpha(sh.filter(ImageFilter.GaussianBlur(14 * K)))
        card.paste((255, 255, 255, 255), (m, m, card.width - m, card.height - m))
        card.paste(im, (m + b, m + b))
        y_ = H * 0.47 - card.height / 2
        txt = (clock(ph["ts"] if ph["ts"] is not None else time_at(ph["d"])) + "     " if tr.has_t else "") + f_km(float(np.interp(ph["d"], tr.cd, tr.rd)))
        cap = text_sprite([(txt, fonts.get("reg", 24 * K), WHITE, 30 * K)], K, "center")
        return Sprite(card), W / 2 - card.width / 2, y_, cap, y_ + card.height - m + 12 * K

    def show_photos(group: list, p: float) -> None:   # photos over the dimmed map; those from one place cross-fade into each other
        bg = render(p)
        dark = (bg * 0.36).astype(np.uint8)

        def draw(fr: np.ndarray, c_, a: float) -> np.ndarray:
            blit(fr, c_[0], c_[1], c_[2], a)
            blit(fr, c_[3], W / 2 - c_[3].w / 2, c_[4], a)
            return fr

        cur = photo_card(group[0])
        for j in range(n_ph[0]):
            a = (j + 1) / (n_ph[0] + 1)
            put(draw((bg * (1 - 0.64 * a)).astype(np.uint8), cur, a))
        for k_ in range(len(group)):
            full = draw(dark.copy(), cur, 1.0)
            for j in range(n_ph[1]):
                put(full)
            if k_ + 1 < len(group):
                nxt = photo_card(group[k_ + 1])
                for j in range(n_xf):
                    a = (j + 1) / (n_xf + 1)
                    put(draw(draw(dark.copy(), cur, 1 - a), nxt, a))
                cur = nxt
        for j in range(n_ph[2]):
            a = 1 - (j + 1) / (n_ph[2] + 1)
            put(draw((bg * (1 - 0.64 * a)).astype(np.uint8), cur, a))

    def outro_backdrop() -> np.ndarray:               # backdrop of the outro: the dimmed map without labels, with just the route
        bk = clean.astype(np.float32) * 0.46
        glow = np.clip(ndi.gaussian_filter(o_red, 7 * K) * 0.9, 0, 0.6)[..., None]
        reg = bk[ry0:ry1, rx0:rx1] * (1 - glow) + o_col * glow
        bk[ry0:ry1, rx0:rx1] = reg * (1 - o_red[..., None]) + o_col * o_red[..., None]
        fr = np.clip(bk + 0.5, 0, 255).astype(np.uint8)
        for sp_, pos in static_marks:
            blit(fr, sp_, pos[0] - sp_.w / 2, pos[1] - sp_.h / 2)
        blit(fr, title_sp, 96 * K, 96 * K)
        if sub_sp is not None:
            blit(fr, sub_sp, 98 * K, sub_y * K)
        blit(fr, credit_sp, 1856 * K - credit_sp.w, 1046 * K)
        return fr

    try:
        if intro:                                           # country view, camera flight, dissolve into the map of the region
            zoom, start_xy = intro, cm.start
            first, pin, held, fr = render(0.0), ring(acc + (255,)), None, None
            us = [0.0] * n_hold                             # camera position (0..1) in each frame; None = the pause over the region
            if u_reg is None:
                us += [ease((z + 1) / n_zoom, 0.3) for z in range(n_zoom)]   # a long run-up and long braking, no rush in the middle
            else:
                nA = max(1, min(n_zoom - 1, int(round(n_zoom * u_reg))))
                us += [u_reg * ease((z + 1) / nA, 0.3) for z in range(nA)] + [None] * n_reg \
                    + [u_reg + (1 - u_reg) * ease((z + 1) / (n_zoom - nA), 0.3) for z in range(n_zoom - nA)]
            reg_base, j_reg, shown, reg_in = None, 0, n_reg - 1 - math.ceil(0.35 * A.fps), peaks_in(slots(reg_pk), True, A.fps)
            for i, u in enumerate(us):
                t = i / A.fps
                if u is None:                               # camera halts: the region's places, then its highest peaks one by one; all fade before it goes on
                    reg_base = fr if reg_base is None else reg_base
                    fr, out_ = reg_base.copy(), clamp((n_reg - 1 - j_reg) / A.fps / 0.35)
                    for k_, mk_, tx, ty, sp_, lx, ly in reg_pk:
                        ap_ = clamp((j_reg / A.fps - 0.3 - 0.22 * k_) / 0.4)
                        ap_ = ap_ * ap_ * (3 - 2 * ap_)
                        if ap_ * out_ > 0:
                            blit(fr, mk_, tx, ty + (1 - ap_) * 8 * K, ap_ * out_)
                            blit(fr, sp_, lx, ly + (1 - ap_) * 8 * K, ap_ * out_)
                    put(fr, "region_peaks" if j_reg == shown else "region_all_in" if j_reg == reg_in else None)
                    j_reg += 1
                    continue
                if i < n_hold and held is not None:
                    fr = held.copy()
                else:
                    fr = zoom(u)
                    blit(fr, title_sp, 96 * K, 96 * K)
                    if sub_sp is not None:
                        blit(fr, sub_sp, 98 * K, sub_y * K)
                    if i < n_hold:
                        held = fr.copy()
                qx, qy = start_xy(u)
                if i < n_hold + n_zoom // 4:                # a pulsing ring around the place
                    ph = (t % 1.8) / 1.8
                    rad, al = (17 + 44 * ph) * K, int(210 * (1 - ph))
                    sz = int(2 * rad + 8 * K)
                    blit(fr, shape_sprite(sz, sz, lambda d, s: d.ellipse([(sz / 2 - rad) * s, (sz / 2 - rad) * s, (sz / 2 + rad) * s, (sz / 2 + rad) * s],
                                                                         outline=acc + (al,), width=int(3 * K * s))), qx - sz / 2, qy - sz / 2)
                blit(fr, pin, qx - pin.w / 2, qy - pin.h / 2)
                put((fr * (t / 0.5)).astype(np.uint8) if t < 0.5 else fr, None if i < len(us) - 1 else "intro_arrived" if n_pk else "intro_last")
            if n_pk:                                        # the camera has stopped: the region's highest peaks come in one by one, highest first
                arrive, all_in = fr.copy(), peaks_in(len(intro_pk), False, A.fps)
                for j in range(n_pk):
                    fr = arrive.copy()
                    for k_, (_ele, tx, ty, sp_, lx, ly) in enumerate(intro_pk):
                        a_ = clamp((j / A.fps - 0.2 - 0.22 * k_) / 0.4)
                        a_ = a_ * a_ * (3 - 2 * a_)
                        if a_ > 0:
                            blit(fr, tri, tx, ty + (1 - a_) * 8 * K, a_)
                            blit(fr, sp_, lx, ly + (1 - a_) * 8 * K, a_)
                    put(fr, "intro_last" if j == n_pk - 1 else "arrival_all_in" if j == all_in else None)
            for j in range(n_x):
                a = (j + 1) / n_x
                put((fr.astype(np.float32) * (1 - a) + first.astype(np.float32) * a + 0.5).astype(np.uint8))
        frame, last_p, i, ei = None, -1.0, 0, 0
        while i < n_main:
            t = i / A.fps
            p = ease((t - T0) / A.route_duration)
            if ei < len(events) and p >= events[ei][0]:     # a stop or a photo: drawing the route waits
                ev = events[ei]
                ei += 1
                (show_stop if ev[2] == "stop" else show_photos)(ev[3], p)
                frame = None
                continue
            settle = t - (T0 + A.route_duration)                      # after reaching the finish the image no longer changes
            if frame is None or p != last_p or 0 <= settle < 0.1:
                frame, last_p = render(p), p
            put((frame * (t / 0.5)).astype(np.uint8) if t < 0.5 and not intro else frame, "route_first" if i == 0 else None)
            i += 1
        if A.outro:                                         # the outro: the map fades, the numbers come in row by row
            back = outro_backdrop()
            rows = [(f_km(tr.rdist), L["dist"]), (f_m(tr.cup[-1]), L["up"])]
            if tr.has_t:
                rows.append((f_hm(tr.total), L["time"]))
                moving = tr.total - float(np.sum(tr.tout - tr.tin))
                if tr.total - moving > 120:
                    rows.append((f_hm(moving), L["moving"]))
            rows.append((f_m(float(np.max(tr.ce))), L["top"]))
            fb, fc = fonts.get("disp", 62 * K), fonts.get("reg", 22 * K)
            vals = [text_sprite([(v, fb, WHITE, 66 * K)], K, halo=False) for v, _ in rows]
            caps = [text_sprite([(c_, fc, MUTE, 30 * K)], K, halo=False) for _, c_ in rows]
            y0_ = (sub_y + (34 if sub_sp is not None else 0) + 70) * K
            pitch = min(78 * K, (1004 * K - y0_) / len(rows))
            cap_x = 96 * K + max(v.w for v in vals) + 30 * K
            cap_dy = (66 * K - sum(fb.getmetrics())) / 2 + fb.getmetrics()[0] - (30 * K - sum(fc.getmetrics())) / 2 - fc.getmetrics()[0]   # a shared baseline
            rule = shape_sprite(58 * K, 6 * K, lambda d, s: d.rectangle([0, 0, 58 * K * s, 5 * K * s], fill=acc + (255,)))
            n_all, held = sum(n_out), None
            for j in range(n_all):
                t_, left = j / A.fps, (n_all - 1 - j) / A.fps
                a = clamp((j + 1) / max(n_out[0], 1))
                settled = a >= 1 and t_ > 0.45 + 0.16 * len(rows) + 0.45
                if settled and held is not None:
                    fr = held
                else:
                    fr = back.copy() if a >= 1 else (frame.astype(np.float32) * (1 - a) + back.astype(np.float32) * a + 0.5).astype(np.uint8)
                    for i_, (v, c_) in enumerate(zip(vals, caps)):
                        ar = clamp((t_ - 0.45 - 0.16 * i_) / 0.4)
                        ar = ar * ar * (3 - 2 * ar)
                        yy = y0_ + i_ * pitch + (1 - ar) * 10 * K
                        if i_ == 0:
                            blit(fr, rule, 98 * K, y0_ - 26 * K, ar)
                        blit(fr, v, 96 * K, yy, ar)
                        blit(fr, c_, cap_x, yy + cap_dy, ar)
                    if settled:
                        held = fr
                put((fr * (left / 0.7)).astype(np.uint8) if left < 0.7 else fr)   # a gentle fade to black at the end
        if keep is None:
            enc.stdin.close()
    except BrokenPipeError:
        sys.exit(_("ffmpeg stopped encoding – see the message above."))
    if keep is not None:
        (keep / "moments.json").write_text(json.dumps({"frames": done, "moments": marks}), encoding="utf-8")
        log(_("Done: frames in {folder} ({seconds} s)").format(folder=keep, seconds=f"{time.time() - t_start:.0f}"))
        return
    if enc.wait() != 0:
        sys.exit(_("ffmpeg ended with an error."))
    log(_("Done: {file} ({size} MB, {seconds} s)").format(file=out, size=f"{out.stat().st_size / 1e6:.1f}",
                                                         seconds=f"{time.time() - t_start:.0f}"))
