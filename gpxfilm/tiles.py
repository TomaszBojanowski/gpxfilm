"""Map backgrounds from tile and WMS servers, with holes filled from further sources."""
from __future__ import annotations

import hashlib
import io
import math
import re
import time
import urllib.error
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from .geo import R_EARTH, Layout, Proj
from .i18n import _, credit, ngettext, ui
from .net import CACHE, cache_dir, http_get
from .util import clamp, log


def tile_url(url: str, z: int, x: int, y: int, ts: int) -> str:
    """The address of one tile. The template is either XYZ style ({z} {x} {y}, also {-y} and {s}) or WMS ({bbox} {width} {height} {proj})."""
    n, C = 2 ** z, 2 * math.pi * R_EARTH
    side = C / n
    out = url.replace("{zoom}", str(z)).replace("{z}", str(z)).replace("{x}", str(x)).replace("{-y}", str(n - 1 - y)).replace("{y}", str(y))
    out = out.replace("{s}", "abc"[(x + y) % 3]).replace("{proj}", "EPSG:3857").replace("{width}", str(ts)).replace("{height}", str(ts))
    out = out.replace("{bbox}", f"{-C / 2 + x * side:.3f},{C / 2 - (y + 1) * side:.3f},{-C / 2 + (x + 1) * side:.3f},{C / 2 - y * side:.3f}")
    m = re.search(r"\{switch:([^}]+)\}", out)
    return out.replace(m[0], m[1].split(",")[(x + y) % len(m[1].split(","))]) if m else out


TILE_TIMEOUT = 20                        # s for one answer of a tile server
TILE_TRIES = 3                           # a tile the server failed to give is asked for again, up to this many tries in all
TILE_PAUSE = 2.0                         # s before the second try of the failed tiles, doubled before each next one
SILENT = 5                               # tries in a row without any answer: the server is not answering
MAX_MISSING = 0.08                       # a source missing more of its tiles than this after all tries is dropped
LAST_CHANCE = (2, 10.0)                  # this many tiles still missing get one more try after this long a pause
GAP = {False: 0.1, True: 0.5}            # s after each download: tile servers, and the slower WMS servers
NO_DATA = {400, 404, 410}                # answers that mean the server has nothing there (400: a WMTS tile out of range)


class Failed:
    """A tile the server did not give this time: no answer, a server error, a page or a broken image instead of it."""

    def __init__(self, wait: float = 0.0, again: bool = True, silent: bool = False):
        self.wait, self.again, self.silent = wait, again, silent   # Retry-After; worth asking again; no answer at all


class SourceDown(Exception):
    """A map server that does not answer, or leaves too many tiles missing even after asking again."""

    def __init__(self, missing: int = 0, total: int = 0, credit: str = ""):
        super().__init__(missing, total, credit)
        self.missing, self.total, self.credit = missing, total, credit

    def __str__(self) -> str:                         # in lowercase: it is part of a longer message
        source = credit(self.credit, ui()) if self.credit else ""
        if self.total and source:
            return ngettext("the server did not return {missing} of {total} tile even after retries ({source})",
                            "the server did not return {missing} of {total} tiles even after retries ({source})",
                            self.total).format(missing=self.missing, total=self.total, source=source)
        if self.total:
            return ngettext("the server did not return {missing} of {total} tile even after retries",
                            "the server did not return {missing} of {total} tiles even after retries",
                            self.total).format(missing=self.missing, total=self.total)
        if source:
            return _("the server does not answer or returns errors ({source})").format(source=source)
        return _("the server does not answer or returns errors")


def _retry_after(e: urllib.error.HTTPError) -> float:
    v = str(e.headers.get("Retry-After", "") if e.headers else "").strip()
    return min(float(v), 30.0) if v.isdigit() else 0.0


def load_tile(url: str, path: Path, ts: int, wms: bool = False):
    """One try for a tile: (RGB, mask of empty places), None when the server has no data there, the width of the server's
    tiles when they differ from ts, or Failed when the server did not give the tile this time."""
    raw = None
    if path.exists():
        raw = path.read_bytes()
    else:
        try:
            raw = http_get(url, timeout=TILE_TIMEOUT, tries=1)
        except urllib.error.HTTPError as e:
            if e.code in NO_DATA:
                return None
            return Failed(_retry_after(e), again=e.code not in (401, 403))
        except Exception:  # noqa: BLE001 – no answer or a broken connection: maybe next time
            return Failed(silent=True)
        finally:
            time.sleep(GAP[wms])
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:  # noqa: BLE001 – not an image: never kept in the cache
        path.unlink(missing_ok=True)
        if not raw.strip() or b"TileOutOfRange" in raw[:4096]:
            return None                           # an empty answer, or a WMTS tile out of range: no data here
        return Failed()                           # a page or an error report instead of the image, or an image cut short
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if im.size != (ts, ts):
        if im.width in (256, 512) and im.width != ts:
            return im.width                           # the server gives tiles of another size than assumed
        im = im.resize((ts, ts), Image.BILINEAR)
    a = np.asarray(im.convert("RGBA"))
    void = (a[..., 3] < 128) | (a[..., :3].min(axis=2) >= 252) | (a[..., :3].max(axis=2) <= 3)   # transparent, pure white or pure black = no data
    if void.mean() > 0.01:                            # only large even patches; bright scree or snow stays part of the photo
        void = ndi.maximum_filter(ndi.minimum_filter(void, 33), 33)
    else:
        void = None
    return a[..., :3], void


def raster_one(pr: Proj, lay: Layout, url: str, zmax: int, probe: bool):
    """The background from one source, resampled to frame pixels: (image 0..1, mask of holes or None, share of holes, (missing, all tiles)).
    Tiles the server fails to give are asked for again; SourceDown when it does not answer or too many stay missing."""
    C, wms = 2 * math.pi * R_EARTH, "{bbox}" in url
    cdir = cache_dir(CACHE, "tiles", "mapa") / hashlib.sha1(url.encode()).hexdigest()[:10]
    x0, y0 = lay.meters(0, 0)
    x1, y1 = lay.meters(1920, 1080)
    pause = TILE_PAUSE * (2 if wms else 1)

    def grid(ts: int):
        z = int(clamp(math.ceil(math.log2(C * pr.k / (ts * lay.mpp * 1.3))), 2, zmax))
        while True:
            n = 2 ** z
            u = lambda xm, n=n: (((xm / pr.k + pr.X0) / C) + 0.5) * n * ts
            v = lambda ym, n=n: (0.5 - (-ym / pr.k + pr.Y0) / C) * n * ts
            box = int(u(x0) // ts), int(u(x1) // ts), int(v(y0) // ts), int(v(y1) // ts)
            if (box[1] - box[0] + 1) * (box[3] - box[2] + 1) * ts * ts <= 30e6 or z <= 2:
                return z, n, u, v, box
            z -= 1

    def tile(z: int, n: int, tx: int, ty: int, ts: int):
        return load_tile(tile_url(url, z, tx % n, min(max(ty, 0), n - 1), ts), cdir / f"{z}-{ts}" / str(tx) / str(ty), ts, wms)

    def first(z: int, n: int, tx: int, ty: int, ts: int):   # the middle of the frame, with all its tries
        for k in range(TILE_TRIES):
            got = tile(z, n, tx, ty, ts)
            if not isinstance(got, Failed):
                return got
            if not got.again:
                break
            if k + 1 < TILE_TRIES:
                time.sleep(max(got.wait, pause * 2 ** k))
            elif got.wait:                            # the next source may live on the same server
                time.sleep(got.wait)
        raise SourceDown()

    TS = 512 if wms else 256
    z, n, u, v, (tx0, tx1, ty0, ty1) = grid(TS)
    mid = first(z, n, (tx0 + tx1) // 2, (ty0 + ty1) // 2, TS)
    if isinstance(mid, int):                          # double-density tiles: the same data, one level lower
        TS = mid
        z, n, u, v, (tx0, tx1, ty0, ty1) = grid(TS)
        mid = first(z, n, (tx0 + tx1) // 2, (ty0 + ty1) // 2, TS)
    if probe and (mid is None or isinstance(mid, int) or (mid[1] is not None and mid[1].mean() > 0.9)):
        return None                                   # the middle of the frame is outside this source's coverage
    nx, ny = tx1 - tx0 + 1, ty1 - ty0 + 1
    log(ngettext("Map background: {n} tile, zoom {zoom}", "Map background: {n} tiles, zoom {zoom}", nx * ny).format(n=nx * ny, zoom=z))
    cells, tiles, missing = [(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)], {}, []
    todo, row = cells, 0                              # row: tries in a row without any answer, over all rounds

    def ask(todo: list) -> list:                      # one try for each tile; the ones that failed and are worth asking again
        nonlocal row
        failed = []
        for c in todo:
            got = tile(z, n, *c, TS)
            if isinstance(got, Failed):
                (failed if got.again else missing).append(c)
                row = row + 1 if got.silent else 0
                if row >= SILENT:                     # it stopped answering
                    raise SourceDown()
                if got.wait:                          # the server said when to ask again: nothing goes out before that
                    time.sleep(got.wait)
            else:
                tiles[c], row = got, 0
        return failed

    for k in range(TILE_TRIES):                       # tiles the server failed to give are asked for again after a pause
        todo = ask(todo)
        if not todo:
            break
        if k + 1 < TILE_TRIES:
            time.sleep(pause * 2 ** k)
    if 0 < len(todo) <= LAST_CHANCE[0]:               # one or two tiles short: one more try after a longer pause
        time.sleep(LAST_CHANCE[1])
        todo = ask(todo)
    missing += todo
    if len(missing) > MAX_MISSING * len(cells):
        raise SourceDown(len(missing), len(cells))
    mos = np.zeros((ny * TS, nx * TS, 4), np.uint8)
    for (tx, ty) in cells:
        got = tiles.get((tx, ty))
        cell = mos[(ty - ty0) * TS:(ty - ty0 + 1) * TS, (tx - tx0) * TS:(tx - tx0 + 1) * TS]
        if got is None or isinstance(got, int):       # no data there, or a tile still missing after all tries
            cell[..., 3] = 255
        else:
            cell[..., :3] = got[0]
            if got[1] is not None:
                cell[..., 3] = got[1] * 255
    if not mos[..., 3].any():
        mos = mos[..., :3]
    uu = u(((np.arange(lay.W) + 0.5) / lay.K - lay.ox) / lay.S) - tx0 * TS - 0.5
    vv = v(((np.arange(lay.H) + 0.5) / lay.K - lay.oy) / lay.S) - ty0 * TS - 0.5
    i0 = np.clip(np.floor(vv).astype(int), 0, mos.shape[0] - 2); fv = (vv - i0)[:, None, None].astype(np.float32)
    j0 = np.clip(np.floor(uu).astype(int), 0, mos.shape[1] - 2); fu = (uu - j0)[None, :, None].astype(np.float32)
    top = mos[i0][:, j0] * (1 - fu) + mos[i0][:, j0 + 1] * fu
    bot = mos[i0 + 1][:, j0] * (1 - fu) + mos[i0 + 1][:, j0 + 1] * fu
    out = ((top * (1 - fv) + bot * fv) / 255).astype(np.float32)
    lost = (len(missing), len(cells))                 # tiles still missing after all tries, of all tiles
    if out.shape[2] == 3:
        return out, None, 0.0, lost
    hole = np.clip(ndi.gaussian_filter(out[..., 3], 3 * lay.K) * 1.6 - 0.3, 0, 1)      # a soft edge of the hole
    return out[..., :3], hole, float(out[..., 3].mean()), lost


def raster_map(pr: Proj, lay: Layout, sources):
    """The background from the given sources: the first one that covers the middle of the frame is the base, and the next ones fill
    its holes. (image, mask of holes, share of holes, credit, whether complete), or None when none reaches there. SourceDown when
    a server that may have this frame does not answer or leaves too many holes."""
    (la0, lo0), (la1, lo1) = ((float(v) for v in pr.inv(*lay.meters(0, 0))), (float(v) for v in pr.inv(*lay.meters(1920, 1080))))
    near = [s for s in sources if len(s) < 4 or not (s[3][2] < min(lo0, lo1) or s[3][0] > max(lo0, lo1) or s[3][3] < min(la0, la1) or s[3][1] > max(la0, la1))]
    best, down, short = None, [], []                 # down: sources that failed; short: sources passed over with tiles missing

    def one(s, probe):
        try:
            return raster_one(pr, lay, s[0], s[1], probe)
        except SourceDown as e:
            e.credit = s[2]
            down.append((s, e))
            return None

    for s in near:
        got = one(s, True)
        if got is not None and got[3][0]:
            short.append((s, SourceDown(*got[3], s[2])))
        if got is not None and (best is None or got[2] < best[0][2] - 0.02):
            best = (got, s)
            if got[2] <= 0.03:
                break
    if best is None:
        if down:                                      # a server that did not answer may have this place
            raise down[0][1]
        return None
    (ras, hole, _share, lost), credits = best[0], [best[1][2]]
    complete = not lost[0]
    for s in near:                                    # routes on the border of regions: the next source fills the holes of the one before
        if hole is None or float(hole.mean()) <= 0.003:
            break
        if s is best[1] or any(s is d_ for d_, _ in down):
            continue
        got = one(s, False)
        if got is None:
            continue
        complete = complete and not got[3][0]
        fill = hole if got[1] is None else hole * (1 - got[1])
        if float(fill.mean()) < 0.005:
            continue
        ras = ras * (1 - fill[..., None]) + got[0] * fill[..., None]
        hole = None if got[1] is None else hole * got[1]
        credits.append(s[2])
    if down and hole is not None and float(hole.mean()) > 0.003:
        raise down[0][1]                              # the rest of the frame may be on the server that did not answer
    at = {id(s): i for i, s in enumerate(near)}
    named = {id(s): (s, e) for s, e in short + down if s is not best[1]}     # one line per source, the last trouble
    for s, e in sorted(named.values(), key=lambda x: at[id(x[0])]):
        if at[id(s)] < at[id(best[1])]:
            log(_("Map background: {error} – using the next source ({source}).").format(error=e, source=credit(best[1][2], ui())))
        else:
            log(_("Map background: {error} – another source fills the gaps.").format(error=e))
    complete = complete and not named                 # next time the first ones may come whole
    return ras, hole, 0.0 if hole is None else float(hole.mean()), "; ".join(credits), complete
