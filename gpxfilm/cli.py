r"""
gpxfilm – a film of a GPX route on a shaded terrain map (MP4, 4K at 60 fps by default).

Usage:
    gpxfilm route.gpx --title "Around\nTre Cime\ndi Lavaredo" --subtitle "Dolomites, October 4, 2026"
    gpxfilm route.gpx --frame-only preview.png         # quick preview of the last frame, no film
    gpxfilm route.gpx --intro                          # start with a view of the country and a camera flight to the region
    gpxfilm route.gpx --photos ~/Pictures/hike         # photos appear where they were taken
    gpxfilm route.gpx --draft                          # quick draft: 1280x720, 30 fps
    gpxfilm --gui                                      # settings window in the browser: preview and rendering
    gpxfilm route.gpx --map aerial                     # background from official aerial photos of the route's country

Settings used every time can be saved in ~/.config/gpxfilm.toml, e.g.:  color = "#FFB21E"   name_language = "it"   intro = true
    pipx run --spec . gpxfilm route.gpx                    # pipx installs the dependencies itself

Requires ffmpeg (preferably with the libx264 encoder): Fedora – sudo dnf install ffmpeg, macOS – brew install ffmpeg pipx.
Data downloaded from the network and kept in ~/.cache/gpxfilm (on macOS: ~/Library/Caches/gpxfilm):
terrain model from Mapterhorn (mapterhorn.com/attribution), trails, lakes and names from OpenStreetMap (Overpass),
country borders from Natural Earth, Big Shoulders Display and Figtree typefaces from Google Fonts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .compose import compose_scene, scrim_vignette
from .film import T_IN, T_OUT, encode, intro_camera, intro_seconds, plan_pause
from .geo import Layout, match_watch, prep_track, read_gpx, sun_position
from .i18n import LANGUAGES, N_, NC_, _, ngettext, pgettext, system_language, translator, ui
from .legacy import upgrade
from .net import CACHE
from .osm import country_info, fetch_osm, place_name
from .sources import AERIAL, MAPS, check_maps
from .terrain import LOOK, ground_elevation, relief
from .text import LABEL, film_texts
from .util import clamp, km1, log, to_u8
from .webgui import run_gui


SLIDE_MAP, SLIDE_FINISH = NC_("slide file name", "map"), NC_("slide file name", "finish")   # 00-map.png, 99-finish.png
ARGPARSE_TEXTS = (   # texts of argparse itself that our help and errors show; argparse would look for them in its own catalog
    N_("usage: "), N_("positional arguments"), N_("options"), N_("show this help message and exit"),
    N_("%(prog)s: error: %(message)s\n"), N_("unrecognized arguments: %s"), N_("expected one argument"),
    N_("invalid %(type)s value: %(value)r"), N_("invalid choice: %(value)r (choose from %(choices)s)"),
    N_("ambiguous option: %(option)s could match %(matches)s"), N_("ignored explicit argument %r"),
)


def main() -> None:
    argparse._, argparse.ngettext = ui().gettext, ui().ngettext   # argparse looks these up each time it shows a text
    ap =argparse.ArgumentParser(prog="gpxfilm", description=_("A film of a GPX route on a shaded terrain map."))
    ap.add_argument("gpx", type=Path, nargs="?", help=_("GPX file with the track"))
    ap.add_argument("--gui", action="store_true", help=_("open the settings window in the browser (preview and render the film without typing options)"))
    ap.add_argument("--check-maps", action="store_true", help=_("check which sources of aerial photos (--map aerial) answer right now, and exit"))
    ap.add_argument("--port", type=int, default=0, metavar=pgettext("placeholder of a number", "N"), help=_("port of the settings window (default: any free one)"))
    ap.add_argument("-o", "--output", type=Path, metavar=pgettext("placeholder", "FILE"), help=_("output MP4 file (default: the name of the GPX file with the .mp4 extension)"))
    ap.add_argument("--title", metavar=pgettext("placeholder", "TEXT"), help=_('title; separate its lines with "\\n" (default: the track name from the file)'))
    ap.add_argument("--subtitle", metavar=pgettext("placeholder", "TEXT"), help=_("line under the title (default: the date of the track)"))
    ap.add_argument("--size", default="3840x2160", metavar=pgettext("placeholder", "WxH"), help=_("16:9 frame size, e.g. 3840x2160 or 1920x1080"))
    ap.add_argument("--fps", type=int, default=60, metavar=pgettext("placeholder of a count", "N"), help=_("frames per second"))
    ap.add_argument("--route-duration", type=float, default=16.0, metavar=pgettext("placeholder", "S"), help=_("seconds the route takes to draw"))
    ap.add_argument("--color", default="#E5202E", metavar=pgettext("placeholder", "COLOR"), help=_("route color, e.g. #E5202E"))
    ap.add_argument("--places", type=int, default=6, metavar=pgettext("placeholder of a count", "N"), help=_("maximum number of labeled places along the route"))
    ap.add_argument("--peaks", type=int, default=3, metavar=pgettext("placeholder of a count", "N"), help=_("maximum number of labeled peaks off the route"))
    ap.add_argument("--intro-peaks", type=int, default=5, metavar=pgettext("placeholder of a count", "N"), help=_("how many of the highest peaks the intro shows, over the region and after the camera arrives (0 = none)"))
    ap.add_argument("--intro-places", type=int, default=4, metavar=pgettext("placeholder of a count", "N"), help=_("how many of the main places (cities, towns, villages) of the region the intro shows (0 = none)"))
    ap.add_argument("--intro-radius", type=float, default=20.0, metavar="KM", help=_("radius around the start within which the intro looks for the highest peaks and the main places"))
    ap.add_argument("--intro-label-duration", type=float, metavar=pgettext("placeholder", "S"), help=_("seconds the labels of the places and peaks of the intro stay fully visible, from the last one coming in until they start to fade, both during the pause over the region and after the camera arrives (0.5 to 20; without it about 1.3 s over the region and 0.8 s after the camera arrives)"))
    ap.add_argument("--language", choices=LANGUAGES, default=system_language(), help=_("language of the texts on the film"))
    ap.add_argument("--label-style", choices=["plain", "strong", "badges"], default="strong", help=_("labels on the map: plain, strong (thick dark outline) or badges (dark background behind the text)"))
    ap.add_argument("--label-size", type=float, default=1.0, metavar="X", help=_("scale of the labels on the map, from 0.7 to 2.0, e.g. 1.25"))
    ap.add_argument("--name-language", default="", metavar=pgettext("placeholder", "CODES"), help=_("preferred language of the names from OpenStreetMap, e.g. it or de,it"))
    ap.add_argument("--rename", action="append", default=[], metavar=pgettext("placeholder", "OLD=NEW"), help=_("rename a place on the film (can be given more than once)"))
    ap.add_argument("--skip", action="append", default=[], metavar=pgettext("placeholder", "NAME"), help=_("do not label places whose name contains this text (can be given more than once)"))
    ap.add_argument("--timezone", metavar=pgettext("placeholder", "ZONE"), help=_("time zone of the clock times on the film, e.g. Europe/Rome (default: the system time zone)"))
    ap.add_argument("--intro", action="store_true", help=_("start with a view of the whole country and fly the camera to the region of the route"))
    ap.add_argument("--intro-duration", type=float, default=6.5, metavar=pgettext("placeholder", "S"), help=_("seconds the camera flight takes"))
    ap.add_argument("--photos", type=Path, metavar=pgettext("placeholder", "DIR"), help=_("folder with photos: each one appears at the place where it was taken (by the time stored in the file)"))
    ap.add_argument("--photo-duration", type=float, default=2.5, metavar=pgettext("placeholder", "S"), help=_("seconds each photo is shown"))
    ap.add_argument("--photo-offset", type=float, default=0.0, metavar=pgettext("placeholder", "S"), help=_("correction of the camera clock in seconds (positive when the camera was behind)"))
    ap.add_argument("--photo-limit", type=int, default=15, metavar=pgettext("placeholder of a count", "N"), help=_("maximum number of photos on the film (0 = all)"))
    ap.add_argument("--stop-minutes", type=float, default=5.0, metavar="MIN", help=_("pause at stops of at least this many minutes (0 = off)"))
    ap.add_argument("--label-file", type=Path, metavar=pgettext("placeholder", "FILE"), help=_('your own labels, one per line: "46.6187, 12.3085 Name", "3.2 km Name" or "12:39 Name"'))
    ap.add_argument("--km-markers", action="store_true", help=_("kilometer markers along the route"))
    ap.add_argument("--distance", type=float, metavar="KM", help=_("distance from your watch in km: the film ends on this value "
                    "(rounded to 0.1 km, like every distance on the film), and the counters and kilometer markers follow it "
                    "(without this option the distance worked out from the track usually differs from the watch by about ±2%%)"))
    ap.add_argument("--ascent", type=float, metavar="M", help=_("total ascent from your watch in m: the film ends on this value"))
    ap.add_argument("--counters", action="store_true", help=_("extra live counters: time of day, elevation, speed and heart rate"))
    ap.add_argument("--no-heart-rate", action="store_true", help=_("leave out the heart rate, even if the file has it"))
    ap.add_argument("--color-by", choices=["slope", "speed", "heart-rate", "elevation"], help=_("color the route by data from the track"))
    ap.add_argument("--font", type=Path, metavar="TTF", help=_("your own typeface for the title and the numbers"))
    ap.add_argument("--logo", type=Path, metavar="PNG", help=_("logo in the top right corner of the frame"))
    ap.add_argument("--outro", action="store_true", help=_("end with a summary card"))
    ap.add_argument("--outro-duration", type=float, default=5.0, metavar=pgettext("placeholder", "S"), help=_("seconds the summary card lasts"))
    ap.add_argument("--music", type=Path, metavar=pgettext("placeholder", "FILE"), help=_("background music (looped and faded out at the end)"))
    ap.add_argument("--draft", action="store_true", help=_("quick draft: 1280x720 at 30 fps"))
    ap.add_argument("--slides", type=Path, metavar=pgettext("placeholder", "DIR"), help=_("save images for slides: the map alone, a frame at each labeled place and the final frame"))
    ap.add_argument("--map", default="terrain", metavar=pgettext("placeholder", "MAP"), help=_("map background: terrain (the default, shaded relief), topo (OpenTopoMap), aerial (official aerial photos of the route's country, very sharp), "
                    "satellite (Sentinel-2, whole world, 10 m) or your own tile address with {z}/{x}/{y} or a WMS address with {bbox}"))
    ap.add_argument("--map-style", choices=["natural", "night", "light", "gray"], default="natural", help=_("color style of the map"))
    ap.add_argument("--sunlight", action="store_true", help=_("light the map from the direction of the sun halfway along the route"))
    ap.add_argument("--minimap", action="store_true", help=_("small outline of the country with a marker of the place in a corner of the map"))
    ap.add_argument("--background-names", action="store_true", help=_("label lakes and towns in the background of the map"))
    ap.add_argument("--auto-title", action="store_true", help=_("when no title is given, take the name of the town and region from the map"))
    ap.add_argument("--report", type=Path, metavar="JSON", help=_("save a description of the film: labeled places, matched photos, expected length"))
    ap.add_argument("--frame", type=Path, metavar="PNG", help=_("also save the last frame as PNG"))
    ap.add_argument("--frame-only", type=Path, metavar="PNG", help=_("save only the last frame, no film"))
    ap.add_argument("--no-osm", action="store_true", help=_("do not download OpenStreetMap data"))
    ap.add_argument("--track", type=int, metavar=pgettext("placeholder of a number", "N"), help=_("draw only the track or route with this number when the file has several (the number from the list the program prints)"))
    ap.add_argument("--keep-times", action="store_true", help=_("use the times from the GPX file even if they look fake "
                    "(e.g. a planned route with times from 1970 or a ride faster than 400 km/h)"))
    ap.add_argument("--crf", type=int, default=16, metavar=pgettext("placeholder of a count", "N"), help=_("encoding quality (lower = better, larger file)"))
    ap.add_argument("--encoder", metavar=pgettext("placeholder", "NAME"), help=_("force an ffmpeg encoder, e.g. libx264"))
    cfg_p = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gpxfilm.toml"
    if cfg_p.exists():                                # the user's fixed settings: names as the options, without the leading dashes
        try:
            import tomllib
            text = cfg_p.read_text(encoding="utf-8")
            cfg, changes = upgrade(tomllib.loads(text))
            if changes:                               # old names still work; say once (until the file changes) what to write instead
                seen, mark = CACHE / "settings-names", hashlib.sha1(text.encode()).hexdigest()
                if not (seen.exists() and seen.read_text(encoding="utf-8").strip() == mark):
                    log(_("The settings file {file} uses old names. They work, but replace them with the new ones:\n{changes}\n"
                          "This notice appears again only after the file changes.").format(
                        file=cfg_p, changes="\n".join(f"  {old}  →  {new}" for old, new in changes)))
                    try:
                        seen.parent.mkdir(parents=True, exist_ok=True)
                        seen.write_text(mark, encoding="utf-8")
                    except OSError:
                        pass
            types = {a.dest: a.type for a in ap._actions}
            ap.set_defaults(**{k: (types[k](v) if types[k] and isinstance(v, str) else v) for k, v in cfg.items() if k in types})
            if set(cfg) - set(types):
                log(_("Unknown settings in {file}: {names}").format(file=cfg_p, names=", ".join(sorted(set(cfg) - set(types)))))
        except ModuleNotFoundError:
            log(_("The settings file needs Python 3.11 or newer – skipping it."))
    A = ap.parse_args()
    if A.distance is not None and not (math.isfinite(A.distance) and 0 < A.distance <= 100000):
        ap.error(_("--distance must be a number greater than zero (in km, at most 100000)"))
    if A.ascent is not None and not (math.isfinite(A.ascent) and 0 <= A.ascent <= 1000000):
        ap.error(_("--ascent must be a non-negative number (in m, at most 1000000)"))
    if A.intro_peaks < 0:
        ap.error(_("--intro-peaks must be a non-negative number"))
    if A.intro_places < 0:
        ap.error(_("--intro-places must be a non-negative number"))
    if not (math.isfinite(A.intro_radius) and 0 < A.intro_radius <= 100):
        ap.error(_("--intro-radius must be a number greater than zero (in km, at most 100)"))
    if A.intro_label_duration is not None and not (math.isfinite(A.intro_label_duration) and 0.5 <= A.intro_label_duration <= 20):
        ap.error(_("--intro-label-duration must be a number from 0.5 to 20 (in seconds)"))
    if not (math.isfinite(A.label_size) and 0.7 <= A.label_size <= 2.0):
        ap.error(_("--label-size must be a number from 0.7 to 2.0"))
    if A.draft:
        A.size, A.fps = "1280x720", 30
    if A.check_maps:
        return check_maps()
    if A.gui:
        return run_gui(A, {a.dest: ap.get_default(a.dest) for a in ap._actions})
    if A.gpx is None:
        ap.error(_("give a GPX file or run with the --gui option"))

    W, H = (int(v) for v in A.size.lower().split("x"))
    if abs(W / H - 16 / 9) > 0.01:
        sys.exit(_("The frame must have a 16:9 aspect ratio."))
    if A.map not in ("terrain", "aerial", *MAPS) and "{z}" not in A.map and "{bbox}" not in A.map:
        sys.exit(_("Unknown map background. Choose terrain, topo, aerial, satellite or a tile address with {z}, {x} and {y}."))
    L = film_texts(A.language)
    acc = tuple(int(A.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    if A.timezone:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(A.timezone)
    else:
        tz = None
    g = read_gpx(A.gpx, keep_times=A.keep_times, track=A.track)
    tr = prep_track(g)
    ground_elevation(tr)
    match_watch(tr, A.distance, None)
    if A.no_heart_rate:
        tr.has_hr = False

    def dist_at_time(ts: float) -> float:             # position on the route (in meters) at the given second from the start
        ts = clamp(ts, 0, tr.total)
        i_ = int(np.clip(np.searchsorted(tr.tin, ts, "right") - 1, 0, len(tr.cd) - 2))
        return float(tr.cd[i_] if ts <= tr.tout[i_] else tr.cd[i_] + (tr.cd[i_ + 1] - tr.cd[i_]) * clamp((ts - tr.tout[i_]) / max(tr.tin[i_ + 1] - tr.tout[i_], 1e-6)))

    if A.label_file:                                     # custom labels: coordinates, kilometer or time of day, then the name
        for ln in A.label_file.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            m1 = re.match(r"(-?\d+[.,]\d+)\s*[,; ]\s*(-?\d+[.,]\d+)\s+(.+)", ln)
            m2 = re.match(r"(\d+(?:[.,]\d+)?)\s*km\s+(.+)", ln, re.I)
            m3 = re.match(r"(\d{1,2}):(\d\d)\s+(.+)", ln)
            if m1:
                g["wpts"].append((float(m1[1].replace(",", ".")), float(m1[2].replace(",", ".")), m1[3]))
                continue
            if m2:
                d_, nm_ = float(m2[1].replace(",", ".")) * 1000, m2[2]
            elif m3 and tr.has_t:
                day = tr.t0.astimezone(tz)
                d_, nm_ = dist_at_time((day.replace(hour=int(m3[1]), minute=int(m3[2]), second=0, microsecond=0) - tr.t0).total_seconds()), m3[3]
            else:
                log("  " + _("label line not understood: {line}").format(line=ln))
                continue
            i_ = int(np.argmin(np.abs(tr.cd - (np.interp(d_, tr.rd, tr.cd) if m2 else d_))))   # kilometers as shown on the film
            g["wpts"].append((*(float(v) for v in tr.proj.inv(tr.cx[i_], tr.cy[i_])), nm_))
    lay = Layout(tr, W, H)
    country = country_info(tr) if A.map == "aerial" or not A.no_osm else None
    country_name = (country[0] if ui().language == "pl" else country[1] or country[0]) if country else None   # in the language of the messages and the window
    if A.map == "aerial" and not AERIAL.get(country[2] if country else ""):
        log(_("No open aerial photos are known for this country ({country}) – using Sentinel-2 satellite imagery.").format(
            country=country_name or _("it could not be determined")))
        A.map = "satellite"
    LOOK["style"], LOOK["map"] = A.map_style, A.map
    LABEL["style"], LABEL["scale"] = A.label_style, A.label_size
    LOOK["sources"] = (() if A.map == "terrain" else tuple(AERIAL[country[2]]) if A.map == "aerial" else (MAPS[A.map],) if A.map in MAPS
                      else ((A.map, 19, N_("Map: user-supplied background. Terrain: Mapterhorn")),))
    if A.sunlight and tr.has_t:
        saz, salt = sun_position(tr.proj.lat0, tr.proj.lon0, tr.t0 + dt.timedelta(seconds=tr.total / 2))
        LOOK["light"] = (saz, clamp(salt, 15.0, 65.0))
        log(_("Sun halfway along the route: azimuth {azimuth}°, altitude {altitude}°").format(azimuth=f"{saz:.0f}", altitude=f"{salt:.0f}"))
    region = None
    if A.auto_title and not A.title:
        try:
            place, region = place_name(tr.proj.lat0, tr.proj.lon0, A.language)
            A.title = place or A.title
        except Exception as e:  # noqa: BLE001
            log(_("Could not get the place name: {error}").format(error=e))
    osm = {"elements": []} if A.no_osm else fetch_osm(tr, lay)
    log(_("Drawing the background…"))
    sv = scrim_vignette(W, H)
    wide8 = None
    if A.intro and not A.frame_only:                  # map of the region with a margin all around, needed for the camera flight
        mx, my = int(0.15 * W), int(0.15 * H)
        cm = [float(v) for v in lay.meters(960, 540)]
        sw = lay.S * W / (W + 2 * mx)
        wide, Dw = relief(tr.proj, Layout.view(W + 2 * mx, H + 2 * my, sw, 960 - sw * cm[0], 540 - sw * cm[1]), osm, tiles=True)
        raw, D, wide8 = wide[my:my + H, mx:mx + W], Dw[my:my + H, mx:mx + W], to_u8(wide)
        del wide, Dw
    else:
        raw, D = relief(tr.proj, lay, osm, tiles=True)
    base = to_u8(raw * sv[..., None])
    clean = base.copy() if A.slides or A.outro else None
    del raw

    sc = compose_scene(A, g, tr, lay, base, D, osm, country, region, tz, L, acc)
    render, chosen, events, photos, n_files, prefs, shown_peaks = \
        sc.render, sc.chosen, sc.events, sc.photos, sc.n_files, sc.prefs, sc.shown_peaks
    if A.slides:                                       # images for slides
        A.slides.mkdir(parents=True, exist_ok=True)
        t = translator(A.language)                    # file names in the language of the film
        Image.fromarray(clean).save(A.slides / f"00-{t.pgettext(*SLIDE_MAP)}.png")
        for n_, c in enumerate(chosen, 1):
            slug = re.sub(r"[^\w]+", "-", c["name"]).strip("-").lower()
            Image.fromarray(render(min(c["f"] + 0.013, 1.0))).save(A.slides / f"{n_:02d}-{slug}.png")
        Image.fromarray(render(1.0)).save(A.slides / f"99-{t.pgettext(*SLIDE_FINISH)}.png")
        log(ngettext("Saved images for slides: {folder} ({n} file)", "Saved images for slides: {folder} ({n} files)", len(chosen) + 2)
            .format(folder=A.slides, n=len(chosen) + 2))
    flight, pause, guess = None, (None, []), False
    if A.intro and (A.report or not A.frame_only):    # the camera flight of the intro and its pause over the region
        try:
            flight = intro_camera(tr, lay, A.language)
            pause = plan_pause(A, tr, flight, sc, max(1, int(A.intro_duration * A.fps)))
        except Exception as e:  # noqa: BLE001 – a still frame does not need the flight; the film does
            if not A.frame_only:
                raise
            log(_("Intro: could not plan the camera flight ({error}) – the film length in the report is approximate.").format(error=e))
            guess = True
    if A.report:                                      # description of the film for the settings window (or for the curious)
        intro_s = (0.4 + 2.4 + A.intro_duration + 1.0 + intro_seconds(A, pause, sc.intro_pk)) if flight is not None or guess else T_IN
        est = intro_s + A.route_duration + T_OUT + ((0.8 + A.outro_duration) if A.outro else 0) \
            + sum(1.4 if e[2] == "stop" else 0.65 + len(e[3]) * A.photo_duration + (len(e[3]) - 1) * 0.4 for e in events)
        A.report.write_text(json.dumps(dict(
            places=[dict(name=c["name"], km=km1(float(tr.rd[c["i"]]))) for c in chosen], peaks=shown_peaks,
            photos=dict(matched=len(photos), total=n_files), stops=sum(1 for e in events if e[2] == "stop"),
            duration=round(est, 1), distance_km=km1(tr.rdist), country=country_name, name_languages=prefs), ensure_ascii=False), encoding="utf-8")
    still = A.frame_only or A.frame
    if still:
        Image.fromarray(render(1.0)).save(still)
        log(_("Saved the frame: {file}").format(file=still))
    if A.frame_only:
        return
    encode(A, sc, tr, lay, wide8, sv, clean, L, acc, flight, pause)
