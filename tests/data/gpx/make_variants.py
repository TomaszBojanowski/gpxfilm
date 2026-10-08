"""Write the small synthetic GPX variants used by tests/test_gpx_read.py.

    python tests/data/gpx/make_variants.py

Every file is written from scratch with made-up coordinates: a straight walk of a few hundred meters that starts at
49.5 N, 20.0 E. The notes in README.md say which real-world exporter each variant imitates.
"""
from __future__ import annotations

import datetime as dt
import io
import struct
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
NS = 'xmlns="http://www.topografix.com/GPX/1/1"'
TPX = 'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"'
T0 = dt.datetime(2025, 6, 14, 8, 0, 0, tzinfo=dt.timezone.utc)


def gz(data: bytes) -> bytes:
    """gzip with one uncompressed block and a fixed header: the same bytes on every Python and zlib (gzip.compress
    writes a different system byte on Python 3.10, and zlib-ng compresses differently)."""
    n = len(data)
    return b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03" + struct.pack("<BHH", 1, n, n ^ 0xFFFF) + data + struct.pack("<II", zlib.crc32(data), n)


def stamp(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def points(n: int = 12, *, tag: str = "trkpt", lat0: float = 49.5, lon0: float = 20.0, step: float = 0.0004, ele0: float | None = 800.0,
           t0: dt.datetime | None = T0, dt_s: float = 10.0, extra=None, inner=None) -> str:
    """n points going north-east; extra(i) adds text inside the point, inner(i) replaces ele/time entirely."""
    out = []
    for i in range(n):
        body = ""
        if inner is not None:
            body = inner(i)
        else:
            if ele0 is not None:
                body += f"<ele>{ele0 + 3 * i:.1f}</ele>"
            if t0 is not None:
                body += f"<time>{stamp(t0 + dt.timedelta(seconds=dt_s * i))}</time>"
        if extra is not None:
            body += extra(i)
        out.append(f'<{tag} lat="{lat0 + step * i:.6f}" lon="{lon0 + step * i:.6f}">{body}</{tag}>')
    return "\n".join(out)


def gpx(body: str, *, head: str = "", ns: str = NS) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="gpxfilm test" {ns}{head}>\n{body}\n</gpx>\n'


def trk(body: str, name: str | None = "Ślad testowy") -> str:
    return "<trk>" + (f"<name>{name}</name>" if name else "") + f"<trkseg>\n{body}\n</trkseg></trk>"


VARIANTS: dict[str, bytes | str] = {}

# ── what kind of file is it ──
VARIANTS["plain.gpx"] = gpx(trk(points()))
VARIANTS["empty.gpx"] = ""
VARIANTS["truncated.gpx"] = gpx(trk(points()))[:300]
VARIANTS["indoor_empty_trkseg.gpx"] = gpx("<trk><name>Bieżnia</name><type>running</type><trkseg/></trk>")
VARIANTS["waypoints_only.gpx"] = gpx('<wpt lat="49.5" lon="20.0"><name>Schronisko</name></wpt>\n<wpt lat="49.51" lon="20.01"><name>Przełęcz</name></wpt>')
VARIANTS["one_point.gpx"] = gpx(trk(points(1)))
VARIANTS["leading_space_bom.gpx"] = "﻿\n  " + gpx(trk(points()))
VARIANTS["activity.tcx"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
                            '<Activities><Activity Sport="Running"><Id>2025-06-14T08:00:00Z</Id></Activity></Activities></TrainingCenterDatabase>\n')
VARIANTS["route.kml"] = ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><LineString>'
                         '<coordinates>20.0,49.5,800 20.001,49.501,803</coordinates></LineString></Placemark></Document></kml>\n')
VARIANTS["not_gpx.xml"] = '<?xml version="1.0"?>\n<osm version="0.6"><node id="1" lat="49.5" lon="20.0"/></osm>\n'
# a FIT header only: header size 14, protocol, profile, data size, ".FIT", CRC
VARIANTS["activity.fit"] = bytes([14, 0x20, 0x5C, 0x08, 0, 0, 0, 0]) + b".FIT" + b"\x00\x00"
VARIANTS["plain.gpx.gz"] = gz(VARIANTS["plain.gpx"].encode())
VARIANTS["broken.gpx.gz"] = gz(VARIANTS["plain.gpx"].encode())[:40]
_zip = io.BytesIO()
with zipfile.ZipFile(_zip, "w") as z:
    z.writestr(zipfile.ZipInfo("activity.fit", date_time=(2025, 6, 14, 8, 0, 0)), VARIANTS["activity.fit"])
VARIANTS["export.zip"] = _zip.getvalue()

# ── bad points are skipped ──
_bad = ['<trkpt lon="20.0001"><time>2025-06-14T08:00:05Z</time></trkpt>',          # no lat
        '<trkpt lat="" lon="20.0002"><time>2025-06-14T08:00:15Z</time></trkpt>',  # empty
        '<trkpt lat="abc" lon="20.0003"/>',                                         # not a number
        '<trkpt lat="nan" lon="20.0004"/>',
        '<trkpt lat="49.5" lon="inf"/>',
        '<trkpt lat="95.0" lon="20.0005"/>',                                        # out of range
        '<trkpt lat="49.5" lon="200.0"/>',
        '<trkpt lat="0" lon="0"><ele>0</ele></trkpt>',                              # GPS glitch at 0, 0
        '<trkpt lat="49,5" lon="20,0"/>']                                           # comma decimals
_good = points().split("\n")
VARIANTS["bad_points.gpx"] = gpx('<wpt lon="20.0"><name>Bez szerokości</name></wpt>\n<wpt lat="49.5" lon="20.0"><name>Dobry</name></wpt>\n'
                                 + trk("\n".join(x for pair in zip(_good, _bad + [""] * 3) for x in pair if x)))
VARIANTS["indoor_no_coords.gpx"] = gpx(trk("\n".join(f'<trkpt><time>2025-06-14T08:00:{i:02d}Z</time><extensions><gpxtpx:TrackPointExtension>'
                                                     f'<gpxtpx:hr>{120 + i}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>'
                                                     for i in range(12))), head=" " + TPX)
VARIANTS["same_time.gpx"] = gpx(trk(points(t0=T0, dt_s=0)))
VARIANTS["backwards_time.gpx"] = gpx(trk(points(t0=T0, dt_s=-10)))
VARIANTS["nan_ele.gpx"] = gpx(trk(points(24, inner=lambda i: f"<ele>{'nan' if i == 3 else 'inf' if i == 4 else 800 + i}</ele><time>{stamp(T0 + dt.timedelta(seconds=10 * i))}</time>")))

# ── time notations (each point is T0 + 10 s * i, written differently) ──
_H2 = dt.timedelta(hours=2)
_NOTATIONS = [lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ"),                    # standard
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%Sz"),                    # lowercase z
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.0Z"),                  # one fraction digit
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.0000000Z"),            # seven digits
              lambda t: (t + _H2).strftime("%Y-%m-%dT%H:%M:%S+0200"),        # offset without colon
              lambda t: (t + _H2).strftime("%Y-%m-%dT%H:%M:%S+02"),          # hours-only offset
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%S,000Z"),                # comma before the fraction
              lambda t: t.strftime("%Y-%m-%d %H:%M:%SZ"),                    # space instead of T
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%S"),                     # no zone: UTC
              lambda t: (t + _H2).strftime("%Y-%m-%dT%H:%M:%S+02:00"),       # offset with colon
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),          # nine digits
              lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.00"),                  # two digits, no zone (Sports Tracker)
              lambda t: (t - dt.timedelta(hours=1, minutes=30)).strftime("%Y-%m-%dT%H:%M:%S-01:30"),
              lambda t: t.strftime("  %Y-%m-%dT%H:%M:%S Z ")]                # spaces around
VARIANTS["time_formats.gpx"] = gpx(trk(points(len(_NOTATIONS), inner=lambda i: f"<ele>{800 + i}</ele><time>{_NOTATIONS[i](T0 + dt.timedelta(seconds=10 * i))}</time>")))

# ── points without time or elevation, made-up times ──
def _t(i, step=10.0, t0=T0):
    return stamp(t0 + dt.timedelta(seconds=step * i))


VARIANTS["partial_time.gpx"] = gpx(trk(points(20, inner=lambda i: f"<ele>{800 + i}</ele>" + ("" if i == 7 else f"<time>{_t(i)}</time>"))))
VARIANTS["sparse_time.gpx"] = gpx(trk(points(12, inner=lambda i: f"<ele>{800 + i}</ele>" + (f"<time>{_t(i)}</time>" if i % 4 == 0 else ""))))
VARIANTS["zepp_duplicates.gpx"] = gpx(trk("\n".join(
    f'<trkpt lat="{49.5 + 0.0004 * i:.6f}" lon="{20.0 + 0.0004 * i:.6f}"><time>{_t(i)}</time>{"" if k == 0 else f"<ele>{800 + i}</ele>"}</trkpt>'
    for i in range(12) for k in (0, 1))))
VARIANTS["ele_zero.gpx"] = gpx(trk(points(12, inner=lambda i: f"<ele>0.0</ele><time>{_t(i)}</time>")))
VARIANTS["ele_sentinel.gpx"] = gpx(trk(points(24, inner=lambda i: f"<ele>{-20000 if i < 2 else 800 + i}</ele><time>{_t(i)}</time>")))
VARIANTS["ele_all_sentinel.gpx"] = gpx(trk(points(12, inner=lambda i: f"<ele>-20000</ele><time>{_t(i)}</time>")))
VARIANTS["ele_short_gap.gpx"] = gpx(trk(points(30, inner=lambda i: ("" if i in (14, 15) else f"<ele>{800 + i}</ele>") + f"<time>{_t(i)}</time>")))
VARIANTS["ele_long_gap.gpx"] = gpx(trk(points(60, inner=lambda i: ("" if 20 <= i < 25 else f"<ele>{800 + i}</ele>") + f"<time>{_t(i)}</time>")))
VARIANTS["ele_sparse.gpx"] = gpx(trk(points(25, inner=lambda i: (f"<ele>{800 + i}</ele>" if i % 5 == 0 else "") + f"<time>{_t(i)}</time>")))
VARIANTS["ele_negative.gpx"] = gpx(trk(points(12, inner=lambda i: f"<ele>{[-5.2, 0.0, -1.0, 2.5][i % 4]}</ele><time>{_t(i)}</time>")))
VARIANTS["fake_1970.gpx"] = gpx(trk(points(12, t0=dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc))))
VARIANTS["fake_ms_ramp.gpx"] = gpx(trk(points(12, inner=lambda i: f"<ele>{800 + i}</ele><time>{(T0 + dt.timedelta(milliseconds=5 * i)).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z</time>")))
VARIANTS["fake_years_gap.gpx"] = gpx(trk(points(12, inner=lambda i: f"<ele>{800 + i}</ele><time>{_t(i, t0=T0.replace(year=2014) if i < 6 else T0)}</time>")))
VARIANTS["fast_car.gpx"] = gpx(trk(points(12, dt_s=1.5)))

# ── heart rate ──
GPXDATA = 'xmlns:gpxdata="http://www.cluetrust.com/XML/GPXDATA/1/0"'
NS3 = 'xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"'
_HR = {
    "hr_garmin_ns3.gpx": (" " + NS3, lambda v: f"<extensions><ns3:TrackPointExtension><ns3:atemp>21.0</ns3:atemp><ns3:hr>{v}</ns3:hr>"
                                              "<ns3:cad>0</ns3:cad></ns3:TrackPointExtension></extensions>"),
    "hr_gpxdata.gpx": (" " + GPXDATA, lambda v: f"<extensions><gpxdata:hr>{v}</gpxdata:hr><gpxdata:cadence>80</gpxdata:cadence></extensions>"),
    "hr_heartrate.gpx": ("", lambda v: f"<extensions><heartrate>{v}</heartrate></extensions>"),
    "hr_heart_rate.gpx": ("", lambda v: f"<extensions><heart_rate>{v}</heart_rate></extensions>"),
    "hr_heatrate.gpx": (" " + GPXDATA, lambda v: f"<extensions><gpxdata:heatrate>{v}</gpxdata:heatrate></extensions>"),
}
for _name, (_ns, _ext) in _HR.items():
    VARIANTS[_name] = gpx(trk(points(12, extra=lambda i, f=_ext: f(120 + i))), head=_ns)
_tpx = lambda v: f"<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{v}</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>"
VARIANTS["hr_invalid.gpx"] = gpx(trk(points(12, extra=lambda i: _tpx(["255", "0", "-nan", "abc", "300"][i // 3 % 5] if i % 3 == 0 else 120 + i))), head=" " + TPX)
VARIANTS["hr_fractional.gpx"] = gpx(trk(points(12, extra=lambda i: _tpx(f"{120.5 + i / 3:.2f}"))), head=" " + TPX)
VARIANTS["hr_sparse.gpx"] = gpx(trk(points(25, extra=lambda i: _tpx(130 + i) if i % 5 == 0 else "")), head=" " + TPX)
VARIANTS["hr_too_sparse.gpx"] = gpx(trk(points(30, extra=lambda i: _tpx(130 + i) if i % 10 == 0 else "")), head=" " + TPX)
VARIANTS["hr_long_gap.gpx"] = gpx(trk(points(40, extra=lambda i: "" if 10 <= i < 26 else _tpx(130 + i))), head=" " + TPX)

# ── several tracks and segments ──
_DAY = dt.timedelta(days=1)
_A = points(12, t0=T0)                                                           # ends at 49.5044, 20.0044
_B = points(12, lat0=49.5070, lon0=20.0044, t0=T0 + dt.timedelta(minutes=10))    # starts about 290 m further north
VARIANTS["multi_touching.gpx"] = gpx(trk(_A, "Rano") + "\n" + trk(_B, "Po przerwie"))
VARIANTS["multi_unordered.gpx"] = gpx(trk(_B, "Po przerwie") + "\n" + trk(_A, "Rano"))
VARIANTS["multi_days.gpx"] = gpx("\n".join(trk(points(12, lat0=49.5 + 0.0044 * d, lon0=20.0 + 0.0044 * d, t0=T0 + d * _DAY), f"Dzień {d + 1}")
                                           for d in range(3)))
_FAR = points(30, lat0=49.6, lon0=20.2, t0=T0 + _DAY)                            # about 18 km away
_FAR_NEXT = points(10, lat0=49.6118, lon0=20.2118, t0=T0 + _DAY + dt.timedelta(hours=1))
VARIANTS["multi_apart.gpx"] = gpx(trk(_A, "Krótki") + "\n" + trk(_FAR, "Długi") + "\n" + trk(_FAR_NEXT, "Dalszy ciąg"))
VARIANTS["multi_no_times.gpx"] = gpx(trk(points(10, t0=None), "Pierwszy") + "\n" + trk(points(20, lat0=49.6, lon0=20.2, t0=None), "Drugi"))
VARIANTS["segments.gpx"] = gpx("<trk><name>Z przerwami</name>" + "".join(
    f"<trkseg>\n{points(8, lat0=49.5 + 0.0032 * k, lon0=20.0 + 0.0032 * k, t0=T0 + dt.timedelta(seconds=110 * k))}\n</trkseg>" for k in range(3)) + "</trk>")
VARIANTS["routes_touching.gpx"] = gpx("<rte><name>Do schroniska</name>\n" + points(12, tag="rtept") + "\n</rte>\n<rte><name>Na szczyt</name>\n"
                                      + points(12, tag="rtept", lat0=49.5048, lon0=20.0048) + "\n</rte>")
VARIANTS["basecamp_routes.gpx"] = gpx("\n".join(
    f"<rte><name>Trasa {k + 1}</name>\n" + points(6 + 4 * k, tag="rtept", lat0=49.5 + 0.05 * k, lon0=20.0, step=0.002,
                                                    t0=T0 - dt.timedelta(days=30 * k), dt_s=-900 if k % 2 else 1337) + "\n</rte>"
    for k in range(3)))
VARIANTS["trk_and_rte.gpx"] = gpx(trk(points(20), "Ślad") + "\n<rte><name>Trasa</name>\n" + points(3, tag="rtept", step=0.003, t0=None) + "\n</rte>")
VARIANTS["metadata_name.gpx"] = gpx("<metadata><name>Zepp Life</name></metadata>\n" + trk(points(), "Poranny spacer"))
VARIANTS["metadata_name_only.gpx"] = gpx("<metadata><name>Wycieczka</name></metadata>\n" + trk(points(), None))

# ── files with a route only ──
GPXX = 'xmlns:gpxx="http://www.garmin.com/xmlschemas/GpxExtensions/v3"'
VARIANTS["route_trk_no_time.gpx"] = gpx(trk(points(15, step=0.0015, t0=None), "Zaplanowana trasa"))
VARIANTS["route_rte_ele.gpx"] = gpx("<rte><name>Trasa z wysokością</name>\n" + points(15, tag="rtept", step=0.0015, t0=None) + "\n</rte>")
VARIANTS["route_rte_bare.gpx"] = gpx("<rte><name>Same punkty</name>\n" + points(15, tag="rtept", step=0.0015, t0=None, ele0=None) + "\n</rte>")


def _via(k: int, last: bool) -> str:
    """A via point of a BaseCamp route; the road to the next one bends east, written as gpxx:rpt points."""
    lat, lon = 49.5 + 0.01 * k, 20.0
    road = "" if last else "".join(f'<gpxx:rpt lat="{lat + 0.002 * j:.6f}" lon="{lon + 0.004 * (j * (5 - j)) / 6:.6f}"/>' for j in range(1, 5))
    ext = "" if last else f"<extensions><gpxx:RoutePointExtension><gpxx:Subclass>000000000000FFFFFFFFFFFFFFFFFFFFFFFF</gpxx:Subclass>{road}</gpxx:RoutePointExtension></extensions>"
    return f'<rtept lat="{lat:.6f}" lon="{lon:.6f}"><time>2025-05-0{k + 1}T10:00:00Z</time><name>Punkt {k + 1}</name>{ext}</rtept>'


VARIANTS["basecamp_rpt.gpx"] = gpx("<rte><name>Droga przez wieś</name>\n" + "\n".join(_via(k, k == 2) for k in range(3)) + "\n</rte>", head=" " + GPXX)

# ── more broken or unusual files ──
_flip = bytearray(VARIANTS["plain.gpx.gz"])
_flip[10] ^= 0xFF                                                               # corrupt deflate block header, gzip header intact
VARIANTS["corrupt_deflate.gpx.gz"] = bytes(_flip)
VARIANTS["activity_header12.fit"] = bytes([12, 0x10, 0x5C, 0x08, 0, 0, 0, 0]) + b".FIT"   # older 12-byte FIT header
VARIANTS["shift_jis.gpx"] = gpx(trk(points(), "富士山")).replace('encoding="UTF-8"', 'encoding="Shift_JIS"').encode("shift_jis")
VARIANTS["unknown_encoding.gpx"] = gpx(trk(points())).replace('encoding="UTF-8"', 'encoding="foobar-99"')
_sjis = gpx(trk(points(), "富士山①")).replace('encoding="UTF-8"', 'encoding = "Shift_JIS"')   # ① exists only in the Windows code page
VARIANTS["shift_jis_windows.gpx"] = _sjis.encode("cp932")
VARIANTS["windows_31j.gpx"] = _sjis.replace('"Shift_JIS"', '"Windows-31J"').encode("cp932")
VARIANTS["shift_jis_truncated.gpx"] = VARIANTS["shift_jis.gpx"][:400]
_cut = VARIANTS["shift_jis.gpx"]
VARIANTS["shift_jis_cut_in_char.gpx"] = _cut[:_cut.index("富".encode("shift_jis")) + 1]              # ends with half a character
VARIANTS["big5_windows.gpx"] = gpx(trk(points(), "恒春")).replace('encoding="UTF-8"', 'encoding="Big5"').encode("cp950")   # 恒 only in cp950
VARIANTS["shift_jis_bad_bytes.gpx"] = VARIANTS["shift_jis.gpx"].replace("富".encode("shift_jis"), b"\x81\x7f")

VARIANTS["one_bad_time.gpx"] = gpx(trk(points(20, inner=lambda i: f"<ele>{800 + i}</ele><time>{'1970-01-01T00:00:00Z' if i == 9 else _t(i)}</time>")))
VARIANTS["first_bad_time.gpx"] = gpx(trk(points(20, inner=lambda i: f"<ele>{800 + i}</ele><time>{'2006-02-12T08:00:00Z' if i == 0 else _t(i)}</time>")))
VARIANTS["bad_time_run.gpx"] = gpx(trk(points(40, inner=lambda i: f"<ele>{800 + i}</ele><time>{f'1970-01-01T00:00:0{i - 20}Z' if 20 <= i < 23 else _t(i)}</time>")))
VARIANTS["bad_time_start.gpx"] = gpx(trk(points(200, step=0.0001, inner=lambda i: f"<ele>{800 + i}</ele><time>{f'2006-02-12T08:00:0{i}Z' if i < 2 else _t(i, step=3.0)}</time>")))
VARIANTS["bad_time_second_last.gpx"] = gpx(trk(points(20, inner=lambda i: f"<ele>{800 + i}</ele><time>{'1970-01-01T00:00:00Z' if i == 18 else _t(i)}</time>")))
_KINDS = {20: "1970-01-01T00:00:00Z", 21: "2006-02-12T08:03:30Z"}                  # epoch 0, then a GPS week rollover
VARIANTS["bad_time_two_kinds.gpx"] = gpx(trk(points(40, inner=lambda i: f"<ele>{800 + i}</ele><time>{_KINDS.get(i) or _t(i)}</time>")))
_EPOCH = {0: "1970-01-01T00:00:00Z", 1: "1970-01-01T00:00:01Z", 30: "1970-01-01T00:00:30Z"}      # the same wrong date twice
VARIANTS["bad_time_start_and_middle.gpx"] = gpx(trk(points(60, inner=lambda i: f"<ele>{800 + i}</ele><time>{_EPOCH.get(i) or _t(i)}</time>")))
_T2014 = dt.datetime(2014, 6, 14, 8, 0, 0, tzinfo=dt.timezone.utc)
VARIANTS["late_extension.gpx"] = gpx(trk(points(303, step=0.0001, inner=lambda i: f"<ele>{800 + i % 100}</ele><time>{_t(i, t0=_T2014) if i < 300 else _t(i)}</time>")))
_EDGE = {(0, i): "1970-01-01T00:00:0%dZ" % i for i in range(4)} | {(1, 146 + i): "1970-01-01T00:00:0%dZ" % i for i in range(4)}
VARIANTS["days_broken_edges.gpx"] = gpx("\n".join(trk(points(150, lat0=49.5 + 0.015 * d, lon0=20.0 + 0.015 * d, step=0.0001, inner=lambda i, d=d:
    f"<ele>{800 + i}</ele><time>{_EDGE.get((d, i)) or _t(i, t0=T0 + d * dt.timedelta(days=1))}</time>"), f"Dzień {d + 1}") for d in range(3)))
VARIANTS["bad_time_end_run.gpx"] = gpx(trk(points(200, step=0.0001, inner=lambda i: f"<ele>{800 + i % 100}</ele><time>{'1970-01-01T00:00:0%dZ' % (i - 196) if i >= 196 else _t(i)}</time>")))
VARIANTS["late_start.gpx"] = gpx(trk(points(500, step=0.0001, inner=lambda i: f"<ele>{800 + i % 100}</ele><time>{'2038-01-19T03:14:0%dZ' % i if i < 6 else _t(i)}</time>")))
VARIANTS["touching_days_apart.gpx"] = gpx(trk(points(2), "Rozgrzewka") + "\n"
                                          + trk(points(250, lat0=49.5008, lon0=20.0008, step=0.0001, t0=T0 + dt.timedelta(days=10)), "Wyprawa"))
VARIANTS["year_one.gpx"] = gpx(trk(points(12, t0=None, extra=lambda i: "<time>0001-01-01T00:00:00Z</time>")) + "\n" + trk(points(12, lat0=49.6, t0=T0), "Drugi"))
VARIANTS["fake_with_untimed.gpx"] = gpx(trk(points(20, t0=None, extra=lambda i: "" if i == 7 else f"<time>{_t(i, t0=dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc))}</time>")))
VARIANTS["polar.gpx"] = gpx(trk(points(12) + '\n<trkpt lat="-90.0" lon="20.0"/>\n<trkpt lat="89.9" lon="20.0"/>'))
VARIANTS["north_pole.gpx"] = gpx(trk(points(12, lat0=89.5)))
VARIANTS["north_pole_glitch.gpx"] = gpx(trk('<trkpt lat="0" lon="0"/>\n' + points(12, lat0=89.5)))
VARIANTS["polar_waypoint_only.gpx"] = gpx('<wpt lat="90.0" lon="0.0"><name>Biegun Północny</name></wpt>\n<wpt lat="78.22" lon="15.65"><name>Longyearbyen</name></wpt>')
VARIANTS["pole_waypoint.gpx"] = gpx('<wpt lat="90.0" lon="0.0"><name>Biegun Północny</name></wpt>\n' + trk(points(12, lat0=78.2, lon0=15.6)))
VARIANTS["one_place.gpx"] = gpx(trk(points(30, step=0.000004, inner=lambda i: f"<ele>2330.0</ele><time>{_t(i)}</time>")))   # 15 m in all
VARIANTS["one_long_stop.gpx"] = gpx(trk(points(61, step=0.0000036, inner=lambda i: f"<ele>1450.0</ele><time>{_t(i)}</time>")))   # 29 m in 10 min

VARIANTS["hr_isolated.gpx"] = gpx(trk(points(65, dt_s=20, extra=lambda i: _tpx(130 + i % 7) if i % 4 == 2 else "")), head=" " + TPX)
VARIANTS["hr_no_times.gpx"] = gpx(trk(points(20, t0=None, extra=lambda i: "" if i in (5, 11, 12) else _tpx(130 + i))), head=" " + TPX)
_FAST = points(100, step=0.00004, dt_s=2, extra=lambda i: _tpx(150 + i % 9))                # strap on, 2 s apart, 5 m apart
_SLOW = points(50, lat0=49.5040, lon0=20.0040, step=0.00012, dt_s=20, t0=T0 + dt.timedelta(seconds=200))   # strap off, 20 s, 16 m
VARIANTS["hr_dense_then_none.gpx"] = gpx(trk(_FAST + "\n" + _SLOW), head=" " + TPX)
VARIANTS["hr_pairs.gpx"] = gpx(trk(points(66, extra=lambda i: _tpx(130 + i % 7) if i % 8 < 2 else "")), head=" " + TPX)
VARIANTS["hr_no_times_long_gap.gpx"] = gpx(trk(points(40, t0=None, extra=lambda i: "" if 20 <= i < 26 else _tpx(130 + i % 7))), head=" " + TPX)


# ── distance like a watch ──
STOP_FROM, STOP_TO, SPIKE_S = 240.0, 540.0, 1000.0                         # seconds after the start


def _walk_with_stop_and_spike() -> str:
    """Up 12 switchbacks (legs of 40 m, 8 m apart), a 5-minute stop, then 500 m straight on; a fix every 2 s at 1.2 m/s,
    1 m of GPS noise on every fix, and one fix thrown 300 m north (the spike, SPIKE_S after the start)."""
    import numpy as np
    rnd = np.random.default_rng(11)
    corners = [(0.0, 0.0)]
    for k in range(12):
        x0, y0 = corners[-1]
        corners += [(x0 + (40.0 if k % 2 == 0 else -40.0), y0), (x0 + (40.0 if k % 2 == 0 else -40.0), y0 + 8.0)]
    xe, ye = corners[-1]
    corners.append((xe, ye + 500.0))
    c = np.array(corners)
    along = np.r_[0, np.cumsum(np.hypot(*np.diff(c, axis=0).T))]
    climb_end = along[-2]                                                 # the switchbacks climb, the straight part is flat
    stop_at, stop_s = along[12], STOP_TO - STOP_FROM                      # after six switchbacks, reached at STOP_FROM
    out, t = [], 0.0
    while True:
        d = 1.2 * t if 1.2 * t < stop_at else stop_at if 1.2 * (t - stop_s) < stop_at else 1.2 * (t - stop_s)
        if d > along[-1]:
            break
        x, y = np.interp(d, along, c[:, 0]), np.interp(d, along, c[:, 1])
        x, y = x + rnd.normal(0, 1.0), y + rnd.normal(0, 1.0)
        if t == SPIKE_S:
            y += 300.0
        ele = 1000.0 + 0.1 * min(d, climb_end) + rnd.normal(0, 0.5)
        lat, lon = 49.5 + y / 111320.0, 20.0 + x / (111320.0 * np.cos(np.radians(49.5)))
        out.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{ele:.1f}</ele><time>{_t(t, step=1.0)}</time></trkpt>')
        t += 2.0
    return "\n".join(out)


VARIANTS["switchbacks_stop_spike.gpx"] = gpx(trk(_walk_with_stop_and_spike(), "Zakosy"))


def _fixes(xy_t, ele=800.0) -> str:
    """trkpt lines from (x east, y north in meters from 49.5 N 20.0 E, seconds after T0)."""
    import math
    k = 111320.0 * math.cos(math.radians(49.5))
    return "\n".join(f'<trkpt lat="{49.5 + y / 111320.0:.7f}" lon="{20.0 + x / k:.7f}"><ele>{ele:.1f}</ele><time>{_t(t, step=1.0)}</time></trkpt>'
                     for x, y, t in xy_t)


def _line(n, step_m, dt_s, noise=1.0, seed=3):
    import numpy as np
    rnd = np.random.default_rng(seed)
    return [[float(rnd.normal(0, noise)), i * step_m + float(rnd.normal(0, noise)), i * dt_s] for i in range(n)]


_series = _line(200, 1.3, 1)
for _i in (100, 101, 102):                                                    # multipath: three fixes 120 m to the side
    _series[_i][0] += 120.0
VARIANTS["spike_series.gpx"] = gpx(trk(_fixes(_series)))
_cold = _line(200, 1.3, 1)
_cold[0][0] += 500.0                                                          # the first fix from where the watch was last used
VARIANTS["cold_start.gpx"] = gpx(trk(_fixes(_cold)))
_moved = _line(200, 6.5, 5) + [[3000.0, 1300.0, 199 * 5 + 1800]]             # paused, driven 3 km, one more fix there
VARIANTS["relocated_end.gpx"] = gpx(trk(_fixes(_moved)))
_car = _line(100, 200.0, 10, noise=3.0)
_car[50][0] += 1500.0                                                         # 1.5 km off: within 8 car steps, but 540 km/h
VARIANTS["car_spike.gpx"] = gpx(trk(_fixes(_car)))


def _sparse_spur():
    """Running at 3 m/s with a fix every 60 s: 1 km, a 150 m spur to a viewpoint and back, 1 km on."""
    import numpy as np
    c = np.array([(0, 0), (0, 1000), (150, 1000), (0, 1002), (0, 2000)], float)
    along = np.r_[0, np.cumsum(np.hypot(*np.diff(c, axis=0).T))]
    ts = np.arange(0, along[-1] / 3.0, 60.0)
    return [[float(np.interp(3 * t, along, c[:, 0])), float(np.interp(3 * t, along, c[:, 1])), float(t)] for t in ts]


VARIANTS["sparse_spur.gpx"] = gpx(trk(_fixes(_sparse_spur())))


def main() -> None:
    for name, content in VARIANTS.items():
        (HERE / name).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    print(f"{len(VARIANTS)} files in {HERE}")


if __name__ == "__main__":
    main()
