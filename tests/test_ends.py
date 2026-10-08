"""The labels of the start and the finish: the elevation, and the name of the place right there."""
import json
import shutil
import subprocess
import sys
import textwrap

import pytest
from offline import ROOT, offline_env

from gpxfilm.osm import end_place, end_places

DATA = ROOT / "tests" / "data"
SPY = textwrap.dedent("""
    import json, os, sys
    from gpxfilm import cli, compose
    force = os.environ.get("FORCE_END")
    if force:                                        # the ends take these places ("start|finish"), whatever is near them
        def end_places(cands, spots):
            return {vi: next(c for c in cands if c["name"] == nm) for vi, nm in zip(spots, force.split("|")) if nm}
        compose.end_places = end_places
    scene = compose.compose_scene
    def compose_scene(*a, **kw):
        sc = scene(*a, **kw)
        print("SCENE", json.dumps({"ends": sc.ends, "background": sc.background, "places": [c["name"] for c in sc.chosen],
                                   "peaks": sc.shown_peaks}, ensure_ascii=False))
        return sc
    cli.compose_scene = compose_scene
    sys.argv = ["gpxfilm", *sys.argv[1:]]
    cli.main()
""")


def scene(tmp_path, gpx: str, *args: str, **env: str) -> dict:
    """The end labels (their lines), the background names and the labels of places and peaks of a frame of the track."""
    cache = tmp_path / "cache"
    if not cache.exists():
        shutil.copytree(DATA / "cache", cache)
    r = subprocess.run([sys.executable, "-c", SPY, str(DATA / gpx), *args, "--frame-only", str(tmp_path / "frame.png")], cwd=ROOT,
                       env=offline_env(cache, XDG_CONFIG_HOME=str(tmp_path / "config"), **env), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("SCENE "))[6:])


NB = "\u00a0"


def test_a_hut_names_the_start_of_a_loop(tmp_path):
    """SLAD starts 31 m from Rifugio Auronzo; a parking is nearer (about 20 m), but a hut comes first."""
    assert scene(tmp_path, "SLAD.gpx", "--size", "640x360")["ends"] == [["Rifugio Auronzo", f"Start i meta, 2331{NB}m, 07:12–09:36"]]


def test_another_place_names_the_start_and_a_finish_without_one_keeps_its_facts(tmp_path):
    """tatry starts 53 m from Kuźnice, a part of Zakopane; nothing is named near its finish."""
    assert scene(tmp_path, "tatry.gpx", "--size", "960x540")["ends"] == [["Kuźnice", f"Start, 1013{NB}m, 05:48"], ["Meta", f"1626{NB}m, 07:43"]]


def test_a_summit_names_the_finish_and_without_times_the_elevation_stays(tmp_path):
    """bez_czasu has no times; nothing is near its start, and it ends 27 m from the top of Śnieżka."""
    assert scene(tmp_path, "bez_czasu.gpx", "--size", "640x360")["ends"] == [["Start", f"717{NB}m"], ["Śnieżka", f"Meta, 1602{NB}m"]]


def test_skip_and_rename_apply_to_the_ends(tmp_path):
    assert scene(tmp_path, "SLAD.gpx", "--size", "640x360", "--rename", "Rifugio Auronzo=Auronzo")["ends"][0][0] == "Auronzo"
    assert scene(tmp_path, "SLAD.gpx", "--size", "640x360", "--skip", "rifugio auronzo")["ends"] == [    # without the hut, the parking
        ["Parcheggio Tre Cime", f"Start i meta, 2331{NB}m, 07:12–09:36"]]
    assert scene(tmp_path, "SLAD.gpx", "--size", "640x360", "--skip", "rifugio auronzo", "--skip", "parcheggio")["ends"] == [
        ["Start i meta", f"2331{NB}m, 07:12–09:36"]]


def test_a_name_never_takes_the_room_of_another_label(tmp_path):
    """With big labels a long name at the start would push labels of places and peaks off the map: the start keeps its
    elevation and times alone, and every label that fits without the name stays."""
    big = ("--size", "640x360", "--label-size", "2.0")
    named = scene(tmp_path, "SLAD.gpx", *big, "--rename", "Rifugio Auronzo=Rifugio Auronzo e il suo grande parcheggio")
    plain = scene(tmp_path, "SLAD.gpx", *big, FORCE_END="|")
    assert named["ends"] == [["Start i meta", f"2331{NB}m, 07:12–09:36"]]
    assert (named["places"], named["peaks"]) == (plain["places"], plain["peaks"])
    short = scene(tmp_path, "SLAD.gpx", *big)
    assert short["ends"][0][0] == "Rifugio Auronzo" and (short["places"], short["peaks"]) == (plain["places"], plain["peaks"])


BADGES = ("--size", "640x360", "--label-size", "2.0", "--label-style", "badges")


def test_the_times_get_a_line_of_their_own_when_the_elevation_would_cost_a_label(tmp_path):
    """tatry with big badges: with the times on the line of the elevation the end labels would take the room of
    Schronisko PTTK Murowaniec, which fits without the elevation; the times go on a line of their own, on both ends, and
    it stays. A name at the finish that would push it out again is not shown."""
    got = scene(tmp_path, "tatry.gpx", *BADGES)
    assert got["ends"] == [["Kuźnice", f"Start, 1013{NB}m", "05:48"], ["Meta", f"1626{NB}m", "07:43"]]
    assert got["places"] == ["Boczań", "Przełęcz między Kopami", "Schronisko PTTK Murowaniec"]
    both = scene(tmp_path, "tatry.gpx", *BADGES, FORCE_END="Kuźnice|Toporowa Cyrhla")
    assert both["ends"] == got["ends"] and both["places"] == got["places"]


def _with_a_village(tmp_path):
    """A copy of the stored data with a village by the route near the start of tatry."""
    shutil.copytree(DATA / "cache", tmp_path / "cache")
    answers = {f: json.loads(f.read_text(encoding="utf-8")) for f in (tmp_path / "cache" / "osm").glob("*.json")}
    stored, d = next((f, d) for f, d in answers.items() if any(e.get("tags", {}).get("name") == "Kuźnice" for e in d["elements"]))
    d["elements"].append({"type": "node", "id": 990001, "lat": 49.267397, "lon": 19.98458, "tags": {"place": "village", "name": "Testowo Wielkie"}})
    stored.write_text(json.dumps(d), encoding="utf-8")


def test_when_the_times_on_their_own_line_cost_a_label_too_the_facts_stay_on_one_line(tmp_path):
    """The known limit: with the village and big badges, the one-line facts push out two places that fit without the
    elevation, and the times on their own line still push out one (Boczań). The facts then stay on one line."""
    _with_a_village(tmp_path)
    got = scene(tmp_path, "tatry.gpx", *BADGES, "--places", "12")
    assert got["ends"] == [["Kuźnice", f"Start, 1013{NB}m, 05:48"], ["Meta", f"1626{NB}m, 07:43"]]
    assert got["places"] == ["Boczań", "Przełęcz między Kopami"]


def test_a_name_never_turns_a_place_along_the_route_into_a_background_name(tmp_path):
    """A village by the route near the start of tatry, added to a copy of the stored data. With Kuźnice in the start label
    the village would lose its label along the route and stay only as a small background name: the start keeps its facts
    alone, and every label is as without the name."""
    _with_a_village(tmp_path)
    args = ("--size", "640x360", "--places", "12", "--background-names", "--label-size", "2.0")
    got, plain = scene(tmp_path, "tatry.gpx", *args), scene(tmp_path, "tatry.gpx", *args, FORCE_END="|")
    assert got["ends"][0] == ["Start", f"1013{NB}m, 05:48"] and "Testowo Wielkie" in got["places"]
    assert {k: got[k] for k in ("places", "peaks", "background")} == {k: plain[k] for k in ("places", "peaks", "background")}


def test_both_ends_may_be_named_and_a_name_that_does_not_fit_goes(tmp_path):
    """tatry with a place forced at each end: both names show. With bigger labels the long name of the finish would take
    the room of other labels: the finish keeps its facts alone, the start keeps its name, and every other label stays."""
    both = scene(tmp_path, "tatry.gpx", "--size", "960x540", FORCE_END="Kuźnice|Toporowa Cyrhla")
    assert both["ends"] == [["Kuźnice", f"Start, 1013{NB}m, 05:48"], ["Toporowa Cyrhla", f"Meta, 1626{NB}m, 07:43"]]
    long, big = "Toporowa Cyrhla nad Zakopanem pod Gubałówką", ("--size", "960x540", "--label-size", "1.5")
    got = scene(tmp_path, "tatry.gpx", *big, "--rename", f"Toporowa Cyrhla={long}", FORCE_END=f"Kuźnice|{long}")
    plain = scene(tmp_path, "tatry.gpx", *big, FORCE_END="|")
    assert got["ends"] == [["Kuźnice", f"Start, 1013{NB}m, 05:48"], ["Meta", f"1626{NB}m, 07:43"]]
    assert (got["places"], got["peaks"]) == (plain["places"], plain["peaks"])


def test_the_place_at_an_end_is_not_a_background_name_as_well(tmp_path):
    """With smaller labels Kuźnice fits as a background name; named at the start, it is not one as well."""
    args = ("--size", "960x540", "--label-size", "0.7", "--background-names")
    assert "Kuźnice" in scene(tmp_path, "tatry.gpx", *args, FORCE_END="|")["background"]
    got = scene(tmp_path, "tatry.gpx", *args)
    assert got["ends"][0][0] == "Kuźnice" and "Kuźnice" not in got["background"]


@pytest.mark.parametrize("name, kind", [("Forcella Lavaredo", "places"), ("Cima Grande di Lavaredo", "peaks")])
def test_the_place_at_an_end_is_not_labeled_twice(tmp_path, name, kind):
    """A place (or a peak) named at an end is not a label of its own as well."""
    alone = scene(tmp_path, "SLAD.gpx", "--size", "640x360")
    assert name in alone[kind]
    got = scene(tmp_path, "SLAD.gpx", "--size", "640x360", FORCE_END=name)
    assert " ".join(got["ends"][0][:-1]) == name and name not in got[kind]        # the name may take two lines


def _cand(kind, x, y=0.0, place=None, name=None):
    return {"kind": kind, "x": x, "y": y, "place": place, "name": name or f"{kind} {x:g}"}


def test_a_hut_comes_first_then_a_summit_then_the_nearest_other_place():
    hut, top, saddle = _cand("hut", 280), _cand("peak", 60), _cand("saddle", 20)
    assert end_place([saddle, top, hut], 0, 0) is hut                        # a hut within 300 m beats nearer places
    assert end_place([saddle, top], 0, 0) is top                             # then a summit within 100 m
    assert end_place([saddle, _cand("peak", 120), _cand("view", 15)], 0, 0)["kind"] == "view"   # then the nearest within 200 m
    assert end_place([_cand("hut", 320), _cand("peak", 110), _cand("saddle", 210)], 0, 0) is None


@pytest.mark.parametrize("cand, ok", [(_cand("parking", 150), True), (_cand("bivouac", 150), True), (_cand("bivouac", 250), False),
                                      (_cand("place", 100, place="hamlet"), True), (_cand("place", 100, place="village"), True),
                                      (_cand("place", 100, place="suburb"), True), (_cand("place", 100, place="town"), False),
                                      (_cand("attr", 50), False), (_cand("wpt", 10), False)])
def test_what_may_name_an_end(cand, ok):
    assert (end_place([cand], 0, 0) is cand) == ok


def test_the_ends_never_share_a_place_and_the_nearer_end_keeps_it():
    hut, parking = _cand("hut", 0), _cand("parking", 285)
    assert end_places([hut, parking], {0: (280, 0), 9: (10, 0)}) == {0: parking, 9: hut}    # the finish is at the hut
    assert end_places([hut], {0: (100, 0), 9: (150, 0)}) == {0: hut}
    twice = _cand("hut", 15, name="hut 0")                        # the same hut twice, e.g. as a point and as a building
    assert end_places([hut, twice], {0: (120, 0), 9: (-120, 0)}) == {0: twice}
