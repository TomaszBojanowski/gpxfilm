"""The Polish translation in po/pl.po: complete, with nothing left over, and with the same {fields} as the English texts."""
import ast
import re
from pathlib import Path

import pytest

from gpxfilm import i18n, webgui

ROOT = Path(__file__).resolve().parent.parent
FIELD = re.compile(r"\{(\w*)[^{}]*\}")


def texts_in_code() -> dict:
    """Every text the code marks for translation: key as in the catalog -> (English text, its plural or None, where)."""
    out = {}
    for path in sorted((ROOT / "gpxfilm").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "N_", "ngettext", "pgettext", "NC_")):
                continue
            n = {"_": 1, "N_": 1, "ngettext": 2, "pgettext": 2, "NC_": 2}[node.func.id]
            args = node.args[:n]
            where = f"{path.name}:{node.lineno}"
            assert len(args) == n and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args), \
                f"{where}: {node.func.id}() needs plain string literals, not f-strings or variables"
            vals = [a.value for a in args]
            if node.func.id in ("pgettext", "NC_"):
                out[vals[0] + "\x04" + vals[1]] = (vals[1], None, where)
            elif node.func.id == "ngettext":
                out[vals[0]] = (vals[0], vals[1], where)
            else:
                out[vals[0]] = (vals[0], None, where)
    for m in re.finditer(r"⟦(.*?)⟧|⟪(.*?)⟫", webgui.GUI_PAGE, re.S):   # texts of the browser window
        text = m[1] if m[1] is not None else m[2]
        out[text] = (text, None, "webgui.py GUI_PAGE")
    return out


def fields(text: str) -> set:
    return {m[1] for m in FIELD.finditer(text)}


def test_every_text_is_translated():
    catalog, used = i18n.read_po(ROOT / "po" / "pl.po"), texts_in_code()
    missing = [f"{where}: {text!r}" for key, (text, _, where) in used.items() if key not in catalog]
    assert not missing, "not in po/pl.po:\n" + "\n".join(missing)


def test_nothing_is_left_over():
    catalog, used = i18n.read_po(ROOT / "po" / "pl.po"), texts_in_code()
    assert not sorted(set(catalog) - set(used)), "in po/pl.po but not used any more"


def test_translations_keep_the_fields():
    catalog, used = i18n.read_po(ROOT / "po" / "pl.po"), texts_in_code()
    for key, (text, plural, where) in used.items():
        got = catalog.get(key)
        if got is None:
            continue
        forms = got if isinstance(got, list) else [got]
        assert (plural is None) == (not isinstance(got, list)), f"{where}: {text!r} needs {'three' if plural else 'one'} form(s)"
        if plural is not None:
            assert len(forms) == 3, f"{where}: {text!r} needs the three Polish plural forms"
        every = fields(text) | (fields(plural) if plural else set())
        may_skip = fields(text) ^ fields(plural) if plural else set()   # "the highest one" / "the {n} highest": the count can go
        for form in forms:
            assert every - may_skip <= fields(form) <= every, f"{where}: {text!r} -> {form!r}"


def test_the_catalog_says_it_is_polish():
    head = (ROOT / "po" / "pl.po").read_text(encoding="utf-8")
    assert '"Language: pl\\n"' in head and "nplurals=3" in head and "charset=UTF-8" in head


def test_the_texts_in_the_code_are_english():
    """Polish belongs in po/pl.po; the texts in the code have no Polish letters (names inside credits aside)."""
    polish = [f"{where}: {text!r}" for text, plural, where in texts_in_code().values()
              if re.search("[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", re.sub(r"Główny Urząd Geodezji i Kartografii", "", text + (plural or "")))]
    assert not polish, "\n".join(polish)


def test_every_text_is_in_the_catalog_once():
    text = re.sub(r'"\n\s*"', "", (ROOT / "po" / "pl.po").read_text(encoding="utf-8"))   # texts split over lines, as gettext tools write them
    entries = re.findall(r'^(?:msgctxt "(.*)"\n)?msgid "(.*)"', text, re.M)
    assert len(entries) == len(set(entries)), sorted(e for e in set(entries) if entries.count(e) > 1)


PLURAL_CASES = [   # English singular and plural, the other values of the text, and what Polish says for some counts
    ("Map background: {n} tile, zoom {zoom}", "Map background: {n} tiles, zoom {zoom}", {"zoom": 14},
     {1: "Podkład mapy: 1 kafel", 2: "Podkład mapy: 2 kafle", 5: "Podkład mapy: 5 kafli", 22: "Podkład mapy: 22 kafle"}),
    ("Terrain model: {n} tile, zoom {zoom}", "Terrain model: {n} tiles, zoom {zoom}", {"zoom": 12},
     {1: "Model terenu: 1 kafel,", 3: "Model terenu: 3 kafle,", 12: "Model terenu: 12 kafli,"}),
    ("the server did not return {missing} of {total} tile even after retries",
     "the server did not return {missing} of {total} tiles even after retries", {"missing": 1},
     {1: "serwer nie oddał 1 z 1 kafla", 2: "serwer nie oddał 1 z 2 kafli", 35: "serwer nie oddał 1 z 35 kafli"}),
    ("Encoding {n} frame {size} at {fps} fps → {file}", "Encoding {n} frames {size} at {fps} fps → {file}",
     {"size": "1280x720", "fps": 30, "file": "f.mp4"}, {1: "Koduję 1 klatkę", 4: "Koduję 4 klatki", 102: "Koduję 102 klatki", 105: "Koduję 105 klatek"}),
    ("Saved images for slides: {folder} ({n} file)", "Saved images for slides: {folder} ({n} files)", {"folder": "s"},
     {1: "(1 plik)", 2: "(2 pliki)", 5: "(5 plików)"}),
    ("Place in the intro: the most important one within {radius} km of the start",
     "Places in the intro: the {n} most important within {radius} km of the start", {"radius": 20},
     {1: "Miejscowość w intrze: najważniejsza w promieniu 20 km", 4: "Miejscowości w intrze: 4 najważniejsze w",
      5: "Miejscowości w intrze: 5 najważniejszych w"}),
    ("Peak in the intro: the highest one within {radius} km of the start",
     "Peaks in the intro: the {n} highest within {radius} km of the start", {"radius": 20},
     {1: "Szczyt w intrze: najwyższy w promieniu 20 km", 3: "Szczyty w intrze: 3 najwyższe w", 5: "Szczyty w intrze: 5 najwyższych w"}),
    ("the server did not return {missing} of {total} tile even after retries ({source})",
     "the server did not return {missing} of {total} tiles even after retries ({source})", {"missing": 1, "source": "IGN"},
     {1: "serwer nie oddał 1 z 1 kafla mimo ponawiania (IGN)", 3: "z 3 kafli"}),
    ("there is {n} track", "there are {n} tracks", {}, {1: "jest 1 ślad", 2: "są 2 ślady", 5: "jest 5 śladów"}),
    ("there is {n} route", "there are {n} routes", {}, {1: "jest 1 trasa", 3: "są 3 trasy", 11: "jest 11 tras"}),
]


@pytest.mark.parametrize("singular, plural, values, forms", PLURAL_CASES)
def test_counts_take_the_right_polish_form(singular, plural, values, forms):
    pl, en = i18n.Translator("pl"), i18n.Translator("en")
    for n, want in forms.items():
        assert want in pl.ngettext(singular, plural, n).format(n=n, total=n, **values), (n, want)
    assert en.ngettext(singular, plural, 1) == singular and en.ngettext(singular, plural, 2) == plural


def test_every_count_is_tested():
    """Each text with plural forms has its Polish forms checked above."""
    assert {text for text, plural, _ in texts_in_code().values() if plural} == {case[0] for case in PLURAL_CASES}
