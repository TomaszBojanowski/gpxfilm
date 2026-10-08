"""Both languages. With the system in Polish the program says and draws what it did before its texts were English (the
reference frames and the other tests check that); with the system in English all it says, serves and draws is English.
--language sets the texts on the film alone."""
import json
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest
from offline import ROOT, offline_env, offline_frames
from PIL import Image
from test_frames import BAD_LEVEL, CASES, GOLDEN, MAX_BAD_SHARE, MAX_MEAN_DIFF

from gpxfilm import i18n, text, webgui

DATA = ROOT / "tests" / "data"
POLISH = re.compile(r"[ąćęłńóśźż]|\b(się|jest|nie|albo|oraz|gdy|tylko|przez|plik|pliku|ślad|trasa|trasy|kafli|podkład|pobieram|"
                    r"zapisano|rysuję|biorę|zostaje|pomijam)\b", re.I)


def run(tmp_path, language: str, *args: str) -> subprocess.CompletedProcess:
    """The program with the stored test data, without network, on a system in the given language."""
    cache = tmp_path / "cache"
    if not cache.exists():
        shutil.copytree(DATA / "cache", cache)
    env = offline_env(cache, LANGUAGE=language, LC_ALL="", LC_MESSAGES="", LANG="C.UTF-8", XDG_CONFIG_HOME=str(tmp_path / "config"))
    return subprocess.run([sys.executable, "-m", "gpxfilm", *args], cwd=ROOT, env=env, capture_output=True, text=True)


def polish_lines(text: str) -> list:
    return [ln for ln in text.splitlines() if POLISH.search(ln)]


def same_frame(png, name: str) -> bool:
    """The frame looks like the reference frame of a case of test_frames.py, with the same tolerance."""
    got, want = (np.asarray(Image.open(p).convert("RGB"), np.int16) for p in (png, GOLDEN / f"{name}.png"))
    diff = np.abs(got - want)
    return got.shape == want.shape and float(diff.mean()) <= MAX_MEAN_DIFF and float((diff.max(-1) > BAD_LEVEL).mean()) <= MAX_BAD_SHARE


def test_help_follows_the_system_language(tmp_path):
    en, pl = (run(tmp_path, language, "--help") for language in ("en", "pl"))
    assert "GPX file with the track" in en.stdout and not polish_lines(en.stdout)
    assert "plik GPX ze śladem" in pl.stdout and "--map-style" in pl.stdout       # option names are English in both
    words = lambda r: " ".join(r.stdout.split())                                  # the help as one line, wherever it wraps
    assert "--intro-label-duration S seconds the labels of the places and peaks of the intro stay fully visible" in words(en)
    assert "--intro-label-duration SEK ile sekund podpisy miejscowości i szczytów w intrze są w pełni widoczne" in words(pl)


POLISH_PLACEHOLDERS = {"N": "LICZBA", "S": "SEK", "FILE": "PLIK", "DIR": "KATALOG", "TEXT": "TEKST", "NAME": "NAZWA",
                       "OLD=NEW": "STARA=NOWA", "MAP": "RODZAJ", "COLOR": "KOLOR", "ZONE": "STREFA", "CODES": "KODY",
                       "WxH": "SZERxWYS"}                      # units and file formats (KM, MIN, PNG, JSON) stay as they are
NUMBERS = {"--track", "--port"}                                 # a number, not a count: NR, not LICZBA


def placeholders(tmp_path, language: str) -> dict:
    """The placeholder of each option in the usage of --help, e.g. {'--track': 'N', '-o': 'FILE'}."""
    usage = run(tmp_path, language, "--help").stdout.split("\n\n")[0]
    return dict(re.findall(r"\[(-[\w-]+) ([^\]\s]+)\]", usage))


def test_help_placeholders_follow_the_system_language(tmp_path):
    en, pl = placeholders(tmp_path, "en"), placeholders(tmp_path, "pl")
    assert len(en) > 40 and en["--track"] == en["--places"] == "N" and en["--photos"] == "DIR" and en["-o"] == "FILE"
    assert pl == {o: "NR" if o in NUMBERS else POLISH_PLACEHOLDERS.get(m, m) for o, m in en.items()}


def test_the_film_words_match_the_option_names():
    """On an English film a stop is a stop and the colors show the slope, as in --stop-minutes and --color-by slope."""
    en, pl = text.film_texts("en"), text.film_texts("pl")
    assert (en["stop"], en["slope"]) == ("stop", "slope") and (pl["stop"], pl["slope"]) == ("postój", "nachylenie")


@pytest.mark.parametrize("args, en, pl", [
    ([], "give a GPX file or run with the --gui option", "podaj plik GPX albo uruchom z opcją --gui"),
    (["--size", "1000x1000", "x.gpx"], "The frame must have a 16:9 aspect ratio.", "Kadr musi mieć proporcje 16:9."),
    ([str(DATA / "gpx" / "activity.tcx")], "is a TCX file, not GPX", "to plik TCX, a nie GPX"),
])
def test_errors_follow_the_system_language(tmp_path, args, en, pl):
    assert en in run(tmp_path, "en", *args).stderr
    assert pl in run(tmp_path, "pl", *args).stderr


def test_an_english_system_gets_english_messages_report_and_film(tmp_path):
    """tatry.gpx as in test_frames.py, without --language: on an English system the film is English, like the reference made
    with --language en, and so are the messages and the country in the report."""
    gpx, args = CASES["tatry"]
    i = args.index("--language")
    png, rep = tmp_path / "frame.png", tmp_path / "report.json"
    r = run(tmp_path, "en", str(DATA / gpx), *args[:i], *args[i + 2:], "--frame-only", str(png), "--report", str(rep))
    assert r.returncode == 0, r.stderr
    assert "Drawing the background…" in r.stderr and "Saved the frame" in r.stderr
    assert not polish_lines(r.stderr), r.stderr
    assert same_frame(png, "tatry")
    report = json.loads(rep.read_text(encoding="utf-8"))
    assert report["country"] == "Poland" and report["peaks"] == ["Kasprowy Wierch", "Mała Kopa Królowa", "Nosal"]   # names are data


def test_language_sets_only_the_film(tmp_path):
    """An English system with --language pl: English messages, the Polish film of the reference frame."""
    gpx, args = CASES["slad"]
    png = tmp_path / "frame.png"
    r = run(tmp_path, "en", str(DATA / gpx), *args, "--language", "pl", "--frame-only", str(png))
    assert r.returncode == 0, r.stderr
    assert "Saved the frame" in r.stderr and not polish_lines(r.stderr), r.stderr
    assert same_frame(png, "slad")


@pytest.mark.parametrize("system, film, names", [("en", "pl", ["00-mapa.png", "99-meta.png"]), ("pl", "en", ["00-map.png", "99-finish.png"])])
def test_slides_are_named_in_the_film_language(tmp_path, system, film, names):
    slides = tmp_path / "slides"
    r = run(tmp_path, system, str(DATA / "SLAD.gpx"), "--size", "640x360", "--language", film, "--slides", str(slides),
            "--frame-only", str(tmp_path / "frame.png"))
    assert r.returncode == 0, r.stderr
    assert sorted(p.name for p in slides.glob("*.png") if p.name[:2] in ("00", "99")) == names


def test_the_intro_names_the_country_in_the_language_of_the_messages(tmp_path):
    _, err = offline_frames(DATA / "SLAD.gpx", tmp_path, "--size", "640x360", "--fps", "5", "--route-duration", "2", "--intro",
                            "--intro-duration", "1", "--language", "pl", LANGUAGE="en")
    assert "country view (Italy)" in err and not polish_lines(err), err


def test_a_language_without_translation_is_english(tmp_path):
    r = run(tmp_path, "de", "--help")
    assert "GPX file with the track" in r.stdout


@pytest.mark.parametrize("language", i18n.LANGUAGES)
def test_the_window_page_is_in_the_language(language):
    page = webgui.localize(webgui.GUI_PAGE, i18n.Translator(language))
    assert not re.search("[⟦⟧⟪⟫]", page)                                       # every marked text was replaced
    if language == "en":
        assert not polish_lines(page), polish_lines(page)
        assert ">Choose…</button>" in page and "'Preview ready.'" in page
    else:
        assert ">Wybierz…</button>" in page and "'Podgląd gotowy.'" in page
        assert "'Brak połączenia z programem. Czy terminal jest nadal otwarty?'" in page


def test_texts_in_javascript_stay_valid_strings():
    """A translation inside a JavaScript string keeps the quotes of the string closed."""
    t = i18n.Translator("pl")
    t.catalog = {"It's here.": "Jest „tu” i 'tam'\\n."}
    page = webgui.localize("say('⟪It's here.⟫');<p>⟦It's here.⟧</p>", t)
    assert page == "say('Jest „tu” i \\'tam\\'\\\\n.');<p>Jest „tu” i &#x27;tam&#x27;\\n.</p>"


def test_settings_file_notice_follows_the_system_language(tmp_path):
    pytest.importorskip("tomllib")
    cfg = tmp_path / "config" / "gpxfilm.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('mapa = "orto"\n', encoding="utf-8")
    r = run(tmp_path, "en", "--help")
    assert 'The settings file' in r.stderr and 'mapa = "orto"  →  map = "aerial"' in r.stderr and not polish_lines(r.stderr)
