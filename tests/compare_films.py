"""Check that the films of this source tree are the same, byte for byte, as the films of an earlier revision.

    python tests/compare_films.py                 # against the revision before the engine was rebuilt
    python tests/compare_films.py --ref main      # against another revision
    python tests/compare_films.py --real          # also encode one film with the real ffmpeg and compare the decoded frames

Each case is a short film made twice with the stored test data and no network: by the code of the reference revision
(unpacked from git into a temporary directory) and by the code of this tree. A stand-in for ffmpeg (fake_ffmpeg.py) takes
the frames. The two runs must agree in everything: every frame, the number of frames, the arguments given to ffmpeg, the
report, the still frame, the images for slides and the messages (times in seconds aside). One argument of ffmpeg is
allowed to differ: the film may be written as film.part.mp4, the name it has until it is finished. The reference frames
of the ordinary tests allow small differences; this check allows none, so it only compares runs on one machine.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
BASE = "7624c65"                         # main before the engine was rebuilt
SHORT = ["--fps", "5", "--route-duration", "2"]

# The sizes are those of the ordinary tests: the stored terrain tiles cover no other.
# name: (track, options); {out} is the directory of the run, {photos} a directory with three photos taken on SLAD.gpx
CASES = {
    "plain": ("SLAD.gpx", ["--size", "640x360", *SHORT]),
    "intro": ("SLAD.gpx", ["--size", "640x360", *SHORT, "--intro"]),
    "intro_hold": ("SLAD.gpx", ["--size", "640x360", *SHORT, "--intro", "--intro-label-duration", "1", "--intro-duration", "3", "--outro"]),
    "everything": ("SLAD.gpx", ["--size", "640x360", "--fps", "6", "--route-duration", "3", "--intro", "--outro", "--outro-duration", "2",
                                "--photos", "{photos}", "--photo-duration", "1", "--counters", "--km-markers", "--minimap",
                                "--background-names", "--color-by", "slope", "--music", "{out}/music.mp3", "--frame", "{out}/frame.png",
                                "--report", "{out}/report.json", "--slides", "{out}/slides", "--title", "Tre Cime\\ndi Lavaredo"]),
    "photos_no_stops": ("SLAD.gpx", ["--size", "640x360", *SHORT, "--photos", "{photos}", "--stop-minutes", "0", "--label-style", "badges",
                                     "--label-size", "1.3", "--map-style", "night"]),
    "tatry": ("tatry.gpx", ["--size", "960x540", *SHORT, "--counters", "--color-by", "heart-rate", "--language", "en", "--outro",
                            "--report", "{out}/report.json"]),
    "no_times": ("bez_czasu.gpx", ["--size", "480x270", *SHORT, "--color-by", "elevation", "--outro", "--sunlight", "--map-style", "gray"]),
}
REAL = ("SLAD.gpx", ["--size", "640x360", "--fps", "10", "--route-duration", "3", "--intro", "--outro", "--photos", "{photos}",
                     "--photo-duration", "1"])


def make_photos(folder: Path) -> None:
    """Three small photos with the times of SLAD.gpx (07:12–09:36 UTC): two from one place and one from later."""
    folder.mkdir(parents=True)
    for k, (when, color) in enumerate((("07:40:00", (190, 120, 60)), ("07:40:20", (60, 130, 190)), ("08:50:00", (90, 160, 90)))):
        im = Image.new("RGB", (320, 240 if k else 400), color)
        im.paste((250, 250, 250), (40 + 60 * k, 40, 120 + 60 * k, 120))
        ex = Image.Exif()
        ex.get_ifd(0x8769)[0x9003] = f"2025:07:14 {when}"
        im.save(folder / f"photo{k}.jpg", quality=90, exif=ex)


def run(tree: Path, out: Path, gpx: str, options: list, photos: Path, ffmpeg: Path | None) -> dict:
    """One film made by the code in tree, with everything it wrote read back: name → bytes or text."""
    out.mkdir(parents=True)
    shutil.copytree(DATA / "cache", out / "cache")
    env = {**os.environ, "PYTHON_COLORS": "0", "NO_COLOR": "1", "LANGUAGE": "pl", "TZ": "UTC", "GPXFILM_TEXT_LAYOUT": "basic",
           "GPXFILM_CACHE_DIR": str(out / "cache"), "XDG_CONFIG_HOME": str(out / "config"), "FAKE_FFMPEG_LOG": str(out / "ffmpeg"),
           "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
           "HTTPS_PROXY": "http://127.0.0.1:9", "no_proxy": "", "NO_PROXY": ""}
    env.pop("GPXFILM_FRAMES", None)
    if ffmpeg is not None:
        env["PATH"] = f"{ffmpeg}{os.pathsep}{env['PATH']}"
    args = [a.format(out=out, photos=photos) for a in options]
    r = subprocess.run([sys.executable, "-m", "gpxfilm", str(DATA / gpx), *args, "-o", str(out / "film.mp4")], cwd=tree, env=env,
                       capture_output=True, text=True)
    same = lambda s: re.sub(r"\(\d+ s\)|, \d+ s\)", "(… s)", s.replace(str(out), "<out>"))   # the run's own directory and times aside
    got = {"exit code": str(r.returncode), "messages": same(r.stderr), "output": same(r.stdout)}
    for p in sorted(out.rglob("*")):
        if not p.is_file() or out / "cache" in p.parents or out / "config" in p.parents:
            continue
        name = str(p.relative_to(out))
        if p.suffix == ".png":
            with Image.open(p) as im:
                got[name] = f"{im.size} {im.mode} " + hashlib.sha256(im.tobytes()).hexdigest()
        elif name == str(Path("ffmpeg", "command.json")):
            cmd = json.loads(p.read_text(encoding="utf-8"))
            if cmd[-1] == str(out / "film.part.mp4"):  # the film under its unfinished name: the same film; only this argument
                cmd[-1] = str(out / "film.mp4")
            got[name] = same(json.dumps(cmd, ensure_ascii=False, indent=1))
        elif p.suffix in (".json", ".txt"):
            got[name] = same(p.read_text(encoding="utf-8"))
        elif p.suffix == ".mp4" and ffmpeg is None:  # a real film: its frames as the decoder gives them
            d = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(p), "-f", "framemd5", "-"], capture_output=True, text=True)
            got[name] = d.stdout if d.returncode == 0 else f"cannot decode: {d.stderr}"
        else:
            got[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return got


def differences(ref: dict, new: dict) -> list[str]:
    out = []
    for name in sorted(set(ref) | set(new)):
        a, b = ref.get(name), new.get(name)
        if a == b:
            continue
        if a is None or b is None:
            out.append(f"{name}: only in the {'new' if a is None else 'reference'} run")
            continue
        la, lb = a.splitlines(), b.splitlines()
        k = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
        out.append(f"{name}: {len(la)} and {len(lb)} lines, first difference in line {k + 1}:\n"
                   f"      reference: {la[k] if k < len(la) else '(nothing)'}\n      new:       {lb[k] if k < len(lb) else '(nothing)'}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", default=BASE, help="git revision with the reference code")
    ap.add_argument("--tree", type=Path, default=ROOT, help="source tree to check (default: this one)")
    ap.add_argument("--case", action="append", choices=list(CASES), help="only this case (can be given more than once)")
    ap.add_argument("--real", action="store_true", help="also encode one film with the real ffmpeg and compare the decoded frames")
    ap.add_argument("--keep", type=Path, help="keep the files of both runs in this directory")
    A = ap.parse_args()
    tmp = A.keep or Path(tempfile.mkdtemp(prefix="gpxfilm-compare-"))
    try:
        if tmp.exists() and any(tmp.iterdir()) and A.keep:
            sys.exit(f"{tmp} is not empty")
        ref_tree, photos, fake = tmp / "ref-tree", tmp / "photos", tmp / "bin"
        ref_tree.mkdir(parents=True)
        tar = subprocess.run(["git", "archive", A.ref, "--", "gpxfilm", "po"], cwd=ROOT, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(ref_tree)], input=tar, check=True)
        make_photos(photos)
        fake.mkdir()
        (fake / "ffmpeg").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_ffmpeg.py"}" "$@"\n', encoding="utf-8")
        (fake / "ffmpeg").chmod(0o755)
        jobs = [(name, gpx, options, fake) for name, (gpx, options) in CASES.items() if not A.case or name in A.case]
        if A.real:
            jobs.append(("real ffmpeg", *REAL, None))
        with ThreadPoolExecutor(os.cpu_count() or 4) as pool:
            runs = [(name, pool.submit(run, ref_tree, tmp / "ref" / name, gpx, options, photos, ffmpeg),
                     pool.submit(run, A.tree, tmp / "new" / name, gpx, options, photos, ffmpeg)) for name, gpx, options, ffmpeg in jobs]
            failed = 0
            for name, ref, new in runs:
                ref, new = ref.result(), new.result()
                diff = differences(ref, new)
                film = "ffmpeg/frames.txt" if "ffmpeg/frames.txt" in ref else "film.mp4"
                if ref["exit code"] != "0" or film not in ref:
                    diff.insert(0, "the reference run failed:\n" + ref["messages"])
                if diff:
                    failed += 1
                    print(f"DIFFERENT  {name}\n    " + "\n    ".join(diff))
                else:
                    print(f"same       {name}: {len(ref[film].splitlines()) - 1} frames, {len(ref)} outputs")
        print(f"\n{'All the same' if not failed else f'{failed} of {len(runs)} differ'} (reference: {A.ref}).")
        return 1 if failed else 0
    finally:
        if not A.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
