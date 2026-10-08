"""GPX reading, map projection, track preparation and frame layout."""
from __future__ import annotations

import codecs
import datetime as dt
import gzip
import math
import re
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .i18n import _, ngettext, number, pgettext
from .util import log

R_EARTH = 6378137.0
R_MEAN = 6371008.8                           # mean Earth radius, for distances along the ground
BOX = (790.0, 130.0, 1800.0, 950.0)      # frame area (in 1920x1080 units) the route is fitted into
LIMITS = (700.0, 64.0, 1856.0, 1016.0)   # limits for the labels on the map


def _tag(e) -> str:
    return e.tag.rsplit("}", 1)[-1]


# Windows tools write the wider Microsoft code pages under these names (browsers read them the same way).
WIDER = {"windows-31j": "cp932", "x-sjis": "cp932", "shift_jis": "cp932", "euc_kr": "cp949", "gb2312": "gb18030", "gbk": "gb18030",
         "big5": "cp950"}


def gpx_root(path: Path):
    """The <gpx> element of the file. Any other file ends the program with a message saying what the file is."""
    try:
        data = path.read_bytes()
    except OSError as e:
        sys.exit(_("Cannot read the file {path}: {error}.").format(path=path, error=e.strerror or e))
    for _layer in range(2):                     # .gpx.gz, e.g. from a Strava bulk export
        if data[:2] == b"\x1f\x8b":
            try:
                data = gzip.decompress(data)
            except (OSError, EOFError, zlib.error):
                sys.exit(_("The file {file} is a damaged gzip archive.").format(file=path.name))
    if data[:4] == b"PK\x03\x04":                  # binary signatures first: a FIT header may start with a form feed (12)
        sys.exit(_("The file {file} is a ZIP archive (for example an export from Garmin Connect or a KMZ file). Unpack it and choose "
                   "the .gpx file.").format(file=path.name))
    if len(data) >= 12 and data[8:12] == b".FIT":
        sys.exit(_("The file {file} is a FIT file straight from a watch or bike computer. The program reads only GPX: export the "
                   "activity as GPX (Garmin Connect, Strava and watch apps have this option).").format(file=path.name))
    if data[:3] == b"\xef\xbb\xbf":
        data = data[3:]
    data = data.lstrip(b" \t\r\n")
    if not data:
        sys.exit(_("The file {file} is empty.").format(file=path.name))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        sys.exit(_("The file {file} is not a valid GPX file: the XML is damaged or incomplete ({error}).").format(file=path.name, error=e))
    except (ValueError, LookupError):             # Shift_JIS, GBK and other encodings expat cannot read itself
        m = re.match(rb"<\?xml[^>]*encoding\s*=\s*[\"']([\w.:-]+)", data)
        enc = m[1].decode("ascii") if m else "?"
        try:
            name = codecs.lookup(WIDER.get(enc.lower(), enc)).name
            text = data.decode(WIDER.get(name, name))
        except LookupError:
            sys.exit(_("The file {file} uses a character encoding the program cannot read ({encoding}).").format(file=path.name, encoding=enc))
        except UnicodeError as e:
            if isinstance(e, UnicodeDecodeError) and e.end == len(data):     # cut off in the middle of a character
                sys.exit(_("The file {file} is not a valid GPX file: the XML is damaged or incomplete (it ends in the middle of a "
                           "character).").format(file=path.name))
            sys.exit(_("The file {file} is not a valid GPX file: its characters do not match the encoding {encoding}.").format(
                file=path.name, encoding=enc))
        try:
            root = ET.fromstring(text)
        except (ET.ParseError, ValueError) as e:
            sys.exit(_("The file {file} is not a valid GPX file: the XML is damaged or incomplete ({error}).").format(file=path.name, error=e))
    kind = _tag(root)
    if kind == "TrainingCenterDatabase":
        sys.exit(_("The file {file} is a TCX file, not GPX. Export the activity as GPX (Garmin Connect, Polar Flow and COROS offer it "
                   "next to TCX).").format(file=path.name))
    if kind == "kml":
        sys.exit(_("The file {file} is a KML file (for example from Google Earth), not GPX. Save or convert the route as GPX.").format(
            file=path.name))
    if kind != "gpx":
        sys.exit(_("The file {file} is not a GPX file (its root XML element is <{element}>).").format(file=path.name, element=kind))
    return root


# ISO 8601 / RFC 3339 times as exporters write them: Z or z, any number of fraction digits (also after a comma),
# offsets with or without a colon or with hours only, a space instead of T. Python's fromisoformat accepts less on 3.10.
_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?\s*(?:([Zz])|([+-])(\d{2})(?::?(\d{2}))?)?")


def parse_time(text: str) -> dt.datetime | None:
    """Time of a point, aware; a time without a zone is taken as UTC. None when the text is not a time."""
    m = _TIME.fullmatch(text.strip())
    if not m:
        return None
    tz = dt.timezone.utc
    if m[9]:
        off = dt.timedelta(hours=int(m[10]), minutes=int(m[11] or 0))
        tz = dt.timezone(off if m[9] == "+" else -off)
    if not 1900 <= int(m[1]) <= 2200:            # placeholders such as 0001-01-01 (.NET) or 9999-12-31
        return None
    try:
        return dt.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6] or 0), int((m[7] or "0")[:6].ljust(6, "0")), tz)
    except ValueError:
        return None


HR_TAGS = {"hr", "heartrate", "heart_rate", "heatrate"}   # Garmin and most apps, COROS (also misspelled), others
HR_RANGE = (25.0, 250.0)                     # 0 and 255 mean "no reading" (Strava, COROS); anything outside is not a pulse
HR_GAP_S = 60.0                              # gaps in heart rate up to this long are filled, longer ones stay empty
HR_GAP_M = 200.0                             # the same for a track without times, along the track (as ELE_GAP_M)


def _heart_rate(e) -> float | None:
    """Heart rate from a point's extensions, whatever the exporter called the element; None when missing or not a pulse."""
    c = next((c for c in e.iter() if _tag(c).lower() in HR_TAGS and c.text), None)
    try:
        v = float(c.text) if c is not None else math.nan
    except ValueError:
        return None
    return v if HR_RANGE[0] <= v <= HR_RANGE[1] else None


MAX_LAT = 85.0511                            # the limit of the Mercator map; nearer the poles a point cannot be drawn


def _position(e):
    """(lat, lon) of a point, or None when the coordinates are missing, not numbers, impossible or beyond the map (0, 0 is a GPS glitch)."""
    try:
        lat, lon = float(e.get("lat")), float(e.get("lon"))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)) or abs(lat) > MAX_LAT or abs(lon) > 180 or (lat == 0 and lon == 0):
        return None
    return lat, lon


def _beyond_map(e) -> bool:
    """A real position nearer the poles than the map reaches (the North Pole Marathon, not a broken fix)."""
    try:
        return MAX_LAT < abs(float(e.get("lat"))) <= 90
    except (TypeError, ValueError):
        return False


ELE_RANGE = (-1000.0, 9000.0)               # anything outside is a placeholder such as -20000 (Zepp Life)
ELE_GAP_M = 200.0                            # longer gaps in elevation: the whole track takes it from the terrain model
FAKE_SPEED = 400 / 3.6                       # m/s between points; faster means times made up by a route planner
FAKE_GAP = dt.timedelta(days=7)


def _steps(p: list) -> np.ndarray:
    """Distance in meters from each point to the next (equirectangular, good enough for neighboring points)."""
    lat, lon = np.radians([q[0] for q in p]), np.radians([q[1] for q in p])
    return R_EARTH * np.hypot(np.diff(lat), np.diff(lon) * np.cos((lat[1:] + lat[:-1]) / 2))


def _merge_repeats(p: list) -> list:
    """Zepp Life writes each fix twice, first without elevation: points at the same place and time become one."""
    out = []
    for q in p:
        if out and q[3] is not None and q[:2] == out[-1][:2] and q[3] == out[-1][3]:
            out[-1] = tuple(a if a is not None else b for a, b in zip(out[-1], q))
        else:
            out.append(q)
    return out


def _fake_times(p: list) -> str | None:
    """Why the times look made up (a planned route with a pace, an old course export), or None."""
    tms = [q[3] for q in p]
    if tms[0].year < 2000:
        return _("start in {year}").format(year=tms[0].year)
    gaps = [b - a for a, b in zip(tms, tms[1:])]
    rel = np.array([(t - tms[0]).total_seconds() for t in tms])
    behind = int((rel[1:] <= np.maximum.accumulate(rel)[:-1]).sum())    # points prep_track drops for going back in time
    big = max(abs(g) for g in gaps) if behind > 0.1 * len(tms) else max(gaps)
    if big > FAKE_GAP:
        return _("a gap of {days} days between points").format(days=f"{big / dt.timedelta(days=1):.0f}")
    secs = np.array([g.total_seconds() for g in gaps])
    moving = secs > 0
    if moving.sum() >= 2:
        speed = float(np.median(_steps(p)[moving] / secs[moving]))
        if speed > FAKE_SPEED:
            grouped = f"{speed * 3.6:,.0f}".replace(",", pgettext("thousands separator", ","))
            return _("{speed} km/h on average between points").format(speed=grouped)
    return None


def _stray_times(tms: list, few: int) -> list:
    """The times without the broken ones (epoch 0, GPS week rollover). A run of times more than FAKE_GAP away from the
    bulk of the times is broken when the times come back to the bulk after it, and at the start or end when it has at
    most `few` points."""
    idx = [i for i, t in enumerate(tms) if t is not None]
    cut = [0] + [k for k in range(1, len(idx)) if abs(tms[idx[k]] - tms[idx[k - 1]]) > FAKE_GAP] + [len(idx)]
    rs = [idx[a:b] for a, b in zip(cut, cut[1:])]
    if len(rs) < 2:
        return list(tms)
    span = [(min(tms[i] for i in r), max(tms[i] for i in r)) for r in rs]
    group, ends = [0] * len(rs), []             # runs whose times lie within FAKE_GAP of each other form one group
    for k in sorted(range(len(rs)), key=lambda k: span[k][0]):
        if ends and span[k][0] <= ends[-1] + FAKE_GAP:
            ends[-1] = max(ends[-1], span[k][1])
        else:
            ends.append(span[k][1])
        group[k] = len(ends) - 1
    size = [sum(len(r) for r, g in zip(rs, group) if g == h) for h in range(len(ends))]
    bulk = max(range(len(ends)), key=size.__getitem__)
    first, last = group.index(bulk), len(group) - 1 - group[::-1].index(bulk)
    out = list(tms)
    for k, r in enumerate(rs):
        if group[k] != bulk and (first < k < last or len(r) <= few):
            for i in r:
                out[i] = None
    return out


SPIKE_FAR = 30.0                             # a jump off the track is longer than this in meters
SPIKE_STEPS = 8.0                            # and longer than this many typical steps around it
SPIKE_NEAR = 0.5                             # while the points before and after it lie closer than this share of the jump
SPIKE_RUN = 5                                # a spike is at most this many points in a row


def _spikes(p: list) -> list[int]:
    """Indices of position spikes in a track with times: a run of up to SPIKE_RUN points that jumps off the track and comes
    back close to where it left, either far (much farther than the steps around it) or faster than FAKE_SPEED both ways;
    and the first or last point when it lies far from the nearest point kept, or the way there is faster than FAKE_SPEED.
    A track without times keeps every point: in a planned route a long straight way out and back (a jetty, a dike, a line
    drawn to a summit) looks just like a spike."""
    tms = [q[3] for q in p]
    if not p or not all(tms):
        return []
    n = len(p)
    lat, lon = np.radians([q[0] for q in p]), np.radians([q[1] for q in p])
    x, y = R_MEAN * lon * math.cos(float(lat.mean())), R_MEAN * lat
    a = np.hypot(np.diff(x), np.diff(y))                        # a[i]: from point i to point i + 1
    t = np.array([(q - tms[0]).total_seconds() for q in tms])
    if np.sum(t[1:] > np.maximum.accumulate(t)[:-1]) < 1:       # one time for every point or running backwards: drawn without times
        return []
    typical = float(np.median(a)) if len(a) else 0.0
    gap = lambda i, j: math.hypot(x[j] - x[i], y[j] - y[i])

    def step(*spans) -> float:                                  # typical step on the given stretches of the track
        near = np.concatenate([a[max(0, lo):max(0, hi)] for lo, hi in spans])
        return float(np.median(near)) if len(near) else typical

    def fast(i: int, j: int) -> bool:                           # from point i to j faster than FAKE_SPEED, time going forward
        return t[j] > t[i] and gap(i, j) / (t[j] - t[i]) > FAKE_SPEED

    out, i = [], 1
    while i < n - 1:
        if a[i - 1] <= SPIKE_FAR and not fast(i - 1, i):       # no jump into this point: nothing to check
            i += 1
            continue
        for j in range(i + 1, min(i + SPIKE_RUN, n - 1) + 1):   # j: the first point after a run of j - i points
            jump_in, jump_out = a[i - 1], a[j - 1]
            far = max(SPIKE_FAR, SPIKE_STEPS * step((i - 6, i - 1), (j, j + 5)))
            back = gap(i - 1, j) < SPIKE_NEAR * min(jump_in, jump_out)
            if back and ((jump_in > far and jump_out > far) or (fast(i - 1, i) and fast(j - 1, j))):
                out += range(i, j)
                i = j + 1                                       # point j came back to the track: the next check starts from it
                break
        else:
            i += 1
    if n > SPIKE_RUN + 7:                                       # the first fix may come from where the watch was switched off
        kept = np.setdiff1d(np.arange(n), out)
        k = int(kept[1])                                        # the nearest point kept after the first one
        if gap(0, k) > max(SPIKE_FAR, SPIKE_STEPS * step((k, k + 5))) or (fast(0, k) and not fast(k, min(k + 1, n - 1))):
            out.append(0)
        k = int(kept[-2])
        if gap(k, n - 1) > max(SPIKE_FAR, SPIKE_STEPS * step((k - 5, k))) or (fast(k, n - 1) and not fast(max(k - 1, 0), k)):
            out.append(n - 1)
    return sorted(set(out))


def _clean_times(p: list, keep_times: bool) -> list:
    """Times stay when at least 90% of the points have a usable one (the rest are dropped) and they do not look made up."""
    tms = [q[3] for q in p]
    n_t = sum(t is not None for t in tms)
    if n_t == 0:
        return [q[:3] + (None,) + q[4:] for q in p]
    untimed = [q[:3] + (None,) + q[4:] for q in p]
    if n_t < 0.9 * len(p):
        log(_("Only {share}% of the points have a time – drawing the track without times.").format(share=100 * n_t // len(p)))
        return untimed
    timed = [q[:3] + (t,) + q[4:] for q, t in zip(p, tms) if t is not None]
    why = None if keep_times or len(timed) < 2 else _fake_times(timed)
    if why:
        log(_("The times in the file look made up ({reason}) – drawing the track as a route without times. If they are real, "
              "use --keep-times.").format(reason=why))
        return untimed
    if n_t < len(p):
        log(_("Skipped points without a time or with a wrong time: {count}.").format(count=len(p) - n_t))
    return timed


def _clean_elevation(p: list) -> tuple[list, str | None]:
    """Placeholder elevations count as missing; short gaps are filled later, otherwise the terrain model gives the elevation
    (and the second value says why, for a message once the track is known to move)."""
    ele = [q[2] if q[2] is not None and math.isfinite(q[2]) and ELE_RANGE[0] < q[2] < ELE_RANGE[1] else None for q in p]
    ok = [e is not None for e in ele]
    if not any(ok):                             # no usable elevation at all: the terrain model gives it
        return [q[:2] + (None,) + q[3:] for q in p], None
    why = None
    vals = [e for e in ele if e is not None]
    if len(vals) > 1 and min(vals) == max(vals):
        why = _("{height} m everywhere").format(height=f"{vals[0]:g}")
    elif sum(ok) <= 0.9 * len(p):
        why = _("only {share}% of the points have an elevation").format(share=100 * sum(ok) // len(p))
    elif not all(ok):
        at = np.concatenate([[0.0], np.cumsum(_steps(p))])
        idx = [i for i, o in enumerate(ok) if o]
        spans = [at[b] - at[a] for a, b in zip(idx, idx[1:]) if b - a > 1] + [at[idx[0]], at[-1] - at[idx[-1]]]
        if max(spans) > ELE_GAP_M:
            why = _("a gap in the elevation over {length} m of the track").format(length=f"{max(spans):.0f}")
    if why:
        ele = [None] * len(p)
    return [q[:2] + (e,) + q[3:] for q, e in zip(p, ele)], why


TOUCH_M = 1000.0                             # tracks closer than this (end of one, start of the next) make one route


def _point(e):
    """(lat, lon, ele, time, heart rate, name) of a trkpt, rtept or wpt; None when its position is unusable."""
    pos = _position(e)
    if pos is None:
        return None
    ele = tm = nm = None
    for c in e:
        ct = _tag(c)
        try:
            if ct == "ele" and c.text:
                ele = float(c.text)
            elif ct == "time" and c.text:
                tm = parse_time(c.text)
            elif ct == "name" and c.text:
                nm = c.text.strip()
        except ValueError:
            pass
    return pos[0], pos[1], ele, tm, _heart_rate(e), nm


def _child_name(e) -> str | None:
    return next((c.text.strip() for c in e if _tag(c) == "name" and c.text and c.text.strip()), None)


def _how_many(n: int, kind: str) -> str:
    """How many tracks (trk) or routes (rte) the file has, as a phrase of the messages: 'there are 3 tracks'. The phrase
    holds the verb, as its form follows the count in some languages (Polish 'jest 5 śladów', 'są 3 ślady')."""
    if kind == "trk":
        return ngettext("there is {n} track", "there are {n} tracks", n).format(n=n)
    return ngettext("there is {n} route", "there are {n} routes", n).format(n=n)


def _pick_tracks(tracks: list, choice: int | None) -> tuple[list, str | None]:
    """Points and name of the track to draw. Tracks that touch (each starts within TOUCH_M of where the previous one ends)
    are joined; otherwise the longest group is drawn. --track N draws exactly that track."""
    if len(tracks) == 1 and choice in (None, 1):
        return tracks[0]["pts"], tracks[0]["name"]
    if all(any(q[3] for q in tr["pts"]) for tr in tracks):    # in time order when every track has times
        tracks = sorted(tracks, key=lambda tr: next(q[3] for q in tr["pts"] if q[3]))
    trk = tracks[0]["kind"] == "trk"
    lines = []
    for k, tr in enumerate(tracks, 1):
        km = number(float(np.sum(_steps(tr["pts"]))) / 1000, 1) if len(tr["pts"]) > 1 else "0"
        t0 = next((q[3].astimezone() for q in tr["pts"] if q[3]), None)
        date = _("{year}-{month}-{day}").format(year=f"{t0:%Y}", month=f"{t0:%m}", day=f"{t0:%d}") if t0 else None
        name = tr["name"] or _("unnamed")
        lines.append(f"  {k}. {name} – {km} km" + (f", {date}" if date else ""))
    count = _how_many(len(tracks), tracks[0]["kind"])
    log(_("In the file {count}:").format(count=count) + "\n" + "\n".join(lines))
    groups = [[0]]
    for k in range(1, len(tracks)):
        gap = float(_steps([tracks[k - 1]["pts"][-1], tracks[k]["pts"][0]])[0])
        if gap <= TOUCH_M:
            groups[-1].append(k)
        else:
            groups.append([k])

    def label(grp: list) -> str:                    # 'track 2', 'tracks 2–3'
        if len(grp) == 1:
            return (_("track {number}") if trk else _("route {number}")).format(number=grp[0] + 1)
        return (_("tracks {first}–{last}") if trk else _("routes {first}–{last}")).format(first=grp[0] + 1, last=grp[-1] + 1)

    if choice is not None:
        if not 1 <= choice <= len(tracks):
            sys.exit(_("There is no number {number}: {count} in the file.").format(number=choice, count=count))
        grp = [choice - 1]
        log((_("Drawing track {number}.") if trk else _("Drawing route {number}.")).format(number=choice))
    elif len(groups) == 1:
        grp = groups[0]
        log(_("Each track starts where the previous one ends – joining them into one. Choose one of them with --track N.") if trk
            else _("Each route starts where the previous one ends – joining them into one. Choose one of them with --track N."))
    else:
        length = lambda grp: sum(float(np.sum(_steps(tracks[k]["pts"]))) for k in grp if len(tracks[k]["pts"]) > 1)
        grp = max(groups, key=length)
        apart = (_("They do not touch – drawing the longest stretch: {part}. Choose another number with --track N.") if len(tracks) == 2
                 else _("Not all of them touch – drawing the longest stretch: {part}. Choose another number with --track N."))
        log(apart.format(part=label(grp)))
    return [q for k in grp for q in tracks[k]["pts"]], tracks[grp[0]]["name"]


def read_gpx(path: Path, keep_times: bool = False, track: int | None = None) -> dict:
    root = gpx_root(path)
    wpts, bad, bad_trk, polar, polar_trk = [], 0, 0, 0, 0
    tracks = {"trk": [], "rte": []}
    for e in root.iter():
        t = _tag(e)
        if t == "wpt":
            q = _point(e)
            if q is None and _beyond_map(e):
                polar += 1
            elif q is None:
                bad += 1
            elif q[5]:
                wpts.append((q[0], q[1], q[5]))
        elif t in tracks:
            pts = []
            for c in e.iter():
                if _tag(c) == ("trkpt" if t == "trk" else "rtept"):
                    q = _point(c)
                    if q is None and _beyond_map(c):
                        polar += 1
                        polar_trk += 1
                    elif q is None:
                        bad += 1
                        bad_trk += 1
                    elif t == "rte":                    # route point times are when the point was planned, not passed
                        pts.append(q[:3] + (None,) + q[4:5])
                        for r in c.iter():              # Garmin BaseCamp: the road to the next via point
                            pos = _position(r) if _tag(r) == "rpt" else None
                            if pos:
                                pts.append((pos[0], pos[1], None, None, None))
                    else:
                        pts.append(q[:5])
            if pts:
                tracks[t].append(dict(kind=t, name=_child_name(e), pts=pts))
    meta = next((e for e in root if _tag(e) == "metadata"), None)
    found = tracks["trk"] or tracks["rte"]
    if bad and found:
        log(_("Skipped points with wrong or missing coordinates: {count}.").format(count=bad))
    if polar and found:
        log(_("Skipped points off the map (beyond {latitude}° north or south): {count}.").format(latitude=f"{MAX_LAT:.0f}", count=polar))
    if not found:
        if polar_trk and polar_trk >= bad_trk:
            sys.exit(_("The track lies beyond {latitude}° north or south – the map does not reach that far.").format(latitude=f"{MAX_LAT:.0f}"))
        if bad_trk:
            sys.exit(_("No point of the track has valid coordinates – probably a workout without GPS (for example on a treadmill or "
                       "indoors)."))
        if any(_tag(e) == "trk" for e in root):
            sys.exit(_("The track in the file has no GPS points – probably a workout without GPS (for example on a treadmill or "
                       "indoors)."))
        if wpts:
            sys.exit(_("The file has only single points (wpt), no track or route."))
        sys.exit(_("The GPX file has no track (trkpt) or route (rtept)."))
    few = max(3, sum(q[3] is not None for tr in found for q in tr["pts"]) // 100)
    for tr in found:                            # broken times inside each track, then between the joined tracks
        tr["pts"] = [q[:3] + (tm,) + q[4:] for q, tm in zip(tr["pts"], _stray_times([q[3] for q in tr["pts"]], few))]
    p, name = _pick_tracks(found, track)
    p = [q[:3] + (tm,) + q[4:] for q, tm in zip(p, _stray_times([q[3] for q in p], 0))]
    if name is None and meta is not None:
        name = _child_name(meta)
    p = _clean_times(_merge_repeats(p), keep_times)
    spikes = set(_spikes(p))
    if spikes:
        log(_("Skipped position spikes (points far off the track): {count}.").format(count=len(spikes)))
        p = [q for i, q in enumerate(p) if i not in spikes]
    if len(p) < 2:
        sys.exit(_("The file has only one track point – too few to draw a route."))
    lat, lon = [q[0] for q in p], [q[1] for q in p]
    if math.hypot(max(lat) - min(lat), (max(lon) - min(lon)) * math.cos(math.radians(lat[0]))) * 111320 < 20:
        sys.exit(_("All track points lie in one place – there is no route to draw."))
    p, ele_note = _clean_elevation(p)
    return dict(pts=p, wpts=wpts, name=name, ele_note=ele_note)


class Proj:
    """Local Mercator projection in meters: x to the east, y to the south."""

    def __init__(self, lat0: float, lon0: float):
        self.k = math.cos(math.radians(lat0))
        self.X0 = R_EARTH * math.radians(lon0)
        self.Y0 = R_EARTH * math.log(math.tan(math.pi / 4 + math.radians(lat0) / 2))
        self.lat0, self.lon0 = lat0, lon0

    def fwd(self, lat, lon):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        return ((R_EARTH * np.radians(lon) - self.X0) * self.k,
                -(R_EARTH * np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) - self.Y0) * self.k)

    def inv(self, x, y):
        X, Y = np.asarray(x, float) / self.k + self.X0, -np.asarray(y, float) / self.k + self.Y0
        return np.degrees(2 * np.arctan(np.exp(Y / R_EARTH)) - np.pi / 2), np.degrees(X / R_EARTH)


def runs(a):
    s = 0
    for i in range(1, len(a) + 1):
        if i == len(a) or a[i] != a[s]:
            yield s, i, bool(a[s])
            s = i


def climb(e: np.ndarray, thr: float) -> np.ndarray:
    """Running ascent with a hysteresis threshold (damps the altimeter noise)."""
    out, ref, acc = np.zeros(len(e)), e[0], 0.0
    for i, v in enumerate(e):
        if v - ref > thr:
            acc += v - ref
            ref = v
        elif ref - v > thr:
            ref = v
        out[i] = acc
    return out


class Track:
    pass


ELE_SMOOTH_S = 9.0                           # elevation is averaged over this many seconds (points, without times)
ELE_STEP = 1.25                              # rises smaller than this are noise in the elevation from the file


def smooth_climb(ele: np.ndarray, t: np.ndarray | None, step: float) -> tuple[np.ndarray, np.ndarray]:
    """Smoothed elevation and the running ascent; rises smaller than step meters are noise."""
    dtm = max(float(np.median(np.diff(t))), 0.5) if t is not None and len(t) > 1 else None
    es = ndi.uniform_filter1d(ele, max(3, int(round(ELE_SMOOTH_S / dtm)) | 1) if dtm else 9, mode="nearest")
    return es, climb(es, step)


STOP_SPEED = 0.25                            # m/s: slower than this for at least STOP_TIME is standing still,
STOP_TIME = 60.0                             # and the GPS drift while standing does not count as distance
STOP_RUN = 10.0                              # s of points slow over both half minutes around them: a minute's stop
POS_SMOOTH_S = 3.0                           # positions are averaged over this many seconds, as watches filter single fixes


def raw_distance(lat: np.ndarray, lon: np.ndarray, t: np.ndarray | None = None) -> np.ndarray:
    """Cumulative distance in meters along the recorded points, added up the way a watch does: positions averaged over
    POS_SMOOTH_S, and where the speed stays under STOP_SPEED for at least STOP_TIME, the drift does not count. A stop is a
    run of points slow over the minute around each of them that holds at least STOP_RUN of points slow over both halves of
    that minute too, so walking back and forth (laps, tight switchbacks) is not a stop. A pause in the recording slower
    than STOP_SPEED does not count either. Without times there is no speed, so every segment counts."""
    if t is not None and len(t) > 2:
        w = int(round(POS_SMOOTH_S / max(float(np.median(np.diff(t))), 0.5)))
        if w > 1:                                    # averaged within each stretch of recording, never across a pause
            lat, lon = np.array(lat, float), np.array(lon, float)
            edges = np.r_[0, np.flatnonzero(np.diff(t) > POS_SMOOTH_S) + 1, len(t)]
            for s, e in zip(edges[:-1], edges[1:]):
                lat[s:e], lon[s:e] = ndi.uniform_filter1d(lat[s:e], w, mode="nearest"), ndi.uniform_filter1d(lon[s:e], w, mode="nearest")
    la, lo = np.radians(lat), np.radians(lon)
    h = np.sin(np.diff(la) / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(np.diff(lo) / 2) ** 2
    seg = 2 * R_MEAN * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    if t is not None and len(t) > 2:
        x, y = R_MEAN * lo * math.cos(float(la.mean())), R_MEAN * la
        ta, tb = np.maximum(t - STOP_TIME / 2, t[0]), np.minimum(t + STOP_TIME / 2, t[-1])
        xa, ya, xb, yb = np.interp(ta, t, x), np.interp(ta, t, y), np.interp(tb, t, x), np.interp(tb, t, y)
        calm = np.hypot(xb - xa, yb - ya) < STOP_SPEED * (tb - ta)
        steady = calm & (np.hypot(x - xa, y - ya) <= STOP_SPEED * (t - ta)) & (np.hypot(xb - x, yb - y) <= STOP_SPEED * (tb - t))
        cut = np.r_[False, seg > STOP_SPEED * STOP_TIME]       # a jump to this point: after a pause the next stop is elsewhere
        for s0, e0, val in runs(calm):
            if not val:
                continue
            bounds = [s0, *(s0 + 1 + np.flatnonzero(cut[s0 + 1:e0])), e0]
            for s, e in zip(bounds[:-1], bounds[1:]):
                if not any(v and t[s + j - 1] - t[s + i] >= STOP_RUN for i, j, v in runs(steady[s:e])):
                    continue
                cx, cy = float(np.median(x[s:e])), float(np.median(y[s:e]))
                off = np.hypot(x[s:e] - cx, y[s:e] - cy)
                r = min(float(off.max()), 2 * float(np.percentile(off, 90)))     # the patch of ground the drift covered
                while s > 0 and not cut[s] and math.hypot(x[s - 1] - cx, y[s - 1] - cy) <= r:
                    s -= 1                                                # the first and last half minute of the stop
                while e < len(t) and not cut[e] and math.hypot(x[e] - cx, y[e] - cy) <= r:
                    e += 1
                seg[s:e - 1] = 0.0
        dt = np.diff(t)
        seg[(dt >= STOP_TIME) & (seg < STOP_SPEED * dt)] = 0.0           # a pause without moving on
    return np.concatenate([[0.0], np.cumsum(seg)])


def _bridge(v: np.ndarray, t: np.ndarray, limit: float) -> np.ndarray:
    """Fill the NaN gaps of v that span at most limit along t (seconds, or meters for a track without times)."""
    out, idx = v.copy(), np.flatnonzero(np.isfinite(v))
    if len(idx) == 0:
        return out
    for a, b in zip(idx, idx[1:]):
        if b - a > 1 and t[b] - t[a] <= limit:
            out[a + 1:b] = np.interp(t[a + 1:b], [t[a], t[b]], [v[a], v[b]])
    if idx[0] > 0 and t[idx[0]] - t[0] <= limit:
        out[:idx[0]] = v[idx[0]]
    if idx[-1] < len(v) - 1 and t[-1] - t[idx[-1]] <= limit:
        out[idx[-1] + 1:] = v[idx[-1]]
    return out


def prep_track(g: dict) -> Track:
    lat = np.array([p[0] for p in g["pts"]])
    lon = np.array([p[1] for p in g["pts"]])
    ele = np.array([np.nan if p[2] is None else p[2] for p in g["pts"]])
    tms = [p[3] for p in g["pts"]]
    hr = np.array([np.nan if p[4] is None else p[4] for p in g["pts"]])
    tr = Track()
    tr.has_t = all(t is not None for t in tms)
    if tr.has_t:                                    # only increasing timestamps
        t = np.array([(q - tms[0]).total_seconds() for q in tms])
        keep = np.concatenate([[True], t[1:] > np.maximum.accumulate(t)[:-1]])
        if keep.sum() < 2:                          # one time for every point, or time running backwards
            log(_("The times in the file do not move forward – drawing the track without them."))
            tr.has_t = False
        else:
            lat, lon, ele, t, hr = lat[keep], lon[keep], ele[keep], t[keep], hr[keep]
            tr.t0 = tms[0]
    tr.proj = Proj((lat.min() + lat.max()) / 2, (lon.min() + lon.max()) / 2)
    x, y = tr.proj.fwd(lat, lon)
    n = len(x)
    med = float(np.median(np.hypot(np.diff(x), np.diff(y))))
    dtm = max(float(np.median(np.diff(t))), 0.5) if tr.has_t else 1.0
    win = (int(round(7 / dtm)) | 1) if tr.has_t else 5
    win = 1 if med > 25 or n < 20 else max(1, min(win, 15))   # sparse points (a planned route): no smoothing
    xs = ndi.uniform_filter1d(x, win, mode="nearest") if win > 1 else x
    ys = ndi.uniform_filter1d(y, win, mode="nearest") if win > 1 else y
    tr.has_e = bool(np.isfinite(ele).mean() > 0.9)
    if tr.has_e:
        idx = np.arange(n)
        ok = np.isfinite(ele)
        ele = np.interp(idx, idx[ok], ele[ok])
        es, cup = smooth_climb(ele, t if tr.has_t else None, ELE_STEP)
    mov = np.ones(n, bool)
    if tr.has_t and n > 40:                          # stops: speed measured over about 16 s
        w = max(1, int(round(8 / dtm)))
        i0, i1 = np.clip(np.arange(n) - w, 0, n - 1), np.clip(np.arange(n) + w, 0, n - 1)
        mov = np.hypot(xs[i1] - xs[i0], ys[i1] - ys[i0]) / np.maximum(t[i1] - t[i0], 1e-6) > 0.38
        for s, e, val in list(runs(mov)):
            if val and t[e - 1] - t[s] < 25 and s > 0 and e < n:
                mov[s:e] = False
        for s, e, val in list(runs(mov)):
            if not val and t[e - 1] - t[s] < 45:
                mov[s:e] = True
    ci, tin, tout = [], [], []                       # cleaned line: a stop is one point
    stride = max(1, int(round(2 / dtm))) if tr.has_t else 1
    cx, cy = [], []
    for s, e, val in runs(mov):
        if val:
            ii = list(range(s, e, stride))
            cx += list(xs[ii]); cy += list(ys[ii]); ci += ii
            if tr.has_t:
                tin += list(t[ii]); tout += list(t[ii])
        else:
            cx.append(float(np.median(xs[s:e]))); cy.append(float(np.median(ys[s:e]))); ci.append(s)
            tin.append(t[s]); tout.append(t[e - 1])
    if ci[-1] != n - 1 and mov[-1]:
        cx.append(xs[-1]); cy.append(ys[-1]); ci.append(n - 1)
        if tr.has_t:
            tin.append(t[-1]); tout.append(t[-1])
    tr.cx, tr.cy = np.array(cx), np.array(cy)
    tr.cd = np.concatenate([[0], np.cumsum(np.hypot(np.diff(tr.cx), np.diff(tr.cy)))])
    tr.dist = float(tr.cd[-1])
    tr.rd = at_vertices(raw_distance(lat, lon, t if tr.has_t else None), ci)   # the distance shown at each vertex of the line
    tr.lat, tr.lon, tr.t, tr.ci = lat, lon, t if tr.has_t else None, np.array(ci)   # for the elevation from the terrain model
    tr.rdist = float(tr.rd[-1])
    if len(tr.cx) < 2 or tr.dist < 1:              # the whole recording is one stop
        sys.exit(_("There is no movement on the track – the whole recording is one stop, there is no route to draw."))
    if g.get("ele_note"):
        log(_("The elevation in the file is not usable for the profile ({reason}) – taking it from the terrain model for the whole "
              "track.").format(reason=g["ele_note"]))
    if tr.has_t:
        tr.tin, tr.tout = np.array(tin), np.array(tout)
        tr.tin[0], tr.tout[0], tr.tin[-1], tr.tout[-1] = 0, 0, t[-1], t[-1]
        tr.total = float(t[-1])
    if tr.has_e:
        tr.ce, tr.cup = at_vertices(es, ci), at_vertices(cup, ci)
    tr.has_hr = bool(np.isfinite(hr).mean() >= 0.2)
    if tr.has_hr:
        idx, ok = np.arange(n), np.isfinite(hr)
        if ok.all():
            tr.chr = at_vertices(ndi.uniform_filter1d(np.interp(idx, idx[ok], hr[ok]), 9, mode="nearest"), ci)
        else:                                       # short gaps are filled, longer ones keep no heart rate (NaN)
            along = t if tr.has_t else np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
            v = _bridge(hr, along, HR_GAP_S if tr.has_t else HR_GAP_M)
            m = np.isfinite(v)
            lone = m & ~np.r_[False, m[:-1]] & ~np.r_[m[1:], False]     # a reading between two long gaps
            m &= ~lone
            num = ndi.uniform_filter1d(np.where(m, v, 0.0), 9, mode="nearest")
            den = ndi.uniform_filter1d(m.astype(float), 9, mode="nearest")
            tr.chr = at_vertices(np.where(m, num / np.maximum(den, 1e-9), np.nan), ci)
            c = np.isfinite(tr.chr)
            if np.diff(tr.cd)[c[:-1] & c[1:]].sum() < 0.2 * tr.dist:    # the line is colored by distance
                log(_("The heart rate reading covers less than 20% of the route – leaving it out."))
                tr.has_hr = False
    tr.loop = math.hypot(tr.cx[0] - tr.cx[-1], tr.cy[0] - tr.cy[-1]) < max(150.0, 0.02 * tr.dist)
    return tr


def at_vertices(v: np.ndarray, ci: np.ndarray) -> np.ndarray:
    """Values of the points at the vertices of the drawn line; a stop at the end is one vertex that stands for all of it."""
    out = np.array(v, float)[ci]
    out[-1] = v[-1]
    return out


def match_watch(tr: Track, km: float | None, up: float | None) -> None:
    """Scale the shown distance and the ascent so that the film ends exactly on the values from the watch."""
    along = tr.rd / tr.rdist if tr.rdist > 0 else tr.cd / tr.dist     # a share of the way, even when no meter counted
    if km is not None:
        tr.rd = km * 1000 * along
        tr.rdist = km * 1000
    if up is not None:
        tr.cup = tr.cup * (up / tr.cup[-1]) if tr.cup[-1] > 0 else up * along


class Layout:
    def __init__(self, tr: Track, W: int, H: int):
        self.W, self.H, self.K = W, H, W / 1920.0
        wm, hm = float(np.ptp(tr.cx)), float(np.ptp(tr.cy))
        self.S = min((BOX[2] - BOX[0]) / max(wm, 1), (BOX[3] - BOX[1]) / max(hm, 1), 1.2)
        self.ox = (BOX[0] + BOX[2]) / 2 - self.S * (tr.cx.min() + tr.cx.max()) / 2
        self.oy = (BOX[1] + BOX[3]) / 2 - self.S * (tr.cy.min() + tr.cy.max()) / 2
        self.mpp = 1.0 / (self.S * self.K)           # meters per output pixel

    @classmethod
    def view(cls, W: int, H: int, S: float, ox: float, oy: float) -> "Layout":
        """Any frame: scale S (design units per meter) and offset."""
        o = cls.__new__(cls)
        o.W, o.H, o.K, o.S, o.ox, o.oy = W, H, W / 1920.0, S, ox, oy
        o.mpp = 1.0 / (S * o.K)
        return o

    def des(self, x, y):                              # meters → design units (1920x1080)
        return self.ox + self.S * np.asarray(x), self.oy + self.S * np.asarray(y)

    def meters(self, dx, dy):
        return (np.asarray(dx) - self.ox) / self.S, (np.asarray(dy) - self.oy) / self.S


def sun_position(lat: float, lon: float, when: dt.datetime):
    """Azimuth (from north, clockwise) and altitude of the Sun in degrees – approximate formulas."""
    n = (when.astimezone(dt.timezone.utc) - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400.0
    gm = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = math.radians((280.460 + 0.9856474 * n) % 360 + 1.915 * math.sin(gm) + 0.020 * math.sin(2 * gm))
    eps = math.radians(23.439 - 0.0000004 * n)
    ra, dec = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam)), math.asin(math.sin(eps) * math.sin(lam))
    ha = math.radians(((18.697374558 + 24.06570982441908 * n) % 24) * 15 + lon) - ra
    la = math.radians(lat)
    alt = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    az = math.atan2(-math.sin(ha), math.tan(dec) * math.cos(la) - math.sin(la) * math.cos(ha))
    return math.degrees(az) % 360, math.degrees(alt)
