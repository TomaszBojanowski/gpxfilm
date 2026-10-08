"""Run the program on a test track with the stored data, without network."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


PLAIN = {"PYTHON_COLORS": "0", "NO_COLOR": "1", "COLUMNS": "200"}   # --help without colors (Python 3.14) and without wrapping


def offline_env(cache: Path, **extra: str) -> dict:
    """Environment of a run with the stored test data and no network."""
    return {**os.environ, **PLAIN, "GPXFILM_CACHE_DIR": str(cache), "TZ": "UTC", "GPXFILM_TEXT_LAYOUT": "basic",
            "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": "", **extra}


def offline_frames(gpx: Path, tmp_path: Path, *args: str, **env: str) -> tuple[dict, str]:
    """The named moments of a film made with the stored test data, as frames by name, and the program's messages."""
    cache, keep = tmp_path / f"cache-{gpx.name}", tmp_path / "frames"
    if not cache.exists():
        shutil.copytree(ROOT / "tests" / "data" / "cache", cache)
    shutil.rmtree(keep, ignore_errors=True)
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(gpx), *args, "-o", str(tmp_path / "film.mp4")], cwd=ROOT,
                       env=offline_env(cache, GPXFILM_FRAMES=str(keep), XDG_CONFIG_HOME=str(tmp_path / "config"), **env),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "film.mp4").exists()
    return {f.stem: np.asarray(Image.open(f).convert("RGB")) for f in sorted(keep.glob("*.png"))}, r.stderr


def offline_run(gpx: Path, tmp_path: Path, *args: str) -> tuple[np.ndarray, dict]:
    """Last frame and report of a track with the stored test data (same coordinates as the reference tracks)."""
    cache = tmp_path / f"cache-{gpx.name}"
    if not cache.exists():
        shutil.copytree(ROOT / "tests" / "data" / "cache", cache)
    env = offline_env(cache)
    png, rep = tmp_path / f"{gpx.name}.png", tmp_path / f"{gpx.name}.json"
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(gpx), *args, "--frame-only", str(png), "--report", str(rep)], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Warning" not in r.stderr and "nan" not in r.stderr.lower()
    return np.asarray(Image.open(png).convert("RGB")), json.loads(rep.read_text(encoding="utf-8"))
