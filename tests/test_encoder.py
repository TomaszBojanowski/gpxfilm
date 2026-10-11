"""The choice of the ffmpeg encoder and its arguments, with a stand-in for ffmpeg that names the encoders a test gives it."""
import sys
from pathlib import Path

import pytest

from gpxfilm import film, task

ROOT = Path(__file__).resolve().parent.parent
X264 = ["-c:v", "libx264", "-preset", "medium", "-crf", "16"]
X265 = ["-c:v", "libx265", "-preset", "medium", "-crf", "20", "-tag:v", "hvc1"]
FIXED = ["-b:v", "45M", "-maxrate", "60M"]            # encoders without a quality setting get a bit rate
ALL = "libsvtav1,libx265,libopenh264,h264_videotoolbox,libx264"


@pytest.fixture
def ffmpeg(tmp_path, monkeypatch):
    """ffmpeg that has the given encoders: ffmpeg("libx264,libx265") gives its path."""
    exe = tmp_path / "ffmpeg"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tests" / "fake_ffmpeg.py"}" "$@"\n', encoding="utf-8")
    exe.chmod(0o755)

    def having(encoders: str) -> str:
        monkeypatch.setenv("FAKE_FFMPEG_ENCODERS", encoders)
        return str(exe)
    return having


def pick(ff: str, **settings) -> tuple[list, list]:
    """The arguments chosen and what was said while choosing."""
    said = []
    with task.running(task.Task(lambda event: said.append(event.text))):
        return film.pick_encoder(ff, settings.get("encoder"), settings.get("crf", 16), settings.get("codec")), said


def test_x264_is_taken_when_ffmpeg_has_it(ffmpeg):
    assert pick(ffmpeg(ALL)) == (X264, [])
    assert pick(ffmpeg("libx264"), crf=23) == (["-c:v", "libx264", "-preset", "medium", "-crf", "23"], [])


@pytest.mark.parametrize("encoders, args", [
    ("libsvtav1,libx265,libopenh264,h264_videotoolbox", ["-c:v", "h264_videotoolbox", *FIXED]),
    ("libsvtav1,libx265,libopenh264", ["-c:v", "libopenh264", *FIXED]),
])
def test_without_x264_another_h264_encoder_is_taken_with_a_warning(ffmpeg, encoders, args):
    got, said = pick(ffmpeg(encoders))
    assert got == args and len(said) == 1 and args[1] in said[0] and "libx264" in said[0]


def test_an_encoder_asked_for_by_name_is_used(ffmpeg):
    assert pick(ffmpeg(ALL), encoder="libx264")[0] == X264
    assert pick(ffmpeg(ALL), encoder="libx265")[0] == X265                  # the quality is moved to the scale of x265
    assert pick(ffmpeg(ALL), encoder="libsvtav1")[0] == ["-c:v", "libsvtav1", "-preset", "7", "-crf", "28"]
    assert pick(ffmpeg(ALL), encoder="h264_videotoolbox")[0] == ["-c:v", "h264_videotoolbox", *FIXED]
    assert pick(ffmpeg(ALL), encoder="h264_nvenc")[0] == ["-c:v", "h264_nvenc"]   # one the program does not know: as it is
    assert pick(ffmpeg("libx264"), encoder="libx265")[0] == ["-c:v", "libx265"]   # ffmpeg will say that it has none


def test_the_codec_asked_for_decides_the_encoder(ffmpeg):
    assert pick(ffmpeg(ALL), codec="h264") == (X264, [])
    assert pick(ffmpeg(ALL), codec="hevc") == (X265, [])                    # no warning: it is what was asked for
    assert pick(ffmpeg(ALL), codec="hevc", crf=23)[0][5] == "27"
    assert pick(ffmpeg("libx265,libopenh264"), codec="h264")[0] == ["-c:v", "libopenh264", *FIXED]


def test_an_encoder_asked_for_alone_decides_the_codec(ffmpeg):
    """--encoder without --codec works as it did before there was a choice of codec, also for AV1, which is not on the
    list of codecs."""
    for name, args in (("libx265", X265), ("libsvtav1", ["-c:v", "libsvtav1", "-preset", "7", "-crf", "28"]), ("libx264", X264)):
        assert pick(ffmpeg(ALL), encoder=name) == (args, []), name


def test_a_codec_and_an_encoder_that_disagree_are_a_failure(ffmpeg):
    with pytest.raises(task.Failure, match="libx264.*HEVC.*--codec.*--encoder"):
        pick(ffmpeg(ALL), codec="hevc", encoder="libx264")
    with pytest.raises(task.Failure, match="libx265.*H.264"):
        pick(ffmpeg(ALL), codec="h264", encoder="libx265")
    with pytest.raises(task.Failure, match="libsvtav1.*HEVC"):
        pick(ffmpeg(ALL), codec="hevc", encoder="libsvtav1")
    assert pick(ffmpeg(ALL), codec="hevc", encoder="libx265")[0] == X265    # they agree
    assert pick(ffmpeg(ALL), codec="h264", encoder="libopenh264")[0] == ["-c:v", "libopenh264", *FIXED]
    assert pick(ffmpeg(ALL), codec="hevc", encoder="hevc_nvenc")[0] == ["-c:v", "hevc_nvenc"]   # one the program does not know: not judged


def test_a_codec_this_ffmpeg_cannot_make_is_a_failure_that_names_the_way_out(ffmpeg):
    """Never a film in another codec than asked for: without an H.264 encoder the default fails too, and says what works."""
    with pytest.raises(task.Failure, match=r"nie ma kodera H\.264 \(libx264, h264_videotoolbox, libopenh264\)\. Ma koder HEVC: użyj --codec hevc\."):
        pick(ffmpeg("libx265,libsvtav1"))
    with pytest.raises(task.Failure, match=r"nie ma kodera HEVC \(libx265\)\. Ma koder H\.264: użyj --codec h264\."):
        pick(ffmpeg("libx264,libsvtav1"), codec="hevc")
    with pytest.raises(task.Failure, match=r"nie ma kodera H\.264 \(libx264, h264_videotoolbox, libopenh264\)\.$"):
        pick(ffmpeg("libsvtav1"))                                           # AV1 is not offered: it is made only when asked for by name
    with pytest.raises(task.Failure, match=r"nie ma kodera HEVC \(libx265\)\.$"):
        pick(ffmpeg(""), codec="hevc")


def test_the_codecs_a_film_can_be_made_in(ffmpeg):
    assert film.codecs_of(film.known_encoders(ffmpeg(ALL))) == ["h264", "hevc"]
    assert film.codecs_of(film.known_encoders(ffmpeg("libopenh264,libsvtav1"))) == ["h264"]
    assert film.codecs_of(film.known_encoders(ffmpeg("libx265"))) == ["hevc"] and film.codecs_of(film.known_encoders(ffmpeg(""))) == []


def test_the_command_takes_the_codec(ffmpeg, tmp_path):
    """--codec hevc reaches ffmpeg; without it the arguments are those of before; a codec and an encoder that disagree end
    the command with the message, before any work."""
    import json
    import os
    import shutil
    import subprocess

    from offline import offline_env
    exe = Path(ffmpeg(ALL))
    shutil.copytree(ROOT / "tests" / "data" / "cache", tmp_path / "cache")
    env = offline_env(tmp_path / "cache", PATH=f"{exe.parent}{os.pathsep}{os.environ['PATH']}", FAKE_FFMPEG_LOG=str(tmp_path / "log"),
                      FAKE_FFMPEG_ENCODERS=ALL, XDG_CONFIG_HOME=str(tmp_path / "config"))
    env.pop("GPXFILM_FRAMES", None)

    def run(*options: str):
        r = subprocess.run([sys.executable, "-m", "gpxfilm", str(ROOT / "tests" / "data" / "SLAD.gpx"), "--size", "640x360", "--fps", "5",
                            "--route-duration", "1", "-o", str(tmp_path / "film.mp4"), *options], cwd=ROOT, env=env, capture_output=True, text=True)
        return r, json.loads((tmp_path / "log" / "command.json").read_text(encoding="utf-8")) if r.returncode == 0 else None

    r, cmd = run()
    assert r.returncode == 0 and cmd[cmd.index("-c:v"):cmd.index("-c:v") + 6] == X264 and "hvc1" not in cmd
    r, cmd = run("--codec", "hevc")
    assert r.returncode == 0 and cmd[cmd.index("-c:v"):cmd.index("-c:v") + 8] == X265 and "format=yuv420p" in cmd[cmd.index("-vf") + 1]
    r, cmd = run("--encoder", "libx265")
    assert r.returncode == 0 and cmd[cmd.index("-c:v"):cmd.index("-c:v") + 8] == X265
    r, cmd = run("--codec", "hevc", "--encoder", "libx264")
    assert r.returncode == 2 and "Koder libx264 nie tworzy filmów HEVC" in r.stderr and "Rysuję tło" not in r.stderr
    r, cmd = run("--codec", "av1")
    assert r.returncode == 2 and "--codec" in r.stderr                      # not on the list


def _real_ffmpeg() -> bool:
    import shutil
    import subprocess
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        return False
    return "libx265" in film.known_encoders(shutil.which("ffmpeg")) and "libx264" in film.known_encoders(shutil.which("ffmpeg")) \
        and subprocess.run(["ffprobe", "-version"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _real_ffmpeg(), reason="needs ffmpeg with libx264 and libx265, and ffprobe")
def test_an_hevc_film_is_one_that_apple_devices_play(tmp_path):
    """Encoded by the real ffmpeg: HEVC in the Main profile (8 bits, 4:2:0), tagged hvc1 (QuickTime and iOS do not open
    hev1), with the color marks of the H.264 film and the index at the front. It is smaller than the H.264 film too."""
    import json
    import shutil
    import subprocess

    from offline import offline_env
    shutil.copytree(ROOT / "tests" / "data" / "cache", tmp_path / "cache")
    env = offline_env(tmp_path / "cache", XDG_CONFIG_HOME=str(tmp_path / "config"))
    env.pop("GPXFILM_FRAMES", None)

    def made(name: str, *options: str) -> dict:
        out = tmp_path / name
        r = subprocess.run([sys.executable, "-m", "gpxfilm", str(ROOT / "tests" / "data" / "SLAD.gpx"), "--size", "640x360", "--fps", "10",
                            "--route-duration", "3", "--outro", "-o", str(out), *options], cwd=ROOT, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json", str(out)], capture_output=True, text=True)
        data = out.read_bytes()
        return {**json.loads(probe.stdout)["streams"][0], "size": len(data), "index first": 0 < data.find(b"moov") < data.find(b"mdat")}

    hevc, h264 = made("hevc.mp4", "--codec", "hevc"), made("h264.mp4")
    assert (hevc["codec_name"], hevc["codec_tag_string"], hevc["profile"], hevc["pix_fmt"]) == ("hevc", "hvc1", "Main", "yuv420p")
    assert (h264["codec_name"], h264["codec_tag_string"], h264["pix_fmt"]) == ("h264", "avc1", "yuv420p")
    for key in ("color_range", "color_space", "color_primaries", "color_transfer", "width", "height", "r_frame_rate", "nb_frames"):
        assert hevc.get(key) == h264.get(key), key     # whatever ffmpeg writes of them: the same in both
    assert (hevc["color_range"], hevc["color_space"]) == ("tv", "bt709") and hevc["index first"] and h264["index first"]
    print(f"H.264 {h264['size']} B, HEVC {hevc['size']} B")
    assert hevc["size"] < h264["size"]
