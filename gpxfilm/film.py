"""The film timeline (intro, stops, photos, outro) and encoding with ffmpeg."""
from __future__ import annotations

import bisect
import contextlib
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps
from scipy import ndimage as ndi

from .draw import MUTE, WHITE, Sprite, blit, shape_sprite
from .geo import Layout, Track
from .i18n import _, ngettext, ui
from .net import CACHE, http_get, store
from .osm import NE_URL, country_rings, fetch_peaks, fetch_places, find_country, pick_name
from .settings import CODECS, ENCODER_CODEC, Settings, codec_clash
from .task import Failure, check, progress
from .terrain import Look, relief
from .text import Fonts, LabelLook, halves, text_sprite, wrap_name
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
        store(p, http_get(NE_URL))
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


def make_intro(tr: Track, lay: Layout, wide8: np.ndarray, sv: np.ndarray, fonts: "Fonts", cm: SimpleNamespace, look: Look):
    """The camera flight from the view of the whole country to the map of the region along the camera path cm. Returns a
    function frame(u) for u in 0..1."""
    W, H, K, S1, P = lay.W, lay.H, lay.K, lay.S, tr.proj
    S0, c0, c1, ps, feat, parts, name, bc, cam = cm.S0, cm.c0, cm.c1, cm.ps, cm.feat, cm.parts, cm.name, cm.bc, cm.cam
    rings_m = [cm.to_m(r) for f in cm.feats for r in country_rings(f)]

    def level(S, c, w, h, strength, label):
        lv = Layout.view(w, h, S, 960 - S * c[0], 540 - S * c[1])
        img, _dem, _look = relief(P, lv, {"elements": []}, look, contours=lv.mpp < 60, quiet=True)
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


def plan_pause(A: Settings, tr: Track, cm: SimpleNamespace, sc: SimpleNamespace, n_zoom: int):
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
    lab = LabelLook(A.label_style, A.label_size)
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
                       text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in lines], K, align, label=lab), box[0] * K, box[1] * K))

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
                probe = text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in lines], K, label=lab)
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
                         K, "center", label=lab)
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
        probe = text_sprite([(l_, name_font, WHITE, 24 * K) for l_ in lines], K, label=lab)
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


def intro_seconds(A: Settings, pause: tuple, intro_pk: list) -> float:
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


HEVC_CRF = 4                             # added to the quality (CRF on the scale of x264) for the same look from x265
ENCODER_ARGS = {                         # for each encoder of ENCODER_CODEC: its arguments for a quality (CRF on the scale of x264)
    "libx264": lambda crf: ["-preset", "medium", "-crf", str(crf)],
    "h264_videotoolbox": lambda crf: ["-b:v", "45M", "-maxrate", "60M"],
    "libopenh264": lambda crf: ["-b:v", "45M", "-maxrate", "60M"],
    "libx265": lambda crf: ["-preset", "medium", "-crf", str(crf + HEVC_CRF), "-tag:v", "hvc1"],   # hvc1: the tag Apple devices play
    "libsvtav1": lambda crf: ["-preset", "7", "-crf", str(crf + 12)],
}


def known_encoders(ff: str) -> list[str]:
    """Those of ENCODERS that the ffmpeg ff has."""
    encs = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return [name for name in ENCODER_CODEC if re.search(rf"\b{name}\b", encs)]


def codecs_of(have: list[str]) -> list[str]:
    """The codecs of CODECS that a film can be made in with the encoders have."""
    return [c for c in CODECS if any(ENCODER_CODEC[name] == c for name in have)]


def pick_encoder(ff: str, want: str | None, crf: int, codec: str | None = None) -> list[str]:
    """The ffmpeg arguments of the encoder. An encoder asked for by name (want) is used, and decides the codec; a codec
    asked for with it must be its own. Otherwise the first encoder of the codec (H.264 when none is asked for) that this
    ffmpeg has. Failure when it has none: a film is never quietly made in another codec."""
    have = known_encoders(ff)
    clash = codec_clash(codec, want)
    if clash:
        raise Failure(clash)
    if want:
        return ["-c:v", want] + (ENCODER_ARGS[want](crf) if want in have else [])
    codec = codec or next(iter(CODECS))
    for name in have:
        if ENCODER_CODEC[name] == codec:
            if codec == "h264" and name != "libx264":
                log(_("Warning: ffmpeg has no libx264, encoding with {encoder}.").format(encoder=name))
            return ["-c:v", name] + ENCODER_ARGS[name](crf)
    names = ", ".join(name for name, c in ENCODER_CODEC.items() if c == codec)
    other = next((c for c in codecs_of(have) if c != codec), None)
    if other:
        raise Failure(_("This ffmpeg has no {codec} encoder ({encoders}). It has one for {other}: use --codec {option}.")
                      .format(codec=CODECS[codec], encoders=names, other=CODECS[other], option=other))
    raise Failure(_("This ffmpeg has no {codec} encoder ({encoders}).").format(codec=CODECS[codec], encoders=names))


@dataclass
class Segment:
    """A stretch of the film of one kind: hold (the country view), flight (the camera flies), pause (it halts over the
    region), peaks (it has arrived, the peaks come in), dissolve (into the map of the route), route, stop, photos, outro."""
    kind: str
    start: int                           # the number of its first frame
    n: int                               # how many frames it takes
    draw: Callable[[int], np.ndarray]    # its frame number j, counted from 0


class Film:
    """The film as a timeline: a list of segments, and frame(i), the frame with any number, drawn without drawing the ones
    before it. Frames drawn in order reuse what the frames before them left (a still background, an opened photo), so
    drawing the whole film in order costs what it did when that was the only way.

    The named moments (moments: frame number → name) are region_all_in (the first frame of the pause over the region with
    every place and peak fully in), region_peaks (the last such frame, just before they fade), intro_arrived (the camera
    has arrived, before the peaks by the route come in), arrival_all_in (the first frame with all of these in), intro_last
    (the last intro frame before it dissolves into the map) and route_first (the first frame of the route).

    A frame given back may be one the film keeps for the next frames: read it, do not draw on it."""

    def __init__(self, A: Settings, sc: SimpleNamespace, tr: Track, lay: Layout, wide8: np.ndarray | None, sv: np.ndarray,
                 clean: np.ndarray | None, L: dict, acc: tuple, look: Look, cm: SimpleNamespace | None = None, pause: tuple = (None, [])):
        """look is the look of the map as its background was drawn, cm the camera flight of the intro and pause the pause
        over the region planned along it (plan_pause)."""
        self.A, self.sc, self.tr, self.lay, self.clean, self.L, self.acc, self.cm = A, sc, tr, lay, clean, L, acc, cm
        self.fps, self.kept = A.fps, {}
        intro = None
        if wide8 is not None:
            if cm is None:
                log(_("The route takes up too much of its country – skipping the camera flight."))
            else:
                log(_("Preparing the camera flight from the country view…"))
                intro = make_intro(tr, lay, wide8, sv, sc.fonts, cm, look)
        self.intro, events = intro, sc.events
        self.T0 = 0.4 if intro else T_IN
        n_hold, n_zoom, n_x = (int(2.4 * A.fps), max(1, int(A.intro_duration * A.fps)), int(1.0 * A.fps)) if intro else (0, 0, 0)
        n_main = int(round((self.T0 + A.route_duration + T_OUT) * A.fps))
        n_stop, n_ph = max(2, int(1.4 * A.fps)), (int(0.35 * A.fps), int(A.photo_duration * A.fps), int(0.3 * A.fps))
        n_xf = int(0.4 * A.fps)                           # cross-fade between photos from one place
        n_out = (int(0.8 * A.fps), int(A.outro_duration * A.fps)) if A.outro else (0, 0)
        u_reg, reg_pk = pause if intro else (None, [])
        intro_pk = sc.intro_pk if intro else []
        n_reg = peak_show(slots(reg_pk), A.intro_label_duration, True, A.fps) if u_reg is not None else 0   # the pause over the region
        n_pk = peak_show(len(intro_pk), A.intro_label_duration, False, A.fps)                     # the camera stands still, the peaks come in one by one
        self.n_hold, self.n_zoom, self.n_x, self.n_main, self.n_stop, self.n_ph, self.n_xf, self.n_out, self.n_reg, self.n_pk = \
            n_hold, n_zoom, n_x, n_main, n_stop, n_ph, n_xf, n_out, n_reg, n_pk
        self.reg_pk, self.intro_pk = reg_pk, intro_pk
        self.count = n_hold + n_zoom + n_reg + n_pk + n_x + n_main + sum(n_stop if e[2] == "stop" else n_ph[0] + len(e[3]) * n_ph[1] + (len(e[3]) - 1) * n_xf + n_ph[2] for e in events) + sum(n_out)
        self.segments, self.moments = [], {}

        def add(kind: str, n: int, draw) -> None:
            if n > 0:
                self.segments.append(Segment(kind, self.total, n, draw))

        if intro:                                           # country view, camera flight, dissolve into the map of the region
            us = [0.0] * n_hold                             # camera position (0..1) in each frame; None = the pause over the region
            if u_reg is None:
                us += [ease((z + 1) / n_zoom, 0.3) for z in range(n_zoom)]   # a long run-up and long braking, no rush in the middle
                nA = n_zoom
            else:
                nA = max(1, min(n_zoom - 1, int(round(n_zoom * u_reg))))
                us += [u_reg * ease((z + 1) / nA, 0.3) for z in range(nA)] + [None] * n_reg \
                    + [u_reg + (1 - u_reg) * ease((z + 1) / (n_zoom - nA), 0.3) for z in range(n_zoom - nA)]
            self.us, self.before_pause = us, n_hold + nA - 1
            add("hold", n_hold, self._flight)
            add("flight", nA, lambda j: self._flight(n_hold + j))
            if u_reg is not None:
                shown, reg_in = n_reg - 1 - math.ceil(0.35 * A.fps), peaks_in(slots(reg_pk), True, A.fps)
                for name, j in (("region_all_in", reg_in), ("region_peaks", shown)):
                    if 0 <= j < n_reg:
                        self.moments[self.total + j] = name
                add("pause", n_reg, self._pause)
                add("flight", n_zoom - nA, lambda j: self._flight(n_hold + nA + n_reg + j))
            self.moments[len(us) - 1] = "intro_arrived" if n_pk else "intro_last"
            if n_pk:                                        # the camera has stopped: the region's highest peaks come in one by one, highest first
                all_in = peaks_in(len(intro_pk), False, A.fps)
                for name, j in (("arrival_all_in", all_in), ("intro_last", n_pk - 1)):
                    if 0 <= j < n_pk:
                        self.moments[self.total + j] = name
                add("peaks", n_pk, self._peaks)
            add("dissolve", n_x, self._dissolve)
        self.route_start, first, ei = None, None, 0         # the route: its frames in runs between the stops and photos
        for i in range(n_main):
            p = self._p(i)
            while ei < len(events) and p >= events[ei][0]:   # a stop or a photo: drawing the route waits
                if first is not None:
                    add("route", i - first, lambda j, i0=first: self._route(i0 + j))
                    first = None
                ev, s = events[ei], len(self.segments)
                ei += 1
                if ev[2] == "stop":
                    add("stop", n_stop, lambda j, v=ev[3], p=p: self._stop(v, p, j))
                else:
                    g = ev[3]
                    add("photos", n_ph[0] + len(g) * n_ph[1] + (len(g) - 1) * n_xf + n_ph[2], lambda j, s=s, g=g, p=p: self._photos(s, g, p, j))
            if first is None:
                first = i
                if i == 0:
                    self.moments[self.total] = "route_first"
        if first is not None:
            add("route", n_main - first, lambda j, i0=first: self._route(i0 + j))
        add("outro", sum(n_out), self._outro)               # the outro: the map fades, the numbers come in row by row
        self.starts = [s.start for s in self.segments]

    @property
    def total(self) -> int:
        """How many frames the segments take."""
        return self.segments[-1].start + self.segments[-1].n if self.segments else 0

    @property
    def seconds(self) -> float:
        return self.total / self.fps

    def keep(self, name: str, key, make):
        """What make() gives, kept under name until it is asked for with another key."""
        kept = self.kept.get(name)
        if kept is None or kept[0] != key:
            kept = self.kept[name] = (key, make())
        return kept[1]

    def frame(self, i: int) -> np.ndarray:
        """Frame number i of the film, 0 to total - 1."""
        seg = self.segments[bisect.bisect_right(self.starts, i) - 1]
        return seg.draw(i - seg.start)

    def frame_at(self, t: float) -> np.ndarray:
        """The frame shown t seconds into the film."""
        return self.frame(int(clamp(int(t * self.fps + 1e-6), 0, self.total - 1)))

    def _p(self, i: int) -> float:                          # how much of the route is drawn in frame i of the route
        return ease((i / self.fps - self.T0) / self.A.route_duration)

    def _titled(self, fr: np.ndarray) -> np.ndarray:
        sc, K = self.sc, self.lay.K
        blit(fr, sc.title_sp, 96 * K, 96 * K)
        if sc.sub_sp is not None:
            blit(fr, sc.sub_sp, 98 * K, sc.sub_y * K)
        return fr

    def _flight_raw(self, i: int) -> np.ndarray:            # the camera in frame i of the intro, before the fade from black
        def make():
            K, acc, u, t = self.lay.K, self.acc, self.us[i], i / self.fps
            if i < self.n_hold:
                fr = self.keep("held", 0, lambda: self._titled(self.intro(u))).copy()
            else:
                fr = self._titled(self.intro(u))
            qx, qy = self.cm.start(u)
            if i < self.n_hold + self.n_zoom // 4:          # a pulsing ring around the place
                ph = (t % 1.8) / 1.8
                rad, al = (17 + 44 * ph) * K, int(210 * (1 - ph))
                sz = int(2 * rad + 8 * K)
                blit(fr, shape_sprite(sz, sz, lambda d, s: d.ellipse([(sz / 2 - rad) * s, (sz / 2 - rad) * s, (sz / 2 + rad) * s, (sz / 2 + rad) * s],
                                                                     outline=acc + (al,), width=int(3 * K * s))), qx - sz / 2, qy - sz / 2)
            pin = self.keep("pin", 0, lambda: self.sc.ring(acc + (255,)))
            blit(fr, pin, qx - pin.w / 2, qy - pin.h / 2)
            return fr
        return self.keep("flight", i, make)

    def _flight(self, i: int) -> np.ndarray:
        fr, t = self._flight_raw(i), i / self.fps
        return (fr * (t / 0.5)).astype(np.uint8) if t < 0.5 else fr

    def _pause(self, j: int) -> np.ndarray:                 # camera halts: the region's places, then its highest peaks one by one; all fade before it goes on
        K, fps = self.lay.K, self.fps
        fr = self.keep("pause_base", 0, lambda: self._flight_raw(self.before_pause)).copy()
        out_ = clamp((self.n_reg - 1 - j) / fps / 0.35)
        for k_, mk_, tx, ty, sp_, lx, ly in self.reg_pk:
            ap_ = clamp((j / fps - 0.3 - 0.22 * k_) / 0.4)
            ap_ = ap_ * ap_ * (3 - 2 * ap_)
            if ap_ * out_ > 0:
                blit(fr, mk_, tx, ty + (1 - ap_) * 8 * K, ap_ * out_)
                blit(fr, sp_, lx, ly + (1 - ap_) * 8 * K, ap_ * out_)
        return fr

    def _peaks(self, j: int) -> np.ndarray:
        def make():
            K, tri = self.lay.K, self.sc.tri
            fr = self.keep("arrive", 0, lambda: self._flight_raw(len(self.us) - 1)).copy()
            for k_, (_ele, tx, ty, sp_, lx, ly) in enumerate(self.intro_pk):
                a_ = clamp((j / self.fps - 0.2 - 0.22 * k_) / 0.4)
                a_ = a_ * a_ * (3 - 2 * a_)
                if a_ > 0:
                    blit(fr, tri, tx, ty + (1 - a_) * 8 * K, a_)
                    blit(fr, sp_, lx, ly + (1 - a_) * 8 * K, a_)
            return fr
        return self.keep("peaks", j, make)

    def _dissolve(self, j: int) -> np.ndarray:
        last = self.keep("dissolve_from", 0, lambda: self._peaks(self.n_pk - 1) if self.n_pk else self._flight_raw(len(self.us) - 1))
        first = self.keep("first", 0, lambda: self.sc.render(0.0))
        a = (j + 1) / self.n_x
        return (last.astype(np.float32) * (1 - a) + first.astype(np.float32) * a + 0.5).astype(np.uint8)

    def _route(self, i: int) -> np.ndarray:
        t, p = i / self.fps, self._p(i)
        frame = self.keep("route", p, lambda: self.sc.render(p))
        return (frame * (t / 0.5)).astype(np.uint8) if t < 0.5 and not self.intro else frame

    def _stop(self, v: int, p: float, j: int) -> np.ndarray:   # the marker waits, the clock runs through the time of the stop
        tr, n_stop = self.tr, self.n_stop
        sp_, bx, by = self.sc.badges[v]
        u = j / (n_stop - 1)
        fr = self.sc.render(p, float(tr.tin[v] + (tr.tout[v] - tr.tin[v]) * u * u * (3 - 2 * u)))
        blit(fr, sp_, bx, by, clamp(min((j + 1) / (0.2 * self.fps), (n_stop - j) / (0.25 * self.fps))))
        return fr

    def _photo_card(self, ph: dict):                        # the photo in a white border with a shadow, the time and the kilometer below it
        W, H, K, tr, sc_ = self.lay.W, self.lay.H, self.lay.K, self.tr, self.sc
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
        txt = (sc_.clock(ph["ts"] if ph["ts"] is not None else sc_.time_at(ph["d"])) + "     " if tr.has_t else "") + sc_.f_km(float(np.interp(ph["d"], tr.cd, tr.rd)))
        cap = text_sprite([(txt, sc_.fonts.get("reg", 24 * K), WHITE, 30 * K)], K, "center")
        return Sprite(card), W / 2 - card.width / 2, y_, cap, y_ + card.height - m + 12 * K

    def _photos(self, s: int, group: list, p: float, j: int) -> np.ndarray:   # photos over the dimmed map; those from one place cross-fade into each other
        W, (n_in, n_show, n_fade), n_xf = self.lay.W, self.n_ph, self.n_xf

        def backdrop():
            bg = self.sc.render(p)
            return bg, (bg * 0.36).astype(np.uint8), {}

        bg, dark, cards = self.keep("photos", s, backdrop)

        def card(k: int):                                   # each photo is opened when it first shows
            if k not in cards:
                cards[k] = self._photo_card(group[k])
            return cards[k]

        def draw(fr: np.ndarray, c_, a: float) -> np.ndarray:
            blit(fr, c_[0], c_[1], c_[2], a)
            blit(fr, c_[3], W / 2 - c_[3].w / 2, c_[4], a)
            return fr

        if j < n_in:
            a = (j + 1) / (n_in + 1)
            return draw((bg * (1 - 0.64 * a)).astype(np.uint8), card(0), a)
        j -= n_in
        body = len(group) * n_show + (len(group) - 1) * n_xf
        if j < body:
            k, r = divmod(j, n_show + n_xf)
            if r < n_show:
                return self.keep("photo_shown", (s, k), lambda: draw(dark.copy(), card(k), 1.0))
            a = (r - n_show + 1) / (n_xf + 1)
            return draw(draw(dark.copy(), card(k), 1 - a), card(k + 1), a)
        a = 1 - (j - body + 1) / (n_fade + 1)
        return draw((bg * (1 - 0.64 * a)).astype(np.uint8), card(len(group) - 1), a)

    def _outro_parts(self) -> SimpleNamespace:
        A, sc, tr, L, K, acc = self.A, self.sc, self.tr, self.L, self.lay.K, self.acc
        p = self._p(self.n_main - 1)
        frame = self.keep("route", p, lambda: sc.render(p))   # the last frame of the route
        bk = self.clean.astype(np.float32) * 0.46         # backdrop of the outro: the dimmed map without labels, with just the route
        glow = np.clip(ndi.gaussian_filter(sc.o_red, 7 * K) * 0.9, 0, 0.6)[..., None]
        reg = bk[sc.ry0:sc.ry1, sc.rx0:sc.rx1] * (1 - glow) + sc.o_col * glow
        bk[sc.ry0:sc.ry1, sc.rx0:sc.rx1] = reg * (1 - sc.o_red[..., None]) + sc.o_col * sc.o_red[..., None]
        back = np.clip(bk + 0.5, 0, 255).astype(np.uint8)
        for sp_, pos in sc.static_marks:
            blit(back, sp_, pos[0] - sp_.w / 2, pos[1] - sp_.h / 2)
        blit(back, sc.title_sp, 96 * K, 96 * K)
        if sc.sub_sp is not None:
            blit(back, sc.sub_sp, 98 * K, sc.sub_y * K)
        blit(back, sc.credit_sp, 1856 * K - sc.credit_sp.w, 1046 * K)
        rows = [(sc.f_km(tr.rdist), L["dist"]), (sc.f_m(tr.cup[-1]), L["up"])]
        if tr.has_t:
            rows.append((sc.f_hm(tr.total), L["time"]))
            moving = tr.total - float(np.sum(tr.tout - tr.tin))
            if tr.total - moving > 120:
                rows.append((sc.f_hm(moving), L["moving"]))
        rows.append((sc.f_m(float(np.max(tr.ce))), L["top"]))
        fb, fc = sc.fonts.get("disp", 62 * K), sc.fonts.get("reg", 22 * K)
        vals = [text_sprite([(v, fb, WHITE, 66 * K)], K, halo=False) for v, _ in rows]
        caps = [text_sprite([(c_, fc, MUTE, 30 * K)], K, halo=False) for _, c_ in rows]
        y0_ = (sc.sub_y + (34 if sc.sub_sp is not None else 0) + 70) * K
        pitch = min(78 * K, (1004 * K - y0_) / len(rows))
        cap_x = 96 * K + max(v.w for v in vals) + 30 * K
        cap_dy = (66 * K - sum(fb.getmetrics())) / 2 + fb.getmetrics()[0] - (30 * K - sum(fc.getmetrics())) / 2 - fc.getmetrics()[0]   # a shared baseline
        rule = shape_sprite(58 * K, 6 * K, lambda d, s: d.rectangle([0, 0, 58 * K * s, 5 * K * s], fill=acc + (255,)))
        return SimpleNamespace(frame=frame, back=back, rows=rows, vals=vals, caps=caps, y0=y0_, pitch=pitch, cap_x=cap_x, cap_dy=cap_dy, rule=rule)

    def _outro(self, j: int) -> np.ndarray:
        o, K, fps, n_out = self.keep("outro", 0, self._outro_parts), self.lay.K, self.fps, self.n_out
        n_all = sum(n_out)
        t_, left = j / fps, (n_all - 1 - j) / fps
        a = clamp((j + 1) / max(n_out[0], 1))
        settled = a >= 1 and t_ > 0.45 + 0.16 * len(o.rows) + 0.45   # every row is in: the image no longer changes

        def make():
            fr = o.back.copy() if a >= 1 else (o.frame.astype(np.float32) * (1 - a) + o.back.astype(np.float32) * a + 0.5).astype(np.uint8)
            for i_, (v, c_) in enumerate(zip(o.vals, o.caps)):
                ar = clamp((t_ - 0.45 - 0.16 * i_) / 0.4)
                ar = ar * ar * (3 - 2 * ar)
                yy = o.y0 + i_ * o.pitch + (1 - ar) * 10 * K
                if i_ == 0:
                    blit(fr, o.rule, 98 * K, o.y0 - 26 * K, ar)
                blit(fr, v, 96 * K, yy, ar)
                blit(fr, c_, o.cap_x, yy + o.cap_dy, ar)
            return fr

        fr = self.keep("outro_settled", 0, make) if settled else make()
        return (fr * (left / 0.7)).astype(np.uint8) if left < 0.7 else fr   # a gentle fade to black at the end


def frames_folder() -> Path | None:
    """The directory of GPXFILM_FRAMES: when set, the frames of the named moments are saved there and no film is encoded."""
    return Path(os.environ["GPXFILM_FRAMES"]) if os.environ.get("GPXFILM_FRAMES") else None


def unfinished(out: Path) -> Path:
    """The name the film out has while it is being encoded: film.mp4 is written as film.part.mp4. The ending stays, since
    ffmpeg picks the kind of file by it."""
    return out.with_name(f"{out.stem}.part{out.suffix}")


def ffmpeg_command(A: Settings, out: Path, lay: Layout) -> list[str]:
    """The ffmpeg command that encodes raw frames from its standard input into the unfinished film of out (see unfinished).
    Failure when there is no ffmpeg."""
    ff = shutil.which("ffmpeg") or "ffmpeg"
    if not shutil.which("ffmpeg"):
        raise Failure(_("ffmpeg was not found (Fedora: sudo dnf install ffmpeg, macOS: brew install ffmpeg)."))
    return [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{lay.W}x{lay.H}", "-r", str(A.fps), "-i", "-",
            "-vf", "scale=in_range=full:out_range=tv:out_color_matrix=bt709,format=yuv420p", *pick_encoder(ff, A.encoder, A.crf, A.codec),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-r", str(A.fps), "-movflags", "+faststart", str(unfinished(out))]


def encode(film: Film, out: Path, cmd: list[str] | None) -> None:
    """Draw every frame of the film in order and encode it with ffmpeg (cmd, from ffmpeg_command).
    With GPXFILM_FRAMES set to a directory (cmd is not needed then, nor is ffmpeg), the frames of the named moments are
    saved there as PNG instead and no film is encoded. moments.json beside them gives the number of each of these frames
    and the number of all frames of the film.
    ffmpeg writes the film under its unfinished name, and only a film encoded to the end takes the name out, in place of
    a film that was there. Stopped or failed, the export ends ffmpeg and removes the unfinished film; one left by an
    export whose process was ended is removed when the next export starts."""
    A, W, H, N = film.A, film.lay.W, film.lay.H, film.count
    keep, part = frames_folder(), unfinished(out)
    if keep is None:
        if A.music:                                  # background music: looped, fading in, and fading out at the end
            dur, i_ = N / A.fps, cmd.index("-vf")
            cmd = cmd[:i_] + ["-stream_loop", "-1", "-i", str(A.music)] + cmd[i_:-1] + [
                "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k",
                "-af", f"afade=t=in:d=1.5,afade=t=out:st={max(dur - 3, 0):.2f}:d=3", "-t", f"{dur:.3f}"] + cmd[-1:]
        log(ngettext("Encoding {n} frame {size} at {fps} fps → {file}", "Encoding {n} frames {size} at {fps} fps → {file}", N)
            .format(n=N, size=f"{W}x{H}", fps=A.fps, file=out))
        part.unlink(missing_ok=True)
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    else:
        log(_("Saving the selected frames of {n} in {folder}").format(n=N, folder=keep))
        keep.mkdir(parents=True, exist_ok=True)
    t_start, done, marks = time.time(), 0, {}
    try:
        for i in range(film.total):
            check()
            fr, moment = film.frame(i), film.moments.get(i)
            if keep is None:
                enc.stdin.write(fr.tobytes())
            elif moment:
                Image.fromarray(fr).save(keep / f"{moment}.png")
                marks[moment] = done
            progress("frames", done, N)
            done += 1
        if keep is None:
            enc.stdin.close()
            progress("encoding")
            while enc.poll() is None:                 # ffmpeg is finishing the file: a stop does not wait for it
                check()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    enc.wait(0.1)
            if enc.returncode != 0:
                raise Failure(_("ffmpeg ended with an error."))
            os.replace(part, out)
    except BrokenPipeError:
        raise Failure(_("ffmpeg stopped encoding – see the message above.")) from None
    finally:
        if keep is None:                              # after a finished film there is nothing left to do here
            if enc.poll() is None:
                enc.kill()
            with contextlib.suppress(OSError):        # frames it will not read any more
                enc.stdin.close()
            enc.wait()
            part.unlink(missing_ok=True)
    if keep is not None:
        (keep / "moments.json").write_text(json.dumps({"frames": done, "moments": marks}), encoding="utf-8")
        log(_("Done: frames in {folder} ({seconds} s)").format(folder=keep, seconds=f"{time.time() - t_start:.0f}"))
        return
    log(_("Done: {file} ({size} MB, {seconds} s)").format(file=out, size=f"{out.stat().st_size / 1e6:.1f}",
                                                         seconds=f"{time.time() - t_start:.0f}"))
