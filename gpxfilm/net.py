"""Downloads and the cache directory."""
from __future__ import annotations

import contextlib
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from .task import rest

PROJECT_URL = "https://github.com/TomaszBojanowski/gpxfilm"
UA = f"gpxfilm/{__version__} (+{PROJECT_URL})"     # how the program introduces itself to every server it asks
if os.environ.get("GPXFILM_CACHE_DIR"):     # explicit cache directory, e.g. stored test data
    CACHE = Path(os.environ["GPXFILM_CACHE_DIR"]).expanduser()
elif sys.platform == "darwin":
    CACHE = Path.home() / "Library" / "Caches" / "gpxfilm"
else:
    CACHE = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "gpxfilm"
try:                                     # a fallback for the python.org Python on macOS, which does not know the system certificates
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = None


# Map servers whose certificate cannot be verified (an incomplete chain on the server side). Only map images are downloaded
# from them and nothing is sent, so for these few addresses the certificate check is skipped when it fails.
LAX_TLS = {"wmts.nlsc.gov.tw"}


def cache_dir(base: Path, name: str, old: str) -> Path:
    """A directory of the cache; one kept under its old Polish name is moved to the new name the first time, so nothing
    has to be downloaded again."""
    p = base / name
    if not p.exists() and (base / old).is_dir():
        try:
            (base / old).rename(p)
        except OSError:
            pass
    return p


@contextlib.contextmanager
def whole(path: Path):
    """Write a file of the cache in one step: the block writes to the temporary path it gets, and only a finished file
    takes the name path. The process may be ended at any moment, and a file cut short must never stay in the cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.part")
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def store(path: Path, data: bytes) -> None:
    """Put data in the cache under path, whole or not at all."""
    with whole(path) as tmp:
        tmp.write_bytes(data)


def http_get(url: str, data: bytes | None = None, timeout: int = 60, tries: int = 3) -> bytes:
    """The body of a GET (or, with data, a POST) to url, with the User-Agent of the program; every download goes through here."""
    err: Exception | None = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read()
            except urllib.error.URLError as e:       # the system certificates failed: try the certifi package
                if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                    raise
                if SSL_CTX is not None:
                    try:
                        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                            return r.read()
                    except urllib.error.URLError as e2:
                        if not isinstance(getattr(e2, "reason", None), ssl.SSLCertVerificationError):
                            raise
                if urllib.parse.urlparse(url).hostname not in LAX_TLS:
                    raise
                with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as r:
                    return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            err = e
        except Exception as e:  # noqa: BLE001 – the network can be flaky, try again
            err = e
        if k + 1 < tries:
            rest(1.5 * (k + 1))
    raise err  # type: ignore[misc]
