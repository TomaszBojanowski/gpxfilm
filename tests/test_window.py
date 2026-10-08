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
