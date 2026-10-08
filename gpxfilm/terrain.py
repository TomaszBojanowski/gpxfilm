"""Terrain model, hill shading, contour lines and the map background of a frame."""
from __future__ import annotations

import hashlib
import http.client
import math
import time
import urllib.error

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from .geo import R_EARTH, Layout, Proj, Track, at_vertices, smooth_climb
from .i18n import _, ngettext
from .net import CACHE, cache_dir, http_get
from .osm import osm_geoms
from .sources import MAPS, SENTINEL, TERRAIN
from .tiles import SourceDown, raster_map
from .util import clamp, log, to_u8

LOOK = {"style": "natural", "light": (315.0, 42.0), "map": "terrain"}   # look of the map: color style, light direction, kind of background
VERSION = 15                             # a change in how the map is drawn makes stored backgrounds stale


def dem_tile(z: int, x: int, y: int, tries: int = 3) -> np.ndarray:
    p = CACHE / "dem" / str(z) / str(x) / f"{y}.webp"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(http_get(f"https://tiles.mapterhorn.com/{z}/{x}/{y}.webp", timeout=60 if tries > 1 else 20, tries=tries))
        time.sleep(0.05)
    a = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


PROFILE_ZOOM = 12                        # terrain tiles for the elevation along the track: the same for every film size
PROFILE_TILES = 90                       # a track over more tiles than this (several hundred km) gets no profile from them


def ground_profile(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Elevation of the terrain model under each point, interpolated between the pixels of PROFILE_ZOOM tiles. A track
    over more than PROFILE_TILES tiles raises ValueError before anything is downloaded."""
    TS, n = 512, 2 ** PROFILE_ZOOM
    px = (np.asarray(lon, float) + 180) / 360 * n * TS - 0.5
    py = (1 - np.arcsinh(np.tan(np.radians(np.asarray(lat, float)))) / np.pi) / 2 * n * TS - 0.5
    x0, y0 = np.floor(px).astype(np.int64), np.floor(py).astype(np.int64)
    fx, fy = px - x0, py - y0
    if len(np.unique((x0 // TS) * n + y0 // TS)) > PROFILE_TILES:
        raise ValueError("too many terrain tiles for the elevation profile")
    tiles = {}

    def at(X, Y):                                # the model at whole pixels, tile by tile
        tx, ty = (X // TS) % n, np.clip(Y // TS, 0, n - 1)
        key = tx * n + ty
        out = np.empty(len(X))
        for k in np.unique(key):
            if k not in tiles:
                tiles[k] = dem_tile(PROFILE_ZOOM, int(k // n), int(k % n), tries=1)    # optional: no waiting without network
            m = key == k
            out[m] = tiles[k][np.clip(Y[m] - ty[m] * TS, 0, TS - 1), X[m] % TS]
        return out

    top = at(x0, y0) * (1 - fx) + at(x0 + 1, y0) * fx
    bot = at(x0, y0 + 1) * (1 - fx) + at(x0 + 1, y0 + 1) * fx
    return top * (1 - fy) + bot * fy


GROUND_STEP = 2.5                        # rises smaller than this are noise in the terrain model
NOISY = (1.2, 50.0)                      # the file's ascent above the model's by more than this share and these meters: noise


def ground_elevation(tr: Track) -> None:
    """Take the elevation from the terrain model when the file has none, or when it shakes so much (a phone without a
    barometer) that its ascent is more than 20% and 50 m above the model's. Without the model's tiles nothing changes."""
    try:
        es, cup = smooth_climb(ground_profile(tr.lat, tr.lon), tr.t, GROUND_STEP)
    except ValueError:                           # a track over too many tiles: keep what there is
        return
    except (OSError, http.client.HTTPException):
        log(_("Could not download the terrain model for the elevation profile – keeping the elevation from the file.") if tr.has_e else
            _("Could not download the terrain model for the elevation profile – using the elevation from the frame's map."))
        return
    es, cup = at_vertices(es, tr.ci), at_vertices(cup, tr.ci)
    if tr.has_e:
        if not (tr.cup[-1] > NOISY[0] * cup[-1] and tr.cup[-1] - cup[-1] > NOISY[1]):
            return
        log(_("The elevation in the file is very noisy (total ascent {ascent} m, {model} m by the terrain model) – taking it from "
              "the terrain model.").format(ascent=f"{tr.cup[-1]:.0f}", model=f"{cup[-1]:.0f}"))
    tr.ce, tr.cup, tr.has_e = es, cup, True


def load_dem(pr: Proj, lay: Layout, quiet: bool = False) -> np.ndarray:
    """The terrain model sampled exactly at the pixels of the frame."""
    C, TS = 2 * math.pi * R_EARTH, 512
    z = int(clamp(math.ceil(math.log2(C * pr.k / (TS * lay.mpp * 1.6))), 3, 15))
    x0, y0 = lay.meters(0, 0)
    x1, y1 = lay.meters(1920, 1080)
    while True:
        n = 2 ** z
        u = lambda xm: (((xm / pr.k + pr.X0) / C) + 0.5) * n * TS
        v = lambda ym: (0.5 - (-ym / pr.k + pr.Y0) / C) * n * TS
        tx0, tx1, ty0, ty1 = int(u(x0) // TS), int(u(x1) // TS), int(v(y0) // TS), int(v(y1) // TS)
        cnt = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
        if cnt > 90 and z > 3:
            z -= 1
            continue
        try:
            if not quiet:
                log(ngettext("Terrain model: {n} tile, zoom {zoom}", "Terrain model: {n} tiles, zoom {zoom}", cnt).format(n=cnt, zoom=z))
            mos = np.zeros(((ty1 - ty0 + 1) * TS, (tx1 - tx0 + 1) * TS), np.float32)
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    mos[(ty - ty0) * TS:(ty - ty0 + 1) * TS, (tx - tx0) * TS:(tx - tx0 + 1) * TS] = dem_tile(z, tx % n, min(max(ty, 0), n - 1))
            break
        except urllib.error.HTTPError:
            if z <= 3:
                raise
            z -= 1                                   # no tiles at this level: go one level down
    jj = (np.arange(lay.W) + 0.5) / lay.K
    ii = (np.arange(lay.H) + 0.5) / lay.K
    uu = u((jj - lay.ox) / lay.S) - tx0 * TS - 0.5
    vv = v((ii - lay.oy) / lay.S) - ty0 * TS - 0.5
    i0 = np.clip(np.floor(vv).astype(int), 0, mos.shape[0] - 2); fv = (vv - i0)[:, None].astype(np.float32)
    j0 = np.clip(np.floor(uu).astype(int), 0, mos.shape[1] - 2); fu = (uu - j0)[None, :].astype(np.float32)
    top = mos[i0][:, j0] * (1 - fu) + mos[i0][:, j0 + 1] * fu
    bot = mos[i0 + 1][:, j0] * (1 - fu) + mos[i0 + 1][:, j0 + 1] * fu
    return top * (1 - fv) + bot * fv


def relief(proj: Proj, lay: Layout, osm: dict, contours: bool = True, quiet: bool = False, tiles: bool = False):
    """Shaded terrain map of the frame (image 0..1) and the elevation model at the pixels of the frame."""
    W, H, K, mpp = lay.W, lay.H, lay.K, lay.mpp
    cache = None
    if lay.W <= 1280:                                 # small frames (preview, draft) are stored on disk: the next preview is quick
        sig = repr((VERSION, lay.W, lay.H, round(lay.S, 9), round(lay.ox, 3), round(lay.oy, 3), round(proj.lat0, 7), round(proj.lon0, 7),
                    sorted((k_, v_) for k_, v_ in LOOK.items() if k_ != "credit"), contours, tiles, len(osm.get("elements", []))))
        cache = cache_dir(CACHE, "backgrounds", "tla") / (hashlib.sha1(sig.encode()).hexdigest() + ".npz")
        if cache.exists():
            try:
                z = np.load(cache)
                if str(z["credit"]):
                    LOOK["credit"] = str(z["credit"])
                return z["img"].astype(np.float32) / 255, z["D"]
            except Exception:  # noqa: BLE001 – a damaged file is simply computed again
                pass
    D0 = load_dem(proj, lay, quiet)
    gs = lambda a, m: ndi.gaussian_filter(a, max(m / mpp, 0.6))
    # variable smoothing: detail where the data has it, smooth where grid artifacts show
    rough = gs(np.abs(D0 - gs(D0, 7.4)), 52.0)
    w = gs(np.clip((rough - 0.10) / 0.32, 0, 1), 37.0)
    D = w * gs(D0, 2.8) + (1 - w) * gs(D0, 17.0)
    del rough, w
    gy, gx = np.gradient(D, mpp)
    g = np.hypot(gx, gy)
    zf = float(np.clip(0.80 / max(float(np.percentile(g[::8, ::8], 95)), 1e-3), 1, 8 if mpp < 40 else 40))   # vertical exaggeration for flat areas
    nrm = np.sqrt((gx * zf) ** 2 + (gy * zf) ** 2 + 1)

    def shade(az, alt):
        a, b = math.radians(az), math.radians(alt)
        return np.clip((-gx * zf * math.sin(a) * math.cos(b) + gy * zf * math.cos(a) * math.cos(b) + math.sin(b)) / nrm, 0, 1)

    az_, alt_ = LOOK["light"]                         # the main light and two weaker fill lights
    lights = [(az_, alt_, 0.58), (az_ - 45, max(alt_ - 8, 12), 0.24), (az_ + 45, min(alt_ + 10, 75), 0.18)]
    hs = sum(w_ * shade(a_, b_) for a_, b_, w_ in lights)
    flat = sum(w_ * math.sin(math.radians(b_)) for _, b_, w_ in lights)
    e1, e99 = np.percentile(D[::8, ::8], [1, 99])
    tt = np.clip((D - e1) / max(e99 - e1, 500.0), 0, 1)
    ts = [0, 0.29, 0.47, 0.62, 0.76, 1.0]
    cs = np.array([(60, 76, 64), (82, 98, 76), (112, 119, 98), (148, 146, 132), (168, 165, 155), (206, 202, 194)]) / 255
    img = np.stack([np.interp(tt, ts, cs[:, c]) for c in range(3)], -1).astype(np.float32)
    rk = (np.clip((np.degrees(np.arctan(g)) - 36) / 22, 0, 1) * 0.72)[..., None]
    img = img * (1 - rk) + np.array([138, 137, 135], np.float32) / 255 * rk
    dv = hs - flat
    warm = np.clip(dv / (1 - flat), 0, 1)[..., None]
    cool = np.clip(-dv / flat, 0, 1)[..., None]
    lum = np.clip(0.30 + 1.02 * hs, 0, 1.25)
    img *= lum[..., None]
    img *= (1 + warm * np.array([0.08, 0.03, -0.05], np.float32)) * (1 - cool * np.array([0.20, 0.10, -0.04], np.float32))
    del hs, warm, cool, rk, tt, nrm
    # contour lines computed analytically (no vectorizing): distance to the nearest contour line in pixels
    rng = float(e99 - e1)
    I = next((c for c in (2, 5, 10, 20, 25, 50, 100, 200) if rng / c <= 70), 500)
    gp = np.maximum(g * mpp, 1e-6)

    def lines(step, wpx):
        m = np.mod(D, step)
        a = np.clip(0.5 + wpx / 2 - np.minimum(m, step - m) / gp, 0, 1)
        sp = step / gp
        return a * np.clip((sp - 1.5) / 2.5, 0, 1) * (sp < 4000)

    ca = np.clip(lines(I, 0.9 * K / 2 + 0.4) * 0.30 + lines(5 * I, 1.6 * K / 2 + 0.5) * 0.36, 0, 0.8)[..., None] * float(contours)
    img = img * (1 - ca) + np.array([0.05, 0.07, 0.09], np.float32) * ca
    del ca, gp
    sea = ((D0 <= 0.3) & (g < 1e-3))[..., None]       # sea and large water bodies in the elevation data
    img = np.where(sea, np.array([0.20, 0.38, 0.47], np.float32), img)
    ready = tiles and LOOK["map"] != "terrain"         # a ready background from tiles instead of our own terrain drawing
    complete = True                                   # a background from incomplete tiles is not stored: next time they may come

    def fetch(sources):
        nonlocal complete
        try:
            got_ = raster_map(proj, lay, sources)
        except SourceDown as e:
            complete = False
            return e
        complete = complete and (got_ is None or got_[4])
        return got_

    if ready:
        got = fetch(LOOK["sources"])
        if not isinstance(got, tuple) and LOOK["map"] == "aerial":   # aerial photos missing: satellite for the whole frame
            log(_("Aerial photos do not cover this place – using Sentinel-2 satellite imagery.") if got is None else
                _("Aerial photos: {error} – using Sentinel-2 satellite imagery for the whole frame.").format(error=got))
            LOOK["map"], LOOK["sources"] = "satellite", (MAPS["satellite"],)
            got = fetch(LOOK["sources"])
        if not isinstance(got, tuple):
            if got is not None:
                log(_("Map background: {error}.").format(error=got))
            log(_("Could not download the map background – keeping the terrain map."))
            ready = False
        else:
            ras, hole, LOOK["credit"] = got[0], got[1], got[3]
            fill = TERRAIN                            # what shows through the gaps: the terrain drawing or satellite imagery
            if LOOK["map"] == "satellite":            # satellite imagery gets relief shading
                ras = np.clip(ras * lum[..., None] * 1.14, 0, 1)
            elif LOOK["map"] == "aerial":              # aerial photos have their own shadows: only slight dimming under the text
                ras = ras * (0.76 + 0.16 * lum[..., None])
                if hole is not None:                  # bits outside the aerial photo coverage are patched with satellite imagery, not the drawing
                    sat = fetch((MAPS["satellite"],))
                    if isinstance(sat, tuple):
                        s_img = np.clip(sat[0] * lum[..., None] * 0.95, 0, 1)
                        img = s_img if sat[1] is None else s_img * (1 - sat[1][..., None]) + img * sat[1][..., None]   # where satellite is missing too, the drawing
                        fill = SENTINEL if sat[1] is None or float((sat[1] * hole).mean()) < 0.001 else f"{SENTINEL}; {TERRAIN}"
            else:
                ras = ras * 0.80
            if hole is not None and fill not in LOOK["credit"]:
                LOOK["credit"] += "; " + fill
            img = ras if hole is None else ras * (1 - hole[..., None]) + img * hole[..., None]
    del lum
    # trails and roads, and water from OpenStreetMap, drawn at double resolution
    ss = 2
    lm = Image.new("L", (W * ss, H * ss), 0); ld = ImageDraw.Draw(lm)
    wm_ = Image.new("L", (W * ss, H * ss), 0); wd = ImageDraw.Draw(wm_)
    for e in (osm.get("elements", []) if not ready or LOOK["map"] == "satellite" else []):
        tg = e.get("tags", {})
        if e["type"] == "node":
            continue
        for geo in osm_geoms(e):
            px, py = lay.des(*proj.fwd([q["lat"] for q in geo], [q["lon"] for q in geo]))
            pts = [(float(a) * K * ss, float(b) * K * ss) for a, b in zip(px, py)]
            if tg.get("natural") == "water" and len(pts) > 3:
                wd.polygon(pts, fill=255)
            elif tg.get("highway") and len(pts) > 1:
                ld.line(pts, fill=255, width=max(1, int(round(1.15 * K * ss / 2 * 2 / 2))))
    la = (np.asarray(lm.resize((W, H), Image.BOX), np.float32) / 255 * 0.30)[..., None]
    img = img * (1 - la) + la
    wa = (np.asarray(wm_.resize((W, H), Image.BOX), np.float32) / 255)[..., None]
    img = img * (1 - wa) + np.array([0.27, 0.52, 0.64], np.float32) * wa
    del la, wa, lm, wm_
    gm = img.mean(-1, keepdims=True)
    img = np.clip((gm + (img - gm) * 1.10) * 1.04, 0, 1)
    if LOOK["style"] == "night":                       # a cool, dimmed map
        gm = img.mean(-1, keepdims=True)
        img = (gm + (img - gm) * 0.45) * np.array([0.60, 0.76, 1.0], np.float32) * 0.85
    elif LOOK["style"] == "light":                     # brightened, with less contrast
        img = img ** 0.62
        gm = img.mean(-1, keepdims=True)
        img = gm + (img - gm) * 0.9
    elif LOOK["style"] == "gray":                     # black and white
        img = np.repeat(np.clip((img.mean(-1, keepdims=True) - 0.4) * 1.2 + 0.42, 0, 1), 3, -1)
    img = np.clip(img, 0, 1)
    if cache is not None and complete:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, img=to_u8(img), D=D.astype(np.float32), credit=np.array(LOOK.get("credit") or "" if tiles else ""))
        for old in sorted(cache.parent.glob("*.npz"), key=lambda q: q.stat().st_mtime)[:-16]:
            old.unlink(missing_ok=True)
    return img, D
