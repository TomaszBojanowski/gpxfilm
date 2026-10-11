"""The cache on disk: directories kept under their old Polish names are moved once to the new names, and a file is
written there whole or not at all."""
import re
from pathlib import Path

import pytest

from gpxfilm.net import cache_dir, store, whole

ROOT = Path(__file__).resolve().parent.parent


def test_an_old_directory_is_moved_once(tmp_path):
    (tmp_path / "mapa" / "a1").mkdir(parents=True)
    (tmp_path / "mapa" / "a1" / "tile.png").write_bytes(b"x")
    got = cache_dir(tmp_path, "tiles", "mapa")
    assert got == tmp_path / "tiles" and (got / "a1" / "tile.png").read_bytes() == b"x" and not (tmp_path / "mapa").exists()
    (tmp_path / "mapa").mkdir()                        # a new old one is left alone once the new name exists
    assert cache_dir(tmp_path, "tiles", "mapa") == got and (tmp_path / "mapa").exists()


def test_nothing_to_move(tmp_path):
    assert cache_dir(tmp_path, "backgrounds", "tla") == tmp_path / "backgrounds" and not list(tmp_path.iterdir())


def test_a_stored_file_replaces_the_old_one_and_leaves_nothing_else(tmp_path):
    p = tmp_path / "tiles" / "a1" / "7"
    store(p, b"first")
    store(p, b"second")
    assert p.read_bytes() == b"second" and [f.name for f in p.parent.iterdir()] == ["7"]


def test_a_write_cut_short_leaves_no_file(tmp_path):
    p = tmp_path / "backgrounds" / "bg.npz"
    with pytest.raises(OSError), whole(p) as tmp:
        tmp.write_bytes(b"half")
        raise OSError("disk full")
    assert not list(p.parent.iterdir())
    store(p, b"old")                                   # a file already there stays as it was
    with pytest.raises(OSError), whole(p) as tmp:
        tmp.write_bytes(b"half")
        raise OSError("disk full")
    assert p.read_bytes() == b"old" and [f.name for f in p.parent.iterdir()] == ["bg.npz"]


def test_every_write_to_the_cache_is_whole_or_none():
    """The modules that fill the cache write there only through store and whole."""
    for name in ("compose", "osm", "terrain", "text", "tiles"):
        src = (ROOT / "gpxfilm" / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"write_bytes|write_text|np\.savez?\((?!f\b)", src), name
    assert "write_bytes" not in (ROOT / "gpxfilm" / "film.py").read_text(encoding="utf-8")
