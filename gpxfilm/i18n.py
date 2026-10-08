"""Translations. The texts in the code are English; other languages come from po/<language>.po, read directly (no compiled
catalogs, no gettext tools). Messages and the browser window follow the system language; the texts on the film follow
--language, which defaults to the system language too."""
from __future__ import annotations

import ast
import functools
import os
import subprocess
import sys
from pathlib import Path

LANGUAGES = ("en", "pl")                               # English is the language of the code, Polish comes from po/pl.po


def po_dir() -> Path:
    """po/ inside the installed package, or next to the package in a source checkout."""
    here = Path(__file__).resolve().parent
    return next((d for d in (here / "po", here.parent / "po") if d.is_dir()), here / "po")


def read_po(path: Path) -> dict:
    """Translations of a .po file: msgid (with its context as 'context\\x04msgid') -> msgstr, or the list of msgstr[n] for a
    text with plural forms. Fuzzy and untranslated entries are left out, as gettext does."""
    out, entry, field = {}, {}, None

    def done():
        if entry.get("msgid") and not entry.get("fuzzy"):
            key = (entry["msgctxt"] + "\x04" if "msgctxt" in entry else "") + entry["msgid"]
            if "msgid_plural" in entry:
                forms = [entry[k] for k in sorted((k for k in entry if k.startswith("msgstr[")), key=lambda k: int(k[7:-1]))]
                if forms and all(forms):
                    out[key] = forms
            elif entry.get("msgstr"):
                out[key] = entry["msgstr"]
        entry.clear()

    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        line = line.strip()
        if not line:
            done()
            field = None
        elif line.startswith("#,") and "fuzzy" in line:
            entry["fuzzy"] = True
        elif line.startswith("#"):
            continue
        elif line.startswith('"'):
            entry[field] += ast.literal_eval(line)
        else:
            field, _sep, rest = line.partition(" ")
            if field == "msgctxt" and "msgid" in entry:   # a new entry without a blank line before it
                done()
            entry[field] = ast.literal_eval(rest)
    return out


def plural_form(language: str, n: int) -> int:
    """Which plural form a count takes: Polish has three (1 ślad, 2 ślady, 5 śladów), English two."""
    if language == "pl":
        return 0 if n == 1 else 1 if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 2
    return 0 if n == 1 else 1


@functools.cache
def _mac_language() -> str | None:
    if sys.platform != "darwin":
        return None
    try:                                              # programs started from the Dock get no LANG; the system setting tells
        out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=2).stdout
        return out.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def system_language() -> str:
    """The language of the system, read like gettext does: the first of LANGUAGE (a list), LC_ALL, LC_MESSAGES and LANG that
    is set, then the macOS setting. English when that language has no translation."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            break
    else:
        value = _mac_language() or ""
    for item in value.split(":"):
        code = item.split(".")[0].split("@")[0].split("_")[0].split("-")[0].lower()
        if code in LANGUAGES:
            return code
    return "en"


class Translator:
    """Texts of one language: gettext, ngettext and pgettext as in the gettext module."""

    def __init__(self, language: str):
        self.language = language if language in LANGUAGES else "en"
        path = po_dir() / f"{self.language}.po"
        self.catalog = read_po(path) if self.language != "en" and path.exists() else {}

    def gettext(self, text: str) -> str:
        return self.catalog.get(text, text)

    def pgettext(self, context: str, text: str) -> str:
        return self.catalog.get(context + "\x04" + text, text)

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        forms = self.catalog.get(singular)
        if isinstance(forms, list):
            return forms[min(plural_form(self.language, n), len(forms) - 1)]
        return singular if n == 1 else plural


@functools.cache
def translator(language: str) -> Translator:
    return Translator(language)


def ui() -> Translator:
    """Texts of messages and of the browser window, in the system language."""
    return translator(system_language())


def _(text: str) -> str:
    return ui().gettext(text)


def ngettext(singular: str, plural: str, n: int) -> str:
    return ui().ngettext(singular, plural, n)


def pgettext(context: str, text: str) -> str:
    return ui().pgettext(context, text)


def N_(text: str) -> str:
    """Marks a text for translation where it is defined; it is translated where it is used."""
    return text


def NC_(context: str, text: str) -> tuple[str, str]:
    """Marks a text with its context; translated where it is used, with pgettext(*marked)."""
    return context, text


def credit(text: str, t: Translator) -> str:
    """A map credit in the language of t. Credits of several sources are joined with '; ' and translated one by one."""
    whole = t.gettext(text)
    return whole if whole != text else "; ".join(t.gettext(part) for part in text.split("; "))


def number(value: float, digits: int = 0, language: str | None = None) -> str:
    """A number with the decimal separator of the language (0,6 in Polish, 0.6 in English)."""
    t = translator(language) if language else ui()
    return f"{value:.{digits}f}".replace(".", t.pgettext("decimal separator", "."))
