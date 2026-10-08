"""Translations: the system language, the .po reader and Polish plural forms."""
import pytest

from gpxfilm import i18n

PO = r'''# a comment
msgid ""
msgstr ""
"Language: pl\n"

msgid "Drawing the background…"
msgstr "Rysuję tło…"

#, python-brace-format
msgid "{n} km"
msgstr ""
"{n} "
"km"

msgctxt "date"
msgid "July"
msgstr "lipca"

msgid "there is {n} track"
msgid_plural "there are {n} tracks"
msgstr[0] "jest {n} ślad"
msgstr[1] "są {n} ślady"
msgstr[2] "jest {n} śladów"

#, fuzzy
msgid "Not sure"
msgstr "Niepewne"

msgid "Not translated"
msgstr ""
'''


@pytest.fixture
def polish(tmp_path, monkeypatch):
    (tmp_path / "pl.po").write_text(PO, encoding="utf-8")
    monkeypatch.setattr(i18n, "po_dir", lambda: tmp_path)
    i18n.translator.cache_clear()
    yield i18n.Translator("pl")
    i18n.translator.cache_clear()


def test_the_po_file_is_read_as_gettext_reads_it(polish):
    assert polish.gettext("Drawing the background…") == "Rysuję tło…"
    assert polish.gettext("{n} km") == "{n} km"                            # lines of one text are joined
    assert polish.pgettext("date", "July") == "lipca" and polish.gettext("July") == "July"
    assert polish.gettext("Not sure") == "Not sure" and polish.gettext("Not translated") == "Not translated"


@pytest.mark.parametrize("n, text", [(1, "jest 1 ślad"), (2, "są 2 ślady"), (4, "są 4 ślady"), (5, "jest 5 śladów"),
                                     (12, "jest 12 śladów"), (22, "są 22 ślady"), (25, "jest 25 śladów"), (0, "jest 0 śladów")])
def test_polish_has_three_plural_forms(polish, n, text):
    assert polish.ngettext("there is {n} track", "there are {n} tracks", n).format(n=n) == text


def test_english_is_the_text_itself():
    en = i18n.Translator("en")
    assert en.gettext("Drawing the background…") == "Drawing the background…"
    assert en.ngettext("there is {n} track", "there are {n} tracks", 1) == "there is {n} track"
    assert en.ngettext("there is {n} track", "there are {n} tracks", 3) == "there are {n} tracks"


@pytest.mark.parametrize("env, language", [
    ({"LANG": "pl_PL.UTF-8"}, "pl"), ({"LANG": "en_US.UTF-8"}, "en"), ({"LANG": "de_DE.UTF-8"}, "en"),
    ({"LANGUAGE": "de:pl", "LANG": "en_US.UTF-8"}, "pl"),                  # the first language of the list that has a translation
    ({"LC_ALL": "pl_PL.UTF-8", "LANG": "en_US.UTF-8"}, "pl"), ({"LC_MESSAGES": "en_GB", "LANG": "pl_PL"}, "en"),
    ({"LANG": "C"}, "en"), ({"LANG": "pl"}, "pl"), ({"LANGUAGE": "pl_PL@euro"}, "pl")])
def test_the_system_language(monkeypatch, env, language):
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert i18n.system_language() == language


def test_numbers_follow_the_language(polish, monkeypatch):
    (i18n.po_dir() / "pl.po").write_text(PO + '\nmsgctxt "decimal separator"\nmsgid "."\nmsgstr ","\n', encoding="utf-8")
    i18n.translator.cache_clear()
    assert i18n.number(0.62, 1, "pl") == "0,6" and i18n.number(0.62, 1, "en") == "0.6"


POLISH_CREDITS = {   # the credits as the films had them before the texts of the program were English
    "topo": "Mapa: © OpenStreetMap, SRTM; styl © OpenTopoMap (CC-BY-SA)",
    "satellite": "Zdjęcia: Sentinel-2 cloudless, s2maps.eu, EOX (dane Copernicus Sentinel 2016–2017). Teren: Mapterhorn. "
                 "Szlaki: © OpenStreetMap",
    "PL": "Ortofotomapa: Główny Urząd Geodezji i Kartografii, geoportal.gov.pl",
    "IT": "Ortofoto 2023: © Provincia Autonoma di Bolzano – Alto Adige, CC BY 4.0; Zdjęcia: Sentinel-2 cloudless, s2maps.eu, "
          "EOX (dane Copernicus Sentinel 2016–2017); Teren: Mapterhorn",
    "topo+terrain": "Mapa: © OpenStreetMap, SRTM; styl © OpenTopoMap (CC-BY-SA); Teren: Mapterhorn",
}


def _credits() -> dict:
    from gpxfilm.sources import AERIAL, MAPS, SENTINEL, TERRAIN
    return {"topo": MAPS["topo"][2], "satellite": MAPS["satellite"][2], "PL": AERIAL["PL"][0][2],
            "IT": f"{AERIAL['IT'][0][2]}; {SENTINEL}; {TERRAIN}", "topo+terrain": f"{MAPS['topo'][2]}; {TERRAIN}"}


@pytest.mark.parametrize("name", POLISH_CREDITS)
def test_credits_are_translated_part_by_part(name):
    english = _credits()[name]
    assert i18n.credit(english, i18n.Translator("pl")) == POLISH_CREDITS[name]
    assert i18n.credit(english, i18n.Translator("en")) == english


def test_the_credit_on_the_frame_follows_the_film_language(monkeypatch):
    from gpxfilm import compose
    monkeypatch.setenv("LANGUAGE", "en")                                   # messages in English, the film in Polish
    monkeypatch.setitem(compose.LOOK, "credit", _credits()["topo+terrain"])
    assert compose.frame_credit("pl", "default") == POLISH_CREDITS["topo+terrain"]
    assert compose.frame_credit("en", "default") == _credits()["topo+terrain"]
    monkeypatch.delitem(compose.LOOK, "credit")
    assert compose.frame_credit("pl", "default") == "default"              # the terrain drawing has its own credit text


def test_names_from_openstreetmap_are_credited_on_every_background(monkeypatch):
    """Aerial photos and a background of one's own do not name OpenStreetMap, but the names of places and peaks come from
    it: the credit names it then, once, and not when the film has no data from it (--no-osm)."""
    from gpxfilm import compose
    aerial = "Orthophotomap: Główny Urząd Geodezji i Kartografii, geoportal.gov.pl"
    monkeypatch.setitem(compose.LOOK, "credit", aerial)
    assert compose.frame_credit("en", "default", names=True) == aerial + "; Names: © OpenStreetMap"
    assert compose.frame_credit("pl", "default", names=True) == "Ortofotomapa: Główny Urząd Geodezji i Kartografii, geoportal.gov.pl; Nazwy: © OpenStreetMap"
    assert compose.frame_credit("en", "default", names=False) == aerial
    monkeypatch.setitem(compose.LOOK, "credit", _credits()["topo+terrain"])
    assert compose.frame_credit("en", "default", names=True) == _credits()["topo+terrain"]   # it names OpenStreetMap already


@pytest.mark.parametrize("env, language", [({"AppleLocale": "pl_PL\n"}, "pl"), ({"AppleLocale": "en_PL\n"}, "en"), ({}, "en")])
def test_the_macos_setting_counts_when_no_variable_is_set(monkeypatch, env, language):
    """A program started from the Dock gets none of the variables; the language then comes from the system setting."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)

    def run(cmd, **kw):
        if "AppleLocale" not in env:
            raise OSError("no defaults command")
        return type("Done", (), {"stdout": env["AppleLocale"]})()

    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    monkeypatch.setattr(i18n.subprocess, "run", run)
    i18n._mac_language.cache_clear()
    try:
        assert i18n.system_language() == language
    finally:
        i18n._mac_language.cache_clear()
