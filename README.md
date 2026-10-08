<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.tomaszbojanowski.Gpxfilm.svg" width="128" height="128" alt="">
</p>

# gpxfilm

Turn a GPX track into a film of your route drawn on a map.

gpxfilm reads a GPX file from a watch or a phone and renders an MP4 in which the route draws itself across a shaded-relief, topographic or aerial-photo map, with place names, an elevation profile and your photos along the way. The default output is 4K at 60 frames per second.

*Polski opis: [README.pl.md](README.pl.md)*

## What it does

- Draws the route on a map of the area, with distance, ascent and time counting up as it goes.
- Labels huts, passes, peaks and villages near the route, using names from OpenStreetMap.
- Gives the start and the finish their elevation and the name of the hut, summit, pass or village they lie at.
- Shows an elevation profile that fills in together with the route.
- Opens with a zoom from the whole country down to the route, pausing over the region to show its main towns and highest peaks, and closes with a summary card.
- Pauses at longer stops and shows your photos where they were taken, matched by time or GPS position.
- Can color the route by gradient, speed, heart rate or elevation, and show live counters.
- Offers four map backgrounds and four color styles.
- Includes a settings window that runs in the browser, with a preview and a quick draft film.
- Reads GPX files from many different devices and apps.
- Speaks English and Polish.

## Requirements

- Python 3.10 or newer
- [ffmpeg](https://ffmpeg.org/) on the `PATH`
- [git](https://git-scm.com/), to install straight from GitHub

It is used on Linux and macOS. Windows has not been tested.

Photos in the HEIC format (for example from an iPhone) need the extra package `pillow-heif`: `pipx inject gpxfilm pillow-heif`. Without it, gpxfilm skips them.

## Install

With [pipx](https://pipx.pypa.io/):

```bash
pipx install git+https://github.com/TomaszBojanowski/gpxfilm
```

Or from a copy of this repository (a git clone or the ZIP from GitHub), inside its folder:

```bash
pipx install .
```

On macOS, ffmpeg and pipx are available from Homebrew: `brew install ffmpeg pipx`.

pipx puts the `gpxfilm` command in `~/.local/bin`. If the terminal then says the command is not found, run `pipx ensurepath` once and open a new terminal window.

To update later:

```bash
pipx upgrade gpxfilm
```

The version number comes from the git history, so every change published here counts as a new version; `pipx list` shows the one you have. If you installed from a clone, run `git pull` in it first.

## Quick start

```bash
# One frame, to check the framing and labels
gpxfilm track.gpx --frame-only preview.png

# A quick draft: 1280×720 at 30 fps
gpxfilm track.gpx --draft -o draft.mp4

# The full film: 4K, with intro, outro and aerial photos
gpxfilm track.gpx -o film.mp4 --route-duration 40 --intro --outro --map aerial

# With photos from a folder, live counters and kilometer markers
gpxfilm track.gpx -o film.mp4 --photos ~/Pictures/trip --counters --km-markers
```

Run `gpxfilm --help` for the full list of options.

## Settings window

```bash
gpxfilm --gui
```

This opens a local page in your browser with every setting, a preview of the final frame, a quick draft of the whole film and a progress bar. The page talks only to the program on your own computer.

## Map backgrounds

| `--map` | Background |
| --- | --- |
| `terrain` | Shaded relief drawn by gpxfilm from elevation data (the default) |
| `topo` | OpenTopoMap |
| `satellite` | Sentinel-2 satellite imagery, 10 m resolution |
| `aerial` | Official aerial photographs published as open data |
| a tile or WMS address | Your own source, with `{z}/{x}/{y}` or `{bbox}` |

Aerial photographs are available for Poland, Germany, Austria, Switzerland, Liechtenstein, France, Spain, Portugal, Italy, Czechia, Slovakia, Slovenia, Belgium, the Netherlands, Luxembourg, Estonia and Finland, and outside Europe for the United States, Japan, Taiwan, Hong Kong, Singapore, and parts of Canada, Australia and Argentina. Elsewhere gpxfilm falls back to satellite imagery.

These services belong to many different agencies and change from time to time. To see which of them answer right now:

```bash
gpxfilm --check-maps
```

## Most used options

| Option | Meaning |
| --- | --- |
| `--title "A\nB"` | Title, one line per `\n` |
| `--route-duration 40` | Seconds the route takes to draw |
| `--intro`, `--outro` | Opening camera flight from the country view; closing summary card |
| `--intro-peaks 5`, `--intro-places 4`, `--intro-radius 20` | How many of the highest peaks the intro shows over the region and after the camera arrives, how many of the main places (cities, towns, villages) it shows over the region (0: none), and within how many km of the start it looks for them over the region |
| `--intro-label-duration 3` | Seconds the labels of the places and peaks of the intro stay fully visible, from the last one coming in until they start to fade, both over the region and after the camera arrives (0.5 to 20). Without it about 1.3 s and 0.8 s |
| `--label-style strong`, `--label-size 1.0` | Look of the labels on the map: `plain`, `strong` (dark outline, the default) or `badges` (on dark badges), and their size from 0.7 to 2.0. Bigger labels and badges need more room, so fewer may fit |
| `--photos DIR` | Photos shown at the place they were taken |
| `--stop-minutes 5` | Pause at stops of at least this many minutes |
| `--counters`, `--km-markers` | Live counters; kilometer markers |
| `--distance 14.3`, `--ascent 929` | Distance in km and total ascent in m from your watch: the film ends on these values, the distance rounded to 0.1 km like every distance on the film. Without them the distance worked out from the track usually differs from the watch by about ±2% |
| `--color-by slope` | Color the route by gradient (`slope`), speed (`speed`), heart rate (`heart-rate`) or elevation (`elevation`) |
| `--map-style night` | Color style: `natural`, `night`, `light`, `gray` |
| `--name-language it` | Preferred language of place names |
| `--rename "OLD=NEW"`, `--skip NAME` | Change the name of a place or peak on the film (OLD exactly as the film shows it), also at the start, the finish and in the intro; leave out places whose name contains this text (letter case does not matter). Both can be given more than once. In the settings window, click a name in the list under the preview to rename it, or × to leave it out |
| `--timezone Europe/Rome` | Time zone for clock times |
| `--music FILE` | Background music |
| `--size 1920x1080`, `--fps 30` | Frame size (16:9 only, e.g. 3840x2160, 1920x1080, 1280x720) and frames per second |
| `--language pl` | Language of the texts on the film: `en` or `pl` |

Settings you always use can go into `~/.config/gpxfilm.toml`, named like the options without the leading dashes:

```toml
map = "aerial"
stop-minutes = 10
km-markers = true
```

The settings file works with Python 3.11 or newer. Settings files written for earlier versions, with the Polish names the options had then or with `intro-peak-radius` and `intro-peak-duration`, still work; gpxfilm says once which names to change.

## Language

gpxfilm speaks English and Polish. Messages, the settings window and the texts on the film follow the language of your system: the first of `LANGUAGE`, `LC_ALL`, `LC_MESSAGES` and `LANG` that is set, and on macOS the system setting when none of them is. Any other language falls back to English. `--language en` or `--language pl` sets the language of the film alone, so you can make an English film on a Polish system and the other way around.

The Polish texts live in `po/pl.po`, a gettext catalog that gpxfilm reads directly.

## What goes over the network

gpxfilm downloads elevation data, map data and aerial or satellite imagery for the area of your track from public servers. The first time, it also downloads two typefaces from Google Fonts and the country outlines from Natural Earth. With `--auto-title` it asks OpenStreetMap's Nominatim for the name of the place in the middle of the track. Everything is kept in a cache, so making the same film again needs no downloads.

Your GPX file and your photos are never uploaded. The servers see which area was requested; with `--intro`, the questions about the peaks and places around the start include the start point itself (`--intro-peaks 0 --intro-places 0` or `--no-osm` leaves them out). Every request names the program, its version and the address of this project, for example `gpxfilm/0.1.0 (+https://github.com/TomaszBojanowski/gpxfilm)`.

The cache is in `~/.cache/gpxfilm` (in `$XDG_CACHE_HOME/gpxfilm` when that is set, on macOS in `~/Library/Caches/gpxfilm`); set `GPXFILM_CACHE_DIR` to move it.

## Data sources

Every film carries the credit of the background it uses, and of OpenStreetMap when it shows names from it. If you publish a film, keep that credit visible. A film with the `topo` background is under the CC BY-SA license, like OpenTopoMap.

| Data | Source |
| --- | --- |
| Elevation | [Mapterhorn](https://mapterhorn.com/), built from open national and global elevation models |
| Trails, water, place names | © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, via Overpass and, with `--auto-title`, Nominatim |
| Topographic map | [OpenTopoMap](https://opentopomap.org/) (CC-BY-SA) |
| Satellite imagery | Sentinel-2 cloudless by [EOX](https://s2maps.eu/), contains modified Copernicus Sentinel data |
| Aerial photographs | Public institutions of individual countries and regions; the credit of each is shown on the film |
| Country outlines | [Natural Earth](https://www.naturalearthdata.com/) (public domain) |
| Typefaces | Big Shoulders Display and Figtree (SIL Open Font License) |

## Development

```bash
pip install -e '.[test]'
pytest -q
```

The tests compare rendered frames with reference images and run without network access.

## Planned

Plans may change.

Next release:

- An app for macOS with its own window, opened with a double click, without the Terminal or a browser tab. It will come as a .dmg file to download, not signed by Apple.
- A rebuilt engine that works apart from the settings window, so the window never freezes. Every long task can be stopped at once, and any moment of the film can be drawn without rendering the film up to it.

Later, in no fixed order:

- A native app for Linux, made with GTK 4 and libadwaita and installed as a Flatpak.
- Vertical 9:16 films for phones.
- The settings window reachable from a phone on the home network, once access to it can be protected.
- The list of map sources in a separate file that you can override with a file of your own, and a weekly automatic check that reports sources that stop working.
- A ride by cable car or bus while the recording is paused will no longer count towards the distance.
- Peak labels set further from their peak and joined to it by a thin line, so that larger labels do not push peaks off the map.
- The name of the place at the start and the finish also when it fits only with the time on a line of its own.

## License

Copyright 2026 Tomasz Bojanowski

gpxfilm is free software under the GNU General Public License, version 3 or later. See [LICENSE](LICENSE). Map data and imagery keep the licenses of their sources.

The test data in `tests/data` include files from other projects under their own licenses: terrain tiles from Mapterhorn (the licenses of its sources), Natural Earth borders (public domain) and the Big Shoulders Display and Figtree typefaces (SIL Open Font License 1.1). See [tests/data/SOURCES.md](tests/data/SOURCES.md).
