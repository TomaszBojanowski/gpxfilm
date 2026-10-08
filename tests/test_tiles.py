"""Map tiles from a fake server: passing failures are asked for again, a source missing too many tiles is dropped."""
import http.server
import io
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from PIL import Image

from gpxfilm import terrain, tiles
from gpxfilm.geo import Layout, prep_track, read_gpx
from gpxfilm.sources import MAPS

ROOT = Path(__file__).resolve().parent.parent
AERIAL = "Ortofoto: urząd testowy"


def _png(color, size=256):
    out = io.BytesIO()
    Image.new("RGB", (size, size), color).save(out, "PNG")
    return out.getvalue()


def _half_empty():
    """A tile whose west half is white: a place without photos inside the tile."""
    im = Image.new("RGB", (256, 256), (60, 140, 60))
    im.paste((255, 255, 255), (0, 0, 128, 256))
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class Server:
    """A tile server on 127.0.0.1. plan(path, tile, try number of that tile) gives (status, body, headers), or None to stay
    silent until the server stops. asked counts the requests per (path, tile)."""

    def __init__(self):
        self.asked, self.plan, self.release = Counter(), lambda path, xy, k: (200, _png((60, 140, 60)), {}), threading.Event()
        self.events = []                              # requests ("req", path, tile, status) and pauses ("sleep", s), in order
        me = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                q = parse_qs(url.query)
                if "BBOX" in q:                       # WMS: the box stands for the tile
                    xy = tuple(round(float(v)) for v in q["BBOX"][0].split(",")[:2])
                else:
                    xy = tuple(int(v) for v in url.path.rsplit(".", 1)[0].split("/")[-2:])
                me.asked[(url.path, xy)] += 1
                got = me.plan(url.path, xy, me.asked[(url.path, xy)])
                me.events.append(("req", url.path, xy, got[0] if got else None))
                if got is None:                       # no answer, and the connection stays open
                    me.release.wait()
                    return
                status, body, headers = got
                if status == 200 and "WIDTH" in q and body[:4] == b"\x89PNG":
                    body = _png((60, 140, 60), int(q["WIDTH"][0]))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(monkeypatch, tmp_path):
    srv = Server()
    for k in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(k, "127.0.0.1,localhost")
    srv.slept = []

    def sleep(v):                                     # pauses are noted, not waited
        srv.slept.append(v)
        srv.events.append(("sleep", v))

    monkeypatch.setattr(tiles, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(tiles, "TILE_TIMEOUT", 0.5)
    monkeypatch.setattr(tiles, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(terrain, "CACHE", tmp_path / "cache")
    yield srv
    srv.stop()


@pytest.fixture(scope="module")
def frame():
    tr = prep_track(read_gpx(ROOT / "tests" / "data" / "SLAD.gpx"))
    return tr.proj, Layout(tr, 1280, 720)                      # 35 tiles of 256 px, 12 of a WMS server


def _source(server, name="aerial"):
    return (f"{server.url}/{name}/{{z}}/{{x}}/{{y}}.png", 19, AERIAL)


def test_a_passing_failure_is_asked_again(server, frame):
    server.plan = lambda path, xy, k: (500, b"busy", {}) if k == 1 else (200, _png((60, 140, 60)), {})
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (_source(server),))
    assert hole is None and share == 0 and credit == AERIAL and complete
    assert set(server.asked.values()) == {2}                    # every tile failed once and came the second time
    assert tiles.TILE_PAUSE in server.slept                     # with a pause before asking again


def _beside_middle(plan_for, n=2):
    """A plan that picks n tiles next to the middle of the frame (the first tile asked for) and lets plan_for answer."""
    mid = {}
    def plan(path, xy, k):
        x0, y0 = mid.setdefault(path.split("/")[1], xy)     # one middle per source
        return plan_for(path, xy, k, (xy[0] - x0) in ((-1, 1) if n == 2 else (1,)) and xy[1] == y0)
    return plan


def _waits_before_next_request(events, wait):
    """Every refusal is followed by a pause of at least the given wait before the next request goes out."""
    for i, ev in enumerate(events):
        if ev[0] == "req" and ev[3] == 429:
            later = events[i + 1:]
            nxt = next((j for j, e in enumerate(later) if e[0] == "req"), len(later))
            assert any(e[0] == "sleep" and e[1] >= wait for e in later[:nxt]), f"request {i} went out too early"


def test_the_server_says_when_to_ask_again(server, frame):
    server.plan = lambda path, xy, k: (429, b"", {"Retry-After": "7"}) if k == 1 else (200, _png((60, 140, 60)), {})
    assert tiles.raster_map(*frame, (_source(server),))[4]
    _waits_before_next_request(server.events, 7.0)


def test_the_server_is_heard_at_the_last_chance_and_by_the_next_source(server, frame):
    server.plan = _beside_middle(lambda path, xy, k, lost: (429, b"", {"Retry-After": "7"}) if lost or path.startswith("/busy/")
                                 else (200, _png((60, 140, 60)), {}))
    assert not tiles.raster_map(*frame, (_source(server),))[4]   # two tiles refused at every try
    assert tiles.LAST_CHANCE[1] in server.slept
    _waits_before_next_request(server.events, 7.0)
    server.events.clear()
    busy = (f"{server.url}/busy/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto 2023")
    fine = (f"{server.url}/fine/{{z}}/{{x}}/{{y}}.png", 19, AERIAL)
    server.plan = lambda path, xy, k: (429, b"", {"Retry-After": "7"}) if path.startswith("/busy/") else (200, _png((60, 140, 60)), {})
    tiles.raster_map(*frame, (busy, fine))                    # the middle tile of the first is refused for good
    _waits_before_next_request(server.events, 7.0)


def test_no_data_is_not_a_failure(server, frame):
    first = {}
    server.plan = lambda path, xy, k: (404, b"", {}) if xy[0] < first.setdefault("x", xy[0]) else (200, _png((60, 140, 60)), {})
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (_source(server),))
    assert hole is not None and 0 < share < 1 and complete      # the edge of the coverage, as before
    assert set(server.asked.values()) == {1}                    # nothing asked twice


@pytest.mark.parametrize("body, kind", [(b"<html><body>Service unavailable</body></html>", tiles.Failed),
                                        (_png((60, 140, 60))[:300], tiles.Failed),                       # cut short
                                        (b'<?xml version="1.0"?><ServiceExceptionReport><ServiceException>This request used more '
                                         b'time than allowed</ServiceException></ServiceExceptionReport>', tiles.Failed),
                                        (b'<ExceptionReport><Exception exceptionCode="TileOutOfRange"/></ExceptionReport>', type(None)),
                                        (b"", type(None))])
def test_what_an_answer_that_is_not_an_image_means(body, kind, server, tmp_path):
    server.plan = lambda path, xy, k: (200, body, {})
    got = tiles.load_tile(f"{server.url}/aerial/12/1/2.png", tmp_path / "t.png", 256)
    assert isinstance(got, kind) and not (tmp_path / "t.png").exists()


def test_a_few_missing_tiles_leave_holes(server, frame):
    lost = []
    def plan(path, xy, k):
        if len(server.asked) == 2 and not lost:              # one tile the server never gives
            lost.append(xy)
        return (503, b"", {}) if xy in lost else (200, _png((60, 140, 60)), {})
    server.plan = plan
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (_source(server),))
    assert len(server.asked) * tiles.MAX_MISSING > 1         # one tile is less than the limit
    lost_asked = [v for (p_, xy), v in server.asked.items() if xy == lost[0]]
    assert not complete and hole is not None and lost_asked == [tiles.TILE_TRIES + 1]   # with the last chance
    assert tiles.LAST_CHANCE[1] in server.slept


def test_a_tile_that_comes_at_the_last_chance_completes_the_frame(server, frame):
    lost = []
    def plan(path, xy, k):
        if len(server.asked) == 2 and not lost:
            lost.append(xy)
        return (503, b"", {}) if xy in lost and k <= tiles.TILE_TRIES else (200, _png((60, 140, 60)), {})
    server.plan = plan
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (_source(server),))
    assert complete and hole is None and tiles.LAST_CHANCE[1] in server.slept


def test_many_missing_tiles_drop_the_source(server, frame):
    server.plan = lambda path, xy, k: (503, b"", {}) if xy[0] % 3 == 0 and len(server.asked) > 1 else (200, _png((60, 140, 60)), {})
    with pytest.raises(tiles.SourceDown) as e:
        tiles.raster_map(*frame, (_source(server),))
    assert e.value.credit == AERIAL and e.value.missing > tiles.MAX_MISSING * e.value.total
    assert f"nie oddał {e.value.missing} z {e.value.total} kafli" in str(e.value)


def test_a_silent_server_is_given_up_quickly(server, frame):
    server.plan = lambda path, xy, k: None
    began = time.monotonic()
    with pytest.raises(tiles.SourceDown) as e:
        tiles.raster_map(*frame, (_source(server),))
    assert time.monotonic() - began < tiles.TILE_TRIES * tiles.TILE_TIMEOUT + 1     # each try waits its time limit, no longer
    assert sum(server.asked.values()) == tiles.TILE_TRIES and "nie odpowiada albo zwraca błędy" in str(e.value)


def test_silence_is_counted_over_the_rounds(server, frame):
    lost = []
    def plan(path, xy, k):
        if len(server.asked) in (2, 3) and len(lost) < 2:
            lost.append(xy)                                    # two tiles that never get an answer
        return None if xy in lost else (200, _png((60, 140, 60)), {})
    server.plan = plan
    with pytest.raises(tiles.SourceDown) as e:                 # five tries in a row without an answer: the server is silent
        tiles.raster_map(*frame, (_source(server),))
    assert "nie odpowiada" in str(e.value)


def test_a_server_that_stops_answering_is_given_up(server, frame):
    server.plan = lambda path, xy, k: (200, _png((60, 140, 60)), {}) if len(server.asked) <= 3 else None
    began = time.monotonic()
    with pytest.raises(tiles.SourceDown):
        tiles.raster_map(*frame, (_source(server),))
    assert len(server.asked) <= 3 + tiles.SILENT and time.monotonic() - began < (tiles.SILENT + 1) * tiles.TILE_TIMEOUT + 1


WMS = "/wms?LAYERS=x&CRS={proj}&BBOX={bbox}&WIDTH={width}&HEIGHT={height}"


def test_wms_servers_are_asked_more_slowly(server, frame):
    tiles.raster_map(*frame, ((server.url + WMS, 19, AERIAL),))
    assert set(server.slept) == {tiles.GAP[True]} and tiles.GAP[True] > tiles.GAP[False]
    server.slept.clear()
    tiles.raster_map(*frame, (_source(server),))
    assert set(server.slept) == {tiles.GAP[False]}


def test_wms_servers_get_longer_pauses_before_asking_again(server, frame):
    lost = []
    def plan(path, xy, k):
        if len(server.asked) == 2 and not lost:              # one tile lost for good
            lost.append(xy)
        return (503, b"", {}) if xy in lost else (200, _png((60, 140, 60)), {})
    server.plan = plan
    with pytest.raises(tiles.SourceDown) as e:                 # 1 of 12 WMS tiles is more than 8%
        tiles.raster_map(*frame, ((server.url + WMS, 19, AERIAL),))
    assert (e.value.missing, e.value.total) == (1, 12)
    assert [v for v in server.slept if v != tiles.GAP[True]] == [2 * tiles.TILE_PAUSE, 4 * tiles.TILE_PAUSE, tiles.LAST_CHANCE[1]]


def test_a_wms_error_report_under_load_is_asked_again(server, frame):
    busy = b'<ServiceExceptionReport><ServiceException>Max rendering time exceeded</ServiceException></ServiceExceptionReport>'
    server.plan = lambda path, xy, k: (200, busy, {}) if k == 1 and len(server.asked) > 1 else (200, _png((60, 140, 60)), {})
    ras, hole, share, credit, complete = tiles.raster_map(*frame, ((server.url + WMS, 19, AERIAL),))
    assert hole is None and complete


def test_a_preferred_source_short_of_tiles_is_named_and_not_stored(server, frame, capsys):
    first = (f"{server.url}/first/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto 2023")
    server.plan = _beside_middle(lambda path, xy, k, lost: (503, b"", {}) if lost and path.startswith("/first/")
                                 else (200, _png((60, 140, 60)), {}))
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (first, _source(server)))
    assert credit == AERIAL and not complete
    assert "nie oddał 2 z 35 kafli mimo ponawiania (Ortofoto 2023) – biorę następne źródło" in capsys.readouterr().err


def test_a_source_that_fails_to_fill_a_gap_is_named_and_not_stored(server, frame, capsys):
    edge = (f"{server.url}/edge/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto A")
    down = (f"{server.url}/down/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto B")
    server.plan = _beside_middle(lambda path, xy, k, gap: (200, _half_empty(), {}) if gap and path.startswith("/edge/")
                                 else (503, b"", {}) if path.startswith("/down/") else (200, _png((60, 140, 60)), {}), n=1)
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (edge, down, _source(server)))
    assert credit == f"Ortofoto A; {AERIAL}" and hole is None and not complete
    assert "(Ortofoto B) – luki wypełnia inne źródło" in capsys.readouterr().err


def test_no_next_source_is_promised_when_the_frame_falls_back(server, frame, capsys):
    edge = (f"{server.url}/edge/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto A")
    down = (f"{server.url}/down/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto B")
    middle = []
    def plan(path, xy, k):
        if path.startswith("/edge/"):
            middle.append(xy) if not middle else None
            return (200, _png((60, 140, 60)), {}) if xy[0] <= middle[0][0] else (404, b"", {})   # no data east of the middle
        return 503, b"", {}
    server.plan = plan
    with pytest.raises(tiles.SourceDown):                      # the rest of the frame may be on the silent server
        tiles.raster_map(*frame, (down, edge))
    assert "biorę następne źródło" not in capsys.readouterr().err


def test_a_preferred_source_that_fails_is_named_and_not_stored(server, frame, capsys):
    first = (f"{server.url}/first/{{z}}/{{x}}/{{y}}.png", 19, "Ortofoto 2023")
    server.plan = lambda path, xy, k: (503, b"", {}) if path.startswith("/first/") else (200, _png((60, 140, 60)), {})
    ras, hole, share, credit, complete = tiles.raster_map(*frame, (first, _source(server)))
    assert credit == AERIAL and not complete                  # the later source draws it, but next time the first may answer
    assert "(Ortofoto 2023) – biorę następne źródło (Ortofoto: urząd testowy)" in capsys.readouterr().err


def _relief(server, frame, monkeypatch, sources):
    proj, lay = frame
    monkeypatch.setattr(terrain, "load_dem", lambda proj_, lay_, quiet=False: np.full((lay.H, lay.W), 2000.0, np.float32))
    monkeypatch.setitem(terrain.LOOK, "map", "aerial")
    monkeypatch.setitem(terrain.LOOK, "sources", sources)
    monkeypatch.setitem(terrain.LOOK, "credit", "")
    monkeypatch.setitem(MAPS, "satellite", (f"{server.url}/sat/{{z}}/{{x}}/{{y}}.png", 14, MAPS["satellite"][2]))
    img, _ = terrain.relief(proj, lay, {"elements": []}, contours=False, quiet=True, tiles=True)
    return img, list((tiles.CACHE / "backgrounds").glob("*.npz")) if (tiles.CACHE / "backgrounds").exists() else []


def test_a_dropped_aerial_source_gives_satellite_imagery_for_the_whole_frame(server, frame, monkeypatch, capsys):
    server.plan = lambda path, xy, k: ((503, b"", {}) if path.startswith("/aerial/") and xy[0] % 3 == 0 and len(server.asked) > 1
                                       else (200, _png((200, 40, 40) if path.startswith("/sat/") else (60, 140, 60)), {}))
    img, stored = _relief(server, frame, monkeypatch, (_source(server),))
    assert terrain.LOOK["map"] == "satellite" and terrain.LOOK["credit"] == MAPS["satellite"][2]
    assert "biorę dla całego kadru zdjęcia satelitarne Sentinel-2" in capsys.readouterr().err
    assert not stored                                           # next time the aerial photos may come
    r, g = img[..., 0].mean(), img[..., 1].mean()
    assert r > g                                                # the satellite color, everywhere


def test_only_a_complete_background_is_stored(server, frame, monkeypatch):
    _, stored = _relief(server, frame, monkeypatch, (_source(server),))
    assert len(stored) == 1 and terrain.LOOK["credit"] == AERIAL

