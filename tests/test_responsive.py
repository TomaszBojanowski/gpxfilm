"""The engine never holds a window up: a job stops within a second, a quick series of changes ends in one preview, and
the worker process can be ended at any moment without leaving a file cut short (PLAN.md, the rules for the engine)."""
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from test_tiles import Server, _png
from test_worker import SMALL, Window

from gpxfilm.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
REACTION = 1.0                           # s from asking a job to stop to hearing that it has
LOCAL = {"no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost"}   # the test's own tile server is reached directly
LONG = {**SMALL, "fps": 30, "route_duration": 40.0}                              # 1200 frames and more


@pytest.fixture
def windows(tmp_path):
    """Workers on one cache and one stand-in for ffmpeg, started when asked for and ended at the end."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ffmpeg").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_ffmpeg.py"}" "$@"\n', encoding="utf-8")
    (tmp_path / "bin" / "ffmpeg").chmod(0o755)
    made = []

    def start() -> Window:
        made.append(Window(tmp_path, PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", FAKE_FFMPEG_LOG=str(tmp_path / "ffmpeg"), **LOCAL))
        return made[-1]

    yield start
    for w in made:
        w.worker.kill()


@pytest.fixture
def tile_server():
    srv = Server()
    yield srv
    srv.stop()


def wait_for(what, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not what():
        assert time.monotonic() < deadline, "waited too long"
        time.sleep(0.01)


def until_frames(w: Window, id: int, n: int) -> None:
    """Wait until job id has drawn n frames of its film."""
    while True:
        e = w.events.get(timeout=120)
        assert e["event"] not in ("gone", "failed", "done"), e
        if e.get("id") == id and e["event"] == "progress" and e["stage"] == "frames" and e["done"] >= n:
            return


def test_an_export_stops_within_a_second_and_cleans_up(windows, tmp_path):
    w, out = windows(), tmp_path / "film.mp4"
    out.write_bytes(b"the old film")
    id = w.worker.request("export", settings=Settings(**LONG).to_dict(), output=str(out))
    until_frames(w, id, 5)
    began = time.monotonic()
    w.worker.request("cancel")
    assert w.until_end(id)[-1] == {"id": id, "event": "cancelled"}
    took = time.monotonic() - began
    print(f"an export stopped in {took:.3f} s")
    assert took < REACTION
    assert out.read_bytes() == b"the old film" and not (tmp_path / "film.part.mp4").exists()
    id = w.worker.request("preview", settings=Settings(**SMALL).to_dict(), width=640, height=360, file=str(tmp_path / "preview.png"))
    assert w.until_end(id)[-1]["event"] == "done"               # the same worker takes the next job


def test_a_pause_a_server_asked_for_does_not_hold_a_stop_back(windows, tile_server, tmp_path):
    """The tile server answers "come back in 30 seconds". The engine waits as it was told, but not once it is stopped."""
    tile_server.plan = lambda path, xy, k: (503, b"", {"Retry-After": "30"})
    w = windows()
    s = Settings(**{**SMALL, "map": tile_server.url + "/busy/{z}/{x}/{y}.png"})
    id = w.worker.request("preview", settings=s.to_dict(), width=640, height=360, file=str(tmp_path / "preview.png"))
    wait_for(lambda: tile_server.asked)
    time.sleep(0.3)                                             # now it is waiting
    began = time.monotonic()
    w.worker.request("cancel", id=id)
    assert w.until_end(id)[-1]["event"] == "cancelled"
    took = time.monotonic() - began
    print(f"a preview waiting for a server stopped in {took:.3f} s")
    assert took < REACTION and sum(tile_server.asked.values()) == 1


def test_a_quick_series_of_changes_ends_in_one_preview_the_last(windows, tmp_path):
    w, file = windows(), tmp_path / "preview.png"
    colors = ["#E5202E", "#FFB21E", "#1E90FF", "#22AA55", "#AA22CC", "#FFFFFF"]
    ids = [w.worker.request("preview", settings=Settings(**{**SMALL, "color": c}).to_dict(), width=640, height=360, file=str(file)) for c in colors]
    ends = [w.until_end(id) for id in ids]
    assert [e[-1]["event"] for e in ends] == ["cancelled"] * 5 + ["done"]
    assert [[f["rough"] for f in e if f["event"] == "frame"][-1:] for e in ends][:5] in ([[]] * 5, [[True]] + [[]] * 4)   # no stale frame
    assert [f["rough"] for f in ends[-1] if f["event"] == "frame"] == [True, False]
    last = np.asarray(Image.open(file).convert("RGB"))
    id = w.worker.request("preview", settings=Settings(**{**SMALL, "color": colors[-1]}).to_dict(), width=640, height=360, file=str(file))
    assert w.until_end(id)[-1]["event"] == "done" and (np.asarray(Image.open(file).convert("RGB")) == last).all()
    red, white = (last == (0xE5, 0x20, 0x2E)).all(-1).sum(), (last == (255, 255, 255)).all(-1).sum()
    assert red == 0 and white > 500                             # the route in the color asked for last


def test_a_worker_ended_while_downloading_leaves_only_whole_files(windows, tile_server, tmp_path):
    """The server gives three tiles and then says nothing; the worker is ended there. What is in the cache is whole, and
    the next worker goes on from it."""
    given, stall, tile = set(), [True], _png((60, 140, 60))

    def plan(path, xy, k):
        if stall[0] and xy not in given:
            if len(given) == 3:
                return None
            given.add(xy)
        return 200, tile, {}

    tile_server.plan = plan
    s, file = Settings(**{**SMALL, "map": tile_server.url + "/map/{z}/{x}/{y}.png"}).to_dict(), tmp_path / "preview.png"
    w = windows()
    w.worker.request("preview", settings=s, width=640, height=360, file=str(file))
    wait_for(lambda: any(e[0] == "req" and e[3] is None for e in tile_server.events))
    w.worker.kill()
    assert not w.worker.alive()
    kept = [p for p in (tmp_path / "cache" / "tiles").rglob("*") if p.is_file()]
    assert len(kept) == 3 and all(p.read_bytes() == tile for p in kept)
    assert not [p for p in (tmp_path / "cache").rglob("*") if ".part" in p.name]

    stall[0] = False
    w = windows()
    id = w.worker.request("preview", settings=s, width=640, height=360, file=str(file))
    got = w.until_end(id)
    assert got[-1]["event"] == "done" and Image.open(file).size == (640, 360)
    assert all(tile_server.asked[(path, xy)] == 1 for (path, xy) in tile_server.asked if xy in given)   # those came from the cache
    assert len([p for p in (tmp_path / "cache" / "tiles").rglob("*") if p.is_file()]) > 3


def test_a_worker_ended_while_exporting_leaves_the_old_film(windows, tmp_path):
    """ffmpeg, left alone, closes the film it has: under the unfinished name, never under the name of the film. The next
    export removes that file and makes the film."""
    out, part, log = tmp_path / "film.mp4", tmp_path / "film.part.mp4", tmp_path / "ffmpeg" / "frames.txt"
    out.write_bytes(b"the old film")
    w = windows()
    id = w.worker.request("export", settings=Settings(**LONG).to_dict(), output=str(out))
    until_frames(w, id, 5)
    w.worker.kill()
    wait_for(lambda: log.exists() and "left over" in log.read_text(encoding="utf-8"))   # ffmpeg has ended too
    wait_for(part.exists, 10)
    assert out.read_bytes() == b"the old film" and len(log.read_text(encoding="utf-8").splitlines()) < 1200

    w = windows()
    id = w.worker.request("export", settings=Settings(**SMALL).to_dict(), output=str(out))
    assert w.until_end(id)[-1]["event"] == "done"
    assert out.read_bytes() == b"fake film\n" and not part.exists()
