"""OpenStreetMap data (Overpass, Nominatim), places along the track and the country of the track."""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.parse

import numpy as np

from .geo import Layout, Track
from .i18n import _
from .net import CACHE, http_get
from .util import log

LANG_BY_COUNTRY = {"IT": "it", "AT": "de", "DE": "de", "CH": "de", "LI": "de", "FR": "fr", "ES": "es", "PT": "pt", "PL": "pl", "CZ": "cs", "SK": "sk", "SI": "sl",
                   "HR": "hr", "NO": "no", "SE": "sv", "FI": "fi", "DK": "da", "GB": "en", "IE": "en", "US": "en", "NL": "nl", "BE": "nl", "RO": "ro", "HU": "hu"}
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"


def fetch_osm(tr: Track, lay: Layout) -> dict:
    (x0, x1), (y0, y1) = lay.meters([0, 1920], [0, 1080])
    (n_, s_), (w_, e_) = tr.proj.inv([x0, x0], [y0, y1])[0], tr.proj.inv([x0, x1], [y0, y0])[1]
    b = f"{s_:.5f},{w_:.5f},{n_:.5f},{e_:.5f}"
    big = (x1 - x0) * (y1 - y0) / 1e6 > 200
    hw = 'way["highway"~"^(motorway|trunk|primary|secondary|tertiary|track|path|cycleway)$"]' if big else 'way["highway"]'
    q = (f'[out:json][timeout:60];({hw}({b});way["natural"="water"]({b});relation["natural"="water"]({b});'
         f'node["natural"~"^(peak|saddle)$"]({b});node["mountain_pass"="yes"]({b});'
         f'nwr["tourism"~"^(alpine_hut|wilderness_hut|viewpoint|attraction)$"]["name"]({b});nwr["amenity"="parking"]["name"]({b});'
         f'node["place"~"^(town|village|hamlet|suburb)$"]["name"]({b}););out geom tags;')
    d = overpass(q, _("OpenStreetMap: downloading data ({server})"))
    if d is None:
        log(_("No OpenStreetMap data – the film will have no trails, lakes or place labels."))
    return d or {"elements": []}


SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://overpass.private.coffee/api/interpreter")


def overpass(q: str, message: str) -> dict | None:
    """The answer to an Overpass query, from the cache or from the first server that gives it; None when none does."""
    p = CACHE / "osm" / (hashlib.sha1(q.encode()).hexdigest() + ".json")
    if p.exists():
        return json.loads(p.read_text())
    for srv in SERVERS:
        try:
            log(message.format(server=srv.split("/")[2]))
            d = json.loads(http_get(srv, data=urllib.parse.urlencode({"data": q}).encode(), timeout=90, tries=2))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d))
            return d
        except Exception as e:  # noqa: BLE001
            log("  " + _("failed: {error}").format(error=e))
    return None


def fetch_peaks(tr: Track, radius: float) -> list:
    """Named peaks with an elevation within radius meters of the start, highest first: (elevation, x, y, tags)."""
    lat, lon = (float(v) for v in tr.proj.inv(tr.cx[0], tr.cy[0]))
    q = f'[out:json][timeout:60];node["natural"="peak"]["name"]["ele"](around:{int(radius)},{lat:.5f},{lon:.5f});out;'
    d = overpass(q, _("OpenStreetMap: downloading the peaks nearby ({server})"))
    out = []
    for e in (d or {}).get("elements", []):
        try:
            ele = float(str(e.get("tags", {}).get("ele", "")).replace(",", ".").split()[0])
        except (ValueError, IndexError):
            continue
        if 0 < ele < 9000 and "lat" in e:
            x, y = (float(v) for v in tr.proj.fwd(e["lat"], e["lon"]))
            if math.hypot(x - tr.cx[0], y - tr.cy[0]) <= radius:
                out.append((ele, x, y, e["tags"]))
    return sorted(out, key=lambda t_: -t_[0])


PLACE_CLASSES = ("city", "town", "village")             # the places of the intro, the most important class first


def population(tags: dict) -> int | None:
    """The population of a place as OpenStreetMap gives it ("5794", "5 794", "5,794", "ca. 5800", "5794;5800"), or None."""
    m = re.match(r"\D*?(\d{1,3}(?:[ ,.'’  ]\d{3})+|\d+)", str(tags.get("population", "")))
    return int(re.sub(r"\D", "", m[1])) if m else None


def fetch_places(tr: Track, radius: float) -> list:
    """Named cities, towns and villages within radius meters of the start, the most important first: a city before a town
    before a village; in a class the larger population first, then the places without one, the nearest first. Each as
    (x, y, tags)."""
    lat, lon = (float(v) for v in tr.proj.inv(tr.cx[0], tr.cy[0]))
    q = f'[out:json][timeout:60];node["place"~"^(city|town|village)$"]["name"](around:{int(radius)},{lat:.5f},{lon:.5f});out;'
    d = overpass(q, _("OpenStreetMap: downloading the places nearby ({server})"))
    out = []
    for e in (d or {}).get("elements", []):
        tags = e.get("tags", {})
        if tags.get("place") not in PLACE_CLASSES or "lat" not in e:
            continue
        x, y = (float(v) for v in tr.proj.fwd(e["lat"], e["lon"]))
        dist, pop = math.hypot(x - tr.cx[0], y - tr.cy[0]), population(tags)
        if dist <= radius:
            out.append(((PLACE_CLASSES.index(tags["place"]), pop is None, -(pop or 0), dist), x, y, tags))
    return [(x, y, tags) for _key, x, y, tags in sorted(out, key=lambda t_: t_[0])]


def osm_geoms(e: dict):
    if e.get("geometry"):
        yield e["geometry"]
    for m in e.get("members", []):
        if m.get("geometry") and m.get("role", "outer") != "inner":
            yield m["geometry"]


def country_info(tr: Track):
    """Country where the route starts: (Polish name, English name, ISO code) or None."""
    try:
        p = CACHE / "ne_50m_admin_0_countries.geojson"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(http_get(NE_URL))
        feat = find_country(json.loads(p.read_text(encoding="utf-8"))["features"], *(float(v) for v in tr.proj.inv(tr.cx[0], tr.cy[0])))[0]
        pr = feat["properties"]
        return pr.get("NAME_PL"), pr.get("NAME_EN"), pr.get("ISO_A2_EH") or pr.get("ISO_A2")
    except Exception:  # noqa: BLE001
        return None


def country_rings(feat: dict) -> list[np.ndarray]:
    g = feat["geometry"]
    return [np.asarray(p[0], float) for p in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])]


def find_country(feats: list, lat: float, lon: float):
    best = None
    with np.errstate(divide="ignore", invalid="ignore"):
        for f in feats:
            for r in country_rings(f):
                x, y = r[:, 0], r[:, 1]
                if not (x.min() <= lon <= x.max() and y.min() <= lat <= y.max()):
                    continue
                x2, y2 = np.roll(x, -1), np.roll(y, -1)
                if (((y > lat) != (y2 > lat)) & (lon < (x2 - x) * (lat - y) / (y2 - y) + x)).sum() % 2:
                    return f, r
                d = float(np.hypot((x - lon) * math.cos(math.radians(lat)), y - lat).min())
                if best is None or d < best[0]:
                    best = (d, f, r)
    return (best[1], best[2]) if best and best[0] < 0.5 else (None, None)   # just off the edge: the nearest country


def pick_name(tags: dict, prefs: list[str]) -> str | None:
    for l in prefs:
        if tags.get("name:" + l):
            return tags["name:" + l]
    nm = tags.get("name")
    if nm and re.search(r" [-/] ", nm):               # bilingual names: take the shorter part
        nm = min(re.split(r" [-/] ", nm), key=len)
    return nm


def find_pois(osm: dict, g: dict, tr: Track, lay: Layout, prefs: list[str], rename: dict, skip: set):
    KIND = {"hut": (90, 130), "bivouac": (62, 80), "peak": (85, 70), "saddle": (80, 70), "attr": (60, 60), "view": (55, 50), "place": (50, 200), "wpt": (100, 250),
            "parking": (0, -1)}                       # a parking only names an end of the route, it is never a label of its own
    cand = [dict(kind="wpt", name=n, lat=a, lon=b, ele=None) for a, b, n in g["wpts"]]
    for e in osm.get("elements", []):
        tg = e.get("tags", {})
        kind = ("peak" if tg.get("natural") == "peak" else "saddle" if tg.get("natural") == "saddle" or tg.get("mountain_pass") == "yes"
                else "hut" if tg.get("tourism") == "alpine_hut" else "bivouac" if tg.get("tourism") == "wilderness_hut" else "view" if tg.get("tourism") == "viewpoint"
                else "attr" if tg.get("tourism") == "attraction" else "place" if tg.get("place") else "parking" if tg.get("amenity") == "parking" else None)
        name = pick_name(tg, prefs)
        if not kind or not name or any(sk.lower() in name.lower() for sk in skip):
            continue
        name = rename.get(name, name)
        if kind in ("attr", "view") and len(name) > 26:   # long attraction names clutter the map
            continue
        if e["type"] == "node":
            la, lo = e["lat"], e["lon"]
        else:
            geo = [q for part in osm_geoms(e) for q in part]
            if not geo:
                continue
            la, lo = float(np.mean([q["lat"] for q in geo])), float(np.mean([q["lon"] for q in geo]))
        try:
            ele = float(str(tg.get("ele", "")).replace(",", ".").split()[0])
        except (ValueError, IndexError):
            ele = None
        cand.append(dict(kind=kind, name=name, lat=la, lon=lo, ele=ele, place=tg.get("place")))
    for c in cand:
        c["x"], c["y"] = (float(v) for v in tr.proj.fwd(c["lat"], c["lon"]))
        dd = np.hypot(tr.cx - c["x"], tr.cy - c["y"])
        c["i"] = int(dd.argmin()); c["off"] = float(dd[c["i"]]); c["prio"], c["lim"] = KIND[c["kind"]]
    return cand


END_PLACES = ((("hut",), 300.0), (("peak",), 100.0), (("saddle", "parking", "place", "view", "bivouac"), 200.0))
END_PLACE_KINDS = ("hamlet", "village", "suburb")      # the places (place=...) that may name an end of the route


def end_place(cands: list, x: float, y: float, taken: tuple = ()) -> dict | None:
    """The named place at an end of the route (x, y in meters) for its label, from the candidates of find_pois (already
    skipped and renamed): a hut within about 300 m; else a summit within about 100 m; else the nearest pass, parking, hamlet,
    village, part of a town, viewpoint or bivouac within about 200 m. None when there is none; taken are the names of
    places already used."""
    near = lambda c: math.hypot(c["x"] - x, c["y"] - y)
    for kinds, reach in END_PLACES:
        found = [c for c in cands if c["kind"] in kinds and near(c) <= reach and c["name"] not in taken
                 and (c["kind"] != "place" or c.get("place") in END_PLACE_KINDS)]
        if found:
            return min(found, key=near)
    return None


def end_places(cands: list, spots: dict) -> dict:
    """The named place of each end that has one (index of the end -> its (x, y) in meters), by end_place. The two ends
    never share a place: the nearer end keeps it, and the other looks again without it."""
    near = {vi: c for vi, (x, y) in spots.items() if (c := end_place(cands, x, y)) is not None}
    if len(near) == 2:
        (a, ca), (b, cb) = near.items()
        if ca["name"] == cb["name"]:
            off = lambda vi: math.hypot(near[vi]["x"] - spots[vi][0], near[vi]["y"] - spots[vi][1])
            far = a if off(a) > off(b) else b
            c = end_place(cands, *spots[far], (ca["name"],))
            if c is None:
                del near[far]
            else:
                near[far] = c
    return near


def place_name(lat: float, lon: float, lang: str):
    """Name of the town and the region from OpenStreetMap (Nominatim)."""
    p = CACHE / "geo" / f"{lat:.3f}_{lon:.3f}_{lang}.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(http_get(f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat:.5f}&lon={lon:.5f}&zoom=12&accept-language={lang}"))
    a = json.loads(p.read_text(encoding="utf-8")).get("address", {})
    return (a.get("village") or a.get("town") or a.get("city") or a.get("municipality") or a.get("hamlet"),
            a.get("state") or a.get("county") or a.get("country"))
