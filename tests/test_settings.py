"""The settings file ~/.config/gpxfilm.toml: English names, and the old Polish ones that still work."""
import re
import subprocess
import sys
from pathlib import Path

import pytest
from offline import offline_env, offline_run

from gpxfilm.legacy import OLD_KEYS, OLD_VALUES, upgrade

ROOT = Path(__file__).resolve().parent.parent
TOML = pytest.mark.skipif(sys.version_info < (3, 11), reason="the settings file needs Python 3.11 (tomllib)")
DATA = ROOT / "tests" / "data"


# the options of the version before the English names, except --gui, --port, --fps, --intro, --logo, --outro and --crf
OLD_OPTIONS = """sprawdz-mapy wyjscie tytul podtytul rozmiar czas kolor punkty szczyty szczyty-intro promien-szczytow czas-szczytow jezyk
    napisy wielkosc-napisow jezyk-nazw zmien pomin strefa intro-czas zdjecia czas-zdjecia przesun-zdjecia maks-zdjec postoje podpisy
    kilometry dystans podejscia liczniki bez-tetna koloruj kroj outro-czas muzyka szkic kadry mapa styl slonce minimapa nazwy auto-tytul
    raport kadr tylko-kadr bez-osm slad zachowaj-godziny koder""".split()


def test_old_names_and_values_get_the_new_ones():
    got, changes = upgrade({"mapa": "orto", "koloruj": "tetno", "napisy": "plakietki", "styl": "szary", "jezyk_nazw": "it",
                            "intro": True, "map_style": "nocny", "label-size": 1.2, "szczyty-intro": 3})
    assert got == {"map": "aerial", "color_by": "heart-rate", "label_style": "badges", "map_style": "night", "name_language": "it",
                   "intro": True, "label_size": 1.2, "intro_peaks": 3}
    assert ('mapa = "orto"', 'map = "aerial"') in changes and ('map_style = "nocny"', 'map-style = "night"') in changes
    assert ('jezyk_nazw = "it"', 'name-language = "it"') in changes and ('szczyty-intro = 3', 'intro-peaks = 3') in changes
    assert len(changes) == 7                                           # intro and label-size were already right


def test_english_names_that_changed_get_the_new_ones():
    got, changes = upgrade({"intro-peak-radius": 12, "promien_szczytow": 8, "intro_radius": 5})
    assert got == {"intro_radius": 5} and changes == [("intro-peak-radius = 12", "intro-radius = 12"), ("promien_szczytow = 8", "intro-radius = 8")]
    got, changes = upgrade({"intro-peak-duration": 3, "czas-szczytow": 2.5})
    assert got == {"intro_label_duration": 2.5}
    assert changes == [("intro-peak-duration = 3", "intro-label-duration = 3"), ("czas-szczytow = 2.5", "intro-label-duration = 2.5")]


def test_every_option_has_its_old_name(tmp_path):
    """Each old Polish option name maps to an option the command knows, and each old value to one of its choices."""
    assert sorted(OLD_KEYS) == sorted(o.replace("-", "_") for o in OLD_OPTIONS)
    r = subprocess.run([sys.executable, "-m", "gpxfilm", "--help"], cwd=ROOT, capture_output=True, text=True, env=offline_env(tmp_path))
    options = set(re.findall(r"--([a-z][a-z-]*)", r.stdout))
    for new in OLD_KEYS.values():
        assert new.replace("_", "-") in options, new
    for key, values in OLD_VALUES.items():
        choices = {"map": {"terrain", "aerial", "satellite"}}.get(key) or set(
            re.search(rf"--{key.replace('_', '-')} {{([a-z,-]+)}}", r.stdout)[1].split(","))
        assert set(values.values()) <= choices, key


def _run(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "gpxfilm", *args], cwd=ROOT, capture_output=True, text=True,
                          env=offline_env(tmp_path / "cache", XDG_CONFIG_HOME=str(tmp_path / "config")))


@TOML
def test_old_names_are_told_once_until_the_file_changes(tmp_path):
    cfg = tmp_path / "config" / "gpxfilm.toml"
    cfg.parent.mkdir()
    cfg.write_text('mapa = "satelita"\njezyk_nazw = "it"\nszczyty-intro = 3\ncolor = "#FFB21E"\n', encoding="utf-8")
    first, again = _run(tmp_path), _run(tmp_path)
    assert 'mapa = "satelita"  →  map = "satellite"' in first.stderr and 'jezyk_nazw = "it"  →  name-language = "it"' in first.stderr
    assert 'szczyty-intro = 3  →  intro-peaks = 3' in first.stderr                       # old names as they are in the file
    assert "color =" not in first.stderr                                                  # a setting already in English is not listed
    assert "starych nazw" not in again.stderr                                             # once is enough
    cfg.write_text('mapa = "satelita"\n', encoding="utf-8")
    assert "starych nazw" in _run(tmp_path).stderr                                         # until the file changes


@TOML
def test_old_names_still_work(tmp_path):
    cfg = tmp_path / "config" / "gpxfilm.toml"
    cfg.parent.mkdir()
    cfg.write_text("punkty = 2\nszczyty = 0\n", encoding="utf-8")
    _, rep = offline_run(DATA / "SLAD.gpx", tmp_path, "--size", "640x360")
    assert len(rep["places"]) == 2 and rep["peaks"] == []
