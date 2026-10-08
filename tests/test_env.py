import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def run(*args, env=None):
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, check=True)


def cache_dir(env):
    return run("-c", "import gpxfilm.net; print(gpxfilm.net.CACHE)", env=env).stdout.strip()


def test_cache_dir_from_env(tmp_path):
    env = dict(os.environ, GPXFILM_CACHE_DIR=str(tmp_path / "c"))
    assert cache_dir(env) == str(tmp_path / "c")


def test_cache_dir_default_when_unset(tmp_path):
    env = dict(os.environ, GPXFILM_CACHE_DIR="", HOME=str(tmp_path), XDG_CACHE_HOME=str(tmp_path / "xdg"))
    expected = tmp_path / "Library" / "Caches" / "gpxfilm" if sys.platform == "darwin" else tmp_path / "xdg" / "gpxfilm"
    assert cache_dir(env) == str(expected)


def test_user_config_not_read():
    assert "gpxfilm.toml" not in run("-m", "gpxfilm", "--help").stderr


@pytest.mark.skipif(sys.version_info < (3, 11), reason="config file needs tomllib")
def test_config_read_from_xdg_config_home():
    cfg = Path(os.environ["XDG_CONFIG_HOME"])
    cfg.mkdir(parents=True)
    (cfg / "gpxfilm.toml").write_text("no_such_option = 1\n", encoding="utf-8")
    assert "gpxfilm.toml" in run("-m", "gpxfilm", "--help").stderr
