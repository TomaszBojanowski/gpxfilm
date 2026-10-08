"""The cache on disk: directories kept under their old Polish names are moved once to the new names."""
from gpxfilm.net import cache_dir


def test_an_old_directory_is_moved_once(tmp_path):
    (tmp_path / "mapa" / "a1").mkdir(parents=True)
    (tmp_path / "mapa" / "a1" / "tile.png").write_bytes(b"x")
    got = cache_dir(tmp_path, "tiles", "mapa")
    assert got == tmp_path / "tiles" and (got / "a1" / "tile.png").read_bytes() == b"x" and not (tmp_path / "mapa").exists()
    (tmp_path / "mapa").mkdir()                        # a new old one is left alone once the new name exists
    assert cache_dir(tmp_path, "tiles", "mapa") == got and (tmp_path / "mapa").exists()


def test_nothing_to_move(tmp_path):
    assert cache_dir(tmp_path, "backgrounds", "tla") == tmp_path / "backgrounds" and not list(tmp_path.iterdir())
