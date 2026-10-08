"""The intro: frames of named moments taken before the encoder, compared with reference frames."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from offline import offline_frames, offline_run
from PIL import Image
from scipy import ndimage as ndi

from gpxfilm import i18n, webgui
from gpxfilm.film import peak_show

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
GOLDEN = DATA / "golden"
UPDATE = os.environ.get("GPXFILM_UPDATE_GOLDEN") == "1"
MAX_MEAN_DIFF, MAX_BAD_SHARE, BAD_LEVEL = 0.25, 0.0005, 8      # as in test_frames.py

# name: (track, options, moments); a short film at a small size, a few frames a second
INTRO_CASES = {
    "intro_plain": ("SLAD.gpx", ["--size", "640x360", "--fps", "5", "--route-duration", "2", "--intro", "--intro-peaks", "0", "--intro-places", "0"],
                    ("intro_last", "route_first")),
    "intro_peaks": ("SLAD.gpx", ["--size", "640x360", "--fps", "5", "--route-duration", "2", "--intro"], ("region_peaks", "intro_last", "route_first")),
}


@pytest.fixture(scope="module")
def films(tmp_path_factory):
    return {name: offline_frames(DATA / gpx, tmp_path_factory.mktemp(name), *args) for name, (gpx, args, _) in INTRO_CASES.items()}


def _same(got: np.ndarray, ref: Path, keep: Path):
    want = np.asarray(Image.open(ref).convert("RGB"), np.int16)
    diff = np.abs(got.astype(np.int16) - want)
    mean, bad = float(diff.mean()), float((diff.max(-1) > BAD_LEVEL).mean())
    print(f"\n{ref.stem}: mean {mean:.4f}, pixels over {BAD_LEVEL}: {bad:.4%}, max {int(diff.max())}")
    if mean > MAX_MEAN_DIFF or bad > MAX_BAD_SHARE:
        keep.mkdir(parents=True, exist_ok=True)
        Image.fromarray(got).save(keep / f"{ref.stem}-got.png")
        pytest.fail(f"{ref.stem} differs: mean {mean:.3f}, pixels over {BAD_LEVEL}: {bad:.4%}; see {keep}")


@pytest.mark.parametrize("name, moment", [(n, m) for n, (_, _, ms) in INTRO_CASES.items() for m in ms])
def test_intro_moment(name, moment, films, tmp_path):
    frames, err = films[name]
    for sign in ("pobieram", "nie udało się", "brak danych"):     # the stored data must be enough
        assert sign not in err.lower(), err
    assert moment in frames, f"no frame named {moment}: {sorted(frames)}"
    ref = GOLDEN / f"{name}-{moment}.png"
    if UPDATE:
        Image.fromarray(frames[moment]).save(ref)
        pytest.skip("reference frame updated")
    _same(frames[moment], ref, Path(os.environ.get("GPXFILM_DIFF_DIR", tmp_path)))


def test_the_frames_need_no_encoder(tmp_path):
    frames, err = offline_frames(DATA / "SLAD.gpx", tmp_path, "--size", "640x360", "--fps", "5", "--route-duration", "1", PATH="")
    assert set(frames) == {"route_first"} and "Zapisuję wybrane klatki" in err                # ffmpeg is nowhere on the path


def test_peak_labels_stay_in_place_from_the_intro_to_the_route(films, tmp_path):
    """Every peak that comes in after the camera arrives either stays where it is on the map of the route, or fades: no
    label jumps. Labels are found as what changed between the arrival and the last intro frame, and the peaks of the
    route map as what a map without peaks lacks."""
    frames, _ = films["intro_peaks"]
    arrived, last, route = (frames[m].astype(int) for m in ("intro_arrived", "intro_last", "route_first"))
    blobs, n = ndi.label(ndi.binary_dilation(np.abs(last - arrived).max(-1) > 40, iterations=3))
    kept = gone = 0
    for k in range(1, n + 1):
        m = blobs == k
        if m.sum() < 20:
            continue
        stays, fades = np.abs(last - route)[m].mean(), np.abs(arrived - route)[m].mean()
        assert min(stays, fades) < 3, f"label {k} moved: {stays:.1f} / {fades:.1f}"
        kept, gone = kept + (stays < 3), gone + (stays >= 3)
    assert kept >= 1 and gone >= 1                                 # both kinds are in the SLAD data
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    bare = offline_frames(DATA / gpx, tmp_path, *args, "--peaks", "0", "--intro-peaks", "0")[0]["route_first"].astype(int)
    blobs, n = ndi.label(ndi.binary_dilation(np.abs(route - bare).max(-1) > 40, iterations=3))
    assert n >= 2                                                  # the two peaks of the route map, with their labels
    for k in range(1, n + 1):
        assert np.abs(last - route)[blobs == k].mean() < 3, f"peak {k} of the route map is not in place at the end of the intro"


def test_the_report_counts_the_peaks_of_the_intro(films, tmp_path):
    """The estimated length of the film includes the pause over the region (the places together, then five peaks one by
    one) and the peaks after the arrival, and matches the frames the film really has."""
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    _, with_peaks = offline_run(DATA / gpx, tmp_path, *args)
    _, without = offline_run(DATA / gpx, tmp_path, *args, "--intro-peaks", "0", "--intro-places", "0")
    assert with_peaks["duration"] - without["duration"] == pytest.approx((2.35 + 0.22 * 5) + (1.4 + 0.22 * 2), abs=0.15)
    n = int(re.search(r"Zapisuję wybrane klatki z (\d+)", films["intro_peaks"][1])[1])
    assert n / 5 == pytest.approx(with_peaks["duration"], abs=0.5)                 # 5 frames a second


def _moments(tmp_path: Path, *extra: str) -> dict:
    """Numbers of the named frames and of all frames of the intro_peaks film, from beside the frames the hook saved."""
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    offline_frames(DATA / gpx, tmp_path, *args, *extra)
    return json.loads((tmp_path / "frames" / "moments.json").read_text(encoding="utf-8"))


def test_without_the_option_the_peaks_keep_their_times():
    """Without --intro-label-duration the shows have exactly the frames they had before the option: about 1.3 s of all peaks over
    the region and 0.8 s after the arrival."""
    for fps in (5, 24, 25, 30, 50, 60):
        for n in range(1, 7):
            assert peak_show(n, None, True, fps) == int((2.35 + 0.22 * (n - 1)) * fps)
            assert peak_show(n, None, False, fps) == int((1.4 + 0.22 * (n - 1)) * fps)
            assert abs(peak_show(n, None, True, fps) - peak_show(n, 1.3, True, fps)) <= 2
            assert abs(peak_show(n, None, False, fps) - peak_show(n, 0.8, False, fps)) <= 1
    assert peak_show(0, 5.0, True, 30) == peak_show(0, None, False, 30) == 0


def test_all_peaks_stay_fully_visible_for_the_asked_time(tmp_path):
    """From the first frame with every peak fully in to the last one before they fade, the frames are the same picture,
    and there are as many as the option asks: 2 s at 5 frames a second."""
    m = _moments(tmp_path, "--intro-label-duration", "2")["moments"]
    for first, last in (("region_all_in", "region_peaks"), ("arrival_all_in", "intro_last")):
        assert m[last] - m[first] + 1 == 10, (first, last)
        a, b = (np.asarray(Image.open(tmp_path / "frames" / f"{k}.png").convert("RGB"), np.int16) for k in (first, last))
        assert np.abs(a - b).max() <= 1, f"{first} and {last} differ"


def test_the_peaks_stay_as_long_as_asked(tmp_path):
    """--intro-label-duration lengthens both shows by the same time: the pause over the region and the peaks after the arrival."""
    short, long_ = (_moments(tmp_path / h, "--intro-label-duration", h) for h in ("1", "4"))
    grow = 3 * 5                                                   # 3 s more, 5 frames a second
    a, b = short["moments"], long_["moments"]
    assert b["region_peaks"] - a["region_peaks"] == pytest.approx(grow, abs=1)          # the frames before the pause stay
    assert (b["intro_last"] - b["intro_arrived"]) - (a["intro_last"] - a["intro_arrived"]) == pytest.approx(grow, abs=1)
    assert long_["frames"] - short["frames"] == pytest.approx(2 * grow, abs=2)


def test_the_report_counts_the_time_of_the_peaks(tmp_path):
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    film = _moments(tmp_path / "film", "--intro-label-duration", "4")
    for d in ("asked", "default"):
        (tmp_path / d).mkdir()
    _, asked = offline_run(DATA / gpx, tmp_path / "asked", *args, "--intro-label-duration", "4")
    _, default = offline_run(DATA / gpx, tmp_path / "default", *args)
    assert asked["duration"] - default["duration"] == pytest.approx((4 - 1.3) + (4 - 0.8), abs=0.4)   # whole frames at 5 a second
    assert film["frames"] / 5 == pytest.approx(asked["duration"], abs=0.5)           # 5 frames a second


def test_the_window_has_the_fields_of_the_intro():
    """Three fields by the intro in the film section, read from the page and passed to the command."""
    page = webgui.GUI_PAGE
    film = page[page.index("<legend>Film</legend>"):page.index("</fieldset>", page.index("<legend>Film</legend>"))]
    assert re.search(r'id="intro_label_duration" min="0\.5" max="20" step="0\.5"', film)
    assert re.search(r'id="intro_peaks" min="0" step="1"', film)
    assert re.search(r'id="intro_places" min="0" step="1"', film)
    assert re.search(r'id="intro_radius" min="0\.5" max="100" step="0\.5"', film)
    fields = re.findall(r"'(\w+)'", re.search(r"const VALS=\[(.*?)\];", page, re.S)[1])
    flags = dict(re.findall(r'\("(\w+)", "(--[\w-]+)"\)', Path(webgui.__file__).read_text(encoding="utf-8")))
    for key in ("intro_label_duration", "intro_peaks", "intro_places", "intro_radius"):
        assert key in fields and flags[key] == "--" + key.replace("_", "-")


def test_the_label_time_field_says_what_it_sets():
    """The field of --intro-label-duration names what it sets, gives the default in the empty field and explains itself on hover."""
    page = webgui.localize(webgui.GUI_PAGE, i18n.Translator("pl"))
    field = page[page.index('<label for="intro_label_duration"'):page.index("</div>", page.index('<label for="intro_label_duration"'))]
    assert ">Jak długo widać miejscowości i szczyty, s</label>" in field and 'placeholder="domyślnie ok. 1 s"' in field
    assert field.count('title="Czas od pojawienia się ostatniego podpisu do początku gaśnięcia, taki sam dla miejscowości i szczytów '
                       'okolicy oraz dla szczytów przy trasie."') == 2        # on the label and on the field


@pytest.mark.parametrize("args, ok", [(["--intro-label-duration", "0.5", "--intro-radius", "0.5", "--intro-peaks", "0"], True),
                                      (["--intro-label-duration", "20", "--intro-radius", "100"], True),
                                      (["--intro-label-duration", "0.49"], False), (["--intro-label-duration", "20.01"], False),
                                      (["--intro-radius", "100.5"], False), (["--intro-peaks", "-1"], False),
                                      (["--intro-places", "0"], True), (["--intro-places", "-1"], False)])
def test_the_ends_of_the_window_fields_suit_the_command(args, ok):
    """The command takes the smallest and largest values of the window fields and refuses values just outside them
    (without a GPX file, so a value it takes ends at the question for the file)."""
    r = subprocess.run([sys.executable, "-m", "gpxfilm", *args], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and ("podaj plik GPX" in r.stderr) == ok, r.stderr


def test_a_short_flight_has_no_pause(tmp_path):
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    frames, _ = offline_frames(DATA / gpx, tmp_path, *args, "--intro-duration", "0.2")
    assert "region_peaks" not in frames and "intro_last" in frames                  # too short to stop on the way


@pytest.mark.parametrize("args, words", [(["--intro-radius", "0"], "--intro-radius musi być liczbą większą od zera"),
                                         (["--intro-radius", "nan"], "--intro-radius musi być liczbą większą od zera"),
                                         (["--intro-peaks", "-1"], "--intro-peaks musi być liczbą nieujemną"),
                                         (["--intro-label-duration", "0.4"], "--intro-label-duration musi być liczbą od 0.5 do 20"),
                                         (["--intro-label-duration", "20.5"], "--intro-label-duration musi być liczbą od 0.5 do 20"),
                                         (["--intro-label-duration", "nan"], "--intro-label-duration musi być liczbą od 0.5 do 20")])
def test_intro_options_must_make_sense(args, words):
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / "SLAD.gpx"), *args], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and words in r.stderr


@pytest.mark.parametrize("old", ["--intro-peak-radius", "--intro-peak-duration"])
def test_the_old_names_of_the_intro_options_are_gone_from_the_command(old):
    """No alias on the command line; only the settings file and the window's remembered settings still read them."""
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / "SLAD.gpx"), old, "3"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 2 and f"{old} 3" in r.stderr


def test_a_preview_needs_no_country_borders(tmp_path):
    """The preview of the window (a still frame and a report) comes also when the borders of countries cannot be had:
    the length of the film is then only guessed."""
    shutil.copytree(DATA / "cache", tmp_path / "cache-SLAD.gpx", ignore=shutil.ignore_patterns("ne_50m_*"))
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    _, rep = offline_run(DATA / gpx, tmp_path, *args)
    assert rep["duration"] > 0


def test_a_negative_count_of_peaks_shows_none(tmp_path):
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    _, rep = offline_run(DATA / gpx, tmp_path, *args, "--peaks", "-1")
    assert rep["peaks"] == []


def test_skipped_and_renamed_peaks_in_the_pause(monkeypatch):
    """--skip is matched against the name from OpenStreetMap and --rename renames what is left, as for the other labels."""
    from gpxfilm import film
    peaks = [(3264.0, 900.0, 700.0, {"name": "Antelao"}), (3221.0, 1300.0, 300.0, {"name": "Monte Cristallo"}),
             (3205.0, 1500.0, 700.0, {"name": "Sorapiss"})]
    monkeypatch.setattr(film, "fetch_peaks", lambda tr, r: peaks)
    monkeypatch.setattr(film, "pause_at", lambda cm, r: 0.5)
    monkeypatch.setattr(film, "text_sprite", lambda rows, K, align, label=False: SimpleNamespace(w=120, h=48, rows=rows))
    cm = SimpleNamespace(K=1.0, start=lambda u: (1200.0, 500.0), spot=lambda u, x, y: (x, y))
    sc = SimpleNamespace(fonts=SimpleNamespace(get=lambda *a: None), title_sp=SimpleNamespace(w=400), sub_sp=None, sub_y=300,
                         prefs=["pl"], rename={"Sorapiss": "Sorapis", "Antelao": "Antelao Peak"}, tri=SimpleNamespace(w=24))
    A = SimpleNamespace(intro_peaks=5, intro_places=0, no_osm=False, intro_radius=20.0, skip=["sorapiss", "peak"])
    u, got = film.plan_pause(A, None, cm, sc, 30)
    assert u == 0.5 and [p[4].rows[0][0] for p in got] == ["Antelao Peak", "Monte Cristallo"]   # "peak" is only in the new name
    assert [p[0] for p in got] == [0, 1]                                                      # one by one


def _pause(monkeypatch, places, peaks=(), width=lambda rows: 120, **opts):
    """The items of the pause over the region planned from the given places and peaks, with labels 120 units wide (or as
    wide as width gives for the rows) and 24 units for each row."""
    from gpxfilm import film
    monkeypatch.setattr(film, "fetch_peaks", lambda tr, r: list(peaks))
    monkeypatch.setattr(film, "fetch_places", lambda tr, r: list(places))
    monkeypatch.setattr(film, "pause_at", lambda cm, r: 0.5)
    monkeypatch.setattr(film, "text_sprite", lambda rows, K, align="left", label=False: SimpleNamespace(w=width(rows), h=24 * len(rows), rows=rows))
    monkeypatch.setattr(film, "shape_sprite", lambda w, h, draw: SimpleNamespace(w=w, h=h, dot=True))
    cm = SimpleNamespace(K=1.0, start=lambda u: (1200.0, 500.0), spot=lambda u, x, y: (x, y))
    sc = SimpleNamespace(fonts=SimpleNamespace(get=lambda *a: None), title_sp=SimpleNamespace(w=400), sub_sp=None, sub_y=300,
                         prefs=["pl", "it"], rename={"Auronzo di Cadore": "Auronzo"}, tri=SimpleNamespace(w=24))
    A = SimpleNamespace(**{"intro_peaks": 5, "intro_places": 4, "no_osm": False, "intro_radius": 20.0, "skip": [], **opts})
    return film.plan_pause(A, None, cm, sc, 30)


def test_the_places_come_in_together_before_the_peaks(monkeypatch):
    places = [(900.0, 700.0, {"name": "Cortina d'Ampezzo"}), (1500.0, 800.0, {"name": "Dobbiaco - Toblach", "name:it": "Dobbiaco"}),
              (1300.0, 300.0, {"name": "Auronzo di Cadore"})]
    u, got = _pause(monkeypatch, places, [(3264.0, 700.0, 300.0, {"name": "Antelao"})])
    assert [(p[0], p[4].rows[0][0], getattr(p[1], "dot", False)) for p in got] == [
        (0, "Cortina d'Ampezzo", True), (0, "Dobbiaco", True), (0, "Auronzo", True), (1, "Antelao", False)]   # a dot, then the peak triangle
    assert film_slots(got) == 2


def film_slots(items):
    from gpxfilm.film import slots
    return slots(items)


def test_places_alone_make_the_pause_and_none_make_none(monkeypatch):
    place = [(900.0, 700.0, {"name": "Cortina d'Ampezzo"})]
    assert _pause(monkeypatch, place, intro_peaks=0)[0] == 0.5                       # places alone: the camera stops
    assert _pause(monkeypatch, place, intro_peaks=0, intro_places=0) == (None, [])   # nothing asked: no pause, as before
    assert _pause(monkeypatch, [], intro_peaks=0) == (None, [])                      # no place data: no pause, as before


def test_places_keep_their_distance_and_follow_skip_rename_and_count(monkeypatch):
    places = [(900.0, 700.0, {"name": "Cortina d'Ampezzo"}), (905.0, 712.0, {"name": "Zuel"}),      # too close to Cortina
              (1210.0, 505.0, {"name": "Misurina"}),                                                # on the start
              (300.0, 150.0, {"name": "Under the title"}), (1500.0, 800.0, {"name": "Sesto - Sexten"}),
              (1600.0, 300.0, {"name": "San Candido"}), (1700.0, 950.0, {"name": "Villabassa"}), (1100.0, 950.0, {"name": "Five"})]
    got = _pause(monkeypatch, places, [(3264.0, 900.0, 640.0, {"name": "Antelao"})], skip=["sesto"])[1]
    names = [p[4].rows[0][0] for p in got if p[0] == 0]
    assert names == ["Cortina d'Ampezzo", "San Candido", "Villabassa", "Five"]   # Zuel too close to Cortina, Misurina on the start, Sesto skipped
    assert [p[4].rows[0][0] for p in got if p[0] == 1] == ["Antelao"]          # the peak's label goes above its mark, out of Cortina's way
    got = _pause(monkeypatch, places[4:], intro_places=2)[1]
    assert [p[4].rows[0][0] for p in got] == ["Sesto", "San Candido"]                  # at most --intro-places, in the given order


def test_the_camera_stops_where_the_whole_circle_fits():
    from gpxfilm.film import PAUSE_FIT, pause_at
    S0, S1, R = 1 / 3000, 1 / 4, 20000.0                       # units of the frame per meter: country view and map of the route

    def start(u):                                              # the start slides from the right towards the middle
        return 1500 - 540 * u * u * (3 - 2 * u), 300 + 240 * u * u * (3 - 2 * u)

    cm = SimpleNamespace(S0=S0, S1=S1, K=1.0, start=start)

    def fits(u):
        r, (x, y) = R * S0 * (S1 / S0) ** u, start(u)
        return PAUSE_FIT[0] <= x - r and x + r <= PAUSE_FIT[2] and PAUSE_FIT[1] <= y - r and y + r <= PAUSE_FIT[3]

    u = pause_at(cm, R)
    assert 0 < u < 1 and fits(u) and not fits(u + 0.002)       # the closest view that still holds the whole circle
    assert pause_at(cm, 10.0) is None                          # a tiny circle fits even the map of the route: no pause

    def away(u):                                               # the start leaves the frame for a while and comes back
        return 1500 + 700 * np.exp(-((u - 0.3) / 0.1) ** 2) - 540 * u * u * (3 - 2 * u), 500.0

    cm.start = start = away
    u = pause_at(cm, R)
    assert not fits(0.3) and u > 0.5 and fits(u) and not fits(u + 0.002)


def _no_network(*a, **kw):
    raise OSError("no network in the tests")


def test_the_main_places_come_first(monkeypatch, tmp_path):
    """The stored synthetic answer for SLAD: towns before villages, in a class the larger population first, then the
    places without one; only cities, towns and villages within the radius count."""
    from gpxfilm import geo, osm
    shutil.copytree(DATA / "cache" / "osm", tmp_path / "osm")
    monkeypatch.setattr(osm, "CACHE", tmp_path)
    monkeypatch.setattr(osm, "http_get", _no_network)              # a missing stored answer fails here, without going online
    tr = geo.prep_track(geo.read_gpx(DATA / "SLAD.gpx"))
    assert [t["name"] for _x, _y, t in osm.fetch_places(tr, 20000)] == [
        "Cortina d'Ampezzo", "Auronzo di Cadore", "Dobbiaco - Toblach", "San Candido - Innichen", "Sesto - Sexten", "Villabassa - Niederdorf"]


def test_a_city_comes_before_a_bigger_town_and_without_population_the_nearest_first(monkeypatch):
    from gpxfilm import geo, osm
    tr = geo.prep_track(geo.read_gpx(DATA / "SLAD.gpx"))
    lat, lon = (float(v) for v in tr.proj.inv(tr.cx[0], tr.cy[0]))
    node = lambda dlat, place, name, *pop: {"type": "node", "lat": lat + dlat, "lon": lon,
                                            "tags": {"place": place, "name": name, **({"population": pop[0]} if pop else {})}}
    found = [node(0.10, "village", "Far village"), node(0.05, "village", "Near village"), node(0.02, "town", "Big town", "9000"),
             node(0.15, "city", "Small city", "5000"), node(0.03, "village", "Village with people", "200"), node(0.5, "city", "Too far")]
    monkeypatch.setattr(osm, "overpass", lambda q, message: {"elements": found})
    assert [t["name"] for _x, _y, t in osm.fetch_places(tr, 20000)] == [
        "Small city", "Big town", "Village with people", "Near village", "Far village"]


@pytest.mark.parametrize("text, n", [("5794", 5794), ("5 794", 5794), ("5,794", 5794), ("1.234.567", 1234567), ("ca. 5800", 5800),
                                     ("5794;5800", 5794), ("3\u00a0270", 3270), ("12", 12), ("", None), ("unknown", None)])
def test_the_population_is_read_as_people_write_it(text, n):
    from gpxfilm.osm import population
    assert population({"population": text}) == n


def test_without_place_data_the_intro_is_as_before(tmp_path):
    """When the places cannot be had (no stored answer and no network), the pause shows the peaks alone, the same frames as
    with --intro-places 0."""
    gpx, args, _ = INTRO_CASES["intro_peaks"]
    stored = next(f.name for f in (DATA / "cache" / "osm").glob("*.json") if "Cortina" in f.read_text(encoding="utf-8"))
    shutil.copytree(DATA / "cache", tmp_path / "none" / f"cache-{gpx}", ignore=shutil.ignore_patterns(stored))
    without, err = offline_frames(DATA / gpx, tmp_path / "none", *args)
    off, _ = offline_frames(DATA / gpx, tmp_path / "off", *args, "--intro-places", "0")
    assert "Miejscowości w intrze" not in err and "Szczyty w intrze: 5" in err
    assert sorted(without) == sorted(off) and all(np.array_equal(without[k], off[k]) for k in off)


def _label_at(item):
    """Where an item's label is: (x, y) of its top left corner in units (K = 1 in these tests)."""
    return round(item[5]), round(item[6])


def test_the_most_important_place_comes_first_unless_it_is_under_the_title(monkeypatch):
    peak = [(3264.0, 1000.0, 600.0, {"name": "Antelao"})]
    got = _pause(monkeypatch, [(1000.0, 560.0, {"name": "Cortina d'Ampezzo"})], peak)[1]
    assert [(p[0], p[4].rows[0][0]) for p in got] == [(0, "Cortina d'Ampezzo"), (1, "Antelao")]
    assert _label_at(got[1]) == (940, 626)                  # Cortina's dot is where the peak's label would go above: it goes below
    got = _pause(monkeypatch, [(300.0, 150.0, {"name": "Under the title"}), (1500.0, 800.0, {"name": "Other"})], peak)[1]
    assert [p[4].rows[0][0] for p in got if p[0] == 0] == ["Other"]


def test_a_peak_label_tries_above_below_right_and_left(monkeypatch):
    """Above the mark first; a place's dot in the way sends it below, then to the right, then to the left."""
    peak = [(3264.0, 1000.0, 600.0, {"name": "Antelao"}), (3100.0, 1600.0, 900.0, {"name": "Low"})]
    at = lambda places: _label_at(_pause(monkeypatch, places, peak)[1][len(places) and 1 or 0])
    assert _label_at(_pause(monkeypatch, [], peak, intro_places=0)[1][0]) == (940, 544)   # above
    assert at([(1000.0, 560.0, {"name": "A"})]) == (940, 626)                              # below
    by_start = [(3264.0, 1200.0, 560.0, {"name": "By the start"})]                        # the start (1200, 500) is above it
    got = _pause(monkeypatch, [(1200.0, 620.0, {"name": "A"})], by_start)[1]
    assert _label_at(got[1])[0] == 1220                                                    # a dot below: on the right


def test_the_highest_peak_always_shows_and_a_lower_one_drops_only_without_room(monkeypatch):
    peaks = [(3264.0, 1200.0, 470.0, {"name": "On the start"}), (3200.0, 1200.0, 475.0, {"name": "Same spot"}),
             (3100.0, 1600.0, 900.0, {"name": "Free"})]
    got = _pause(monkeypatch, [], peaks, intro_places=0)[1]
    assert [p[4].rows[0][0] for p in got] == ["On the start", "Free"]   # the highest shows even by the start; the next has no room


def test_only_the_highest_peak_is_sure_to_show(monkeypatch):
    """The highest peak under the title does not show, and the next one does not take over its guarantee."""
    peaks = [(3264.0, 300.0, 200.0, {"name": "Under the title"}), (3200.0, 1200.0, 475.0, {"name": "On the start"}),
             (3100.0, 1600.0, 900.0, {"name": "Free"})]
    assert [p[4].rows[0][0] for p in _pause(monkeypatch, [], peaks, intro_places=0)[1]] == ["Free"]


def _box(item):
    """The label of an item as (left, top, right, bottom) in units (K = 1 in these tests)."""
    return item[5], item[6], item[5] + item[4].w, item[6] + item[4].h


def test_the_label_of_the_most_important_place_covers_no_peak(monkeypatch):
    """By the start the label of the most important place would go on the left of its dot, onto the mark of the highest
    peak; it takes a spot that covers neither the mark nor the start."""
    from gpxfilm.film import _overlap
    got = _pause(monkeypatch, [(1150.0, 520.0, {"name": "Pieve"})], [(3264.0, 1100.0, 520.0, {"name": "Sorapiss"})])[1]
    assert [(p[0], p[4].rows[0][0]) for p in got] == [(0, "Pieve"), (1, "Sorapiss")]
    assert not _overlap(_box(got[0]), (1084, 514, 1116, 546)) and not _overlap(_box(got[0]), (1170, 470, 1230, 530))


def test_the_label_of_the_most_important_place_takes_the_best_spot_and_may_break_in_two(monkeypatch):
    """By the right edge of the frame, with a peak on its left: the label goes above the dot; nearer the edge the name
    breaks into two lines, never cut."""
    by_letters = lambda rows: 12 * max(len(r[0]) for r in rows)
    peak = lambda x: [(3264.0, x, 520.0, {"name": "Peak"})]
    got = _pause(monkeypatch, [(1700.0, 520.0, {"name": "Le Bourg-d'Oisans"})], peak(1600.0), by_letters)[1]
    assert [r[0] for r in got[0][4].rows] == ["Le Bourg-d'Oisans"] and _label_at(got[0]) == (1598, 470)    # above
    got = _pause(monkeypatch, [(1800.0, 520.0, {"name": "Le Bourg-d'Oisans"})], peak(1700.0), by_letters)[1]
    assert [r[0] for r in got[0][4].rows] == ["Le", "Bourg-d'Oisans"]


def test_another_place_may_have_its_name_above_or_below_its_dot(monkeypatch):
    """Peaks on both sides of a place's dot: its name goes above the dot."""
    places = [(1000.0, 600.0, {"name": "First"}), (1000.0, 700.0, {"name": "Second"})]
    peaks = [(3264.0, 1150.0, 700.0, {"name": "East"}), (3200.0, 850.0, 700.0, {"name": "West"})]
    got = [p for p in _pause(monkeypatch, places, peaks)[1] if p[0] == 0]
    assert [p[4].rows[0][0] for p in got] == ["First", "Second"] and _label_at(got[1]) == (940, 664)
