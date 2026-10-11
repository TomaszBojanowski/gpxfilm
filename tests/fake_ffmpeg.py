"""A stand-in for ffmpeg: instead of encoding, it writes down what it was asked to do and a SHA-256 of every frame it got.

Asked for its encoders, it names libx264, or those of $FAKE_FFMPEG_ENCODERS (names separated with commas; it may be
empty). Asked to encode, it reads raw rgb24 frames from standard input and writes
$FAKE_FFMPEG_LOG/command.json (its arguments) and $FAKE_FFMPEG_LOG/frames.txt (one hash per frame, then the bytes left
over, which must be 0), and a few bytes under the name of the output file.
"""
import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    if "-encoders" in args:
        for name in filter(None, os.environ.get("FAKE_FFMPEG_ENCODERS", "libx264").split(",")):
            print(f" V....D {name:<20} {'H.264' if name == 'libx264' else name} (fake)")
        return 0
    log = Path(os.environ["FAKE_FFMPEG_LOG"])
    log.mkdir(parents=True, exist_ok=True)
    (log / "command.json").write_text(json.dumps(args, ensure_ascii=False, indent=1), encoding="utf-8")
    w, h = (int(v) for v in args[args.index("-s") + 1].split("x"))
    size, left = w * h * 3, 0
    with open(log / "frames.txt", "w", encoding="utf-8") as out:
        while True:
            fr = sys.stdin.buffer.read(size)
            if len(fr) < size:
                left = len(fr)
                break
            out.write(hashlib.sha256(fr).hexdigest() + "\n")
        out.write(f"left over: {left}\n")
    Path(args[-1]).write_bytes(b"fake film\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
