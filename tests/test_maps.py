"""Map credits: every imagery that reaches the frame is named on the film."""
from pathlib import Path

import numpy as np
import pytest

from gpxfilm import terrain
from gpxfilm.geo import Layout, prep_track, read_gpx
from gpxfilm.sources import MAPS, SENTINEL, TERRAIN

ROOT = Path(__file__).resolve().parent.parent
AERIAL = "Ortofoto: urząd testowy"


@pytest.mark.parametrize("mapa, gap, satellite, credit", [
    ("aerial", True, True, f"{AERIAL}; {SENTINEL}"),            # gaps in aerial photos filled with satellite imagery
    ("aerial", True, False, f"{AERIAL}; {TERRAIN}"),            # no satellite imagery either: the terrain drawing shows
    ("aerial", False, True, AERIAL),
    ("topo", True, True, f"{MAPS['topo'][2]}; {TERRAIN}"),    # gaps in the topographic map show the terrain drawing
    ("satellite", True, True, MAPS["satellite"][2]),           # already names the terrain model
])
def test_what_fills_the_gaps_is_credited(mapa, gap, satellite, credit, monkeypatch, tmp_path):
    tr = prep_track(read_gpx(ROOT / "tests" / "data" / "SLAD.gpx"))
    lay = Layout(tr, 320, 180)
    hole = np.zeros((lay.H, lay.W), np.float32)
    hole[:, :80] = 1                                  # a quarter of the frame lies outside the background
    own = AERIAL if mapa == "aerial" else MAPS[mapa][2]
    main = (np.full((lay.H, lay.W, 3), 0.5, np.float32), hole if gap else None, 0.25 if gap else 0.0, own, True)
    sat = (np.full((lay.H, lay.W, 3), 0.3, np.float32), None, 0.0, MAPS["satellite"][2], True) if satellite else None
    sources = (("https://example.invalid/{z}/{x}/{y}.jpg", 19, AERIAL),) if mapa == "aerial" else (MAPS[mapa],)
    monkeypatch.setattr(terrain, "raster_map", lambda proj, lay_, src: main if src == sources else sat)
    monkeypatch.setattr(terrain, "load_dem", lambda proj, lay_, quiet=False: np.full((lay.H, lay.W), 2000.0, np.float32))
    monkeypatch.setattr(terrain, "CACHE", tmp_path)
    monkeypatch.setitem(terrain.LOOK, "map", mapa)
    monkeypatch.setitem(terrain.LOOK, "sources", sources)
    monkeypatch.setitem(terrain.LOOK, "credit", "")
    terrain.relief(tr.proj, lay, {"elements": []}, contours=False, quiet=True, tiles=True)
    assert terrain.LOOK["credit"] == credit


def test_a_satellite_patch_with_its_own_gap_shows_the_terrain_there(monkeypatch, tmp_path):
    tr = prep_track(read_gpx(ROOT / "tests" / "data" / "SLAD.gpx"))
    lay = Layout(tr, 320, 180)
    hole = np.zeros((lay.H, lay.W), np.float32)
    hole[:, :80] = 1                                  # the aerial photos miss the left quarter
    sat_hole = np.zeros_like(hole)
    sat_hole[:60, :40] = 1                            # and the satellite imagery misses a tile there too
    sources = (("https://example.invalid/{z}/{x}/{y}.jpg", 19, AERIAL),)
    main = (np.full((lay.H, lay.W, 3), 0.5, np.float32), hole, 0.25, AERIAL, True)
    sat = (np.where(sat_hole[..., None] > 0, 0.0, 0.3).astype(np.float32), sat_hole, 0.04, MAPS["satellite"][2], False)
    monkeypatch.setattr(terrain, "raster_map", lambda proj, lay_, src: main if src == sources else sat)
    monkeypatch.setattr(terrain, "load_dem", lambda proj, lay_, quiet=False: np.full((lay.H, lay.W), 2000.0, np.float32))
    monkeypatch.setattr(terrain, "CACHE", tmp_path)
    monkeypatch.setitem(terrain.LOOK, "map", "aerial")
    monkeypatch.setitem(terrain.LOOK, "sources", sources)
    monkeypatch.setitem(terrain.LOOK, "credit", "")
    img, _ = terrain.relief(tr.proj, lay, {"elements": []}, contours=False, quiet=True, tiles=True)
    assert img[:50, :30].max(-1).min() > 0.1                   # no black square where both are missing
    assert terrain.LOOK["credit"] == f"{AERIAL}; {SENTINEL}; {TERRAIN}"
    assert not (tmp_path / "backgrounds").exists() or not list((tmp_path / "backgrounds").glob("*.npz"))   # incomplete: not stored


def test_the_italian_photos_of_2012_name_who_took_them_and_who_serves_them(monkeypatch):
    """The description of the service names AGEA as the agency that took the photos and the Geoportale Nazionale of the
    ministry of the environment (MASE) as the one that serves them, and asks for no other text. The credit is the same on
    an English and a Polish film, with OpenStreetMap added for the names."""
    from gpxfilm import compose
    from gpxfilm.sources import AERIAL as SOURCES
    credit = next(src[2] for src in SOURCES["IT"] if "ortofoto_colore_12" in src[0])
    assert credit == "Ortofoto 2012: AGEA, Geoportale Nazionale (MASE)"
    monkeypatch.setitem(compose.LOOK, "credit", credit)
    assert compose.frame_credit("en", "", names=True) == credit + "; Names: © OpenStreetMap"
    assert compose.frame_credit("pl", "", names=True) == credit + "; Nazwy: © OpenStreetMap"
