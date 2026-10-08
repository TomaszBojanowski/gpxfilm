"""Old names of the options, so settings written with them still work: the Polish names the options had before they were
English, and English names that changed later."""
from __future__ import annotations

import json

OLD_KEYS = {                                           # old name of a setting: its new name
    "sprawdz_mapy": "check_maps",
    "wyjscie": "output",
    "tytul": "title",
    "podtytul": "subtitle",
    "auto_tytul": "auto_title",
    "rozmiar": "size",
    "czas": "route_duration",
    "kolor": "color",
    "koloruj": "color_by",
    "mapa": "map",
    "styl": "map_style",
    "slonce": "sunlight",
    "minimapa": "minimap",
    "bez_osm": "no_osm",
    "punkty": "places",
    "szczyty": "peaks",
    "nazwy": "background_names",
    "podpisy": "label_file",
    "zmien": "rename",
    "pomin": "skip",
    "napisy": "label_style",
    "wielkosc_napisow": "label_size",
    "jezyk": "language",
    "jezyk_nazw": "name_language",
    "strefa": "timezone",
    "intro_czas": "intro_duration",
    "szczyty_intro": "intro_peaks",
    "promien_szczytow": "intro_radius",
    "czas_szczytow": "intro_label_duration",
    "outro_czas": "outro_duration",
    "zdjecia": "photos",
    "czas_zdjecia": "photo_duration",
    "przesun_zdjecia": "photo_offset",
    "maks_zdjec": "photo_limit",
    "postoje": "stop_minutes",
    "kilometry": "km_markers",
    "liczniki": "counters",
    "bez_tetna": "no_heart_rate",
    "dystans": "distance",
    "podejscia": "ascent",
    "slad": "track",
    "zachowaj_godziny": "keep_times",
    "kroj": "font",
    "muzyka": "music",
    "szkic": "draft",
    "kadry": "slides",
    "kadr": "frame",
    "tylko_kadr": "frame_only",
    "raport": "report",
    "koder": "encoder",
}
RENAMED = {                                            # English names that changed: the old name, the new one
    "intro_peak_radius": "intro_radius",
    "intro_peak_duration": "intro_label_duration",
}
OLD_VALUES = {                                         # setting: its old values and their new names
    "map": {"teren": "terrain", "orto": "aerial", "satelita": "satellite"},
    "map_style": {"naturalny": "natural", "nocny": "night", "jasny": "light", "szary": "gray"},
    "label_style": {"zwykle": "plain", "mocne": "strong", "plakietki": "badges"},
    "color_by": {"nachylenie": "slope", "predkosc": "speed", "tetno": "heart-rate", "wysokosc": "elevation"},
}


def upgrade(settings: dict) -> tuple[dict, list[tuple[str, str]]]:
    """The settings, named with dashes or underscores, under their new names (with underscores) and with new values, and
    each replacement as two lines of a settings file: the old one as it was written and the new one with the name written
    as the option is (e.g. 'jezyk_nazw = "it"' and 'name-language = "it"')."""
    out, changes = {}, []
    for key, value in settings.items():
        name = key.replace("-", "_")
        new_key = OLD_KEYS.get(name) or RENAMED.get(name, name)
        new_value = OLD_VALUES.get(new_key, {}).get(value, value) if isinstance(value, str) else value
        out[new_key] = new_value
        if (new_key, new_value) != (name, value):
            changes.append((f"{key} = {toml(value)}", f"{new_key.replace('_', '-')} = {toml(new_value)}"))
    return out, changes


def toml(value) -> str:
    """A value as it is written in a settings file."""
    return ("true" if value else "false") if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)
