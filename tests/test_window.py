"""The browser window: its message box and the server behind it, as far as they can be checked without a browser."""
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from gpxfilm import webgui

ROOT = Path(__file__).resolve().parent.parent
LINE = 12.5 * 1.45                                     # px: one line of the message box (font size times line height)


def _rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\{([^}]*)\}", css)
    assert m, f"no rule for {selector}"
    return m[1]


def test_the_box_keeps_the_last_messages_and_counts_them():
    m = webgui.Messages()
    for i in range(500):
        m.add(f"message {i}\n")
        m.add(f"  {i % 100:3d}%  ({i} s)\n")            # progress lines set the bar, not the box
    m.add("   \n")
    got = m.tail()
    assert got["n"] == 500 and len(got["log"]) == webgui.LOG_LINES == 200
    assert got["log"][0] == "message 300" and got["log"][-1] == "message 499" and m.pct == 99


def test_the_box_fills_the_free_height_and_scrolls_inside():
    page = webgui.GUI_PAGE
    css = page[page.index("<style>"):page.index("</style>")]
    box = _rule(css, "#log")
    assert "flex:1 1 0" in box and "max-height" not in box   # all the height left under the preview
    for row in (".actions", ".bar", ".note", "#chips"):      # the rows above keep their height; only the box gives way
        assert "flex:none" in _rule(css, row), row
    lines = float(re.search(r"min-height:calc\(([\d.]+)em", box)[1]) * 12.5 / LINE
    assert lines >= 10                                 # but never fewer than about 10 lines
    assert "max-height" not in _rule(css, "pre") and "overflow:auto" in _rule(css, "pre")
    assert "white-space:pre-wrap" in _rule(css, "pre") # long lines wrap, nothing sticks out on a narrow screen
    narrow = css[css.index("@media(max-width:900px)"):]
    assert ".app{grid-template-columns:1fr;height:auto}" in narrow
    assert re.search(r"#log\{flex:none;height:max\(calc\([\d.]+em \+ 24px\),50vh\)\}", narrow)


def test_new_lines_follow_the_bottom_unless_scrolled_up():
    page = webgui.GUI_PAGE
    assert f"const LOG_KEEP={webgui.LOG_LINES};" in page
    assert "l.scrollHeight-l.scrollTop-l.clientHeight<4" in page
    assert "$('log').addEventListener('scroll',()=>{stick=atBottom();});" in page   # only the reader's scrolling unpins the box
    assert "new ResizeObserver(()=>{if(stick)$('log').scrollTop=$('log').scrollHeight;}).observe($('log'));" in page
    show = page[page.index("function showLog("):page.index("function newLog(")]
    assert "follow=fresh||stick" in show and "l.scrollTop=follow?l.scrollHeight:top" in show
    assert re.search(r"if\(follow&&logLines\.length>LOG_KEEP\)", show)      # old lines go only when the reader is at the bottom
    poll = page[page.index("async function poll("):]
    assert "logLines.push(...s.log.slice(-Math.min(fresh,s.log.length)));logSeen=s.n;" in poll   # only the new lines
    assert "clearInterval(timer);timer=null;" in poll


def test_a_preview_during_a_film_keeps_the_films_messages():
    page = webgui.GUI_PAGE
    pre = page[page.index("async function preview("):page.index("$('prev').onclick")]
    assert "if(timer){" in pre and pre.index("if(timer){") < pre.index("newLog(r.cmd,lines)")


def test_the_server_gives_the_messages_with_their_count(tmp_path):
    """A job with more than 14 lines of messages (a wrong option prints the whole usage) gives all of them, counted; the
    next job starts its own count; a preview gives all its lines too. The page comes in the language of the system, with
    the settings the window remembered under their old names given the new ones."""
    env = dict(os.environ, BROWSER="true", COLUMNS="80", PYTHON_COLORS="0")   # no browser opens; the usage is long enough
    memo = Path(env["GPXFILM_CACHE_DIR"]) / "gui.json"
    memo.parent.mkdir(parents=True, exist_ok=True)
    memo.write_text(json.dumps({"styl": "nocny", "punkty": 4, "intro_peak_radius": 7, "intro_peak_duration": 3}), encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "gpxfilm", "--gui", "--port", "0"], cwd=ROOT, env=env, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(ln) for ln in p.stderr], daemon=True).start()
    try:
        url = None
        while url is None:
            m = re.search(r"(http://127\.0\.0\.1:\d+/)\?k=(\S+)", lines.get(timeout=30))
            url = m and (m[1], m[2])
        get = urllib.request.build_opener(urllib.request.ProxyHandler({})).open    # straight to the local server

        def status() -> dict:
            return json.loads(get(f"{url[0]}status?k={url[1]}", timeout=10).read())

        def post(path: str, body: dict) -> dict:
            return json.loads(get(urllib.request.Request(f"{url[0]}{path}?k={url[1]}", data=json.dumps(body).encode()), timeout=60).read())

        assert status()["log"] == [] and status()["n"] == 0 and status()["pct"] == 0
        page = get(f"{url[0]}?k={url[1]}", timeout=10).read().decode()
        assert 'id="log"' in page and '<html lang="pl">' in page and ">Wybierz…</button>" in page
        assert not re.search("[⟦⟧⟪⟫]", page)
        init = json.loads(re.search(r"INIT=(\{.*?\}),\$=", page)[1])
        assert init["map_style"] == "night" and init["places"] == 4 and init["intro_radius"] == 7 and init["intro_label_duration"] == 3
        assert init["decimal"] == ","
        wrong = {"gpx": str(tmp_path / "none.gpx"), "fps": "abc", "output": str(tmp_path / "x.mp4")}
        counts = []
        for _ in range(2):
            assert post("/render", wrong)["ok"]
            deadline = time.monotonic() + 60
            while not (s := status())["done"] or s["running"]:
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert s["n"] == len(s["log"]) > 14 and s["log"][0].startswith("użycie:") and not s["ok"]
            counts.append(s["n"])
        assert counts[0] == counts[1]                    # each job has its own messages
        assert len(post("/preview", wrong)["log"].splitlines()) > 14
    finally:
        p.send_signal(signal.SIGINT)                  # as Ctrl+C in the terminal: the window cleans up after itself
        p.wait(timeout=10)


def test_a_low_window_makes_the_preview_smaller():
    """The page shrinks the 16:9 preview so that the buttons and at least 10 lines of messages fit under it without
    scrolling, but not below 240 px; on a narrow screen it leaves the layout alone. Measured by the page itself, since the
    rows under the preview change (labels, a wrapped row of buttons)."""
    page = webgui.GUI_PAGE
    css = page[page.index("<style>"):page.index("</style>")]
    assert "aspect-ratio:16/9" in _rule(css, ".stage") and "flex:none" in _rule(css, ".stage")
    fit = page[page.index("function fitStage("):page.index("const fit=")]
    assert "const STAGE_MIN=240;" in page and "Math.max(STAGE_MIN,Math.min(room,innerHeight*0.66))" in fit
    assert "getComputedStyle(l).minHeight" in fit and "h*16/9" in fit           # room for the minimum of the message box
    assert "if(innerWidth<=900){st.style.width='';return;}" in fit and "@media(max-width:900px)" in css
    watched = page[page.index("const fit="):page.index("const fit=") + 300]
    assert all(s in watched for s in ("document.querySelector('main')", "document.querySelector('.actions')", "$('info')", "$('chips')"))


def test_the_window_previews_and_makes_films_through_worker_processes(tmp_path):
    """With the stored test data and a stand-in for ffmpeg: a preview comes in the size of the page's box, with the report
    and the messages; a film is made and reported as before; a film that is stopped leaves the old film and nothing else."""
    import io
    import shutil

    from offline import offline_env
    from PIL import Image
    shutil.copytree(ROOT / "tests" / "data" / "cache", tmp_path / "cache")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ffmpeg").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_ffmpeg.py"}" "$@"\n', encoding="utf-8")
    (tmp_path / "bin" / "ffmpeg").chmod(0o755)
    env = offline_env(tmp_path / "cache", BROWSER="true", XDG_CONFIG_HOME=str(tmp_path / "config"), FAKE_FFMPEG_LOG=str(tmp_path / "ffmpeg"),
                      PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    env.pop("GPXFILM_FRAMES", None)
    p = subprocess.Popen([sys.executable, "-m", "gpxfilm", "--gui", "--port", "0"], cwd=ROOT, env=env, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(ln) for ln in p.stderr], daemon=True).start()
    try:
        url = None
        while url is None:
            m = re.search(r"(http://127\.0\.0\.1:\d+/)\?k=(\S+)", lines.get(timeout=30))
            url = m and (m[1], m[2])
        get = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

        def status() -> dict:
            return json.loads(get(f"{url[0]}status?k={url[1]}", timeout=10).read())

        def post(path: str, body: dict) -> dict:
            return json.loads(get(urllib.request.Request(f"{url[0]}{path}?k={url[1]}", data=json.dumps(body).encode()), timeout=120).read())

        def finished() -> dict:
            deadline = time.monotonic() + 120
            while not (s := status())["done"] or s["running"]:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            return s

        out = tmp_path / "film.mp4"
        form = {"gpx": str(ROOT / "tests" / "data" / "SLAD.gpx"), "size": "640x360", "fps": "5", "route_duration": "2", "language": "pl",
                "counters": True, "output": str(out)}
        r = post("/preview", {**form, "box": [600, 400]})
        assert r["ok"] and r["report"]["distance_km"] == 7.5 and "Rysuję tło" in r["log"] and r["cmd"].startswith("python -m gpxfilm ")
        assert Image.open(io.BytesIO(get(f"{url[0]}preview.png?k={url[1]}", timeout=10).read())).size == (600, 338)
        r = post("/preview", {**form, "km_markers": True, "box": [600, 400]})   # the same worker: the background is not drawn again
        assert r["ok"] and "Rysuję tło" not in r["log"]
        assert post("/preview", form)["ok"]                                     # a page that does not say how large its preview is
        assert Image.open(io.BytesIO(get(f"{url[0]}preview.png?k={url[1]}", timeout=10).read())).size == (1280, 720)
        r = post("/preview", {**form, "gpx": str(ROOT / "tests" / "data" / "gpx" / "empty.gpx")})
        assert not r["ok"] and "jest pusty" in r["log"] and r["report"] is None

        assert post("/render", form)["ok"]
        s = finished()
        assert s["ok"] and not s["stopped"] and s["pct"] == 100 and out.read_bytes() == b"fake film\n"
        assert any(ln.startswith("Gotowe: ") for ln in s["log"])
        assert len((tmp_path / "ffmpeg" / "frames.txt").read_text(encoding="utf-8").splitlines()) > 30

        out.write_bytes(b"the old film")
        assert post("/render", {**form, "fps": "30", "route_duration": "40"})["ok"]
        deadline = time.monotonic() + 120
        while status()["pct"] < 2:
            assert time.monotonic() < deadline and status()["running"]
            time.sleep(0.02)
        began = time.monotonic()
        post("/cancel", {})
        s = finished()
        assert time.monotonic() - began < 3 and not s["ok"] and s["pct"] < 100
        assert s["stopped"] and s["log"][-1] == "Zatrzymano generowanie."      # stopped by the user: said so, and not a failure
        assert out.read_bytes() == b"the old film" and not (tmp_path / "film.part.mp4").exists()

        assert post("/render", {**form, "gpx": str(ROOT / "tests" / "data" / "gpx" / "empty.gpx")})["ok"]
        s = finished()                                                           # a film that cannot be made is a failure
        assert not s["ok"] and not s["stopped"] and "jest pusty" in s["log"][-1] and "Zatrzymano" not in "\n".join(s["log"])
        page = get(f"{url[0]}?k={url[1]}", timeout=10).read().decode()
        assert "if(!s.ok){say(s.stopped?'Zatrzymano.':'Generowanie się nie powiodło. Szczegóły poniżej.');return;}" in page
    finally:
        p.send_signal(signal.SIGINT)
        p.wait(timeout=10)


def test_a_film_name_proposed_for_one_track_is_not_kept_for_the_next(tmp_path):
    """The window remembers its settings. The film name it proposed itself for the last track does not come back (the
    page proposes one for the track it opens with, and for every track chosen later); a name the user typed does.
    The proposing itself is in the script of the page and is checked there only as text."""
    assert webgui.proposed_output("/a/b/track.gpx", "/a/b/track.mp4")
    assert webgui.proposed_output("/tmp/gpxfilm-x/track.gpx", str(Path.home() / "track.mp4"))     # a file dropped on the window
    assert not webgui.proposed_output("/a/b/track.gpx", "/a/b/holiday.mp4") and not webgui.proposed_output("/a/b/track.gpx", "/a/c/track.mp4")
    assert not webgui.proposed_output("", "/a/b/track.mp4") and not webgui.proposed_output("/a/b/track.gpx", "")

    env = dict(os.environ, BROWSER="true", PYTHON_COLORS="0")
    memo = Path(env["GPXFILM_CACHE_DIR"]) / "gui.json"
    memo.parent.mkdir(parents=True, exist_ok=True)
    old, new = tmp_path / "activity_1.gpx", tmp_path / "activity_2.gpx"
    old.write_text("<gpx/>", encoding="utf-8")

    def opened(remembered: dict) -> dict:             # what the page starts with
        memo.write_text(json.dumps(remembered), encoding="utf-8")
        p = subprocess.Popen([sys.executable, "-m", "gpxfilm", "--gui", "--port", "0"], cwd=ROOT, env=env, stderr=subprocess.PIPE, text=True)
        try:
            m = None
            while m is None:
                m = re.search(r"(http://127\.0\.0\.1:\d+/\?k=\S+)", p.stderr.readline())
            page = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(m[1], timeout=10).read().decode()
            return json.loads(re.search(r"INIT=(\{.*?\}),\$=", page)[1])
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=10)

    init = opened({"gpx": str(old), "output": str(old.with_suffix(".mp4")), "places": 4})
    assert init["gpx"] == str(old) and init["output"] is None and init["places"] == 4
    assert opened({"gpx": str(new), "output": str(new.with_suffix(".mp4"))})["output"] is None    # also when that track is gone
    assert opened({"gpx": str(old), "output": str(tmp_path / "holiday.mp4")})["output"] == str(tmp_path / "holiday.mp4")

    page = webgui.GUI_PAGE
    assert "if(p&&(!$('output').value||$('output').dataset.auto)){$('output').value=out||filmName(p);$('output').dataset.auto='1';}" in page
    assert "$('output').addEventListener('input',()=>{delete $('output').dataset.auto;});" in page   # a typed name is the user's own
    assert "propose(p,out);" in page[page.index("function setGpx("):page.index("async function upload(")]
    assert page.index("for(const k of FLAGS)$(k).checked") < page.index("propose($('gpx').value.trim());") < page.index("function setGpx(")


def test_the_window_offers_the_codecs_ffmpeg_has_and_asks_for_the_chosen_one(tmp_path):
    """The page learns which codecs this ffmpeg can make. HEVC chosen in the window reaches ffmpeg; the default codec is
    not asked for, so an encoder kept in the "ffmpeg encoder" field decides the codec as it did before there was a choice;
    a codec and an encoder that disagree are an error with the reason, not a stopped film."""
    import contextlib
    import shutil

    from offline import offline_env
    shutil.copytree(ROOT / "tests" / "data" / "cache", tmp_path / "cache")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ffmpeg").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_ffmpeg.py"}" "$@"\n', encoding="utf-8")
    (tmp_path / "bin" / "ffmpeg").chmod(0o755)
    get = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    @contextlib.contextmanager
    def window(encoders: str):
        env = offline_env(tmp_path / "cache", BROWSER="true", XDG_CONFIG_HOME=str(tmp_path / "config"), FAKE_FFMPEG_LOG=str(tmp_path / "ffmpeg"),
                          FAKE_FFMPEG_ENCODERS=encoders, PATH=f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        env.pop("GPXFILM_FRAMES", None)
        p = subprocess.Popen([sys.executable, "-m", "gpxfilm", "--gui", "--port", "0"], cwd=ROOT, env=env, stderr=subprocess.PIPE, text=True)
        try:
            m = None
            while m is None:
                m = re.search(r"(http://127\.0\.0\.1:\d+/)\?k=(\S+)", p.stderr.readline())
            yield m[1], m[2]
        finally:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=10)

    def init(url) -> dict:
        return json.loads(re.search(r"INIT=(\{.*?\}),\$=", get(f"{url[0]}?k={url[1]}", timeout=10).read().decode())[1])

    with window("libx264") as url:
        assert init(url)["codecs"] == ["h264"]
    with window("libx265") as url:
        assert init(url)["codecs"] == ["hevc"]
    with window("libx264,libx265,libsvtav1") as url:
        assert init(url)["codecs"] == ["h264", "hevc"] and init(url)["codec"] is None

        def film(**fields) -> tuple[dict, dict, list]:    # the reply, the last status and what ffmpeg was asked to do
            shutil.rmtree(tmp_path / "ffmpeg", ignore_errors=True)
            form = {"gpx": str(ROOT / "tests" / "data" / "SLAD.gpx"), "size": "640x360", "fps": "5", "route_duration": "1", "language": "pl",
                    "output": str(tmp_path / "film.mp4"), **fields}
            r = json.loads(get(urllib.request.Request(f"{url[0]}render?k={url[1]}", data=json.dumps(form).encode()), timeout=60).read())
            deadline = time.monotonic() + 120
            while not (s := json.loads(get(f"{url[0]}status?k={url[1]}", timeout=10).read()))["done"] or s["running"]:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            log = tmp_path / "ffmpeg" / "command.json"
            return r, s, json.loads(log.read_text(encoding="utf-8")) if log.exists() else []

        r, s, cmd = film(codec="h264")
        assert s["ok"] and cmd[cmd.index("-c:v") + 1] == "libx264" and "--codec" not in r["cmd"]
        r, s, cmd = film(codec="hevc")
        assert s["ok"] and cmd[cmd.index("-c:v") + 1] == "libx265" and "hvc1" in cmd and "--codec hevc" in r["cmd"]
        r, s, cmd = film(codec="h264", encoder="libx265")                       # the encoder field alone, as before
        assert s["ok"] and cmd[cmd.index("-c:v") + 1] == "libx265" and "hvc1" in cmd and "--codec" not in r["cmd"]
        r, s, cmd = film(codec="h264", encoder="libsvtav1")
        assert s["ok"] and cmd[cmd.index("-c:v") + 1] == "libsvtav1"
        r, s, cmd = film(codec="hevc", encoder="libx264")
        assert not s["ok"] and not s["stopped"] and not cmd and "Koder libx264 nie tworzy filmów HEVC" in s["log"][-1]

    page = webgui.localize(webgui.GUI_PAGE, webgui.Translator("pl"))
    assert '<label for="codec">Kodek</label><select id="codec"><option value="h264">zgodny (H.264)</option><option value="hevc">mniejszy plik (HEVC)</option></select>' in page
    assert "if(!INIT.codecs.includes(op.value)){op.disabled=true;op.textContent+=' – ten ffmpeg go nie obsługuje';}" in page
    assert "if($('codec').selectedOptions[0].disabled)$('codec').value=INIT.codecs[0];" in page
