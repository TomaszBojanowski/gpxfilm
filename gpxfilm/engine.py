"""The engine as its callers use it: prepare a film from the settings, then draw a frame, describe the film or render it.

    project = Project()
    p = prepare(project, settings, film=True)
    frame = render_still(p)                       # the whole route drawn
    frame = frame_at(p, 12.5)                     # the frame 12.5 s into the film
    render_film(p, Path("route.mp4"))

Nothing here prints or ends the program: messages and progress go to the task the call runs in, a cancelled task stops at
the next tile or frame, and an error meant for the user is raised as Failure (see task.py). A Project remembers what was
worked out for the last settings, stage by stage (the track, the map data, the background, the labels, the camera
flight), so a change of one setting works out again only what depends on it.
"""
from __future__ import annotations

import copy
import datetime as dt
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

from .compose import compose_scene, scrim_vignette
from .film import T_IN, T_OUT, Film, encode, ffmpeg_command, frames_folder, intro_camera, intro_seconds, plan_pause
from .geo import Layout, Track, match_watch, prep_track, read_gpx, sun_position
from .i18n import N_, NC_, _, ngettext, translator, ui
from .osm import country_info, fetch_osm, place_name
from .settings import Settings
from .sources import AERIAL, MAPS
from .task import Failure, muted
from .terrain import Look, ground_elevation, relief
from .text import film_texts
from .util import clamp, km1, log, to_u8

SLIDE_MAP, SLIDE_FINISH = NC_("slide file name", "map"), NC_("slide file name", "finish")   # 00-map.png, 99-finish.png
FILM_ONLY = dict(fps=0, route_duration=0.0, intro_duration=0.0, intro_label_duration=None, photo_duration=0.0, outro_duration=0.0,
                 crf=0, codec=None, encoder=None, music=None)    # settings that change the film but not the picture of the map and its labels


class Project:
    """What has been worked out for a film so far. Each stage keeps its last result with the key it was made for: asked
    for the same key again it gives the result back, asked for another it works it out anew and forgets the old one."""

    def __init__(self):
        self.memo: dict[str, tuple] = {}

    def once(self, stage: str, key, make):
        kept = self.memo.get(stage)
        if kept is None or kept[0] != key:
            kept = self.memo[stage] = (key, make())
        return kept[1]


@dataclass
class Prepared:
    """A film ready to draw: the settings as they are used (the title from the map filled in, satellite imagery where there
    are no aerial photos) and everything worked out from them."""
    project: Project
    key: tuple                           # of the picture: what the map and its labels depend on
    settings: Settings
    film: bool                           # prepared for a film (with the wide map of the camera flight), not just for a still frame
    g: dict
    tr: Track
    lay: Layout
    texts: dict                          # the fixed texts of the film, in its language
    color: tuple
    look: Look                           # of the map, as its background was drawn
    vignette: np.ndarray
    clean: np.ndarray                    # the background alone, before the labels
    wide: np.ndarray | None              # the background with a margin all around, for the camera flight
    scene: SimpleNamespace               # from compose_scene: render(p) and what it placed on the map
    country: str | None                  # the name of the country, in the language of the messages
    flight: tuple | None = None          # what plan_flight found, once it has been asked


def _file_key(path: Path | None):
    if path is None:
        return None
    try:
        st = path.stat()
        return str(path), st.st_mtime_ns, st.st_size
    except OSError:
        return str(path), None, None


def _track_key(S: Settings) -> tuple:
    """What the track read from the file depends on."""
    return _file_key(S.gpx), S.keep_times, S.track, S.distance, S.no_heart_rate, _file_key(S.label_file), S.timezone


def _own_labels(S: Settings, g: dict, tr: Track, tz) -> None:
    """Add the labels of --label-file to the waypoints: coordinates, a kilometer or a time of day, then the name."""

    def dist_at_time(ts: float) -> float:             # position on the route (in meters) at the given second from the start
        ts = clamp(ts, 0, tr.total)
        i_ = int(np.clip(np.searchsorted(tr.tin, ts, "right") - 1, 0, len(tr.cd) - 2))
        return float(tr.cd[i_] if ts <= tr.tout[i_] else tr.cd[i_] + (tr.cd[i_ + 1] - tr.cd[i_]) * clamp((ts - tr.tout[i_]) / max(tr.tin[i_ + 1] - tr.tout[i_], 1e-6)))

    for ln in S.label_file.read_text(encoding="utf-8").splitlines():
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


def prepare(project: Project, S: Settings, film: bool = True) -> Prepared:
    """Work out everything the frames of the film are drawn from. film=False prepares a still frame only: without the wide
    map that the camera flight of the intro needs."""
    W, H = (int(v) for v in S.size.lower().split("x"))
    if abs(W / H - 16 / 9) > 0.01:
        raise Failure(_("The frame must have a 16:9 aspect ratio."))
    if S.map not in ("terrain", "aerial", *MAPS) and "{z}" not in S.map and "{bbox}" not in S.map:
        raise Failure(_("Unknown map background. Choose terrain, topo, aerial, satellite or a tile address with {z}, {x} and {y}."))
    L = film_texts(S.language)
    acc = tuple(int(S.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    if S.timezone:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(S.timezone)
    else:
        tz = None

    def track():
        g = read_gpx(S.gpx, keep_times=S.keep_times, track=S.track)
        tr = prep_track(g)
        ground_elevation(tr)
        match_watch(tr, S.distance, None)
        if S.no_heart_rate:
            tr.has_hr = False
        if S.label_file:
            _own_labels(S, g, tr, tz)
        return g, tr

    k_track = _track_key(S)
    g, tr = project.once("track", k_track, track)
    lay = Layout(tr, W, H)
    country = project.once("country", k_track, lambda: country_info(tr)) if S.map == "aerial" or not S.no_osm else None
    country_name = (country[0] if ui().language == "pl" else country[1] or country[0]) if country else None   # in the language of the messages and the window
    if S.map == "aerial" and not AERIAL.get(country[2] if country else ""):
        log(_("No open aerial photos are known for this country ({country}) – using Sentinel-2 satellite imagery.").format(
            country=country_name or _("it could not be determined")))
        S = replace(S, map="satellite")
    look = Look(style=S.map_style, map=S.map, sources=(() if S.map == "terrain" else tuple(AERIAL[country[2]]) if S.map == "aerial" else (MAPS[S.map],) if S.map in MAPS
                      else ((S.map, 19, N_("Map: user-supplied background. Terrain: Mapterhorn")),)))
    if S.sunlight and tr.has_t:
        saz, salt = sun_position(tr.proj.lat0, tr.proj.lon0, tr.t0 + dt.timedelta(seconds=tr.total / 2))
        look = replace(look, light=(saz, clamp(salt, 15.0, 65.0)))
        log(_("Sun halfway along the route: azimuth {azimuth}°, altitude {altitude}°").format(azimuth=f"{saz:.0f}", altitude=f"{salt:.0f}"))
    region = None
    if S.auto_title and not S.title:
        try:
            place, region = project.once("place", (k_track, S.language), lambda: place_name(tr.proj.lat0, tr.proj.lon0, S.language))
            S = replace(S, title=place or S.title)
        except Exception as e:  # noqa: BLE001
            log(_("Could not get the place name: {error}").format(error=e))
    k_frame = (k_track, W, H)
    osm = {"elements": []} if S.no_osm else project.once("osm", k_frame, lambda: fetch_osm(tr, lay))
    wide_map = S.intro and film                       # map of the region with a margin all around, needed for the camera flight

    def background():
        log(_("Drawing the background…"))
        sv = scrim_vignette(W, H)
        wide8 = None
        if wide_map:
            mx, my = int(0.15 * W), int(0.15 * H)
            cm = [float(v) for v in lay.meters(960, 540)]
            sw = lay.S * W / (W + 2 * mx)
            wide, Dw, drawn = relief(tr.proj, Layout.view(W + 2 * mx, H + 2 * my, sw, 960 - sw * cm[0], 540 - sw * cm[1]), osm, look, tiles=True)
            raw, D, wide8 = wide[my:my + H, mx:mx + W], Dw[my:my + H, mx:mx + W], to_u8(wide)
            del wide, Dw
        else:
            raw, D, drawn = relief(tr.proj, lay, osm, look, tiles=True)
        return sv, to_u8(raw * sv[..., None]), D, wide8, drawn

    k_back = (k_frame, S.no_osm, look, wide_map)
    sv, clean, D, wide8, look = project.once("background", k_back, background)

    def scene():                                      # composing draws on the background and corrects the track: both get their own copies
        g2, tr2 = copy.deepcopy((g, tr))
        return g2, tr2, compose_scene(S, g2, tr2, lay, clean.copy(), D, osm, country, region, tz, L, acc, look.credit)

    k_scene = (k_back, replace(S, **FILM_ONLY))
    g2, tr2, sc = project.once("scene", k_scene, scene)
    return Prepared(project=project, key=k_scene, settings=S, film=film, g=g2, tr=tr2, lay=lay, texts=L, color=acc, look=look, vignette=sv,
                    clean=clean, wide=wide8, scene=sc, country=country_name)


ROUGH_GROUND = (58, 66, 62)              # the plain background of a rough frame


def rough_still(project: Project, S: Settings) -> np.ndarray | None:
    """A rough picture of the last frame, at once and from what is at hand: nothing is downloaded and nothing is said.
    The route is a line in its place in the frame, on the background this project drew last when that was for the same
    track and frame size, else on a plain one. None when even that cannot be drawn (the file is not a track, say):
    prepare tells why."""
    try:
        W, H = (int(v) for v in S.size.lower().split("x"))
        k_track = _track_key(S)
        kept = project.memo.get("track")
        if kept is not None and kept[0] == k_track:
            tr = kept[1][1]
        else:                                         # the file alone: no terrain model for its elevation
            with muted():
                tr = project.once("rough", k_track, lambda: prep_track(read_gpx(S.gpx, keep_times=S.keep_times, track=S.track)))
        lay, back = Layout(tr, W, H), project.memo.get("background")
        if back is not None and back[0][0] == (k_track, W, H):
            im = Image.fromarray(back[1][1])
        else:
            im = Image.new("RGB", (W, H), ROUGH_GROUND)
        x, y = lay.des(tr.cx, tr.cy)
        pts = [(float(a) * lay.K, float(b) * lay.K) for a, b in zip(x, y)]
        d = ImageDraw.Draw(im)
        d.line(pts, fill=(14, 17, 19), width=max(3, round(10 * lay.K)), joint="curve")
        d.line(pts, fill=tuple(int(S.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)), width=max(2, round(6 * lay.K)), joint="curve")
        return np.asarray(im)
    except Exception:  # noqa: BLE001 – a rough frame is a courtesy; the real one reports what is wrong
        return None


def plan_flight(p: Prepared, strict: bool = True) -> tuple:
    """The camera flight of the intro and its pause over the region: (flight, pause, guess). Without an intro, or when the
    route takes up too much of its country, flight is None. When the flight cannot be planned (no network, say), strict
    raises the error; otherwise it is reported and guess is True: there will be a flight, its length is an estimate."""
    S = p.settings
    if p.flight is not None:
        return p.flight
    if not S.intro:
        return None, (None, []), False

    def plan():
        flight = intro_camera(p.tr, p.lay, S.language)
        return flight, plan_pause(S, p.tr, flight, p.scene, max(1, int(S.intro_duration * S.fps)))

    try:
        p.flight = (*p.project.once("flight", (p.key, S), plan), False)
    except Exception as e:  # noqa: BLE001 – a still frame does not need the flight; the film does
        if strict:
            raise
        log(_("Intro: could not plan the camera flight ({error}) – the film length in the report is approximate.").format(error=e))
        p.flight = None, (None, []), True
    return p.flight


def render_still(p: Prepared) -> np.ndarray:
    """The last frame of the route: the whole route drawn, all its labels shown."""
    return p.scene.render(1.0)


def report(p: Prepared) -> dict:
    """A description of the film: the labeled places and peaks, the matched photos, the stops and the expected length."""
    S, sc, tr = p.settings, p.scene, p.tr
    flight, pause, guess = plan_flight(p, strict=p.film)
    intro_s = (0.4 + 2.4 + S.intro_duration + 1.0 + intro_seconds(S, pause, sc.intro_pk)) if flight is not None or guess else T_IN
    est = intro_s + S.route_duration + T_OUT + ((0.8 + S.outro_duration) if S.outro else 0) \
        + sum(1.4 if e[2] == "stop" else 0.65 + len(e[3]) * S.photo_duration + (len(e[3]) - 1) * 0.4 for e in sc.events)
    return dict(
        places=[dict(name=c["name"], km=km1(float(tr.rd[c["i"]]))) for c in sc.chosen], peaks=sc.shown_peaks,
        photos=dict(matched=len(sc.photos), total=sc.n_files), stops=sum(1 for e in sc.events if e[2] == "stop"),
        duration=round(est, 1), distance_km=km1(tr.rdist), country=p.country, name_languages=sc.prefs)


def save_slides(p: Prepared, folder: Path) -> None:
    """Images for slides: the map alone, a frame at each labeled place and the final frame."""
    render, chosen = p.scene.render, p.scene.chosen
    folder.mkdir(parents=True, exist_ok=True)
    t = translator(p.settings.language)               # file names in the language of the film
    Image.fromarray(p.clean).save(folder / f"00-{t.pgettext(*SLIDE_MAP)}.png")
    for n_, c in enumerate(chosen, 1):
        slug = re.sub(r"[^\w]+", "-", c["name"]).strip("-").lower()
        Image.fromarray(render(min(c["f"] + 0.013, 1.0))).save(folder / f"{n_:02d}-{slug}.png")
    Image.fromarray(render(1.0)).save(folder / f"99-{t.pgettext(*SLIDE_FINISH)}.png")
    log(ngettext("Saved images for slides: {folder} ({n} file)", "Saved images for slides: {folder} ({n} files)", len(chosen) + 2)
        .format(folder=folder, n=len(chosen) + 2))


def timeline(p: Prepared) -> Film:
    """The film as a timeline: its segments, and any of its frames by number or by time."""
    flight, pause, _guess = plan_flight(p)
    return p.project.once("film", (p.key, p.settings, p.film), lambda: Film(
        p.settings, p.scene, p.tr, p.lay, p.wide, p.vignette, p.clean, p.texts, p.color, p.look, flight, pause))


def frame_at(p: Prepared, t: float) -> np.ndarray:
    """The frame shown t seconds into the film. Read it, do not draw on it: the film may keep it for the next frames."""
    return timeline(p).frame_at(t)


def render_film(p: Prepared, out: Path) -> None:
    """Render every frame of the film and encode it into out. The file out appears, or replaces an older film, only when
    the whole film is encoded; a stopped or failed export leaves no file of its own."""
    plan_flight(p)
    cmd = None if frames_folder() else ffmpeg_command(p.settings, out, p.lay)   # before the work on the intro: no ffmpeg, no film
    encode(timeline(p), out, cmd)
