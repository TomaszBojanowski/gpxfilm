"""How the program introduces itself to the servers it asks."""
import re
import urllib.request
from pathlib import Path

import gpxfilm
from gpxfilm import net

ROOT = Path(__file__).resolve().parent.parent


def test_every_request_names_the_program_its_version_and_the_project(monkeypatch):
    sent = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"ok"

    def urlopen(req, timeout=None, context=None):
        sent.append(req)
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert net.http_get("https://example.org/tile.png") == b"ok"
    assert net.http_get("https://example.org/api", data=b"q=1") == b"ok"                 # a query sent as a form
    ua = f"gpxfilm/{gpxfilm.__version__} (+https://github.com/TomaszBojanowski/gpxfilm)"
    assert [r.get_header("User-agent") for r in sent] == [ua, ua]


def test_every_download_goes_through_http_get():
    """No other module opens a connection of its own, so none can leave the User-Agent out."""
    for f in sorted((ROOT / "gpxfilm").glob("*.py")):
        if f.name != "net.py":
            assert not re.search(r"urlopen|HTTPS?Connection|urllib3|\brequests\b|socket\.create_connection", f.read_text(encoding="utf-8")), f.name


def test_a_source_tree_never_built_has_a_version_too():
    assert re.fullmatch(r"\d+(\.\d+)*((a|b|rc|\.dev|\.post)\d+)*(\+[\w.]+)?", gpxfilm.__version__), gpxfilm.__version__
