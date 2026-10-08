"""Reading GPX files from many exporters: the small synthetic files in tests/data/gpx (see make_variants.py)."""
import gzip
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from gpxfilm.geo import prep_track, read_gpx
from offline import PLAIN, offline_run

ROOT = Path(__file__).resolve().parent.parent
GPX = ROOT / "tests" / "data" / "gpx"


def exits(name: str) -> str:
    """The message the program ends with for this file."""
    with pytest.raises(SystemExit) as e:
        read_gpx(GPX / name)
    return str(e.value)


# ── what kind of file is it ──

def test_plain_file_reads():
    g = read_gpx(GPX / "plain.gpx")
    assert len(g["pts"]) == 12 and g["name"] == "Ślad testowy"


def test_gzip_reads_like_plain():
    assert read_gpx(GPX / "plain.gpx.gz") == read_gpx(GPX / "plain.gpx")


def test_east_asian_encoding():
    g = read_gpx(GPX / "shift_jis.gpx")
    assert g["name"] == "富士山" and len(g["pts"]) == 12


@pytest.mark.parametrize("name, title", [("shift_jis_windows.gpx", "富士山①"), ("windows_31j.gpx", "富士山①"), ("big5_windows.gpx", "恒春")])
def test_east_asian_files_from_windows_tools(name, title):
    g = read_gpx(GPX / name)
    assert g["name"] == title and len(g["pts"]) == 12


def test_bom_and_leading_whitespace():
    assert read_gpx(GPX / "leading_space_bom.gpx")["pts"] == read_gpx(GPX / "plain.gpx")["pts"]


@pytest.mark.parametrize("name, words", [
    ("empty.gpx", "jest pusty"),
    ("truncated.gpx", "XML jest uszkodzony"),
    ("broken.gpx.gz", "uszkodzonym archiwum gzip"),
    ("activity.tcx", "plik TCX"),
    ("route.kml", "plik KML"),
    ("activity.fit", "plik FIT"),
    ("export.zip", "archiwum ZIP"),
    ("not_gpx.xml", "<osm>"),
    ("indoor_empty_trkseg.gpx", "trening bez GPS"),
    ("waypoints_only.gpx", "tylko pojedyncze punkty"),
    ("one_point.gpx", "tylko jeden punkt"),
    ("no_such_file.gpx", "Nie mogę odczytać pliku"),
    ("corrupt_deflate.gpx.gz", "uszkodzonym archiwum gzip"),
    ("activity_header12.fit", "plik FIT"),
    ("unknown_encoding.gpx", "kodowanie znaków, którego nie umiem odczytać (foobar-99)"),
    ("polar_waypoint_only.gpx", "tylko pojedyncze punkty"),
    ("shift_jis_truncated.gpx", "XML jest uszkodzony"),
    ("shift_jis_cut_in_char.gpx", "XML jest uszkodzony albo niepełny (urywa się w połowie znaku)"),
    ("shift_jis_bad_bytes.gpx", "znaki nie pasują do kodowania Shift_JIS"),
])
def test_clear_message(name, words):
    assert words in exits(name)


def offline_frame(gpx: Path, tmp_path: Path, *args: str) -> np.ndarray:
    return offline_run(gpx, tmp_path, *args)[0]


def test_gzip_frame_matches_plain(tmp_path):
    """A gzipped track gives exactly the frame of the plain one (end to end, offline)."""
    plain = ROOT / "tests" / "data" / "SLAD.gpx"
    gz = tmp_path / "SLAD.gpx.gz"
    gz.write_bytes(gzip.compress(plain.read_bytes(), mtime=0))
    assert np.array_equal(offline_frame(gz, tmp_path, "--size", "1280x720"), offline_frame(plain, tmp_path, "--size", "1280x720"))


# ── bad points are skipped ──

def test_bad_points_are_skipped(capsys):
    g = read_gpx(GPX / "bad_points.gpx")
    assert g["pts"] == read_gpx(GPX / "plain.gpx")["pts"]
    assert [w[2] for w in g["wpts"]] == ["Dobry"]
    assert "Pominięte punkty z błędnymi albo brakującymi współrzędnymi: 10." in capsys.readouterr().err
    assert prep_track(g).has_t


def test_activity_without_coordinates():
    assert "Żaden punkt śladu nie ma poprawnych współrzędnych" in exits("indoor_no_coords.gpx")


@pytest.mark.parametrize("name", ["same_time.gpx", "backwards_time.gpx"])
def test_times_not_going_forward(name, capsys):
    tr = prep_track(read_gpx(GPX / name))
    assert not tr.has_t and len(tr.cx) > 1
    assert "nie idą do przodu" in capsys.readouterr().err


def test_impossible_elevation_counts_as_missing():
    tr = prep_track(read_gpx(GPX / "nan_ele.gpx"))
    assert tr.has_e and np.isfinite(tr.ce).all()


# ── time notations ──

def test_time_notations():
    from gpxfilm.geo import parse_time
    import datetime as dt
    t0 = dt.datetime(2025, 6, 14, 8, 0, 0, tzinfo=dt.timezone.utc)
    g = read_gpx(GPX / "time_formats.gpx")
    assert [p[3] for p in g["pts"]] == [t0 + dt.timedelta(seconds=10 * i) for i in range(14)]
    assert prep_track(g).total == 130
    assert parse_time("2025-06-14T08:00:00.5Z").microsecond == 500000
    assert parse_time("2025-06-14T08:00:00.1234567Z").microsecond == 123456
    assert parse_time("2025-06-14T08:00:00,25Z").microsecond == 250000
    assert parse_time("2025-06-14T08:00Z") == t0
    assert parse_time("yesterday") is None and parse_time("2025-02-30T08:00:00Z") is None


# ── points without time or elevation, made-up times ──

def test_few_points_without_time_are_dropped(capsys):
    g = read_gpx(GPX / "partial_time.gpx")
    assert len(g["pts"]) == 19 and all(p[3] for p in g["pts"])
    assert "Pominięte punkty bez godziny albo z błędną godziną: 1." in capsys.readouterr().err
    assert prep_track(g).has_t


def test_sparse_times_are_ignored(capsys):
    g = read_gpx(GPX / "sparse_time.gpx")
    assert len(g["pts"]) == 12 and not any(p[3] for p in g["pts"])
    assert "Godzinę ma tylko 25% punktów" in capsys.readouterr().err


def test_repeated_fixes_are_merged():
    g = read_gpx(GPX / "zepp_duplicates.gpx")
    assert len(g["pts"]) == 12 and all(p[2] is not None for p in g["pts"])
    assert prep_track(g).has_e


@pytest.mark.parametrize("name, why", [
    ("ele_zero.gpx", "wszędzie 0 m"),
    ("ele_sparse.gpx", "tylko 20% punktów ma wysokość"),
    ("ele_long_gap.gpx", "luka w wysokości"),
    ("ele_all_sentinel.gpx", None),
])
def test_unusable_elevation_comes_from_terrain(name, why, capsys):
    g = read_gpx(GPX / name)
    assert not any(p[2] is not None for p in g["pts"])
    assert not prep_track(g).has_e
    if why:
        assert why in capsys.readouterr().err


@pytest.mark.parametrize("name", ["ele_sentinel.gpx", "ele_short_gap.gpx", "ele_negative.gpx"])
def test_usable_elevation_is_kept(name):
    tr = prep_track(read_gpx(GPX / name))
    assert tr.has_e and np.isfinite(tr.ce).all() and tr.ce.min() > -100


@pytest.mark.parametrize("name, why", [
    ("fake_1970.gpx", "start w 1970 roku"),
    ("fake_ms_ramp.gpx", "km/h"),
    ("fake_years_gap.gpx", "dni między punktami"),
])
def test_made_up_times(name, why, capsys):
    g = read_gpx(GPX / name)
    assert not any(p[3] for p in g["pts"])
    err = capsys.readouterr().err
    assert why in err and "--keep-times" in err
    assert prep_track(read_gpx(GPX / name, keep_times=True)).has_t


def test_fast_but_real_times_are_kept():
    assert prep_track(read_gpx(GPX / "fast_car.gpx")).has_t


def test_keep_times_option_exists():
    out = subprocess.run([sys.executable, "-m", "gpxfilm", "--help"], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, **PLAIN}).stdout
    assert "--keep-times" in out


# ── heart rate ──

@pytest.mark.parametrize("name", ["hr_garmin_ns3.gpx", "hr_gpxdata.gpx", "hr_heartrate.gpx", "hr_heart_rate.gpx", "hr_heatrate.gpx"])
def test_heart_rate_names(name):
    g = read_gpx(GPX / name)
    assert [p[4] for p in g["pts"]] == [120.0 + i for i in range(12)]
    assert prep_track(g).has_hr


def test_heart_rate_placeholders_are_missing():
    g = read_gpx(GPX / "hr_invalid.gpx")
    assert [p[4] for p in g["pts"]][:4] == [None, 121.0, 122.0, None]
    tr = prep_track(g)
    assert tr.has_hr and np.isfinite(tr.chr).all() and 115 < tr.chr.min() and tr.chr.max() < 135


def test_fractional_heart_rate():
    assert read_gpx(GPX / "hr_fractional.gpx")["pts"][1][4] == 120.83


def test_sparse_heart_rate_with_short_gaps():
    tr = prep_track(read_gpx(GPX / "hr_sparse.gpx"))
    assert tr.has_hr and np.isfinite(tr.chr).all()


def test_too_sparse_heart_rate():
    assert not prep_track(read_gpx(GPX / "hr_too_sparse.gpx")).has_hr


def test_long_heart_rate_gap_stays_empty():
    tr = prep_track(read_gpx(GPX / "hr_long_gap.gpx"))
    assert tr.has_hr
    gap = ~np.isfinite(tr.chr)
    assert gap.any() and np.isfinite(tr.chr[:8]).all() and np.isfinite(tr.chr[-8:]).all()


def test_film_frame_with_heart_rate_gap(tmp_path):
    """Live counters and coloring by heart rate work when the last third of the track has no heart rate (offline)."""
    src = (ROOT / "tests" / "data" / "tatry.gpx").read_text(encoding="utf-8").split("\n")
    pts = [i for i, ln in enumerate(src) if "<trkpt" in ln]
    for i in pts[2 * len(pts) // 3:]:
        src[i] = src[i].split("<extensions>")[0] + "</trkpt>"
    gpx = tmp_path / "tatry.gpx"
    gpx.write_text("\n".join(src), encoding="utf-8")
    frame = offline_frame(gpx, tmp_path, "--size", "960x540", "--counters", "--color-by", "heart-rate")
    assert frame.shape == (540, 960, 3)


# ── several tracks and segments ──

@pytest.mark.parametrize("name", ["multi_touching.gpx", "multi_unordered.gpx"])
def test_touching_tracks_are_joined_in_time_order(name, capsys):
    g = read_gpx(GPX / name)
    assert len(g["pts"]) == 24 and g["name"] == "Rano"
    assert [p[3] for p in g["pts"]] == sorted(p[3] for p in g["pts"])
    err = capsys.readouterr().err
    assert "W pliku są 2 ślady:" in err and "1. Rano – 0,6 km, 14.06.2025" in err and "łączę je w jeden przebieg. Jeden z nich wybierzesz opcją --track NR." in err
    assert prep_track(g).has_t


def test_one_track_per_day_is_joined():
    tr = prep_track(read_gpx(GPX / "multi_days.gpx"))
    assert tr.has_t and tr.total > 2 * 24 * 3600


def test_tracks_apart_take_the_longest(capsys):
    g = read_gpx(GPX / "multi_apart.gpx")
    assert g["name"] == "Długi" and len(g["pts"]) == 40
    assert "rysuję najdłuższy przebieg: ślady 2–3. Inny numer wybierzesz opcją --track NR." in capsys.readouterr().err


@pytest.mark.parametrize("choice, name, n", [(1, "Krótki", 12), (2, "Długi", 30), (3, "Dalszy ciąg", 10)])
def test_track_choice(choice, name, n, capsys):
    g = read_gpx(GPX / "multi_apart.gpx", track=choice)
    assert g["name"] == name and len(g["pts"]) == n
    assert f"Rysuję ślad {choice}." in capsys.readouterr().err


def test_one_day_of_touching_tracks():
    g = read_gpx(GPX / "multi_days.gpx", track=2)
    assert g["name"] == "Dzień 2" and len(g["pts"]) == 12


def test_track_choice_out_of_range():
    with pytest.raises(SystemExit) as e:
        read_gpx(GPX / "multi_apart.gpx", track=4)
    assert "Nie ma numeru 4: w pliku są 3 ślady." in str(e.value)


def test_tracks_without_times_keep_file_order(capsys):
    g = read_gpx(GPX / "multi_no_times.gpx")
    assert g["name"] == "Drugi"
    err = capsys.readouterr().err
    assert "1. Pierwszy – 0,5 km\n" in err and "Nie stykają się – rysuję najdłuższy przebieg: ślad 2." in err


def test_segments_of_one_track_are_joined(capsys):
    g = read_gpx(GPX / "segments.gpx")
    assert len(g["pts"]) == 24 and prep_track(g).has_t
    assert "W pliku" not in capsys.readouterr().err


def test_touching_routes_are_joined(capsys):
    assert len(read_gpx(GPX / "routes_touching.gpx")["pts"]) == 24
    err = capsys.readouterr().err
    assert "W pliku są 2 trasy:" in err and "łączę je w jeden przebieg. Jedną z nich wybierzesz opcją --track NR." in err


def test_several_routes_ignore_planning_times(capsys):
    g = read_gpx(GPX / "basecamp_routes.gpx")
    assert g["name"] == "Trasa 3" and not any(p[3] for p in g["pts"])
    err = capsys.readouterr().err
    assert "W pliku są 3 trasy:" in err and "najdłuższy przebieg: trasa 3." in err
    read_gpx(GPX / "basecamp_routes.gpx", track=2)
    assert "Rysuję trasę 2." in capsys.readouterr().err


def test_track_wins_over_route():
    assert read_gpx(GPX / "trk_and_rte.gpx")["name"] == "Ślad"


def test_title_from_track_name_not_app_name():
    assert read_gpx(GPX / "metadata_name.gpx")["name"] == "Poranny spacer"
    assert read_gpx(GPX / "metadata_name_only.gpx")["name"] == "Wycieczka"


def test_polish_counts():
    from gpxfilm.geo import _how_many
    assert [_how_many(n, "trk") for n in (1, 2, 5, 12, 22)] == ["jest 1 ślad", "są 2 ślady", "jest 5 śladów", "jest 12 śladów", "są 22 ślady"]
    assert _how_many(3, "rte") == "są 3 trasy"


def test_track_option_exists():
    out = subprocess.run([sys.executable, "-m", "gpxfilm", "--help"], cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, **PLAIN}).stdout
    assert "--track N" in out


# ── files with a route only ──

@pytest.mark.parametrize("name, has_e", [("route_trk_no_time.gpx", True), ("route_rte_ele.gpx", True), ("route_rte_bare.gpx", False)])
def test_planned_routes(name, has_e):
    tr = prep_track(read_gpx(GPX / name))
    assert not tr.has_t and tr.has_e == has_e
    assert 2700 < tr.dist < 2900                       # sparse points are not smoothed away


def test_basecamp_route_follows_the_road():
    g = read_gpx(GPX / "basecamp_rpt.gpx")
    assert len(g["pts"]) == 11 and not any(p[3] for p in g["pts"])
    assert max(p[1] for p in g["pts"]) > 20.003        # the road bends east between the via points
    assert prep_track(g).dist > 2400                   # longer than the straight lines (about 2220 m)


# ── single broken times, placeholders, positions beyond the map, tracks without extent ──

@pytest.mark.parametrize("name, n, bad", [("one_bad_time.gpx", 20, 1), ("first_bad_time.gpx", 20, 1),
                                          ("bad_time_run.gpx", 40, 3), ("bad_time_start.gpx", 200, 2),
                                          ("bad_time_second_last.gpx", 20, 1), ("bad_time_two_kinds.gpx", 40, 2),
                                          ("bad_time_start_and_middle.gpx", 60, 3), ("late_extension.gpx", 303, 3),
                                          ("days_broken_edges.gpx", 450, 8)])
def test_broken_times_do_not_drop_all_times(name, n, bad, capsys):
    g = read_gpx(GPX / name)
    assert len(g["pts"]) == n - bad and prep_track(g).has_t
    assert f"z błędną godziną: {bad}." in capsys.readouterr().err


@pytest.mark.parametrize("name, n", [("first_bad_time.gpx", 19), ("bad_time_start.gpx", 198), ("late_extension.gpx", 300)])
def test_keep_times_does_not_keep_broken_fixes(name, n):
    g = read_gpx(GPX / name, keep_times=True)
    assert len(g["pts"]) == n and prep_track(g).has_t


def test_short_end_run_in_the_past_keeps_the_times(capsys):
    g = read_gpx(GPX / "bad_time_end_run.gpx")
    tr = prep_track(g)
    assert tr.has_t and tr.total == 1950 and "udawane" not in capsys.readouterr().err


def test_times_jumping_back_are_made_up(capsys):
    g = read_gpx(GPX / "late_start.gpx")
    assert len(g["pts"]) == 500 and not any(p[3] for p in g["pts"])
    assert "udawane (przerwa" in capsys.readouterr().err


def test_tracks_days_apart_keep_all_points(capsys):
    g = read_gpx(GPX / "touching_days_apart.gpx")
    err = capsys.readouterr().err
    assert len(g["pts"]) == 252 and "udawane (przerwa 10 dni" in err and "błędną godziną" not in err


def test_placeholder_years_are_no_time():
    from gpxfilm.geo import parse_time
    assert parse_time("0001-01-01T00:00:00Z") is None and parse_time("9999-12-31T23:30:00Z") is None
    g = read_gpx(GPX / "year_one.gpx")                 # two tracks listed without crashing on the year 1
    assert len(g["pts"]) == 12


def test_made_up_times_keep_points_without_time():
    assert len(read_gpx(GPX / "fake_with_untimed.gpx")["pts"]) == 20


def test_positions_beyond_the_map_are_skipped(capsys):
    assert len(read_gpx(GPX / "polar.gpx")["pts"]) == 12
    err = capsys.readouterr().err
    assert "poza mapą (bliżej bieguna niż 85° szerokości): 2." in err and "błędnymi" not in err


@pytest.mark.parametrize("name", ["north_pole.gpx", "north_pole_glitch.gpx"])
def test_track_beyond_the_map(name):
    assert "mapa nie sięga tak daleko" in exits(name)


def test_waypoint_beyond_the_map(capsys):
    assert len(read_gpx(GPX / "pole_waypoint.gpx")["pts"]) == 12
    err = capsys.readouterr().err
    assert "poza mapą (bliżej bieguna niż 85° szerokości): 1." in err and "błędnymi" not in err


@pytest.mark.parametrize("name, words", [("one_place.gpx", "leżą w jednym miejscu"), ("one_long_stop.gpx", "jeden postój")])
def test_track_without_extent(name, words, capsys):
    with pytest.raises(SystemExit) as e:
        prep_track(read_gpx(GPX / name))
    assert words in str(e.value)
    assert "Wysokość z pliku" not in capsys.readouterr().err      # no promise of work that will not happen


@pytest.mark.parametrize("name", ["hr_isolated.gpx", "hr_pairs.gpx"])
def test_heart_rate_mostly_without_reading_is_skipped(name, capsys):
    assert not prep_track(read_gpx(GPX / name)).has_hr
    assert "Pomiar tętna obejmuje mniej niż 20% trasy" in capsys.readouterr().err


def test_heart_rate_coverage_is_measured_along_the_line():
    tr = prep_track(read_gpx(GPX / "hr_dense_then_none.gpx"))    # 17% of the time, 40% of the distance
    assert tr.has_hr and np.isnan(tr.chr).any()


def test_heart_rate_gaps_filled_by_distance_without_times():
    tr = prep_track(read_gpx(GPX / "hr_no_times.gpx"))
    assert not tr.has_t and tr.has_hr and np.isfinite(tr.chr).all()


def test_long_heart_rate_gap_stays_empty_without_times():
    tr = prep_track(read_gpx(GPX / "hr_no_times_long_gap.gpx"))
    assert not tr.has_t and tr.has_hr and np.isnan(tr.chr).any() and np.isfinite(tr.chr).mean() > 0.7
