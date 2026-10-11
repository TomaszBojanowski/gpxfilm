"""The interface of the engine: the settings as one structure that can be sent between processes."""
import dataclasses
import json
import os
import pickle
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from offline import offline_env

from gpxfilm.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
NOT_SETTINGS = {"help", "gui", "check-maps", "port", "output", "draft", "slides", "report", "frame", "frame-only"}   # what to do and where to write


def test_every_option_of_the_command_is_a_setting(tmp_path):
    r = subprocess.run([sys.executable, "-m", "gpxfilm", "--help"], cwd=ROOT, capture_output=True, text=True, env=offline_env(tmp_path))
    options = set(re.findall(r"^  (?:-\w, )?--([a-z][a-z-]*)", r.stdout, re.M)) - NOT_SETTINGS
    assert sorted(o.replace("-", "_") for o in options) == sorted(f.name for f in dataclasses.fields(Settings) if f.name != "gpx")


def test_settings_survive_the_way_to_another_process():
    s = Settings(gpx=Path("a/route.gpx"), title="Tre Cime\\ndi Lavaredo", rename=("Rifugio=Hut", "A=B"), skip=("Malga",), photos=Path("photos"),
                 intro=True, intro_label_duration=2.5, distance=12.3, language="pl")
    assert pickle.loads(pickle.dumps(s)) == s
    assert Settings.from_dict(json.loads(json.dumps(s.to_dict()))) == s
    assert Settings.from_dict({"language": "en"}) == Settings(language="en")      # what is left out keeps its default


def test_settings_out_of_range_name_the_option():
    assert Settings(language="en").problem() is None
    assert "--label-size" in Settings(label_size=3.0).problem() and "--distance" in Settings(distance=0).problem()
    assert "--intro-radius" in Settings(intro_radius=float("nan")).problem()


def test_the_settings_load_no_heavy_library():
    code = "import sys, gpxfilm.settings; print(sorted(m for m in ('numpy', 'scipy', 'PIL') if m in sys.modules))"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.stdout.strip() == "[]", r.stdout + r.stderr


def test_a_task_gets_the_events_and_can_be_stopped():
    from gpxfilm import task
    seen = []
    with task.running(task.Task(seen.append)) as t:
        task.log("drawing")
        task.progress("frames", 5, 20)
        task.check()                                            # nothing asked it to stop yet
        t.cancel()
        with pytest.raises(task.Cancelled):
            task.check()
    assert seen == [task.Message("drawing"), task.Progress("frames", 5, 20)] and seen[1].fraction == 0.25
    assert task.Progress("osm").fraction is None                # the length is not known
    task.check()                                                # outside the task nothing is cancelled
    assert not issubclass(task.Cancelled, Exception)            # "carry on after any error" does not swallow a stop


def test_a_pause_ends_at_once_when_the_task_is_stopped(monkeypatch):
    """The pauses between downloads are long (up to 30 s when a server asks for it): a stop does not wait for them."""
    import threading
    import time
    import urllib.request

    from gpxfilm import net, task
    began = time.monotonic()
    task.rest(0.05)                                             # nothing stops it: the whole pause
    assert time.monotonic() - began >= 0.05
    with task.running(task.Task(lambda event: None)) as t:
        threading.Timer(0.1, t.cancel).start()
        began = time.monotonic()
        with pytest.raises(task.Cancelled):
            task.rest(30)
        assert time.monotonic() - began < 0.5
        with pytest.raises(task.Cancelled):                     # already stopped: no pause at all
            task.rest(30)

    def urlopen(req, timeout=None, context=None):
        raise OSError("no answer")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with task.running(task.Task(lambda event: None)) as t:      # the pause before the next try of a download
        threading.Timer(0.1, t.cancel).start()
        began = time.monotonic()
        with pytest.raises(task.Cancelled):
            net.http_get("https://example.org/tile.png")
        assert time.monotonic() - began < 0.5


def test_the_command_shows_the_progress_of_the_frames_as_percent(capsys):
    from gpxfilm import task
    show = task.Console()
    for done in range(50):
        show(task.Progress("frames", done, 50))
    show(task.Progress("map", 1, 4))
    show(task.Message("Done"))
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 26 and lines[0].startswith("    0%  (") and lines[-2].startswith("   96%  (") and lines[-1] == "Done"


DATA = ROOT / "tests" / "data"


@pytest.fixture
def stored(tmp_path, monkeypatch):
    """The stored test data as the cache, no network, and the text layout of the reference frames."""
    import shutil

    from gpxfilm import compose, film, net, osm, terrain, text, tiles
    shutil.copytree(DATA / "cache", tmp_path / "cache")
    for module in (net, osm, terrain, tiles, text, compose, film):
        monkeypatch.setattr(module, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(text, "LAYOUT", text.ImageFont.Layout.BASIC)
    monkeypatch.setattr(net, "http_get", lambda *a, **kw: (_ for _ in ()).throw(OSError("no network in tests")))
    monkeypatch.setenv("TZ", "UTC")
    return tmp_path


def test_a_project_works_out_again_only_what_a_setting_changes(stored, monkeypatch):
    from gpxfilm import engine
    calls = []
    for name in ("read_gpx", "fetch_osm", "relief", "compose_scene"):
        monkeypatch.setattr(engine, name, lambda *a, _f=getattr(engine, name), _n=name, **kw: (calls.append(_n), _f(*a, **kw))[1])
    project, s = engine.Project(), Settings(gpx=DATA / "SLAD.gpx", size="640x360", language="pl")
    first = engine.render_still(engine.prepare(project, s, film=False))
    assert calls == ["read_gpx", "fetch_osm", "relief", "compose_scene"]
    del calls[:]
    engine.prepare(project, dataclasses.replace(s, fps=30, route_duration=5.0, crf=20), film=False)
    assert calls == []                                          # the length of the film changes no picture
    p = engine.prepare(project, dataclasses.replace(s, km_markers=True), film=False)
    assert calls == ["compose_scene"]                           # new labels on the same background
    assert (engine.render_still(p) != first).any()
    del calls[:]
    engine.prepare(project, dataclasses.replace(s, map_style="night"), film=False)
    assert calls == ["relief", "compose_scene"]                 # a new background, the same track and map data
    del calls[:]
    again = engine.render_still(engine.prepare(project, s, film=False))
    worst = int(np.abs(again.astype(np.int16) - first).max())
    print("back to the first settings, largest difference:", worst)
    assert calls == ["relief", "compose_scene"] and worst <= 2  # the same frame; its background now comes from the disk, rounded to 8 bits


def test_the_report_describes_the_film(stored):
    from gpxfilm import engine
    p = engine.prepare(engine.Project(), Settings(gpx=DATA / "SLAD.gpx", size="640x360", language="pl", outro=True), film=False)
    got = engine.report(p)
    assert got["stops"] == 1 and got["places"] and got["duration"] == pytest.approx(1.0 + 16.0 + 3.0 + 1.4 + 0.8 + 5.0)
    assert json.loads(json.dumps(got)) == got                   # plain values: it can be sent to another process


def test_a_file_that_is_not_a_track_is_a_failure_not_an_exit(stored):
    from gpxfilm import engine, task
    with pytest.raises(task.Failure, match="jest pusty"):
        engine.prepare(engine.Project(), Settings(gpx=DATA / "gpx" / "empty.gpx", language="pl"))
    with pytest.raises(task.Failure, match="16:9"):
        engine.prepare(engine.Project(), Settings(gpx=DATA / "SLAD.gpx", size="800x800", language="pl"))


def test_any_frame_equals_the_frame_drawn_in_order(stored):
    """The timeline gives a frame for any moment: asked for out of order, in the intro, at a stop, at a photo and in the
    outro, each frame is the one the film has there when it is drawn from the start."""
    import hashlib

    from compare_films import make_photos

    from gpxfilm import engine
    make_photos(stored / "photos")
    s = Settings(gpx=DATA / "SLAD.gpx", size="640x360", fps=5, route_duration=2.0, intro=True, outro=True, outro_duration=2.0,
                 photos=stored / "photos", photo_duration=1.0, language="pl")
    project = engine.Project()
    p = engine.prepare(project, s)
    film = engine.timeline(p)
    assert engine.timeline(p) is film and film.total == film.count == round(film.seconds * 5)
    assert {seg.kind for seg in film.segments} == {"hold", "flight", "pause", "peaks", "dissolve", "route", "stop", "photos", "outro"}
    assert [seg.start for seg in film.segments] == [sum(q.n for q in film.segments[:k]) for k in range(len(film.segments))]
    in_order = [hashlib.sha1(film.frame(i).tobytes()).digest() for i in range(film.total)]
    film.kept.clear()                                           # as if it had drawn nothing yet
    picked = sorted({i for seg in film.segments for i in (seg.start, seg.start + seg.n // 2, seg.start + seg.n - 1)}, reverse=True)
    assert len(picked) > 2 * len(film.segments)
    for i in picked + picked[::-3]:                             # from the end to the start, then jumping forward
        seg = next(q for q in film.segments if q.start <= i < q.start + q.n)
        assert hashlib.sha1(engine.frame_at(p, i / 5).tobytes()).digest() == in_order[i], f"frame {i} ({seg.kind})"
    assert (engine.frame_at(p, 1e9) == film.frame(film.total - 1)).all() and (engine.frame_at(p, -1) == film.frame(0)).all()
    assert set(film.moments.values()) == {"region_all_in", "region_peaks", "intro_arrived", "arrival_all_in", "intro_last", "route_first"}


FFMPEG = '''import os, sys
args = sys.argv[1:]
if "-encoders" in args:
    print(" V....D libx264              H.264 (fake)")
    sys.exit(0)
with open(os.environ["FAKE_FFMPEG_ARGS"], "w", encoding="utf-8") as f:
    f.write(args[-1])
with open(args[-1], "wb") as out:
    out.write(b"begun")
    out.flush()
    sys.stdin.buffer.read()
    if os.environ.get("FAKE_FFMPEG_FAILS"):
        sys.exit(1)
    out.write(b", finished")
'''


def test_a_film_takes_its_name_only_when_it_is_finished(stored, monkeypatch):
    """ffmpeg writes film.part.mp4. A finished film replaces the one that was there; a stopped or failed export ends
    ffmpeg, removes its unfinished film and leaves the old film alone."""
    import subprocess as sp

    from gpxfilm import engine, task
    (stored / "bin").mkdir()
    (stored / "ffmpeg.py").write_text(FFMPEG, encoding="utf-8")
    (stored / "bin" / "ffmpeg").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stored / "ffmpeg.py"}" "$@"\n', encoding="utf-8")
    (stored / "bin" / "ffmpeg").chmod(0o755)
    monkeypatch.setenv("PATH", f"{stored / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_FFMPEG_ARGS", str(stored / "target.txt"))
    monkeypatch.delenv("GPXFILM_FRAMES", raising=False)
    started, popen = [], sp.Popen
    monkeypatch.setattr(sp, "Popen", lambda *a, **kw: (started.append(popen(*a, **kw)), started[-1])[1])
    out, part = stored / "film.mp4", stored / "film.part.mp4"
    quiet = lambda event: None
    p = engine.prepare(engine.Project(), Settings(gpx=DATA / "SLAD.gpx", size="640x360", fps=5, route_duration=2.0, language="pl"))

    part.write_bytes(b"left by an export whose process was ended")
    with task.running(task.Task(quiet)):
        engine.render_film(p, out)
    assert out.read_bytes() == b"begun, finished" and not part.exists()
    assert (stored / "target.txt").read_text(encoding="utf-8") == str(part)

    def stop_at_the_third_frame(event):
        if isinstance(event, task.Progress) and event.stage == "frames" and event.done == 2:
            stopping.cancel()

    out.write_bytes(b"the old film")
    stopping = task.Task(stop_at_the_third_frame)
    with task.running(stopping), pytest.raises(task.Cancelled):
        engine.render_film(p, out)
    assert started[-1].poll() is not None                       # ffmpeg is not left running
    assert out.read_bytes() == b"the old film" and not part.exists()

    monkeypatch.setenv("FAKE_FFMPEG_FAILS", "1")
    with task.running(task.Task(quiet)), pytest.raises(task.Failure, match="ffmpeg"):
        engine.render_film(p, out)
    assert out.read_bytes() == b"the old film" and not part.exists()
    assert [f.name for f in stored.iterdir() if f.name.startswith("film")] == ["film.mp4"]
