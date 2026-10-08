"""Regenerate the synthetic test data in tests/data.

    python tests/data/make_test_data.py gpx     # synthetic GPX tracks (needs network once: elevations from Mapterhorn)
    python tests/data/make_test_data.py osm     # synthetic Overpass responses, stored in tests/data/cache/osm (offline)
    python tests/data/make_test_data.py cache   # download terrain tiles (with their list of sources), fonts and borders the test frames and intros need

The tracks imitate real recordings (GPS noise, a stop of a few minutes, one position jump), but are made up.
The OpenStreetMap data is synthetic as well: a few named peaks, huts, passes, lakes, paths and roads in the
Overpass JSON format, so labels and overlays are drawn without asking a server.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

DATA = Path(__file__).resolve().parent
ROOT = DATA.parent.parent
CACHE = DATA / "cache"
sys.path.insert(0, str(ROOT))


def find(name: str):
    """A function of the program, wherever in the package it lives."""
    import importlib
    import pkgutil
    import gpxfilm
    for m in pkgutil.iter_modules(gpxfilm.__path__):
        mod = importlib.import_module(f"gpxfilm.{m.name}")
        if getattr(mod, name, None) is not None and getattr(getattr(mod, name), "__module__", None) == mod.__name__:
            return getattr(mod, name)
    raise LookupError(name)

# Anchor points of each route (lat, lon); the track follows a smooth curve through them.
TRACKS = {
    "SLAD": dict(
        name="Tre Cime di Lavaredo Piesze wędrówki", start="2025-07-14T07:12:03Z", seed=1, hr=False, times=True,
        stop=(11, 7.0), jump=0.74,
        anchors=[(46.6124, 12.2957), (46.6127, 12.2995), (46.6131, 12.3036), (46.6145, 12.3070), (46.6158, 12.3097),
                 (46.6180, 12.3118), (46.6205, 12.3134), (46.6232, 12.3150), (46.6262, 12.3148), (46.6290, 12.3125),
                 (46.6312, 12.3100), (46.6326, 12.3083), (46.6318, 12.3045), (46.6300, 12.3005), (46.6290, 12.2960),
                 (46.6282, 12.2925), (46.6255, 12.2900), (46.6230, 12.2885), (46.6200, 12.2895), (46.6170, 12.2915),
                 (46.6145, 12.2935), (46.6126, 12.2955)]),
    "tatry": dict(
        name="Hala Gąsienicowa", start="2025-09-06T05:48:41Z", seed=2, hr=True, times=True,
        hr_dropout=(0.45, 120.0), hr_zeros=(0.2, 0.6, 0.85),
        stop=(13, 8.5), jump=0.31,
        anchors=[(49.2700, 19.9806), (49.2688, 19.9840), (49.2675, 19.9862), (49.2660, 19.9870), (49.2640, 19.9885),
                 (49.2622, 19.9902), (49.2605, 19.9920), (49.2590, 19.9935), (49.2577, 19.9950), (49.2555, 19.9975),
                 (49.2530, 19.9995), (49.2500, 20.0020), (49.2470, 20.0040), (49.2440, 20.0058), (49.2420, 20.0080),
                 (49.2395, 20.0110), (49.2370, 20.0140), (49.2348, 20.0165), (49.2330, 20.0185)]),
    "bez_czasu": dict(
        name="Śnieżka z Karpacza", start="2025-05-01T08:30:00Z", seed=3, hr=False, times=False,
        stop=(10, 6.0), jump=0.55,
        anchors=[(50.7735, 15.7400), (50.7705, 15.7375), (50.7672, 15.7350), (50.7640, 15.7338), (50.7610, 15.7330),
                 (50.7578, 15.7322), (50.7545, 15.7310), (50.7510, 15.7300), (50.7475, 15.7295), (50.7440, 15.7290),
                 (50.7410, 15.7290), (50.7395, 15.7320), (50.7385, 15.7350), (50.7372, 15.7378), (50.7362, 15.7398)]),
}

NEAR = {"IT", "AT", "CH", "SI", "DE", "PL", "CZ", "SK"}    # countries kept in the stored border file

# Synthetic OpenStreetMap features per track. Positions are approximate; names follow the real places nearby.
def node(i, lat, lon, **tags):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags}


def way(i, pts, closed=False, **tags):
    pts = list(pts) + ([pts[0]] if closed else [])
    lat, lon = [p[0] for p in pts], [p[1] for p in pts]
    return {"type": "way", "id": i, "bounds": {"minlat": min(lat), "minlon": min(lon), "maxlat": max(lat), "maxlon": max(lon)},
            "nodes": list(range(i * 100, i * 100 + len(pts))), "geometry": [{"lat": a, "lon": b} for a, b in pts], "tags": tags}


def lake(cx, cy, rx, ry, n=14, rot=0.3):
    """Closed, slightly irregular ring around (lat, lon) with radii in degrees."""
    out = []
    for k in range(n):
        a = 2 * math.pi * k / n
        f = 1 + 0.12 * math.sin(3 * a + rot)
        out.append((round(cx + ry * f * math.sin(a), 6), round(cy + rx * f * math.cos(a), 6)))
    return out


OSM = {
    "SLAD": lambda: [
        node(1, 46.6187, 12.3048, natural="peak", name="Cima Grande di Lavaredo", **{"name:de": "Große Zinne"}, ele="2999"),
        node(2, 46.6185, 12.3010, natural="peak", name="Cima Ovest di Lavaredo", **{"name:de": "Westliche Zinne"}, ele="2973"),
        node(3, 46.6198, 12.3070, natural="peak", name="Cima Piccola di Lavaredo", ele="2857"),
        node(4, 46.6290, 12.3185, natural="peak", name="Monte Paterno - Paternkofel", ele="2744"),
        node(5, 46.6105, 12.2780, natural="peak", name="Monte Campedelle", ele="2346"),
        node(6, 46.6212, 12.3136, natural="saddle", name="Forcella Lavaredo", ele="2454"),
        node(7, 46.6229, 12.2884, mountain_pass="yes", name="Forcella Col di Mezzo", ele="2315"),
        node(8, 46.6124, 12.2961, tourism="alpine_hut", name="Rifugio Auronzo", ele="2320"),
        node(9, 46.6157, 12.3099, tourism="alpine_hut", name="Rifugio Lavaredo", ele="2344"),
        node(10, 46.6327, 12.3086, tourism="alpine_hut", name="Rifugio Antonio Locatelli - Dreizinnenhütte", ele="2405"),
        node(11, 46.6281, 12.2922, tourism="wilderness_hut", name="Malga Langalm", ele="2283"),
        node(12, 46.6131, 12.3037, tourism="viewpoint", name="Cappella degli Alpini"),
        way(20, lake(46.6338, 12.3128, 0.0009, 0.0004), True, natural="water", name="Laghi dei Piani"),
        way(21, lake(46.6080, 12.2600, 0.0022, 0.0011), True, natural="water", name="Lago d'Antorno"),
        way(22, [(46.6124, 12.2957), (46.6131, 12.3036), (46.6158, 12.3097), (46.6205, 12.3134)], highway="track"),
        way(23, [(46.6205, 12.3134), (46.6262, 12.3148), (46.6326, 12.3083)], highway="path", name="Sentiero 101"),
        way(24, [(46.6326, 12.3083), (46.6300, 12.3005), (46.6282, 12.2925), (46.6230, 12.2885), (46.6126, 12.2955)], highway="path"),
        way(25, [(46.6124, 12.2957), (46.6100, 12.2880), (46.6085, 12.2760), (46.6070, 12.2640), (46.6065, 12.2520)],
            highway="secondary", name="Strada Panoramica delle Tre Cime"),
        node(26, 46.6075, 12.2550, place="hamlet", name="Misurina"),
        way(27, [(46.6121, 12.2953), (46.6121, 12.2956), (46.6124, 12.2956), (46.6124, 12.2953)], True, amenity="parking",
            name="Parcheggio Tre Cime"),                 # nearer the start than the hut
    ],
    "tatry": lambda: [
        node(1, 49.2318, 19.9817, natural="peak", name="Kasprowy Wierch", ele="1987"),
        node(2, 49.2268, 20.0141, natural="peak", name="Kościelec", ele="2155"),
        node(3, 49.2370, 20.0005, natural="peak", name="Mała Kopa Królowa", ele="1577"),
        node(4, 49.2520, 19.9725, natural="peak", name="Nosal", ele="1206"),
        node(5, 49.2577, 19.9950, natural="saddle", name="Przełęcz między Kopami", ele="1499"),
        node(6, 49.2441, 20.0061, tourism="alpine_hut", name="Schronisko PTTK Murowaniec", ele="1500"),
        node(7, 49.2650, 19.9878, tourism="viewpoint", name="Boczań"),
        way(10, lake(49.2312, 20.0192, 0.0030, 0.0013), True, natural="water", name="Czarny Staw Gąsienicowy"),
        way(11, lake(49.2364, 20.0050, 0.0007, 0.0004), True, natural="water", name="Zielony Staw Gąsienicowy"),
        way(12, [(49.2700, 19.9806), (49.2660, 19.9870), (49.2605, 19.9920), (49.2577, 19.9950)], highway="path", name="Szlak przez Boczań"),
        way(13, [(49.2577, 19.9950), (49.2500, 20.0020), (49.2440, 20.0058), (49.2330, 20.0185)], highway="path"),
        way(14, [(49.2700, 19.9806), (49.2735, 19.9790), (49.2770, 19.9760), (49.2810, 19.9720)], highway="tertiary", name="Droga do Kuźnic"),
        way(15, [(49.2440, 20.0058), (49.2480, 20.0100), (49.2560, 20.0160), (49.2650, 20.0200)], highway="track"),
        node(16, 49.2697, 19.9800, place="suburb", name="Kuźnice"),
        node(17, 49.2735, 20.0190, place="hamlet", name="Toporowa Cyrhla"),
    ],
    "bez_czasu": lambda: [
        node(1, 50.7360, 15.7398, natural="peak", name="Śnieżka", **{"name:cs": "Sněžka"}, ele="1603"),
        node(2, 50.7445, 15.7140, natural="peak", name="Kopa", ele="1377"),
        node(3, 50.7480, 15.6900, natural="peak", name="Smogornia", ele="1489"),
        node(4, 50.7402, 15.7290, mountain_pass="yes", name="Przełęcz pod Śnieżką", ele="1394"),
        node(5, 50.7408, 15.7285, tourism="alpine_hut", name="Dom Śląski / Slezský dům", ele="1400"),
        node(6, 50.7553, 15.7268, tourism="alpine_hut", name="Schronisko Samotnia", ele="1195"),
        node(7, 50.7610, 15.7330, tourism="attraction", name="Biały Jar"),
        way(10, lake(50.7560, 15.7248, 0.0012, 0.0005), True, natural="water", name="Mały Staw"),
        way(11, lake(50.7578, 15.7103, 0.0016, 0.0007), True, natural="water", name="Wielki Staw"),
        way(12, [(50.7735, 15.7400), (50.7640, 15.7338), (50.7545, 15.7310), (50.7440, 15.7290)], highway="path"),
        way(13, [(50.7440, 15.7290), (50.7395, 15.7320), (50.7362, 15.7398)], highway="path", name="Droga Jubileuszowa"),
        way(14, [(50.7735, 15.7400), (50.7760, 15.7460), (50.7780, 15.7540), (50.7790, 15.7620)], highway="secondary"),
        node(15, 50.7766, 15.7569, place="town", name="Karpacz"),
    ],
}


# Synthetic answers to the intro's question for the highest peaks within the radius around the start (fetch_peaks).
# Positions are approximate; one peak lies outside 20 km, one elevation carries a unit and one cannot be read.
OSM_PEAKS = {
    "SLAD": lambda: [
        node(901, 46.6187, 12.3048, natural="peak", name="Cima Grande di Lavaredo", ele="2999", **{"name:de": "Große Zinne"}),
        node(902, 46.6185, 12.301, natural="peak", name="Cima Ovest di Lavaredo", ele="2973", **{"name:de": "Westliche Zinne"}),
        node(903, 46.6198, 12.307, natural="peak", name="Cima Piccola di Lavaredo", ele="2857"),
        node(904, 46.629, 12.3185, natural="peak", name="Monte Paterno - Paternkofel", ele="2744"),
        node(905, 46.6105, 12.278, natural="peak", name="Monte Campedelle", ele="2346"),
        node(906, 46.452, 12.262, natural="peak", name="Antelao", ele="3264"),
        node(907, 46.537, 12.069, natural="peak", name="Tofana di Mezzo", ele="3244"),
        node(908, 46.58, 12.205, natural="peak", name="Monte Cristallo", ele="3221"),
        node(909, 46.515, 12.215, natural="peak", name="Sorapiss", ele="3205"),
        node(910, 46.59, 12.22, natural="peak", name="Piz Popena", ele="3152"),
        node(911, 46.665, 12.333, natural="peak", name="Punta Tre Scarperi - Dreischusterspitze", ele="3152"),
        node(912, 46.637, 12.13, natural="peak", name="Croda Rossa d'Ampezzo", ele="3146", **{"name:de": "Hohe Gaisl"}),
        node(913, 46.63, 12.36, natural="peak", name="Croda dei Toni - Zwölferkofel", ele="3094 m"),
        node(914, 46.645, 12.38, natural="peak", name="Cima Undici - Elferkofel", ele="3092"),
        node(915, 46.584, 12.265, natural="peak", name="Cadin di San Lucano", ele="ca. 2839"),
        node(916, 46.66, 12.148, natural="peak", name="Picco di Vallandro - Dürrenstein", ele="2839"),
        node(917, 46.617, 12.234, natural="peak", name="Monte Piana", ele="2324"),
        node(918, 46.428, 12.136, natural="peak", name="Monte Pelmo", ele="3168"),
    ],
}
# Synthetic answers to the intro's question for the main places within the radius around the start (fetch_places).
# Positions are approximate. Populations are written in several ways; two villages have none, one village lies outside
# 20 km, a city lies far away and a hamlet is not a place the intro shows.
OSM_PLACES = {
    "SLAD": lambda: [
        node(951, 46.5405, 12.1357, place="town", name="Cortina d'Ampezzo", population="5794"),
        node(952, 46.5543, 12.4325, place="town", name="Auronzo di Cadore", population="3 270"),
        node(953, 46.7333, 12.2215, place="village", name="Dobbiaco - Toblach", population="3,341",
             **{"name:it": "Dobbiaco", "name:de": "Toblach"}),
        node(954, 46.7327, 12.2817, place="village", name="San Candido - Innichen", population="ca. 3205",
             **{"name:it": "San Candido", "name:de": "Innichen"}),
        node(955, 46.7023, 12.3500, place="village", name="Sesto - Sexten", **{"name:it": "Sesto", "name:de": "Sexten"}),
        node(956, 46.7373, 12.1725, place="village", name="Villabassa - Niederdorf", **{"name:it": "Villabassa", "name:de": "Niederdorf"}),
        node(957, 46.5560, 12.5470, place="village", name="Santo Stefano di Cadore", population="2600"),
        node(958, 46.1405, 12.2167, place="city", name="Belluno", population="35000"),
        node(959, 46.6075, 12.2550, place="hamlet", name="Misurina"),
    ],
}
RADIUS = 20.0                                            # km, the program's default (--intro-radius)


def spline(anchors: list, step_m: float = 1.0):
    """Catmull-Rom curve through the anchors, resampled every step_m meters: (lat, lon) arrays."""
    a = np.array(anchors, float)
    k = math.cos(math.radians(a[:, 0].mean()))
    xy = np.c_[a[:, 1] * k, a[:, 0]] * 111320.0
    p = np.vstack([2 * xy[0] - xy[1], xy, 2 * xy[-1] - xy[-2]])
    out = []
    for i in range(1, len(p) - 2):
        t = np.linspace(0, 1, 40, endpoint=False)[:, None]
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        out.append(0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(p[-2][None])
    c = np.vstack(out)
    d = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
    s = np.arange(0, d[-1], step_m)
    x, y = np.interp(s, d, c[:, 0]), np.interp(s, d, c[:, 1])
    return y / 111320.0, x / 111320.0 / k, s


def elevation(lat, lon):
    """Terrain height from Mapterhorn tiles (zoom 13), bilinear."""
    dem_tile = find("dem_tile")
    z, n, ts = 13, 2 ** 13, 512
    u = (np.asarray(lon) + 180) / 360 * n * ts
    v = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n * ts
    out = np.zeros(len(u))
    tiles = {}
    for i, (uu, vv) in enumerate(zip(u, v)):
        tx, ty = int(uu // ts), int(vv // ts)
        if (tx, ty) not in tiles:
            tiles[(tx, ty)] = dem_tile(z, tx, ty)
        t = tiles[(tx, ty)]
        fx, fy = uu - tx * ts - 0.5, vv - ty * ts - 0.5
        j, k = int(np.clip(math.floor(fx), 0, ts - 2)), int(np.clip(math.floor(fy), 0, ts - 2))
        ax, ay = fx - j, fy - k
        out[i] = (t[k, j] * (1 - ax) + t[k, j + 1] * ax) * (1 - ay) + (t[k + 1, j] * (1 - ax) + t[k + 1, j + 1] * ax) * ay
    return out


def make_gpx(key: str, spec: dict) -> str:
    rng = np.random.default_rng(spec["seed"])
    lat, lon, s = spline(spec["anchors"])
    ele = elevation(lat[::10], lon[::10])
    ele = ndi.uniform_filter1d(np.interp(s, s[::10], ele), 61, mode="nearest")    # a trail is smoother than the slope it crosses
    # walking time from Tobler's hiking function, a little slower than the formula
    slope = np.clip(np.gradient(ndi.uniform_filter1d(ele, 151, mode="nearest"), s), -0.35, 0.35)
    v = np.maximum(6 / 3.6 * np.exp(-3.5 * np.abs(slope + 0.05)) * 0.9, 0.6)
    t = np.concatenate([[0], np.cumsum(1.0 / v[:-1])])
    stop_s = s[np.argmin(np.hypot(lat - spec["anchors"][spec["stop"][0]][0], lon - spec["anchors"][spec["stop"][0]][1]))]
    stop_dur = spec["stop"][1] * 60
    t = t + np.where(s > stop_s, stop_dur, 0.0)
    # recording: a point every 1–10 s ("smart recording"), denser while moving fast
    times, tt = [], 0.0
    while tt <= t[-1]:
        times.append(tt)
        tt += float(rng.choice([2, 3, 4, 5, 6, 7, 8, 9, 10], p=[.06, .1, .12, .14, .16, .14, .12, .08, .08]))
    times = np.array(times)
    t_moving = np.interp(times, t, s)                 # distance along the route at each fix
    plat, plon, pele = np.interp(t_moving, s, lat), np.interp(t_moving, s, lon), np.interp(t_moving, s, ele)
    # GPS noise: slowly wandering error plus a little jitter, in meters
    n = len(times)
    dtv = np.diff(times, prepend=times[0])
    ex, ey, eh = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(1, n):
        a = math.exp(-dtv[i] / 45)
        ex[i] = a * ex[i - 1] + math.sqrt(1 - a * a) * 3.2 * rng.standard_normal()
        ey[i] = a * ey[i - 1] + math.sqrt(1 - a * a) * 3.2 * rng.standard_normal()
        b = math.exp(-dtv[i] / 600)
        eh[i] = b * eh[i - 1] + math.sqrt(1 - b * b) * 2.5 * rng.standard_normal()
    ex += rng.normal(0, 1.2, n)
    ey += rng.normal(0, 1.2, n)
    # one position jump: a few fixes thrown 150–250 m to the side (multipath under a rock face)
    j0 = int(spec["jump"] * n)
    ang = rng.uniform(0, 2 * math.pi)
    for k_, dist in enumerate((160, 240, 210, 150)):
        ex[j0 + k_] += dist * math.cos(ang)
        ey[j0 + k_] += dist * math.sin(ang)
    k = math.cos(math.radians(plat.mean()))
    plat = plat + ey / 111320.0
    plon = plon + ex / 111320.0 / k
    pele = pele + eh + rng.normal(0, 0.4, n)
    hr = None
    if spec["hr"]:
        grade = np.interp(t_moving, s, slope)
        moving = np.r_[True, np.diff(t_moving) > 0.05]
        target = np.where(moving, 96 + 270 * np.clip(grade, 0, 0.3), 82)      # at most about 185 on the steepest climb
        hr = np.zeros(n)
        hr[0] = 88
        for i in range(1, n):
            a = math.exp(-dtv[i] / 40)
            hr[i] = a * hr[i - 1] + (1 - a) * target[i] + rng.normal(0, 1.2)
        # A deliberate dropout of the chest strap: about two minutes of 255 (the "no reading" value of ANT+ and FIT, which
        # Strava exports as is) and a few single 0 values. The reader treats both as missing, so the reference frame shows
        # a short gray stretch "no heart rate" on the route colored by heart rate.
        if spec.get("hr_dropout"):
            start, length = spec["hr_dropout"]
            t_a = start * times[-1]
            hr[(times >= t_a) & (times < t_a + length)] = 255
        for f in spec.get("hr_zeros", ()):
            hr[int(f * (n - 1))] = 0
    t0 = dt.datetime.fromisoformat(spec["start"].replace("Z", "+00:00"))
    ext = ' xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"' if hr is not None else ""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<gpx creator="gpxfilm test data" version="1.1" xmlns="http://www.topografix.com/GPX/1/1"{ext}>']
    if spec["times"]:
        out.append(f"  <metadata><time>{spec['start']}</time></metadata>")
    out += [f"  <trk>\n    <name>{spec['name']}</name>\n    <type>hiking</type>\n    <trkseg>"]
    for i in range(n):
        row = f'      <trkpt lat="{plat[i]:.7f}" lon="{plon[i]:.7f}"><ele>{pele[i]:.1f}</ele>'
        if spec["times"]:
            row += f"<time>{(t0 + dt.timedelta(seconds=float(times[i]))).strftime('%Y-%m-%dT%H:%M:%SZ')}</time>"
        if hr is not None:
            row += f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{int(round(hr[i]))}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>"
        out.append(row + "</trkpt>")
    out += ["    </trkseg>", "  </trk>", "</gpx>", ""]
    return "\n".join(out)


def cmd_gpx() -> None:
    os.environ["GPXFILM_CACHE_DIR"] = tempfile.mkdtemp(prefix="gpxfilm-dem-")
    for key, spec in TRACKS.items():
        (DATA / f"{key}.gpx").write_text(make_gpx(key, spec), encoding="utf-8")
        print(f"{key}.gpx")


def cmd_osm() -> None:
    """Store each synthetic response where the program looks for the Overpass answer to its query."""
    os.environ["GPXFILM_CACHE_DIR"] = str(CACHE)
    shutil.rmtree(CACHE / "osm", ignore_errors=True)   # the program does not ask again for a query it has stored
    fetch_osm = find("fetch_osm")
    for key, make in OSM.items():
        tr = find("prep_track")(find("read_gpx")(DATA / f"{key}.gpx"))
        lay = find("Layout")(tr, 1280, 720)
        body = json.dumps({"version": 0.6, "generator": "gpxfilm synthetic test data", "elements": make()}).encode()
        sys.modules[fetch_osm.__module__].http_get = lambda *a, body=body, **kw: body
        fetch_osm(tr, lay)
        print(f"osm for {key}")
    fetch_peaks = find("fetch_peaks")
    for key, make in OSM_PEAKS.items():
        tr = find("prep_track")(find("read_gpx")(DATA / f"{key}.gpx"))
        body = json.dumps({"version": 0.6, "generator": "gpxfilm synthetic test data", "elements": make()}).encode()
        sys.modules[fetch_peaks.__module__].http_get = lambda *a, body=body, **kw: body
        fetch_peaks(tr, RADIUS * 1000)
        print(f"intro peaks for {key}")
    fetch_places = find("fetch_places")
    for key, make in OSM_PLACES.items():
        tr = find("prep_track")(find("read_gpx")(DATA / f"{key}.gpx"))
        body = json.dumps({"version": 0.6, "generator": "gpxfilm synthetic test data", "elements": make()}).encode()
        sys.modules[fetch_places.__module__].http_get = lambda *a, body=body, **kw: body
        fetch_places(tr, RADIUS * 1000)
        print(f"intro places for {key}")


def profile_tiles() -> None:
    """The terrain model under each test track, for its elevation profile (fixed zoom, whatever the frame size)."""
    os.environ["GPXFILM_CACHE_DIR"] = str(CACHE)
    for key in TRACKS:
        pts = find("read_gpx")(DATA / f"{key}.gpx")["pts"]
        find("ground_profile")(np.array([q[0] for q in pts]), np.array([q[1] for q in pts]))
        print(f"terrain profile for {key}")


def intro_tiles(env: dict) -> None:
    """Terrain tiles for the camera flight of each intro case, from the view of the whole country down to the track. The
    film runs through the frame hook (nothing is encoded) and without OpenStreetMap, so no Overpass answer is stored."""
    sys.path.insert(0, str(DATA.parent))
    from test_intro import INTRO_CASES
    for name, (gpx, args, _) in INTRO_CASES.items():
        keep = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / gpx), *args, "--no-osm", "-o", str(keep / "film.mp4")],
                       env=dict(env, GPXFILM_FRAMES=str(keep)), cwd=ROOT, check=True)
        print(f"intro tiles for {name}")


def cmd_cache() -> None:
    """Run each test frame once with network access so the needed tiles, fonts and borders land in the cache."""
    sys.path.insert(0, str(DATA.parent))
    from test_frames import CASES
    env = dict(os.environ, GPXFILM_CACHE_DIR=str(CACHE), TZ="UTC", XDG_CONFIG_HOME=tempfile.mkdtemp())
    for name, (gpx, args) in CASES.items():
        out = Path(tempfile.mkdtemp()) / "frame.png"
        subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / gpx), *args, "--frame-only", str(out)], env=env, cwd=ROOT, check=True)
        print(f"cache for {name}")
    intro_tiles(env)
    shutil.rmtree(CACHE / "backgrounds", ignore_errors=True)   # computed backgrounds, not downloaded data
    profile_tiles()
    # borders: keep only the countries around the test tracks
    ne = CACHE / "ne_50m_admin_0_countries.geojson"
    d = json.loads(ne.read_text(encoding="utf-8"))
    d["features"] = [f for f in d["features"] if (f["properties"].get("ISO_A2_EH") or f["properties"].get("ISO_A2")) in NEAR]
    ne.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    # the elevation models behind the terrain tiles, with their producers and licenses
    (DATA / "mapterhorn-attribution.json").write_bytes(find("http_get")("https://download.mapterhorn.com/attribution.json"))


if __name__ == "__main__":
    {"gpx": cmd_gpx, "osm": cmd_osm, "cache": cmd_cache}[sys.argv[1]]()
