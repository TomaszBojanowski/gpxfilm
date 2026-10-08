"""Distance and ascent shown on the film, and the values from the watch (--distance, --ascent)."""
import datetime as dt_
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from gpxfilm import geo
from gpxfilm.compose import km_marks, live_speed
from gpxfilm.geo import _spikes, match_watch, prep_track, raw_distance, read_gpx
from gpxfilm.util import km1
from offline import offline_run

ROOT = Path(__file__).resolve().parent.parent
SLAD = ROOT / "tests" / "data" / "SLAD.gpx"


def test_watch_values_end_the_film_exactly():
    tr = prep_track(read_gpx(SLAD))
    cd = tr.cd.copy()
    match_watch(tr, 12.5, 777)
    assert tr.rdist == 12500 and tr.rd[-1] == pytest.approx(12500) and tr.cup[-1] == pytest.approx(777)
    assert np.all(np.diff(tr.rd) >= 0) and np.array_equal(tr.cd, cd)        # the drawn line stays as it was


def test_ascent_from_the_watch_on_a_flat_track():
    tr = prep_track(read_gpx(SLAD))
    tr.cup = np.zeros_like(tr.cup)
    match_watch(tr, None, 120)
    assert tr.cup[-1] == pytest.approx(120) and np.all(np.diff(tr.cup) >= 0)


def test_kilometer_markers_follow_the_watch():
    tr = prep_track(read_gpx(SLAD))
    marks = km_marks(tr)
    assert [k for k, _ in marks] == list(range(1, int(tr.rdist / 1000) + 1))
    match_watch(tr, 12.5, None)
    marks = km_marks(tr)
    assert [k for k, _ in marks] == list(range(1, 13))
    assert np.interp(marks[-1][1], tr.cd, tr.rd) == pytest.approx(12000)        # marker 12 stands where the counter shows 12 km


def test_watch_values_reach_the_report(tmp_path):
    golden = json.loads((ROOT / "tests" / "data" / "golden" / "slad.json").read_text(encoding="utf-8"))
    _, rep = offline_run(SLAD, tmp_path, "--size", "1280x720", "--distance", "12.5", "--ascent", "777")
    assert rep["distance_km"] == 12.5
    scale = 12.5 / golden["distance_km"]
    for got, ref in zip(rep["places"], golden["places"]):
        assert got["name"] == ref["name"] and got["km"] == pytest.approx(ref["km"] * scale, abs=0.15)


def test_watch_values_reach_every_number_on_the_film(tmp_path):
    """The distance counter while the line is drawn, the profile label and the ascent counter all follow the watch."""
    def run(name, *args):
        kadry = tmp_path / name
        final, _ = offline_run(SLAD, tmp_path, "--size", "960x540", "--slides", str(kadry), *args)
        mid = np.asarray(Image.open(sorted(kadry.glob("0[1-9]-*.png"))[0]).convert("RGB"))
        return final.astype(int), mid.astype(int)

    def changed(a, b, x0, x1, y0, y1):                         # in 1920 × 1080 units of the design
        return np.abs(a - b)[y0 // 2:y1 // 2, x0 // 2:x1 // 2].max() > 60

    final, mid = run("file")
    final_km, mid_km = run("km", "--distance", "9.9")
    final_up, _ = run("up", "--ascent", "2000")
    assert changed(mid, mid_km, 96, 250, 250, 600)            # the distance counter half way
    assert changed(final, final_km, 480, 660, 930, 975)       # the label at the end of the profile
    assert changed(final, final_up, 260, 520, 250, 600) and not changed(final, final_up, 96, 250, 250, 600)


def test_watch_values_round_halves_up():
    assert [km1(v * 1000) for v in (12.45, 12.25, 9.95, 7.549)] == [12.5, 12.3, 10.0, 7.5]
    tr = prep_track(read_gpx(SLAD))
    match_watch(tr, 12.45, None)
    assert km1(tr.rdist) == 12.5


def test_watch_values_on_a_track_without_counted_meters():
    tr = prep_track(read_gpx(SLAD))
    tr.rd, tr.rdist, tr.cup = np.zeros_like(tr.rd), 0.0, np.zeros_like(tr.cup)   # all of it slower than a stop
    match_watch(tr, 2.0, 50)
    for v, end in ((tr.rd, 2000), (tr.cup, 50)):
        assert np.all(np.isfinite(v)) and np.all(np.diff(v) >= 0) and v[-1] == pytest.approx(end)


def test_speed_follows_the_distance_on_the_film():
    tr = prep_track(read_gpx(SLAD))
    gd = np.linspace(0, tr.dist, 421)
    before = live_speed(tr, gd)
    moving = tr.total - float(np.sum(tr.tout - tr.tin))
    assert np.median(before) == pytest.approx(tr.rdist / moving * 3.6, rel=0.25)
    match_watch(tr, 2 * tr.rdist / 1000, None)
    assert live_speed(tr, gd) == pytest.approx(2 * before)


def _slow_end():
    """1 km on the flat at 1.2 m/s, then 12 minutes at 0.3 m/s up 150 m to a summit, where the recording ends."""
    t0 = dt_.datetime(2025, 7, 14, 8, tzinfo=dt_.timezone.utc)
    t = np.r_[np.arange(0, 834, 1.0), 834 + np.arange(0, 720, 1.0)]
    north = np.r_[1.2 * np.arange(0, 834, 1.0), 1000.8 + 0.3 * np.arange(0, 720, 1.0)]
    ele = np.r_[np.full(834, 1000.0), 1000 + 150 * np.arange(0, 720, 1.0) / 719]
    return [(49.5 + d / 111320, 20.0, e, t0 + dt_.timedelta(seconds=float(s)), None) for d, e, s in zip(north, ele, t)]


def test_a_stop_at_the_end_keeps_its_distance_and_ascent():
    tr = prep_track(dict(pts=_slow_end(), wpts=[], name="", ele_note=None))
    assert tr.rdist == pytest.approx(1000.8 + 0.3 * 719, rel=0.01)
    assert tr.cup[-1] == pytest.approx(150, abs=3) and tr.ce[-1] == pytest.approx(1150, abs=3)


def test_a_stop_at_the_end_keeps_its_heart_rate():
    pts = [q[:4] + (110 + 50 * max(0, k - 834) / 719,) for k, q in enumerate(_slow_end())]   # 110 on the flat, up to 160 on top
    tr = prep_track(dict(pts=pts, wpts=[], name="", ele_note=None))
    assert tr.chr[-1] == pytest.approx(160, abs=2)


def test_a_stop_at_the_end_keeps_the_ascent_of_the_frame_model():
    from types import SimpleNamespace
    from gpxfilm.compose import frame_elevation
    tr = prep_track(dict(pts=[q[:2] + (None,) + q[3:] for q in _slow_end()], wpts=[], name="", ele_note=None))
    y0 = float(tr.proj.fwd(_slow_end()[0][0], 20.0)[1])
    lay = SimpleNamespace(K=1.0, W=1920, H=2000, des=lambda x, y: (np.asarray(x) / 10 + 960, (np.asarray(y) - y0) / 10 + 1900))
    D = np.repeat(1000 + np.clip(1900 - 100.08 - np.arange(2000.0), 0, None)[:, None] * 150 / 21.6, 1920, axis=1)  # flat, then 150 m up
    frame_elevation(tr, lay, D)
    assert tr.cup[-1] == pytest.approx(150, abs=10) and tr.ce[-1] == pytest.approx(1150, abs=10)


def test_a_stop_at_the_end_keeps_the_ascent_of_the_terrain_model(monkeypatch):
    from gpxfilm import terrain
    tr = prep_track(dict(pts=[q[:2] + (None,) + q[3:] for q in _slow_end()], wpts=[], name="", ele_note=None))
    monkeypatch.setattr(terrain, "ground_profile", lambda lat, lon: 1000 + 150 * np.clip((np.arange(len(lat)) - 834) / 719, 0, 1))
    terrain.ground_elevation(tr)
    assert tr.cup[-1] == pytest.approx(150, abs=3) and tr.ce[-1] == pytest.approx(1150, abs=3)


@pytest.mark.parametrize("args, words", [(["--distance", "0"], "--distance musi być liczbą większą od zera"),
                                         (["--distance", "nan"], "--distance musi być liczbą większą od zera"),
                                         (["--distance", "1e27"], "--distance musi być liczbą większą od zera"),
                                         (["--ascent", "-5"], "--ascent musi być liczbą nieujemną"),
                                         (["--ascent", "2e6"], "--ascent musi być liczbą nieujemną")])
def test_watch_values_must_make_sense(args, words):
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(SLAD), *args], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and words in r.stderr


WALK = ROOT / "tests" / "data" / "gpx" / "switchbacks_stop_spike.gpx"
STOP_FROM, STOP_TO, SPIKE_S = 240.0, 540.0, 1000.0           # as written by make_variants.py


def _haversine(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    h = np.sin(np.diff(la) / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(np.diff(lo) / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(h))


def _walk(without_spike=True):
    pts = read_gpx(WALK)["pts"]
    t = np.array([(q[3] - pts[0][3]).total_seconds() for q in pts])
    keep = t != SPIKE_S if without_spike else np.ones(len(t), bool)
    return np.array([q[0] for q in pts])[keep], np.array([q[1] for q in pts])[keep], t[keep]


def test_raw_distance_leaves_out_only_the_stop(monkeypatch):
    monkeypatch.setattr(geo, "POS_SMOOTH_S", 0.0)             # the stop alone, without averaging the positions
    lat, lon, t = _walk()
    seg = _haversine(lat, lon)
    in_stop = (t[:-1] >= STOP_FROM) & (t[1:] <= STOP_TO)
    expected, drift = seg[~in_stop].sum(), seg[in_stop].sum()
    got = raw_distance(lat, lon, t)[-1]
    assert drift > 150                                         # standing for 5 minutes with 1 m of noise adds up
    assert abs(got - expected) < 0.05 * drift                  # that drift is left out, and nothing else
    assert raw_distance(lat, lon)[-1] == pytest.approx(seg.sum())   # without times every segment counts


def test_raw_distance_averages_the_jitter_of_single_fixes():
    rng = np.random.default_rng(4)
    t = np.arange(0, 1000, 1.0)                                # 1.3 km straight north, a fix a second, 1.5 m of jitter
    lat = 49.5 + (1.3 * t + rng.normal(0, 1.5, len(t))) / 111320
    lon = 20.0 + rng.normal(0, 1.5, len(t)) / (111320 * np.cos(np.radians(49.5)))
    raw, averaged = _haversine(lat, lon).sum(), raw_distance(lat, lon, t)[-1]
    assert raw > 1.3 * 999 * 1.3                               # the jitter alone adds more than 30%
    assert abs(averaged / (1.3 * 999) - 1) < abs(raw / (1.3 * 999) - 1) / 2


def test_raw_distance_keeps_slow_walking():
    t = np.arange(0, 600, 5.0)                                 # 0.3 m/s for 10 minutes: slow, but moving
    lat, lon = 49.5 + 0.3 * t / 111320, np.full(len(t), 20.0)
    assert raw_distance(lat, lon, t)[-1] == pytest.approx(0.3 * 595, rel=0.01)


def _ll(x, y):
    return 49.5 + np.asarray(y) / 111320, 20 + np.asarray(x) / (111320 * np.cos(np.radians(49.5)))


def test_laps_of_a_small_loop_are_not_a_stop():
    t = np.arange(0, 3000, 1.0)                                # a minute a lap round a 250 m path, at 4.2 m/s
    ang = 2 * np.pi * 4.2 * t / 250
    rng = np.random.default_rng(11)
    lat, lon = _ll(250 / (2 * np.pi) * np.cos(ang) + rng.normal(0, 1.5, len(t)), 250 / (2 * np.pi) * np.sin(ang) + rng.normal(0, 1.5, len(t)))
    assert raw_distance(lat, lon, t)[-1] == pytest.approx(4.2 * 2999, rel=0.03)


def test_tight_switchbacks_are_not_a_stop():
    rng = np.random.default_rng(5)                             # legs of 20 m at 0.7 m/s, 4 m up the slope each, 1 m of noise
    corners = np.array([(20.0 * (k % 2), 4.0 * k) for k in range(31)])
    along = np.r_[0, np.cumsum(np.hypot(*np.diff(corners, axis=0).T))]
    t = np.arange(0, along[-1] / 0.7, 1.0)
    lat, lon = _ll(np.interp(0.7 * t, along, corners[:, 0]) + rng.normal(0, 1, len(t)), np.interp(0.7 * t, along, corners[:, 1]) + rng.normal(0, 1, len(t)))
    assert raw_distance(lat, lon, t)[-1] > 0.95 * along[-1]       # GPS noise adds a little, but nothing is left out


@pytest.mark.parametrize("pause, gone", [(5400.0, 1500.0),     # the watch paused for a cable car: the walks on both sides count
                                         (50400.0, 1500.0)])   # a night between two days, slower than a stop: the jump is out too
def test_a_pause_that_moves_on_keeps_the_walks(pause, gone):
    rng = np.random.default_rng(1)
    walk = np.arange(0, 1667, 1.0)                             # 2 km at 1.2 m/s, 3 minutes standing, the pause, 2 minutes, 2 km
    t = np.r_[walk, 1667 + np.arange(180.0), 1847 + pause + np.arange(120.0), 1967 + pause + walk]
    y = np.r_[1.2 * walk, np.full(180, 2000.0), np.full(120, 2000.0 + gone), 2000 + gone + 1.2 * walk]
    lat, lon = _ll(rng.normal(0, 0.5, len(t)), y + rng.normal(0, 0.5, len(t)))
    jump = gone if gone / pause >= geo.STOP_SPEED else 0.0
    assert raw_distance(lat, lon, t)[-1] == pytest.approx(4000 + jump, rel=0.02)


@pytest.mark.parametrize("stop", [75.0, 120.0, 600.0])
def test_short_stops_leave_out_their_drift(stop):
    rng = np.random.default_rng(3)                             # walking at 1.2 m/s, a stop with 1 m of drift
    t = np.arange(0, 600 + stop, 1.0)
    y = np.where(t < 300, 1.2 * t, np.where(t < 300 + stop, 360.0, 1.2 * (t - stop)))
    drift = (t >= 300) & (t < 300 + stop)
    lat, lon = _ll(drift * rng.normal(0, 1, len(t)), y + drift * rng.normal(0, 1, len(t)))
    assert raw_distance(lat, lon, t)[-1] == pytest.approx(720, abs=25)


GPX = ROOT / "tests" / "data" / "gpx"


@pytest.mark.parametrize("name, spikes", [
    ("switchbacks_stop_spike.gpx", [500]),                 # one fix 300 m off a walk recorded every 2 s
    ("spike_series.gpx", [100, 101, 102]),                 # three fixes 120 m to the side
    ("cold_start.gpx", [0]),                               # the first fix 500 m away
    ("relocated_end.gpx", [200]),                          # one fix after the watch was carried 3 km
    ("car_spike.gpx", [50]),                               # 1.5 km off while driving: caught by the 400 km/h rule
    ("sparse_spur.gpx", []),                               # a spur to a viewpoint, recorded every 60 s: real, kept
    ("SLAD.gpx", [1055, 1056, 1057, 1058]),                # the four fixes thrown off under a rock face (make_test_data.py)
])
def test_position_spikes(name, spikes, monkeypatch, capsys):
    path = GPX / name if (GPX / name).exists() else ROOT / "tests" / "data" / name
    found = []
    monkeypatch.setattr(geo, "_spikes", lambda p: found.append(_spikes(p)) or found[-1])
    read_gpx(path)
    assert found == [spikes]
    err = capsys.readouterr().err
    assert (f"Pominięte odskoki pozycji (punkty daleko od śladu): {len(spikes)}." in err) == bool(spikes)


def _line(n, step=1.2, dt=1.0, moves=()):
    """GPX points on a straight walk east, with the given points moved by (dx, dy) meters; the same jitter every time."""
    rng = np.random.default_rng(0)
    xy = np.c_[np.arange(n) * step, np.zeros(n)] + rng.normal(0, 0.5, (n, 2))
    for i, d in moves:
        xy[i] += d
    t0 = dt_.datetime(2025, 7, 14, 8, tzinfo=dt_.timezone.utc)
    return [(49.5 + y / 111320, 20 + x / (111320 * np.cos(np.radians(49.5))), None, t0 + dt_.timedelta(seconds=float(k * dt)), None)
            for k, (x, y) in enumerate(xy)]


@pytest.mark.parametrize("pts, spikes", [
    (_line(200, moves=[(100, (120, 0)), (104, (-120, 0))]), [100, 104]),     # two spikes close together: the fixes between stay
    (_line(200, 6.0, 5.0, moves=[(1, (0, 100))]), [1]),                     # a spike at the second fix: the first one stays
    (_line(40, moves=[(38, (0, 100))]), [38]),                              # a spike at the last but one fix: the last one stays
    (_line(200, moves=[(20, (0, 150)), (26, (2.4, 150))]), [20, 26]),       # the same wrong place twice: only those two go
])
def test_spikes_keep_the_fixes_around_them(pts, spikes):
    assert _spikes(pts) == spikes


def _untimed(pts):
    return [q[:3] + (None,) + q[4:] for q in pts]


def test_a_planned_route_without_times_keeps_every_point():
    rng = np.random.default_rng(2)                             # a promenade with nodes every 15 m, out to the end of a 1.84 km jetty
    shore = np.cumsum(rng.uniform(8, 25, 60))                  # drawn as one straight way, and back the same way
    xy = [(x, 0.0) for x in shore[:30]] + [(shore[29], 1840.0)] + [(x, 0.0) for x in shore[29:]]
    route = [(49.5 + y / 111320, 20 + x / (111320 * np.cos(np.radians(49.5))), 3.0, None, None) for x, y in xy]
    assert _spikes(route) == []


@pytest.mark.parametrize("step", [0.0, -10.0])                  # the export time on every point, or times running backwards
def test_a_planned_route_with_useless_times_keeps_every_point(step):
    test_a_planned_route_without_times_keeps_every_point()
    rng = np.random.default_rng(2)
    shore = np.cumsum(rng.uniform(8, 25, 60))
    xy = [(x, 0.0) for x in shore[:30]] + [(shore[29], 1840.0)] + [(x, 0.0) for x in shore[29:]]
    t0 = dt_.datetime(2025, 6, 1, 10, tzinfo=dt_.timezone.utc)
    route = [(49.5 + y / 111320, 20 + x / (111320 * np.cos(np.radians(49.5))), 3.0, t0 + dt_.timedelta(seconds=step * k), None)
             for k, (x, y) in enumerate(xy)]
    assert _spikes(route) == []


def test_a_recording_without_times_keeps_its_spikes():
    pts = _line(200, moves=[(100, (0, 2000))])                 # known limit: without times a spike looks like a planned spur
    assert _spikes(pts) == [100] and _spikes(_untimed(pts)) == []


def test_a_flight_is_not_a_spike():
    walk = _line(200, 0.05, 5.0)                               # an hour on the ground, 45 minutes at 800 km/h, an hour again
    fly = [(q[0], q[1] + 1111.0 * (k + 1) / (111320 * np.cos(np.radians(49.5))), None, walk[-1][3] + dt_.timedelta(seconds=5.0 * (k + 1)), None)
           for k, q in enumerate([walk[-1]] * 540)]
    land = [(q[0], q[1] + fly[-1][1] - walk[0][1], None, fly[-1][3] + (q[3] - walk[0][3]) + dt_.timedelta(seconds=5), None) for q in walk]
    assert _spikes(walk + fly + land) == []


def test_shown_distance_is_the_raw_sum_without_stop_and_spike(monkeypatch, capsys):
    monkeypatch.setattr(geo, "POS_SMOOTH_S", 0.0)
    tr = prep_track(read_gpx(WALK))
    lat, lon, t = _walk()
    seg = _haversine(lat, lon)
    in_stop = (t[:-1] >= STOP_FROM) & (t[1:] <= STOP_TO)
    assert abs(tr.rdist - seg[~in_stop].sum()) < 0.05 * seg[in_stop].sum()
    assert tr.rdist > tr.dist                                  # the smoothed line drawn on the map is shorter
    assert "odskoki pozycji (punkty daleko od śladu): 1." in capsys.readouterr().err


def test_ascent_of_the_switchbacks():
    tr = prep_track(read_gpx(WALK))                            # 12 switchbacks climb 57.6 m, with 0.5 m of noise
    assert 52 < tr.cup[-1] < 60


def test_ground_profile_reads_the_terrain_model(monkeypatch):
    from gpxfilm import terrain
    n = 2 ** terrain.PROFILE_ZOOM * 512
    lat = np.array([49.5, 49.5003, 49.5101, 49.4950])
    lon = np.array([20.0, 20.0004, 20.0420, 19.9870])         # the last two lie on other tiles than the first
    px = (lon + 180) / 360 * n - 0.5
    py = (1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n - 0.5
    x0, y0 = int(px.min()) - 600, int(py.min()) - 600

    def plane(z, x, y, tries=3):                               # a tilted plane: 0.5 m per pixel east, 0.25 m south
        X, Y = np.meshgrid(np.arange(512) + x * 512 - x0, np.arange(512) + y * 512 - y0)
        return (1000 + 0.5 * X + 0.25 * Y).astype(np.float32)

    monkeypatch.setattr(terrain, "dem_tile", plane)
    want = 1000 + 0.5 * (px - x0) + 0.25 * (py - y0)
    assert terrain.ground_profile(lat, lon) == pytest.approx(want, abs=1e-3)


def test_ground_profile_refuses_a_very_long_track(monkeypatch):
    from gpxfilm import terrain
    monkeypatch.setattr(terrain, "dem_tile", lambda *a, **k: pytest.fail("nothing is downloaded"))
    lat, lon = np.full(400, 47.0), np.linspace(5.0, 25.0, 400)  # 1500 km east across the Alps: about 230 tiles
    with pytest.raises(ValueError):
        terrain.ground_profile(lat, lon)


def _model_rise(monkeypatch, rise):
    """The terrain model climbs evenly by rise meters along the track."""
    from gpxfilm import terrain
    monkeypatch.setattr(terrain, "ground_profile", lambda lat, lon: np.linspace(1000.0, 1000.0 + rise, len(lat)))
    return terrain


@pytest.mark.parametrize("scale, rise, from_model", [(1.0, 500.0, True),     # the file climbs 878 m: far more than the model
                                                     (1.0, 760.0, False),    # less than 20% more
                                                     (0.2, 140.0, False),    # 176 m: 25% more, but only 36 m
                                                     (0.2, 100.0, True)])    # 76% and 76 m more
def test_noisy_elevation_comes_from_the_terrain_model(scale, rise, from_model, monkeypatch, capsys):
    tr = prep_track(read_gpx(SLAD))
    assert 870 < tr.cup[-1] < 885
    tr.cup = tr.cup * scale
    up = tr.cup[-1]
    _model_rise(monkeypatch, rise).ground_elevation(tr)
    assert tr.has_e and tr.cup[-1] == pytest.approx(rise if from_model else up, abs=3)
    assert ("biorę ją z modelu terenu" in capsys.readouterr().err) == from_model


def test_missing_elevation_comes_from_the_terrain_model(monkeypatch, capsys):
    tr = prep_track(read_gpx(SLAD))
    tr.has_e = False
    capsys.readouterr()
    _model_rise(monkeypatch, 300.0).ground_elevation(tr)
    assert tr.has_e and tr.cup[-1] == pytest.approx(300, rel=0.01) and len(tr.ce) == len(tr.cx)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("has_e", [True, False])
def test_elevation_stays_without_the_terrain_model(has_e, monkeypatch, capsys):
    from gpxfilm import terrain

    def offline(lat, lon):
        raise OSError("no network")

    monkeypatch.setattr(terrain, "ground_profile", offline)
    tr = prep_track(read_gpx(SLAD))
    tr.has_e = has_e
    ce, cup = tr.ce, tr.cup
    capsys.readouterr()
    terrain.ground_elevation(tr)
    assert tr.has_e == has_e and tr.ce is ce and tr.cup is cup
    assert "Nie udało się pobrać modelu terenu do profilu wysokości" in capsys.readouterr().err


def test_the_profile_tiles_are_tried_once(monkeypatch):
    from gpxfilm import terrain
    tries = []

    def tile(z, x, y, tries_=None, **kw):
        tries.append(kw.get("tries", tries_))
        raise OSError("no network")

    monkeypatch.setattr(terrain, "dem_tile", tile)
    with pytest.raises(OSError):
        terrain.ground_profile(np.array([49.5]), np.array([20.0]))
    assert tries == [1]


def test_a_wrong_map_stops_before_any_download(tmp_path):
    env = dict(__import__("os").environ, GPXFILM_CACHE_DIR=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path), https_proxy="http://127.0.0.1:9",
               HTTPS_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", no_proxy="", NO_PROXY="")
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(SLAD), "--map", "topo2", "--frame-only", str(tmp_path / "k.png")], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 1 and "Nieznany podkład" in r.stderr and "modelu terenu" not in r.stderr
