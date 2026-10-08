"""Styles and size of the labels on the map (--label-style, --label-size)."""
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
from offline import offline_env, offline_run
from PIL import Image, ImageDraw, ImageFont

from gpxfilm import text, webgui
from gpxfilm.draw import MUTE, WHITE

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
FONT = ImageFont.truetype(str(DATA / "cache" / "fonts" / "Figtree-600.ttf"), 26, layout_engine=ImageFont.Layout.BASIC)


def _label(monkeypatch, style="plain", size=1.0, label=True):
    monkeypatch.setitem(text.LABEL, "style", style)
    monkeypatch.setitem(text.LABEL, "scale", size)
    return text.text_sprite([("Rifugio", FONT, WHITE, 30), ("2405 m", FONT, MUTE, 26)], 1.0, "left", label=label)


def _rgba(sp):
    """The sprite as plain RGBA, 0..255 (sprites keep premultiplied color)."""
    a = sp.a[..., None]
    rgb = np.where(a > 0, sp.pre / np.maximum(a, 1e-6), 0)
    return np.concatenate([np.round(rgb), np.round(a * 255)], -1).astype(np.int16)


def test_other_text_keeps_its_look(monkeypatch):
    plain = _label(monkeypatch, label=False)
    other = _label(monkeypatch, "badges", 1.5, label=False)       # titles, counters and the legend are not labels
    assert (other.w, other.h) == (plain.w, plain.h) and np.array_equal(_rgba(other), _rgba(plain))


def test_the_size_scales_the_label(monkeypatch):
    base, big = _label(monkeypatch), _label(monkeypatch, size=1.5)
    assert big.w == pytest.approx(1.5 * base.w, abs=4) and big.h == pytest.approx(1.5 * base.h, abs=2)


def test_strong_labels_take_the_same_room_and_badges_more(monkeypatch):
    plain, strong, badge = (_label(monkeypatch, s) for s in ("plain", "strong", "badges"))
    assert (strong.w, strong.h) == (plain.w, plain.h)
    assert (badge.w, badge.h) == (plain.w + 18, plain.h + 10)        # 9 and 5 units of padding on each side
    a = _rgba(badge)[..., 3]
    assert a[badge.pad + 2, badge.pad + 2] >= 170                     # the dark box under the text, also near its corner
    assert a[badge.pad, badge.pad] < 100                              # which is rounded
    assert _rgba(strong)[..., 3].sum() > _rgba(plain)[..., 3].sum()   # the outline and the stronger glow


@pytest.mark.parametrize("style, light", [("plain", False), ("strong", True), ("badges", True)])
def test_second_rows_are_light_in_the_strong_styles(monkeypatch, style, light):
    sp = _label(monkeypatch, style)
    y = sp.pad + (5 if style == "badges" else 0) + 32                       # the second row, below the badge's padding
    rows = _rgba(sp)[y:sp.pad + sp.h, sp.pad:sp.pad + sp.w]
    top = rows[rows[..., 3] == 255][..., :3].max(0)
    assert tuple(top) == ((236, 239, 241) if light else MUTE[:3])


@pytest.mark.parametrize("style", ["plain", "strong", "badges"])
def test_background_names_keep_their_colors(monkeypatch, style):
    """Lake names stay blue and town names dimmed in every style: only the gray second rows turn light. The reference
    frames are too tolerant to see this, the names are small."""
    monkeypatch.setitem(text.LABEL, "style", style)
    monkeypatch.setitem(text.LABEL, "scale", 1.0)
    for color, blue in (((196, 226, 240, 235), True), ((236, 236, 230, 225), False)):     # a lake, a town (compose.py)
        a = _rgba(text.text_sprite([("Laghi dei Piani", FONT, color, 30)], 1.0, "left", label=True))
        top = a[a[..., 3] > 200][..., :3]
        r, _, b = top[np.argsort(top.sum(-1))[-20:]].mean(0)               # the brightest pixels of the letters
        assert (b - r > 25) if blue else (b < r), f"{color}: letters drawn as {r:.0f} … {b:.0f}"


def test_the_strong_outline_grows_with_the_frame(monkeypatch):
    """The dark outline of strong labels is 1.7 units wide, so the labels look the same at every frame size."""
    monkeypatch.setitem(text.LABEL, "style", "strong")
    monkeypatch.setitem(text.LABEL, "scale", 1.0)
    for K in (1.0, 2.0):
        font = ImageFont.truetype(str(DATA / "cache" / "fonts" / "Figtree-600.ttf"), int(26 * K), layout_engine=ImageFont.Layout.BASIC)
        got = _rgba(text.text_sprite([("Rifugio", font, WHITE, 30 * K)], K, "left", label=True))
        ref = Image.new("RGBA", (got.shape[1], got.shape[0]), (0, 0, 0, 0))
        ImageDraw.Draw(ref).text((0, 0), "Rifugio", font=font, fill=WHITE, stroke_width=round(1.7 * K), stroke_fill=(8, 10, 12, 255))
        dark = [int(((a[..., 3] == 255) & (np.abs(a[..., :3] - (8, 10, 12)).max(-1) <= 1)).sum()) for a in (got, np.asarray(ref, np.int16))]
        assert dark[0] == pytest.approx(dark[1], rel=0.15), f"K={K}: outline {dark[0]} pixels, expected about {dark[1]}"


def test_stop_badges_are_labels(tmp_path):
    """The badge of a stop is a label on the map too: it gets the chosen style and size."""
    shutil.copytree(DATA / "cache", tmp_path / "cache")
    spy = textwrap.dedent("""
        import sys
        from gpxfilm import cli, compose
        seen, draw = [], compose.text_sprite
        def text_sprite(rows, *a, **kw):
            seen.append((rows[0][0], kw.get("label", False)))
            return draw(rows, *a, **kw)
        compose.text_sprite = text_sprite
        sys.argv = ["gpxfilm", *sys.argv[1:]]
        cli.main()
        print("badges:", [lab for t, lab in seen if t.startswith("postój ")])
    """)
    r = subprocess.run([sys.executable, "-c", spy, str(DATA / "SLAD.gpx"), "--size", "640x360", "--frame-only", str(tmp_path / "k.png")],
                       cwd=ROOT, env=offline_env(tmp_path / "cache", XDG_CONFIG_HOME=str(tmp_path / "config")), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "badges: [True]" in r.stdout                               # SLAD has one stop


@pytest.mark.parametrize("value", ["0.69", "2.01", "nan"])
def test_the_size_must_be_in_range(value):
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / "SLAD.gpx"), "--label-size", value], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "--label-size musi być liczbą od 0.7 do 2.0" in r.stderr


def test_the_largest_badges_still_make_a_frame(tmp_path):
    args = ("--size", "640x360", "--label-style", "badges")
    for d in ("base", "big"):                                         # each with a fresh cache, so the map below is the same
        (tmp_path / d).mkdir()
    base, _ = offline_run(DATA / "bez_czasu.gpx", tmp_path / "base", *args)
    big, rep = offline_run(DATA / "bez_czasu.gpx", tmp_path / "big", *args, "--label-size", "2.0")
    assert rep["places"]                                             # bigger labels, still placed
    assert (np.abs(big.astype(int) - base).max(-1) > 0).sum() > 2000  # and the command really draws them bigger


def test_the_window_offers_the_same_choices():
    page = webgui.GUI_PAGE
    assert re.search(r'id="label_size" min="0\.7" max="2"', page)
    assert re.findall(r'<option value="(\w+)"', page[page.index('id="label_style"'):page.index("</select>", page.index('id="label_style"'))]) == \
        ["plain", "strong", "badges"]
    src = Path(webgui.__file__).read_text(encoding="utf-8")
    flags = dict(re.findall(r'\("(\w+)", "(--[\w-]+)"\)', src))               # field of the window: option of the command
    assert flags["label_style"] == "--label-style" and flags["label_size"] == "--label-size"
    fields = re.findall(r"'(\w+)'", re.search(r"const VALS=\[(.*?)\];", page, re.S)[1])
    assert set(flags) <= set(fields) and all(f'id="{k}"' in page for k in flags)    # each one read from the page
