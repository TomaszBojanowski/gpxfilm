"""Reference frames and reports: the last frame of each test track must look like the stored one.

Set GPXFILM_UPDATE_GOLDEN=1 to rewrite the stored frames and reports instead of comparing.
Text is always laid out with Pillow's basic engine (GPXFILM_TEXT_LAYOUT=basic): whether libraqm is available
differs between machines and would shift every label by a pixel or two.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
GOLDEN = DATA / "golden"
UPDATE = os.environ.get("GPXFILM_UPDATE_GOLDEN") == "1"

# name: (track, options); each case renders one still frame and writes a report
CASES = {
    "slad": ("SLAD.gpx", ["--size", "1280x720"]),
    "slad_overlays": ("SLAD.gpx", ["--size", "1280x720", "--background-names", "--minimap", "--km-markers", "--timezone", "Europe/Rome",
                                   "--name-language", "de"]),
    "tatry": ("tatry.gpx", ["--size", "960x540", "--counters", "--color-by", "heart-rate", "--language", "en"]),
    "bez_czasu": ("bez_czasu.gpx", ["--size", "640x360", "--title", "Śnieżka\\nz Karpacza", "--color-by", "elevation"]),
    # the styles of the labels on the map; "slad" above has the default one, strong
    "labels_plain": ("SLAD.gpx", ["--size", "1280x720", "--label-style", "plain"]),
    "labels_badges": ("SLAD.gpx", ["--size", "1280x720", "--label-style", "badges"]),
}

# Tolerance for small rendering differences between platforms (FreeType, numpy builds).
# A label color changed by 15 levels touches about 0.3% of the pixels by more than 8 levels and must fail.
MAX_MEAN_DIFF = 0.25         # mean absolute difference per channel, 0..255
MAX_BAD_SHARE = 0.0005       # share of pixels allowed to differ by more than BAD_LEVEL
BAD_LEVEL = 8


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """Render every case once, offline, from a private copy of the stored cache."""
    out = {}
    for name, (gpx, args) in CASES.items():
        tmp = tmp_path_factory.mktemp(name)
        cache = tmp / "cache"
        shutil.copytree(DATA / "cache", cache)
        env = dict(os.environ, GPXFILM_CACHE_DIR=str(cache), XDG_CONFIG_HOME=str(tmp / "config"), TZ="UTC", GPXFILM_TEXT_LAYOUT="basic",
                   http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9",
                   HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", no_proxy="", NO_PROXY="")
        png, rep = tmp / "frame.png", tmp / "report.json"
        r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / gpx), *args, "--frame-only", str(png), "--report", str(rep)],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        out[name] = (r, png, rep)
    return out


def check_run(r):
    assert r.returncode == 0, r.stderr
    for sign in ("pobieram", "nie udało się", "Brak danych"):   # any of these means the stored data was not enough
        assert sign.lower() not in r.stderr.lower(), r.stderr


@pytest.mark.parametrize("name", CASES)
def test_frame(name, rendered, tmp_path):
    r, png, _ = rendered[name]
    check_run(r)
    ref = GOLDEN / f"{name}.png"
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        shutil.copy(png, ref)
        pytest.skip("reference frame updated")
    got, want = (np.asarray(Image.open(p).convert("RGB"), np.int16) for p in (png, ref))
    assert got.shape == want.shape
    diff = np.abs(got - want)
    mean, bad = float(diff.mean()), float((diff.max(-1) > BAD_LEVEL).mean())
    print(f"\n{name}: mean {mean:.4f}, pixels over {BAD_LEVEL}: {bad:.4%}, max {int(diff.max())}")   # shown with pytest -s
    if mean > MAX_MEAN_DIFF or bad > MAX_BAD_SHARE:
        keep = Path(os.environ.get("GPXFILM_DIFF_DIR", tmp_path))
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copy(png, keep / f"{name}-got.png")
        Image.fromarray(np.clip(diff * 4, 0, 255).astype(np.uint8)).save(keep / f"{name}-diff.png")
        pytest.fail(f"frame differs: mean {mean:.3f}, pixels over {BAD_LEVEL}: {bad:.4%}; see {keep}")


@pytest.mark.parametrize("name", CASES)
def test_report(name, rendered):
    r, _, rep = rendered[name]
    check_run(r)
    ref = GOLDEN / f"{name}.json"
    got = json.loads(rep.read_text(encoding="utf-8"))
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        ref.write_text(json.dumps(got, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        pytest.skip("reference report updated")
    assert got == json.loads(ref.read_text(encoding="utf-8"))


def test_heart_rate_dropout_is_gray(rendered):
    """tatry.gpx has a two-minute dropout (255) below the pass: on the route colored by heart rate it must be gray."""
    r, png, _ = rendered["tatry"]
    check_run(r)
    crop = np.asarray(Image.open(png).convert("RGB"), np.int16)[235:290, 630:680]
    assert (np.abs(crop - 170).max(-1) <= 6).sum() >= 15
