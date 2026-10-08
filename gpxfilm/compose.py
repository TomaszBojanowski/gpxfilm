"""Composition of a frame from layers: map, title, statistics, elevation profile, labels and the route."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from . import i18n
from .draw import MUTE, WHITE, Sprite, blit, shape_sprite, stroke_mask
from .film import read_photo
from .geo import BOX, LIMITS, Layout, Track, at_vertices, match_watch, smooth_climb
from .i18n import N_, _
from .net import CACHE, http_get
from .osm import LANG_BY_COUNTRY, NE_URL, country_rings, end_places, find_country, find_pois, osm_geoms, pick_name
from .terrain import LOOK
from .text import NB, Fonts, Placer, text_sprite, wrap_name
from .util import clamp, km1, log


def scrim_vignette(W: int, H: int) -> np.ndarray:
    """Brightness multiplier: darkening under the title on the left, and a vignette."""
    u = (np.arange(W, dtype=np.float32) + 0.5) / W
    t = np.clip((u - 0.10) / 0.37, 0, 1)
    scr = 1 - 0.86 * (1 - t * t * (3 - 2 * t))
    xx, yy = (np.arange(W, dtype=np.float32) - W / 2) / (W / 2), (np.arange(H, dtype=np.float32) - H / 2) / (H / 2)
    vig = 1 - 0.24 * (np.sqrt(xx[None, :] ** 2 + yy[:, None] ** 2) / math.sqrt(2)) ** 2.4
    return (scr[None, :] * vig).astype(np.float32)


def mini_map(tr: Track, K: float, acc: tuple):
    """Small outline of the country with a marker of the place (design units: 128 x 128)."""
    p = CACHE / "ne_50m_admin_0_countries.geojson"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(http_get(NE_URL))
    feats, P = json.loads(p.read_text(encoding="utf-8"))["features"], tr.proj
    feat, ring = find_country(feats, *(float(v) for v in P.inv(tr.cx[0], tr.cy[0])))
    if feat is None:
        return None
    to_m = lambda r: np.c_[P.fwd(np.clip(r[:, 1], -84, 84), r[:, 0])]
    main_ = to_m(ring)
    bc, diag = (main_.min(0) + main_.max(0)) / 2, float(np.hypot(*np.ptp(main_, axis=0)))
    parts = [m for m in map(to_m, country_rings(feat)) if np.hypot(*((m.min(0) + m.max(0)) / 2 - bc)) < 1.2 * diag]
    lo, hi = np.min([m.min(0) for m in parts], 0), np.max([m.max(0) for m in parts], 0)
    sc = 112 / float(max(hi - lo))
    off = 64 - sc * (lo + hi) / 2
    def fn(d, s):
        for m in parts:
            pts = [(float(a) * K * s, float(b) * K * s) for a, b in m * sc + off]
            d.polygon(pts, fill=(255, 255, 255, 62))
            d.line(pts + pts[:1], fill=(255, 255, 255, 225), width=max(1, int(1.4 * K * s)), joint="curve")
        qx, qy = (np.array([tr.cx[0], tr.cy[0]]) * sc + off) * K * s
        r_ = 6.5 * K * s
        d.ellipse([qx - r_, qy - r_, qx + r_, qy + r_], fill=acc + (255,), outline=(255, 255, 255, 255), width=max(1, int(2 * K * s)))
    return shape_sprite(128 * K, 128 * K, fn)


def km_marks(tr: Track) -> list[tuple[int, float]]:
    """Kilometer markers: (number of the kilometer on the film, distance along the drawn line), at most 15 of them."""
    step = next((v for v in (1, 2, 5, 10, 20, 25, 50, 100, 1000) if tr.rdist / 1000 / v <= 15), 1000 * math.ceil(tr.rdist / 15e6))
    return [(kmv, float(np.interp(kmv * 1000, tr.rd, tr.cd))) for kmv in range(step, int(tr.rdist / 1000) + 1, step)]


def frame_elevation(tr: Track, lay: Layout, D: np.ndarray) -> None:
    """Elevation and ascent from the terrain model of the frame under each point, as a stand-in for the profile."""
    px, py = lay.des(*tr.proj.fwd(tr.lat, tr.lon))
    ii, jj = np.clip((py * lay.K).astype(int), 0, lay.H - 1), np.clip((px * lay.K).astype(int), 0, lay.W - 1)
    es, cup = smooth_climb(D[ii, jj].astype(float), tr.t, 5.0)
    tr.ce, tr.cup = at_vertices(es, tr.ci), at_vertices(cup, tr.ci)


def live_speed(tr: Track, gd: np.ndarray) -> np.ndarray:
    """Moving speed in km/h (stops left out), smoothed, at the distances gd along the drawn line. It is measured on the
    distance shown on the film, so it agrees with the distance counter and follows --distance."""
    mt = tr.tin - np.concatenate([[0], np.cumsum(tr.tout - tr.tin)[:-1]])
    gmt, ia, ib = np.interp(gd, tr.cd, mt), np.maximum(np.arange(len(gd)) - 8, 0), np.minimum(np.arange(len(gd)) + 8, len(gd) - 1)
    grd = np.interp(gd, tr.cd, tr.rd)
    gsp = ndi.median_filter((grd[ib] - grd[ia]) / np.maximum(gmt[ib] - gmt[ia], 1.0), 9, mode="nearest") * 3.6
    return np.minimum(gsp, 3 * float(np.median(gsp)))                 # GPS position jumps must not pass for a sprint


NAMES = N_("Names: © OpenStreetMap")


def frame_credit(language: str, default: str, names: bool = False) -> str:
    """The credit of the map source in the frame, in the language of the film; default for the terrain drawing alone.
    names: the film shows names from OpenStreetMap (places, peaks), which the credit then names when its source does not,
    as with aerial photos or a background of one's own."""
    text = LOOK.get("credit")
    if text and names and "OpenStreetMap" not in text:
        text += "; " + NAMES
    return i18n.credit(text, i18n.translator(language)) if text else default


def compose_scene(A, g: dict, tr: Track, lay: Layout, base: np.ndarray, D: np.ndarray, osm: dict, country, region, tz, L: dict,
                  acc: tuple) -> SimpleNamespace:
    """Draw the static layers onto base and prepare render(p), the frame with the route drawn up to fraction p."""
    W, H, K = lay.W, lay.H, lay.K
    dxs, dys = lay.des(tr.cx, tr.cy)                  # route in design units
    if not tr.has_e:                                  # no elevation and no profile from the terrain model: the frame's model
        frame_elevation(tr, lay, D)
    match_watch(tr, None, A.ascent)
    fonts = Fonts()
    if A.font:
        fonts.paths["disp"] = A.font
    clock = lambda s: (tr.t0 + dt.timedelta(seconds=float(s))).astimezone(tz).strftime("%H:%M")
    num = lambda v, nd=0: f"{v:.{nd}f}".replace(".", L["dec"]).replace("-", "\u2212")
    f_km = lambda m: num(km1(m), 1) + NB + "km"
    f_m = lambda m: f"{int(round(m))}{NB}m"
    f_hm = lambda s: f"{int(round(s / 60)) // 60}{NB}h{NB}{int(round(s / 60)) % 60:02d}{NB}min"
    hr_text = lambda v: str(int(round(v))) if math.isfinite(v) else "–"      # no reading in a longer gap

    # dense polyline in output pixels
    px, py = dxs * K, dys * K
    cl = np.concatenate([[0], np.cumsum(np.hypot(np.diff(px), np.diff(py)))])
    sl = np.arange(0, cl[-1], 2.5 * K / 2)
    rp = np.c_[np.interp(sl, cl, px), np.interp(sl, cl, py)]
    rp = np.vstack([rp, [px[-1], py[-1]]])
    rcum = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(rp, axis=0).T))])
    rfr = rcum / rcum[-1]
    vfr = cl / cl[-1]                                 # route fraction at the vertices of the cleaned track
    at = lambda f: (float(np.interp(f, rfr, rp[:, 0])), float(np.interp(f, rfr, rp[:, 1])))
    placer = Placer(rp[::4] / K)

    # ── static elements: title, statistics, profile, north arrow, scale bar ──
    gname = re.sub(r"\s+(Piesze wędrówki|Wędrówka|Chód|Spacer|Bieg|Bieganie|Bieg terenowy|Kolarstwo|Kolarstwo górskie|Jazda rowerem|Hiking|Walking|Running|Trail Running|Cycling|Mountain Biking)$",
                   "", g["name"] or "", flags=re.I)             # Garmin appends the activity type to the name
    title = (A.title or gname or A.gpx.stem).replace("\\n", "\n")
    given = [p.strip() for p in title.split("\n") if p.strip()]
    lines = None
    if len(given) > 1:                                # line breaks given: no wrapping, at most a smaller font
        for size in (112, 96, 84, 72, 60, 50):
            f = fonts.get("disp", size * K)
            if max(f.getlength(l) for l in given) <= 640 * K and len(given) * size * 0.93 <= 345:
                lines = given
                break
    if lines is None:
        for size in (112, 96, 84, 72, 60, 50):
            f = fonts.get("disp", size * K)
            lines, cur = [], ""
            for wd in " ".join(given).split():
                if cur and f.getlength(cur + " " + wd) > 600 * K:
                    lines.append(cur); cur = wd
                else:
                    cur = (cur + " " + wd).strip()
            lines.append(cur)
            if len(lines) <= (3 if size > 72 else 4) and max(f.getlength(l) for l in lines) <= 640 * K:
                break
    lh = size * 0.93
    title_sp = text_sprite([(l, f, WHITE, lh * K) for l in lines], K)
    blit(base, title_sp, 96 * K, 96 * K)
    y = 96 + lh * len(lines) + 18
    sub = A.subtitle
    if sub is None and tr.has_t:
        d0 = tr.t0.astimezone(tz)
        sub = (region + ", " if region else "") + f"{d0.day} {L['months'][d0.month - 1]} {d0.year}"
    sub_sp, sub_y = None, y
    if sub:
        sub_sp = text_sprite([(sub, fonts.get("reg", 28 * K), MUTE, 34 * K)], K)
        blit(base, sub_sp, 98 * K, y * K)
        y += 34
    y += 58
    stats = [("dist", lambda d, i: f_km(float(np.interp(d, tr.cd, tr.rd))), f_km(tr.rdist))]
    stats.append(("up", lambda d, i: f_m(i["up"]), f_m(tr.cup[-1])))
    if tr.has_t:
        stats.append(("time", lambda d, i: f_hm(i["t"]), f_hm(tr.total)))
    fnum, sx, cols = fonts.get("disp", 60 * K), 96.0, []
    for key, fn, final in stats:
        cap = text_sprite([(L[key], fonts.get("reg", 20 * K), MUTE, 26 * K)], K)
        wcol = max(fnum.getlength(final), fnum.getlength(final.replace("1", "0")), cap.w) / K
        cols.append((sx, fn, final))
        blit(base, cap, sx * K, (y + 62) * K)
        sx += wcol + 40
    stat_y = y
    fl, live = fonts.get("disp", 38 * K), []          # extra live counters
    if A.counters:
        lx = 96.0
        for key, sample in ([("clock", "00:00")] if tr.has_t else []) + [("ele", "8888" + NB + "m")] + ([("spd", "88,8" + NB + "km/h")] if tr.has_t else []) + ([("hr", "188")] if tr.has_hr else []):
            cap = text_sprite([(L[key], fonts.get("reg", 17 * K), MUTE, 22 * K)], K)
            blit(base, cap, lx * K, (stat_y + 166) * K)
            live.append((lx, key))
            lx += max(fl.getlength(sample), cap.w) / K + 34
    # elevation profile
    PX0, PY0, PW, PH = 96.0, 790.0, 560.0, 140.0
    gd = np.linspace(0, tr.dist, 421)
    ge = np.interp(gd, tr.cd, tr.ce)
    gup = np.interp(gd, tr.cd, tr.cup)
    ghr = np.interp(gd, tr.cd, tr.chr) if tr.has_hr else None
    gsp = live_speed(tr, gd) if tr.has_t else None
    lut = None
    if A.color_by:                                     # route color from the data
        modes = {"slope": (ndi.uniform_filter1d(np.gradient(ge, gd) * 100, 9, mode="nearest"), (-25.0, 25.0), [(42, 157, 143), (244, 211, 94), (229, 32, 46)], L["slope"], "%"),
                 "speed": (gsp, None, [(69, 117, 180), (254, 224, 144), (215, 48, 39)], L["spd"], NB + "km/h"),
                 "heart-rate": (ghr, None, [(76, 175, 80), (255, 193, 7), (229, 32, 46)], L["hr"], ""),
                 "elevation": (ge, None, [(42, 157, 143), (244, 211, 94), (250, 248, 244)], L["ele"], NB + "m")}
        val, rng_, ramp, lname, unit = modes[A.color_by]
        if val is not None and not np.isfinite(val).any():
            val = None
        if val is None:
            log(_("No data to color the route by ({mode}) – keeping one color.").format(mode=A.color_by))
        else:
            pct = np.nanpercentile if np.isnan(val).any() else np.percentile     # heart rate may have long gaps
            lo_, hi_ = rng_ or (float(v) for v in pct(val, [5, 95]))
            rc = np.array(ramp, np.float32)
            tone = lambda t_: np.stack([np.interp(t_, [0, 0.5, 1], rc[:, c_]) for c_ in range(3)], -1)
            lut = tone(np.interp(np.linspace(0, tr.dist, 1024), gd, np.clip((val - lo_) / max(hi_ - lo_, 1e-6), 0, 1))).astype(np.float32)
            lut[np.isnan(lut).any(axis=1)] = (170, 170, 170)   # no reading: gray, not a color of the scale
            bar = Image.fromarray(tone(np.linspace(0, 1, 256))[None].astype(np.uint8)).resize((int(200 * K), int(10 * K)), Image.BILINEAR).convert("RGBA")
            blit(base, Sprite(bar), 1656 * K, 914 * K)
            for txt_, x_, al in ((lname, 1656, "l"), (num(lo_, 0) + unit, 1656, "l"), (num(hi_, 0) + unit, 1856, "r")):
                sp = text_sprite([(txt_, fonts.get("semi" if txt_ == lname else "reg", 16 * K), MUTE, 20 * K)], K)
                blit(base, sp, x_ * K - (sp.w if al == "r" else 0), (890 if txt_ == lname else 927) * K)
    def time_at(d: float) -> float:                   # time of arrival at a place on the route; a stop is a clock jump at one point
        i_ = int(np.clip(np.searchsorted(tr.cd, d, "right") - 1, 0, len(tr.cd) - 2))
        return float(tr.tout[i_] + (tr.tin[i_ + 1] - tr.tout[i_]) * clamp((d - tr.cd[i_]) / max(tr.cd[i_ + 1] - tr.cd[i_], 1e-9)))
    lo, hi = float(ge.min()), float(ge.max())
    padv = max((hi - lo) * 0.12, 8)
    fy = lambda e: PH - (np.asarray(e) - (lo - padv)) / ((hi - lo) + 2 * padv) * PH
    m_ = int(10 * K)
    preg = (int(PX0 * K) - m_, int(PY0 * K) - m_, int((PX0 + PW) * K) + m_, int((PY0 + PH) * K) + m_)
    psz = (preg[2] - preg[0], preg[3] - preg[1])
    ppts = np.c_[gd / tr.dist * PW * K + m_, fy(ge) * K + m_]
    ss = 2 if W > 2600 else 3
    am = Image.new("L", (psz[0] * ss, psz[1] * ss), 0)
    ImageDraw.Draw(am).polygon([(float(a) * ss, float(b) * ss) for a, b in ppts] + [((PW * K + m_) * ss, (PH * K + m_) * ss), (m_ * ss, (PH * K + m_) * ss)], fill=255)
    area = (np.asarray(am.resize(psz, Image.LANCZOS), np.float32) / 255 * 0.14)[..., None]
    dim = (stroke_mask(psz, ppts, 3 * K, ss) * 0.45)[..., None]
    reg = base[preg[1]:preg[3], preg[0]:preg[2]].astype(np.float32)
    reg = reg * (1 - area) + 255 * area
    reg = reg * (1 - dim) + 255 * dim
    base[preg[1]:preg[3], preg[0]:preg[2]] = np.clip(reg + 0.5, 0, 255).astype(np.uint8)
    small = fonts.get("reg", 18 * K)
    blit(base, text_sprite([("0" + NB + "km", small, MUTE, 22 * K)], K), PX0 * K, (PY0 + PH + 10) * K)
    sp = text_sprite([(f_km(tr.rdist), small, MUTE, 22 * K)], K)
    blit(base, sp, (PX0 + PW) * K - sp.w, (PY0 + PH + 10) * K)
    for ix, low in ((int(ge.argmax()), False), (int(ge.argmin()), True)):
        ex = PX0 + gd[ix] / tr.dist * PW
        if low and not 80 < ex - PX0 < PW - 110:      # lowest point near the edge: its label would collide with the axis
            continue
        sp = text_sprite([(f_m(ge[ix]), fonts.get("reg", 20 * K if not low else 18 * K), MUTE, 24 * K)], K)
        blit(base, sp, clamp(ex * K - sp.w / 2, PX0 * K, (PX0 + PW) * K - sp.w), (PY0 + PH + 9) * K if low else (PY0 + float(fy(ge[ix])) - 34) * K)
    brt = stroke_mask(psz, ppts, 4 * K, ss)[..., None]         # bright line revealed as the walk goes on
    PB = np.clip(base[preg[1]:preg[3], preg[0]:preg[2]].astype(np.float32) * (1 - brt) + 255 * brt + 0.5, 0, 255).astype(np.uint8)
    # north arrow and scale bar
    def north(d, s):
        d.line([(16 * K * s, 60 * K * s), (16 * K * s, 18 * K * s)], fill=MUTE, width=int(2.5 * K * s))
        d.polygon([(16 * K * s, 2 * K * s), (24 * K * s, 20 * K * s), (8 * K * s, 20 * K * s)], fill=MUTE)
    blit(base, shape_sprite(32 * K, 62 * K, north), 728 * K, 106 * K)
    sp = text_sprite([("N", fonts.get("semi", 20 * K), MUTE, 24 * K)], K, "center")
    blit(base, sp, 744 * K - sp.w / 2, 170 * K)
    nice = max(v for v in (50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000) if v * lay.S <= 300 or v == 50)
    bw = nice * lay.S * K
    def bar(d, s):
        d.line([(2 * K * s, 2 * K * s), (2 * K * s, 12 * K * s), ((bw + 2 * K) * s, 12 * K * s), ((bw + 2 * K) * s, 2 * K * s)], fill=MUTE, width=int(2.5 * K * s), joint="curve")
    blit(base, shape_sprite(bw + 4 * K, 15 * K, bar), 1856 * K - bw - 2 * K, 976 * K)
    sp = text_sprite([((f"{nice}{NB}m" if nice < 1000 else num(nice / 1000, 0 if nice % 1000 == 0 else 1) + NB + "km"), fonts.get("reg", 20 * K), MUTE, 24 * K)], K)
    blit(base, sp, 1856 * K - sp.w, 950 * K)
    placer.rects.append((1856 - bw / K - 10, 944, 1860, 996))
    credit = frame_credit(A.language, L["credit"], names=bool(osm.get("elements")))
    sp = credit_sp = text_sprite([(credit, fonts.get("reg", 13 * K), (230, 233, 235, 170), 16 * K)], K)
    blit(base, sp, 1856 * K - sp.w, 1046 * K)
    if lut is not None:
        placer.rects.append((1646, 884, 1860, 950))
    if A.logo:
        lg = Image.open(A.logo).convert("RGBA")
        sc = min(64 * K / lg.height, 220 * K / lg.width)
        lg = lg.resize((max(1, int(lg.width * sc)), max(1, int(lg.height * sc))), Image.LANCZOS)
        blit(base, Sprite(lg), 1856 * K - lg.width, 64 * K)
        placer.rects.append((1856 - lg.width / K - 8, 56, 1864, 72 + lg.height / K))

    # ── labels: start and finish, places along the route, peaks ──
    fn26, fn20 = fonts.get("semi", 26 * K), fonts.get("reg", 20 * K)
    prefs = [p for p in A.name_language.split(",") if p] + [A.language]
    if country and not A.name_language and LANG_BY_COUNTRY.get(country[2]) and LANG_BY_COUNTRY[country[2]] not in prefs:
        prefs.append(LANG_BY_COUNTRY[country[2]])        # no name language given: the language of the country the route is in
    rename = dict(v.split("=", 1) for v in A.rename if "=" in v)
    pois = find_pois(osm, g, tr, lay, prefs, rename, set(A.skip))
    ends = [(0, L["loop"] if tr.loop else L["start"])] + ([] if tr.loop else [(len(dxs) - 1, L["finish"])])
    near = end_places(pois, {vi: (float(tr.cx[vi]), float(tr.cy[vi])) for vi, _word in ends})   # a hut, a summit, a pass, a village...
    dot = shape_sprite(26 * K, 26 * K, lambda d, s: d.ellipse([2.5 * K * s, 2.5 * K * s, 23.5 * K * s, 23.5 * K * s], fill=WHITE, outline=(14, 17, 19, 255), width=int(3 * K * s)))
    tri = shape_sprite(24 * K, 24 * K, lambda d, s: d.polygon([(12 * K * s, 2 * K * s), (22 * K * s, 21 * K * s), (2 * K * s, 21 * K * s)], fill=WHITE, outline=(14, 17, 19, 255), width=int(2.2 * K * s)))
    n_intro, n_route = A.intro_peaks if A.intro else 0, max(A.peaks, 0)   # a negative count means none, as before
    mm = None
    if A.minimap:                                    # country outline in a free corner of the map
        try:
            mm = mini_map(tr, K, acc)
        except Exception as e:  # noqa: BLE001
            log(_("Could not prepare the minimap: {error}").format(error=e))
    fixed = list(placer.rects)                       # what the frame holds before the labels

    def lay_out(named: dict, form: str = "wide") -> SimpleNamespace:
        """Every label of the map, with the ends in named (index of the end -> its place) carrying the name of the place.
        The facts of an end go on one line ("wide"), with the times on a line of their own ("narrow"), or as before the
        elevation, the word and the times alone ("bare", only to see what fits without it). Nothing is drawn yet: the
        result holds the placer, the items that appear along the route and what goes on the map."""
        pl = copy.copy(placer)
        pl.rects = list(fixed)
        items, draws = [], []                        # items: (route fraction, image, x, y), each appears once reached

        def label(ax, ay, rows_fn, frac, **kw):
            probe = rows_fn("left")
            res = pl.place(ax, ay, probe.w / K, probe.h / K, **kw)
            if res is None:
                return False
            r, align = res
            sp_ = rows_fn(align)
            items.append((frac, sp_, r[0] * K if align == "left" else r[2] * K - sp_.w if align == "right" else (r[0] + r[2]) / 2 * K - sp_.w / 2, r[1] * K))
            return True

        def two(name_lines, *sublines):
            def mk(align):
                rows = [(l, fn26, WHITE, 30 * K) for l in name_lines]
                rows += [(l, fn20, MUTE, 26 * K) for l in sublines if l]
                return text_sprite(rows, K, align, label=True)
            return mk

        for vi, _word in ends:
            pl.rects.append((dxs[vi] - 15, dys[vi] - 15, dxs[vi] + 15, dys[vi] + 15))
        placed = {}                                   # index of each end whose label is placed: its lines
        for vi, word in ends:                         # the name of the place, then the word, the elevation and the times
            when = (f"{clock(0)}–{clock(tr.total)}" if tr.loop else clock(0 if vi == 0 else tr.total)) if tr.has_t else ""
            c = named.get(vi)
            head, facts = (wrap_name(c["name"]), [word, f_m(tr.ce[vi])]) if c is not None else ([word], [f_m(tr.ce[vi])])
            rows = [", ".join(facts), when] if form == "narrow" else [", ".join(facts + [when] if when else facts)]
            if form == "bare":
                head, rows = [word], [when]
            if label(dxs[vi], dys[vi], two(head, *rows), -1.0):
                placed[vi] = [*head, *(r_ for r_ in rows if r_)]
        at_ends = list(named.values())                # a place named at an end is not a label of its own as well
        own_end = lambda c: any(c is e or c["name"] == e["name"] for e in at_ends)
        chosen = []
        for c in sorted((c for c in pois if c["off"] <= c["lim"]), key=lambda c: (-c["prio"], c["off"])):
            f_ = vfr[c["i"]]
            own = c["kind"] == "wpt"                  # own labels and points from the GPX file come first
            if (len(chosen) >= A.places and not own) or not (0.01 if own else 0.05) < f_ < (0.99 if own else 0.95) or any(abs(f_ - o["f"]) < (0.02 if own else 0.07) for o in chosen):
                continue
            if any(o["name"] == c["name"] for o in chosen) or own_end(c):
                continue
            c["f"] = float(f_); chosen.append(c)
        chosen.sort(key=lambda c: c["f"])
        for c in chosen:
            pl.rects.append((dxs[c["i"]] - 11, dys[c["i"]] - 11, dxs[c["i"]] + 11, dys[c["i"]] + 11))
        shown = []
        for c in chosen:
            e_ = c["ele"] if c["ele"] else tr.ce[c["i"]]
            s_ = f_m(e_) + (", " + clock(tr.tin[c["i"]]) if tr.has_t else "")
            if label(dxs[c["i"]], dys[c["i"]], two(wrap_name(c["name"]), s_), c["f"], dists=(20, 30, 44, 62), relaxes=(1.0, 0.6)):
                items.append((c["f"], dot, dxs[c["i"]] * K - dot.w / 2, dys[c["i"]] * K - dot.h / 2))
                shown.append(c)                       # with no free space nearby, the point is left out of the film
        chosen = shown
        if A.km_markers:                               # markers of successive kilometers
            for kmv, d_ in km_marks(tr):
                f_ = float(np.interp(d_, tr.cd, vfr))
                qx, qy = at(f_)
                if any(math.hypot(qx / K - dxs[c["i"]], qy / K - dys[c["i"]]) < 20 for c in chosen) or min(math.hypot(qx - px[0], qy - py[0]), math.hypot(qx - px[-1], qy - py[-1])) < 26 * K:
                    continue
                def disc(d, s, t_=str(kmv)):
                    d.ellipse([2 * K * s, 2 * K * s, 22 * K * s, 22 * K * s], fill=WHITE, outline=(14, 17, 19, 255), width=max(1, int(1.6 * K * s)))
                    d.text((12 * K * s, 12 * K * s), t_, font=fonts.get("semi", (11 if len(t_) < 3 else 9) * K * s), fill=(14, 17, 19, 255), anchor="mm")
                sp_ = shape_sprite(24 * K, 24 * K, disc)
                items.append((f_, sp_, qx - sp_.w / 2, qy - sp_.h / 2))
        # peaks off the route, within the map
        cand_pk = []                                  # highest first; the first go on the route map, the rest only on the end of the intro
        for c in sorted((c for c in pois if c["kind"] == "peak" and c["off"] > 150 and c["ele"]), key=lambda c: -c["ele"]):
            qx, qy = (float(v) for v in lay.des(c["x"], c["y"]))
            if not (BOX[0] - 60 < qx < BOX[2] + 40 and BOX[1] - 40 < qy < BOX[3] + 40) or len(cand_pk) >= max(n_route, n_intro):
                continue
            if any(math.hypot(qx - p_[0], qy - p_[1]) < 70 for p_ in cand_pk) or any(o["name"] == c["name"] for o in chosen) or own_end(c):
                continue
            cand_pk.append((qx, qy, c))
        peaks, extra_pk = cand_pk[:n_route], cand_pk[n_route:]
        shown_peaks, intro_pk = [], []                # intro_pk: peaks for the end of the intro, (elevation, name, mark x, y, label, x, y)
        for qx, qy, c in peaks:
            pl.rects.append((qx - 12, qy - 4, qx + 12, qy + 22))

        def peak_label(pl_, qx, qy, c):               # peak label laid out by the given placer; None when it does not fit
            mk = lambda align: text_sprite([(l, fonts.get("semi", 20 * K), WHITE, 24 * K) for l in wrap_name(c["name"])] + [(str(int(round(c["ele"]))), fn20, MUTE, 24 * K)], K, align, label=True)
            probe = mk("center")
            res = (pl_.place(qx, qy + 9, probe.w / K, probe.h / K, ("S", "N"), min_gap=8, dists=(16, 24), relaxes=(1.0,))
                   or pl_.place(qx, qy + 9, probe.w / K, probe.h / K, ("E", "W"), min_gap=8, dists=(16, 24), relaxes=(1.0,)))   # beside the mark when it fits nowhere else
            if not res:
                return None
            r, align = res
            sp_ = mk(align)
            return sp_, r[0] * K if align == "left" else r[2] * K - sp_.w if align == "right" else (r[0] + r[2]) / 2 * K - sp_.w / 2, r[1] * K

        for qx, qy, c in peaks:
            res = peak_label(pl, qx, qy, c)
            if res:                                   # peak mark only together with its label
                shown_peaks.append(c["name"])
                draws.extend([(tri, qx * K - tri.w / 2, qy * K - 2 * K), res])
                intro_pk.append((c["ele"], c["name"], qx * K - tri.w / 2, qy * K - 2 * K, *res))
        if n_intro > 0 and extra_pk:                  # extra peaks show only at the end of the intro; placed on a copy so the route map stays as is
            pl2 = copy.copy(pl)
            pl2.rects = list(pl.rects) + [(qx - 12, qy - 4, qx + 12, qy + 22) for qx, qy, c in extra_pk]
            for qx, qy, c in extra_pk:
                res = peak_label(pl2, qx, qy, c)
                if res:
                    intro_pk.append((c["ele"], c["name"], qx * K - tri.w / 2, qy * K - 2 * K, *res))
        intro_pk = sorted(intro_pk, key=lambda p_: -p_[0])[:n_intro]
        background = []
        if A.background_names:                        # lakes and towns in the background, in smaller type
            bgn, seen = [], {o["name"] for o in chosen} | {e["name"] for e in at_ends}
            for e in osm.get("elements", []):
                tg = e.get("tags", {})
                if tg.get("natural") == "water" and e["type"] != "node" and pick_name(tg, prefs):
                    geo = [q for part in osm_geoms(e) for q in part]
                    if geo:
                        bgn.append((pick_name(tg, prefs), *(float(v) for v in tr.proj.fwd(np.mean([q["lat"] for q in geo]), np.mean([q["lon"] for q in geo]))), (196, 226, 240, 235)))
            bgn += [(c["name"], c["x"], c["y"], (236, 236, 230, 225)) for c in pois if c["kind"] == "place"]
            for nm, x_, y_, col in bgn:
                qx, qy = (float(v) for v in lay.des(x_, y_))
                if nm in seen or len(background) >= 8 or not (LIMITS[0] < qx < LIMITS[2] and LIMITS[1] < qy < LIMITS[3]):
                    continue
                mk = lambda align, nm=nm, col=col: text_sprite([(l, fonts.get("reg", 17 * K), col, 21 * K) for l in wrap_name(nm, 16)], K, align, label=True)
                probe = mk("center")
                res = pl.place(qx, qy, probe.w / K, probe.h / K, ("S", "N", "E", "W"), min_gap=8, dists=(3, 14, 26), relaxes=(1.0,))
                if res:
                    r, align = res
                    sp_ = mk(align)
                    draws.append((sp_, r[0] * K if align == "left" else r[2] * K - sp_.w if align == "right" else (r[0] + r[2]) / 2 * K - sp_.w / 2, r[1] * K))
                    seen.add(nm); background.append(nm)
        if mm is not None:
            spot = next(((x_, y_) for x_, y_ in ((1728, 64), (1728, 780), (770, 64), (770, 830)) if pl.free((x_, y_, x_ + 128, y_ + 128)) and pl.gap((x_, y_, x_ + 128, y_ + 128)) > 14), None)
            if spot:
                draws.append((mm, spot[0] * K, spot[1] * K))
                pl.rects.append((spot[0], spot[1], spot[0] + 128, spot[1] + 128))
        names = dict(places={c["name"] for c in chosen}, peaks=set(shown_peaks), intro={p_[1] for p_ in intro_pk}, background=set(background))
        return SimpleNamespace(placer=pl, items=items, draws=draws, chosen=chosen, shown_peaks=shown_peaks, form=form,
                               intro_pk=[(p_[0], *p_[2:]) for p_ in intro_pk], placed=placed, names=names, background=background)

    def keeps_all(trial: SimpleNamespace, ref: SimpleNamespace, named: dict) -> bool:
        """trial shows every label of ref but the names now at the ends, each kind as such: a place along the route, a
        peak, a peak of the intro; a background name may only become the label of a place along the route."""
        gone, was, now = {c["name"] for c in named.values()}, ref.names, trial.names
        return (set(named) | set(ref.placed) <= set(trial.placed) and all(was[k] - gone <= now[k] for k in ("places", "peaks", "intro"))
                and was["background"] - gone <= now["background"] | now["places"])

    # The elevation at the ends is always there. When it takes the room of a label that fits without it, the times go on
    # a line of their own, on both ends at once; when even that loses a label, the facts stay on one line and every label
    # they push out is left out (a known limit).
    plain = lay_out({})
    if tr.has_t and not keeps_all(plain, bare := lay_out({}, "bare"), {}):
        narrow = lay_out({}, "narrow")
        plain = narrow if keeps_all(narrow, bare, {}) else plain
    # A name at an end shows only when it takes no room from a label of a place or a peak (or from the other end) that fits
    # without it; otherwise that end keeps the elevation and the times alone.
    chosen_layout = plain
    for named in ([near] + ([{vi: c} for vi, c in near.items()] if len(near) > 1 else [])) if near else []:
        trial = lay_out(named, plain.form)
        if keeps_all(trial, plain, named):
            chosen_layout = trial
            break
    labels = chosen_layout
    placer, items, chosen, shown_peaks, intro_pk = labels.placer, labels.items, labels.chosen, labels.shown_peaks, labels.intro_pk
    for sp_, x_, y_ in labels.draws:
        blit(base, sp_, x_, y_)
    for frac, sp_, x_, y_ in [it for it in items if it[0] < 0]:      # the start label shows from the beginning
        blit(base, sp_, x_, y_)
    items = sorted((it for it in items if it[0] >= 0), key=lambda it: it[0])
    # direction arrows beside the route
    for target in (0.10, 0.36, 0.62, 0.87):
        best = None
        for dd in np.linspace(-0.05, 0.05, 11):
            f_ = target + dd
            (ax, ay), (bx, by) = at(f_ - 0.004), at(f_ + 0.004)
            hx, hy = bx - ax, by - ay
            hn = math.hypot(hx, hy) or 1.0
            hx, hy = hx / hn, hy / hn
            qx, qy = (ax + bx) / 2 - hy * 24 * K, (ay + by) / 2 + hx * 24 * K
            r = (qx / K - 11, qy / K - 11, qx / K + 11, qy / K + 11)
            if not placer.free(r, 10) or r[0] < LIMITS[0]:
                continue
            gp_ = placer.gap(r)
            if best is None or gp_ > best[0]:
                best = (gp_, f_, qx, qy, hx, hy, r)
        if best and best[0] > 7:
            f_, qx, qy, hx, hy, r = best[1:]
            c0 = 14 * K
            poly = lambda s: [((c0 + hx * 11 * K) * s, (c0 + hy * 11 * K) * s), ((c0 - hx * 8 * K - hy * 7.5 * K) * s, (c0 - hy * 8 * K + hx * 7.5 * K) * s),
                              ((c0 - hx * 3 * K) * s, (c0 - hy * 3 * K) * s),           # notch at the back: an arrowhead, not a peak triangle
                              ((c0 - hx * 8 * K + hy * 7.5 * K) * s, (c0 - hy * 8 * K - hx * 7.5 * K) * s)]
            asp = shape_sprite(28 * K, 28 * K, lambda d, s, poly=poly: d.polygon(poly(s), fill=MUTE, outline=(14, 17, 19, 255), width=int(1.5 * K * s)))
            items.append((f_, asp, qx - c0, qy - c0))
            placer.rects.append(r)
    # ── stops and photos: events at which drawing the route waits ──
    events, badges = [], {}
    if tr.has_t and A.stop_minutes > 0:
        for v in range(1, len(tr.cx) - 1):
            dur = float(tr.tout[v] - tr.tin[v])
            if dur < A.stop_minutes * 60:
                continue
            sp_ = text_sprite([(f"{L['stop']} {int(round(dur / 60))}{NB}min", fonts.get("semi", 22 * K), WHITE, 28 * K)], K, label=True)
            res = placer.place(dxs[v], dys[v], sp_.w / K, sp_.h / K, dists=(26, 40, 58, 80), min_gap=9)
            badges[v] = (sp_, res[0][0] * K, res[0][1] * K) if res else (sp_, dxs[v] * K - sp_.w / 2, (dys[v] - 62) * K)
            events.append((float(vfr[v]), 0, "stop", v))
    photos, n_files = [], 0
    if A.photos:
        exts = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
        files = sorted(q for q in (A.photos.iterdir() if A.photos.is_dir() else [A.photos]) if q.suffix.lower() in exts)
        for q in files:
            when, gps = read_photo(q, tz, A.photo_offset)
            d_, ts = None, None
            if when is not None and tr.has_t:         # place from the time the photo was taken
                ts = (when - tr.t0).total_seconds()
                if -120 <= ts <= tr.total + 120:
                    ts = clamp(ts, 0, tr.total)
                    i_ = int(np.clip(np.searchsorted(tr.tin, ts, "right") - 1, 0, len(tr.cd) - 2))
                    d_ = tr.cd[i_] if ts <= tr.tout[i_] else tr.cd[i_] + (tr.cd[i_ + 1] - tr.cd[i_]) * clamp((ts - tr.tout[i_]) / max(tr.tin[i_ + 1] - tr.tout[i_], 1e-6))
            if d_ is None and gps:                    # or from the GPS position stored in the photo
                dd = np.hypot(*(np.c_[tr.cx, tr.cy] - [float(v) for v in tr.proj.fwd(*gps)]).T)
                if dd.min() < 300:
                    d_ = tr.cd[int(dd.argmin())]
            if d_ is None:
                log("  " + _("skipping {name}: neither the time nor the position of the photo matches the route").format(name=q.name))
                continue
            photos.append(dict(path=q, d=float(d_), f=float(np.interp(d_, tr.cd, vfr)), ts=ts if when is not None and tr.has_t else None))
        photos.sort(key=lambda q: q["f"])
        if A.photo_limit and len(photos) > A.photo_limit:
            photos = [photos[i] for i in np.round(np.linspace(0, len(photos) - 1, A.photo_limit)).astype(int)]
        n_files = len(files)
        log(_("Photos on the route: {n} of {total}").format(n=len(photos), total=len(files)))
        psq = shape_sprite(20 * K, 20 * K, lambda d, s: d.rounded_rectangle([2 * K * s, 2 * K * s, 18 * K * s, 18 * K * s], radius=3.5 * K * s, fill=WHITE, outline=(14, 17, 19, 255), width=int(2.4 * K * s)))
        grp = []                                      # photos from one place are shown together, one after another
        for ph in photos:
            qx, qy = at(ph["f"])
            items.append((ph["f"], psq, qx - psq.w / 2, qy - psq.h / 2))
            if grp and ph["f"] - grp[-1]["f"] > 0.004:
                events.append((grp[0]["f"], 1, "photos", grp))
                grp = []
            grp.append(ph)
        if grp:
            events.append((grp[0]["f"], 1, "photos", grp))
    events.sort(key=lambda e: (e[0], e[1]))
    items.sort(key=lambda it: it[0])

    # ── route layers: the "traveled" and "ahead" versions and the arrival time field ──
    m_ = int(22 * K)
    rx0, ry0 = max(int(rp[:, 0].min()) - m_, 0), max(int(rp[:, 1].min()) - m_, 0)
    rx1, ry1 = min(int(rp[:, 0].max()) + m_ + 1, W), min(int(rp[:, 1].max()) + m_ + 1, H)
    rsz, loc = (rx1 - rx0, ry1 - ry0), rp - [rx0, ry0]
    reg = base[ry0:ry1, rx0:rx1].astype(np.float32)
    gmask = Image.new("L", (rsz[0] * ss, rsz[1] * ss), 0)
    gdraw, rr = ImageDraw.Draw(gmask), 1.7 * K * ss
    for s_ in np.arange(0, rcum[-1], 9 * K):
        gx_, gy_ = np.interp(s_, rcum, loc[:, 0]) * ss, np.interp(s_, rcum, loc[:, 1]) * ss
        gdraw.ellipse([gx_ - rr, gy_ - rr, gx_ + rr, gy_ + rr], fill=255)
    gh = (np.asarray(gmask.resize(rsz, Image.LANCZOS), np.float32) / 255 * 0.8)[..., None]
    G = np.clip(reg * (1 - gh) + 255 * gh + 0.5, 0, 255).astype(np.uint8)
    cas = stroke_mask(rsz, loc, 12 * K, ss)[..., None]
    red = stroke_mask(rsz, loc, 7 * K, ss)[..., None]
    Rc = reg * (1 - cas) + 255 * cas
    Rr = np.clip(Rc * (1 - red) + np.array(acc, np.float32) * red + 0.5, 0, 255).astype(np.uint8)
    del gh, cas, reg, gmask
    Fi = Image.new("F", rsz, 9.0)
    fd, rr = ImageDraw.Draw(Fi), 8 * K
    for i in range(len(loc) - 2, -1, -1):             # from the end: an earlier pass overwrites a later one
        a_, b_, f_ = (float(loc[i, 0]), float(loc[i, 1])), (float(loc[i + 1, 0]), float(loc[i + 1, 1])), float(rfr[i])
        fd.line([a_, b_], fill=f_, width=int(round(2 * rr)))
        fd.ellipse([a_[0] - rr, a_[1] - rr, a_[0] + rr, a_[1] + rr], fill=f_)
    F = np.asarray(Fi)
    if lut is not None:                               # each line pixel knows its route fraction, and so its color
        Rr = np.clip(Rc * (1 - red) + lut[np.clip(np.minimum(F, 1.0) * 1023, 0, 1023).astype(np.int32)] * red + 0.5, 0, 255).astype(np.uint8)
    o_red = red[..., 0].copy() if A.outro else None   # for the end card: the route line alone and its colors
    o_col = (lut[np.clip(np.minimum(F, 1.0) * 1023, 0, 1023).astype(np.int32)] if lut is not None else np.array(acc, np.float32)) if A.outro else None
    del Rc, red
    # start marker (and finish marker when the route is not a loop)
    ring = lambda fill: shape_sprite(34 * K, 34 * K, lambda d, s: d.ellipse([3 * K * s, 3 * K * s, 31 * K * s, 31 * K * s], fill=fill, outline=WHITE, width=int(4 * K * s)))
    static_marks = [(ring(acc + (255,)), rp[0])] + ([] if tr.loop else [(ring((14, 17, 19, 255)), rp[-1])])

    def marker(frame, cx_, cy_, r_out, r_in):
        sz = int(2 * r_out + 8 * K)
        x0_, y0_ = int(math.floor(cx_ - sz / 2)), int(math.floor(cy_ - sz / 2))
        fx, fy_ = cx_ - x0_, cy_ - y0_
        def fn(d, s):
            d.ellipse([(fx - r_out) * s, (fy_ - r_out) * s, (fx + r_out) * s, (fy_ + r_out) * s], fill=acc + (255,))
            d.ellipse([(fx - r_in) * s, (fy_ - r_in) * s, (fx + r_in) * s, (fy_ + r_in) * s], fill=WHITE)
        blit(frame, shape_sprite(sz, sz, fn), x0_, y0_)

    pdot = shape_sprite(15 * K, 15 * K, lambda d, s: d.ellipse([1.5 * K * s, 1.5 * K * s, 13.5 * K * s, 13.5 * K * s], fill=acc + (255,), outline=(14, 17, 19, 255), width=int(2 * K * s)))
    pdots = [(c["f"], PX0 * K + c["f"] * PW * K - pdot.w / 2, (PY0 + float(fy(np.interp(c["f"] * tr.dist, gd, ge)))) * K - pdot.h / 2) for c in chosen]
    cnt_cache: dict[str, Sprite] = {}

    def render(p: float, t_now: float | None = None) -> np.ndarray:
        frame = base.copy()
        frame[ry0:ry1, rx0:rx1] = np.where((F <= p)[..., None], Rr, G)
        for sp_, pos in static_marks:
            blit(frame, sp_, pos[0] - sp_.w / 2, pos[1] - sp_.h / 2)
        for f_, sp_, x_, y_ in items:
            if p > f_:
                blit(frame, sp_, x_, y_, clamp((p - f_) / 0.012))
        xp = int(round(p * PW * K)) + m_p
        frame[preg[1]:preg[3], preg[0]:preg[0] + xp] = PB[:, :xp]
        for f_, x_, y_ in pdots:
            if p > f_:
                blit(frame, pdot, x_, y_, clamp((p - f_) / 0.012))
        d = p * tr.dist
        info = dict(up=tr.cup[-1] if p >= 1 else float(np.interp(d, gd, gup)))
        if tr.has_t:
            info["t"] = t_now if t_now is not None else tr.total if p >= 1 else time_at(d)
        for sx_, fn, final in cols:
            s_ = final if p >= 1 else fn(d, info)
            if s_ not in cnt_cache:
                cnt_cache[s_] = text_sprite([(s_, fnum, WHITE, 60 * K)], K)
            blit(frame, cnt_cache[s_], sx_ * K, stat_y * K)
        if live:
            vals = dict(ele=f_m(float(np.interp(d, gd, ge))), clock=clock(info["t"]) if tr.has_t else "",
                        spd=num(float(np.interp(d, gd, gsp)), 1) + NB + "km/h" if gsp is not None else "",
                        hr=hr_text(float(np.interp(d, gd, ghr))) if ghr is not None else "")
            for lx_, key in live:
                s_ = "l:" + vals[key]
                if s_ not in cnt_cache:
                    cnt_cache[s_] = text_sprite([(vals[key], fl, WHITE, 40 * K)], K)
                blit(frame, cnt_cache[s_], lx_ * K, (stat_y + 124) * K)
        marker(frame, PX0 * K + p * PW * K, (PY0 + float(fy(np.interp(d, gd, ge)))) * K, 8 * K, 4.5 * K)
        marker(frame, *at(p), 13.5 * K, 8.5 * K)
        return frame

    m_p = int(10 * K)
    return SimpleNamespace(render=render, chosen=chosen, events=events, photos=photos, n_files=n_files, prefs=prefs, shown_peaks=shown_peaks,
                           rename=rename, tri=tri, intro_pk=intro_pk, ends=[labels.placed[vi] for vi, _w in ends if vi in labels.placed],
                           background=labels.background,
                           badges=badges, clock=clock, time_at=time_at, f_km=f_km, f_m=f_m, f_hm=f_hm, fonts=fonts,
                           title_sp=title_sp, sub_sp=sub_sp, sub_y=sub_y, credit_sp=credit_sp, static_marks=static_marks, ring=ring,
                           o_red=o_red, o_col=o_col, rx0=rx0, ry0=ry0, rx1=rx1, ry1=ry1)
