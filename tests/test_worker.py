"""The engine as a process of its own: requests and events as lines of JSON (gpxfilm.worker)."""
import json
import os
import queue
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from offline import offline_env
from PIL import Image

from gpxfilm import worker
from gpxfilm.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
SMALL = dict(gpx=DATA / "SLAD.gpx", size="640x360", fps=5, route_duration=2.0, language="pl")


class Window:
    """A worker with the stored test data and no network, and its events as they came."""

    def __init__(self, tmp_path: Path, **env: str):
        self.dir, self.events = tmp_path, queue.Queue()
        if not (tmp_path / "cache").exists():
            shutil.copytree(DATA / "cache", tmp_path / "cache")
        self.env = dict(offline_env(tmp_path / "cache", XDG_CONFIG_HOME=str(tmp_path / "config"), **env),
                        PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])))
        self.env.pop("GPXFILM_FRAMES", None)
        self.worker = worker.Worker(self.events.put, env=self.env)

    def command_env(self, **extra: str) -> dict:
        """The environment for the command to do the same work from the start: with stored data that the worker has not
        added to (a background read back from the disk is rounded to 8 bits, so it gives frames one level off)."""
        shutil.copytree(DATA / "cache", self.dir / "cache-command")
        return {**self.env, "GPXFILM_CACHE_DIR": str(self.dir / "cache-command"), **extra}

    def until_end(self, id: int, timeout: float = 120) -> list[dict]:
        """The events of job id, up to the one that ends it."""
        got = []
        while not got or got[-1]["event"] not in ("done", "cancelled", "failed", "busy"):
            e = self.events.get(timeout=timeout)
            assert e["event"] != "gone", (e, got)
            if e.get("id") == id:
                got.append(e)
        return got


@pytest.fixture
def window(tmp_path):
    w = Window(tmp_path)
    yield w
    w.worker.kill()


def test_the_frame_fits_the_box_and_is_drawn_in_one_of_a_few_sizes():
    assert worker.preview_sizes(640, 360) == ((640, 360), (640, 360))
    assert worker.preview_sizes(500, 400) == ((640, 360), (500, 281))            # a narrow box: its whole width
    assert worker.preview_sizes(2000, 300) == ((640, 360), (533, 300))           # a low box: its whole height
    assert worker.preview_sizes(900, 900) == ((960, 540), (900, 506))
    assert worker.preview_sizes(5000, 5000) == ((1920, 1080), (1920, 1080))      # never larger than it was drawn


def test_the_worker_says_hello_with_the_number_of_its_protocol(tmp_path):
    p = subprocess.Popen([sys.executable, "-m", "gpxfilm.worker"], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                         env=offline_env(tmp_path / "cache"))
    try:
        hello = json.loads(p.stdout.readline())
        assert hello == {"event": "hello", "protocol": worker.PROTOCOL, "version": hello["version"]} and worker.PROTOCOL == 1
        p.stdin.write('nonsense\n{"do": "dance", "id": 7}\n')
        p.stdin.flush()
        assert json.loads(p.stdout.readline()) == {"event": "error", "text": "not a request: nonsense"}
        assert json.loads(p.stdout.readline()) == {"id": 7, "event": "error", "text": "unknown request: dance"}
        p.stdin.close()                                # the window is gone: so is the worker
        assert p.wait(timeout=20) == 0 and p.stdout.read() == ""
    finally:
        p.kill()


def test_a_preview_gives_a_rough_frame_then_the_frame_of_the_command(window, tmp_path):
    """The real frame is, to the pixel, the one the command saves with --frame-only; the rough one comes first, without any
    message or download; a smaller box gets the same frame scaled down."""
    file, s = tmp_path / "preview.png", Settings(**SMALL).to_dict()
    id = window.worker.request("preview", settings={**s, "size": "3840x2160"}, width=640, height=360, file=str(file), report=True)
    got = window.until_end(id)
    kinds = [e["event"] for e in got]
    frames = [e for e in got if e["event"] == "frame"]
    assert kinds[0] == "frame" and frames[0] == {"id": id, "event": "frame", "file": str(file), "width": 640, "height": 360, "rough": True}
    assert [f["rough"] for f in frames] == [True, False] and kinds[-2:] == ["report", "done"]
    assert "message" in kinds and "progress" in kinds and kinds.index("message") > 0
    assert all(set(e) == {"id", "event", "stage", "done", "total", "text"} for e in got if e["event"] == "progress")
    report = got[-2]["report"]
    assert report["stops"] == 1 and report["places"] and report["distance_km"] == 7.5
    shown = np.asarray(Image.open(file).convert("RGB"))
    assert not list(tmp_path.glob("preview.part*"))

    png = tmp_path / "command.png"
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / "SLAD.gpx"), "--size", "640x360", "--language", "pl", "--frame-only", str(png)],
                       cwd=ROOT, env=window.command_env(), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (shown == np.asarray(Image.open(png).convert("RGB"))).all()
    said = [e["text"] for e in got if e["event"] == "message"]
    assert said and all(line in r.stderr for line in said)      # the messages of the command, as events

    id = window.worker.request("preview", settings=s, width=320, height=400, file=str(file))
    got = window.until_end(id)
    assert [(e["width"], e["height"], e["rough"]) for e in got if e["event"] == "frame"] == [(320, 180, True), (320, 180, False)]
    assert Image.open(file).size == (320, 180) and got[-1] == {"id": id, "event": "done"}
    assert not [e for e in got if e["event"] == "message"]      # nothing is worked out again for another size of the window


def test_a_frame_for_a_moment_of_the_film(window, tmp_path):
    file, s = tmp_path / "moment.png", Settings(**SMALL).to_dict()
    id = window.worker.request("preview", settings=s, width=640, height=360, file=str(file), at=1.0)
    got = window.until_end(id)
    assert [e["rough"] for e in got if e["event"] == "frame"] == [False] and got[-1]["event"] == "done"
    middle = np.asarray(Image.open(file).convert("RGB"))
    id = window.worker.request("preview", settings=s, width=640, height=360, file=str(file))
    window.until_end(id)
    assert (middle != np.asarray(Image.open(file).convert("RGB"))).any()        # less of the route is drawn there


def test_a_job_that_fails_tells_why_and_the_worker_goes_on(window, tmp_path):
    file = tmp_path / "preview.png"
    id = window.worker.request("preview", settings=Settings(**{**SMALL, "gpx": DATA / "gpx" / "empty.gpx"}).to_dict(), width=640, height=360, file=str(file))
    got = window.until_end(id)
    assert got[-1]["event"] == "failed" and "jest pusty" in got[-1]["text"] and "trace" not in got[-1] and not file.exists()
    id = window.worker.request("preview", settings={**Settings(**SMALL).to_dict(), "intro_radius": 0}, width=640, height=360, file=str(file))
    assert "--intro-radius" in window.until_end(id)[-1]["text"]
    id = window.worker.request("preview", settings={"no_such_setting": 1}, width=640, height=360, file=str(file))
    assert "TypeError" in window.until_end(id)[-1]["text"]      # a mistake of the window, with the place it was noticed in
    id = window.worker.request("preview", settings=Settings(**SMALL).to_dict(), width=640, height=360, file=str(file))
    assert window.until_end(id)[-1]["event"] == "done" and file.exists()


def test_an_export_is_the_film_of_the_command(tmp_path):
    """The worker gives ffmpeg the frames and the arguments that the command gives it, and says where the film is."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ffmpeg").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_ffmpeg.py"}" "$@"\n', encoding="utf-8")
    (fake / "ffmpeg").chmod(0o755)
    path = f"{fake}{os.pathsep}{os.environ['PATH']}"
    w = Window(tmp_path, PATH=path, FAKE_FFMPEG_LOG=str(tmp_path / "from-worker"))
    try:
        s = Settings(**SMALL, intro=True, outro=True, counters=True)
        id = w.worker.request("export", settings=s.to_dict(), output=str(tmp_path / "film.mp4"), report=True)
        busy = w.worker.request("preview", settings=s.to_dict(), width=640, height=360, file=str(tmp_path / "preview.png"))
        assert w.until_end(busy) == [{"id": busy, "event": "busy"}]             # an export is not pushed aside
        got = w.until_end(id, timeout=300)
        assert got[-1] == {"id": id, "event": "done", "output": str(tmp_path / "film.mp4")}
        assert {e["stage"] for e in got if e["event"] == "progress"} >= {"frames", "encoding"}
        assert [e["event"] for e in got].count("report") == 1
    finally:
        w.worker.close()
    assert not w.worker.alive()
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / "SLAD.gpx"), "--size", "640x360", "--fps", "5", "--route-duration", "2", "--language", "pl",
                        "--intro", "--outro", "--counters", "-o", str(tmp_path / "film.mp4")], cwd=ROOT, capture_output=True, text=True,
                       env=w.command_env(FAKE_FFMPEG_LOG=str(tmp_path / "from-command")))
    assert r.returncode == 0, r.stderr
    for name in ("frames.txt", "command.json"):
        assert (tmp_path / "from-worker" / name).read_text(encoding="utf-8") == (tmp_path / "from-command" / name).read_text(encoding="utf-8"), name
    assert len((tmp_path / "from-worker" / "frames.txt").read_text(encoding="utf-8").splitlines()) > 50
