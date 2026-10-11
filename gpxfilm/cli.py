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
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image

from .engine import Project, plan_flight, prepare, render_film, render_still, report, save_slides
from .i18n import LANGUAGES, N_, _, pgettext, ui
from .legacy import upgrade
from .net import CACHE
from .settings import Settings
from .sources import check_maps
from .task import Console, Failure, Task, running
from .util import log
from .webgui import run_gui


ARGPARSE_TEXTS = (   # texts of argparse itself that our help and errors show; argparse would look for them in its own catalog
    N_("usage: "), N_("positional arguments"), N_("options"), N_("show this help message and exit"),
    N_("%(prog)s: error: %(message)s\n"), N_("unrecognized arguments: %s"), N_("expected one argument"),
    N_("invalid %(type)s value: %(value)r"), N_("invalid choice: %(value)r (choose from %(choices)s)"),
    N_("ambiguous option: %(option)s could match %(matches)s"), N_("ignored explicit argument %r"),
)


def main() -> None:
    """The command: the engine reports to the terminal, and an error meant for the user ends the program with its message."""
    try:
        with running(Task(Console())):
            run()
    except Failure as e:
        sys.exit(str(e))


def options(argv: list[str] | None = None, raising: bool = False) -> tuple[argparse.Namespace, dict]:
    """The options of the command (argv; by default those the program was started with), with the settings file read and
    the numbers checked, and the default of every option. A wrong option ends the program with the usage and the error,
    as argparse does; with raising it is a Failure with that text instead, for a window that reads options in its own
    process."""
    argparse._, argparse.ngettext = ui().gettext, ui().ngettext   # argparse looks these up each time it shows a text
    D = Settings()                                    # the defaults of the options
    ap =argparse.ArgumentParser(prog="gpxfilm", description=_("A film of a GPX route on a shaded terrain map."))
    ap.add_argument("gpx", type=Path, nargs="?", help=_("GPX file with the track"))
    ap.add_argument("--gui", action="store_true", help=_("open the settings window in the browser (preview and render the film without typing options)"))
    ap.add_argument("--check-maps", action="store_true", help=_("check which sources of aerial photos (--map aerial) answer right now, and exit"))
    ap.add_argument("--port", type=int, default=0, metavar=pgettext("placeholder of a number", "N"), help=_("port of the settings window (default: any free one)"))
    ap.add_argument("-o", "--output", type=Path, metavar=pgettext("placeholder", "FILE"), help=_("output MP4 file (default: the name of the GPX file with the .mp4 extension)"))
    ap.add_argument("--title", metavar=pgettext("placeholder", "TEXT"), help=_('title; separate its lines with "\\n" (default: the track name from the file)'))
    ap.add_argument("--subtitle", metavar=pgettext("placeholder", "TEXT"), help=_("line under the title (default: the date of the track)"))
    ap.add_argument("--size", default=D.size, metavar=pgettext("placeholder", "WxH"), help=_("16:9 frame size, e.g. 3840x2160 or 1920x1080"))
    ap.add_argument("--fps", type=int, default=D.fps, metavar=pgettext("placeholder of a count", "N"), help=_("frames per second"))
    ap.add_argument("--route-duration", type=float, default=D.route_duration, metavar=pgettext("placeholder", "S"), help=_("seconds the route takes to draw"))
    ap.add_argument("--color", default=D.color, metavar=pgettext("placeholder", "COLOR"), help=_("route color, e.g. #E5202E"))
    ap.add_argument("--places", type=int, default=D.places, metavar=pgettext("placeholder of a count", "N"), help=_("maximum number of labeled places along the route"))
    ap.add_argument("--peaks", type=int, default=D.peaks, metavar=pgettext("placeholder of a count", "N"), help=_("maximum number of labeled peaks off the route"))
    ap.add_argument("--intro-peaks", type=int, default=D.intro_peaks, metavar=pgettext("placeholder of a count", "N"), help=_("how many of the highest peaks the intro shows, over the region and after the camera arrives (0 = none)"))
    ap.add_argument("--intro-places", type=int, default=D.intro_places, metavar=pgettext("placeholder of a count", "N"), help=_("how many of the main places (cities, towns, villages) of the region the intro shows (0 = none)"))
    ap.add_argument("--intro-radius", type=float, default=D.intro_radius, metavar="KM", help=_("radius around the start within which the intro looks for the highest peaks and the main places"))
    ap.add_argument("--intro-label-duration", type=float, metavar=pgettext("placeholder", "S"), help=_("seconds the labels of the places and peaks of the intro stay fully visible, from the last one coming in until they start to fade, both during the pause over the region and after the camera arrives (0.5 to 20; without it about 1.3 s over the region and 0.8 s after the camera arrives)"))
    ap.add_argument("--language", choices=LANGUAGES, default=D.language, help=_("language of the texts on the film"))
    ap.add_argument("--label-style", choices=["plain", "strong", "badges"], default=D.label_style, help=_("labels on the map: plain, strong (thick dark outline) or badges (dark background behind the text)"))
    ap.add_argument("--label-size", type=float, default=D.label_size, metavar="X", help=_("scale of the labels on the map, from 0.7 to 2.0, e.g. 1.25"))
    ap.add_argument("--name-language", default=D.name_language, metavar=pgettext("placeholder", "CODES"), help=_("preferred language of the names from OpenStreetMap, e.g. it or de,it"))
    ap.add_argument("--rename", action="append", default=[], metavar=pgettext("placeholder", "OLD=NEW"), help=_("rename a place on the film (can be given more than once)"))
    ap.add_argument("--skip", action="append", default=[], metavar=pgettext("placeholder", "NAME"), help=_("do not label places whose name contains this text (can be given more than once)"))
    ap.add_argument("--timezone", metavar=pgettext("placeholder", "ZONE"), help=_("time zone of the clock times on the film, e.g. Europe/Rome (default: the system time zone)"))
    ap.add_argument("--intro", action="store_true", help=_("start with a view of the whole country and fly the camera to the region of the route"))
    ap.add_argument("--intro-duration", type=float, default=D.intro_duration, metavar=pgettext("placeholder", "S"), help=_("seconds the camera flight takes"))
    ap.add_argument("--photos", type=Path, metavar=pgettext("placeholder", "DIR"), help=_("folder with photos: each one appears at the place where it was taken (by the time stored in the file)"))
    ap.add_argument("--photo-duration", type=float, default=D.photo_duration, metavar=pgettext("placeholder", "S"), help=_("seconds each photo is shown"))
    ap.add_argument("--photo-offset", type=float, default=D.photo_offset, metavar=pgettext("placeholder", "S"), help=_("correction of the camera clock in seconds (positive when the camera was behind)"))
    ap.add_argument("--photo-limit", type=int, default=D.photo_limit, metavar=pgettext("placeholder of a count", "N"), help=_("maximum number of photos on the film (0 = all)"))
    ap.add_argument("--stop-minutes", type=float, default=D.stop_minutes, metavar="MIN", help=_("pause at stops of at least this many minutes (0 = off)"))
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
    ap.add_argument("--outro-duration", type=float, default=D.outro_duration, metavar=pgettext("placeholder", "S"), help=_("seconds the summary card lasts"))
    ap.add_argument("--music", type=Path, metavar=pgettext("placeholder", "FILE"), help=_("background music (looped and faded out at the end)"))
    ap.add_argument("--draft", action="store_true", help=_("quick draft: 1280x720 at 30 fps"))
    ap.add_argument("--slides", type=Path, metavar=pgettext("placeholder", "DIR"), help=_("save images for slides: the map alone, a frame at each labeled place and the final frame"))
    ap.add_argument("--map", default=D.map, metavar=pgettext("placeholder", "MAP"), help=_("map background: terrain (the default, shaded relief), topo (OpenTopoMap), aerial (official aerial photos of the route's country, very sharp), "
                    "satellite (Sentinel-2, whole world, 10 m) or your own tile address with {z}/{x}/{y} or a WMS address with {bbox}"))
    ap.add_argument("--map-style", choices=["natural", "night", "light", "gray"], default=D.map_style, help=_("color style of the map"))
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
    ap.add_argument("--crf", type=int, default=D.crf, metavar=pgettext("placeholder of a count", "N"), help=_("encoding quality (lower = better, larger file)"))
    ap.add_argument("--codec", choices=["h264", "hevc"], help=_("codec of the film: h264 (plays everywhere, the default) or hevc (a smaller file, for newer devices)"))
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
    if raising:
        def error(message: str):
            usage = io.StringIO()
            ap.print_usage(usage)
            raise Failure(usage.getvalue() + (argparse._("%(prog)s: error: %(message)s\n") % {"prog": ap.prog, "message": message}).rstrip("\n"))
        ap.error = error
    A = ap.parse_args(argv)
    wrong = Settings.from_options(A).problem()
    if wrong:
        ap.error(wrong)
    if A.draft:
        A.size, A.fps = "1280x720", 30
    if A.gpx is None and not (A.gui or A.check_maps):
        ap.error(_("give a GPX file or run with the --gui option"))
    return A, {a.dest: ap.get_default(a.dest) for a in ap._actions}


def run() -> None:
    A, defaults = options()
    if A.check_maps:
        return check_maps()
    if A.gui:
        return run_gui(A, defaults)
    film = not A.frame_only
    p = prepare(Project(), Settings.from_options(A), film=film)
    if A.slides:
        save_slides(p, A.slides)
    if p.settings.intro and (A.report or film):       # planned here, so that its messages come before those of the frame
        plan_flight(p, strict=film)
    if A.report:                                      # description of the film for the settings window (or for the curious)
        A.report.write_text(json.dumps(report(p), ensure_ascii=False), encoding="utf-8")
    still = A.frame_only or A.frame
    if still:
        Image.fromarray(render_still(p)).save(still)
        log(_("Saved the frame: {file}").format(file=still))
    if film:
        render_film(p, A.output or Path(A.gpx.stem + ".mp4"))
