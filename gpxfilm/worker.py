"""The engine as a process of its own, for a window: python -m gpxfilm.worker.

The window never runs the engine itself. It starts this process, writes what it asks for to its standard input and reads events
from its standard output, one JSON object in a line each way. A window that has to get rid of a job at once (a download
may wait for a server for over a minute) ends the process and starts another: nothing is left half-written.

The first line the worker writes is {"event": "hello", "protocol": PROTOCOL, "version": ...}. PROTOCOL grows with every
change of the lines that the other side must know about.

Requests ("id" is any number the window chooses; the events of the job carry it):

    {"id": 1, "do": "preview", "settings": {...}, "width": 960, "height": 540, "file": "/tmp/x/preview.png"}
        A frame for the window: settings as Settings.to_dict gives them (their size does not count), drawn to fit a box of
        width x height pixels and saved as "file". Optional: "at" (seconds into the film; without it the last frame of
        the route), "report" (true: describe the film too).
    {"id": 2, "do": "export", "settings": {...}, "output": "/home/me/film.mp4"}
        The film. Optional: "slides" (a folder for the images for slides), "report" (true).
    {"do": "cancel"}                     stop the job that runs or waits ("id": only that one)
    {"do": "quit"}                       stop and end the process; the end of the input does the same

Events of a job:

    {"id": 1, "event": "message", "text": "..."}                                    a line for the user
    {"id": 1, "event": "progress", "stage": "frames", "done": 5, "total": 90, "text": ""}
    {"id": 1, "event": "frame", "file": "...", "width": 960, "height": 540, "rough": true}
    {"id": 1, "event": "report", "report": {...}}
    {"id": 1, "event": "done"}           the job ended well ("output": the film, for an export)
    {"id": 1, "event": "cancelled"}      it was stopped, or a newer preview took its place
    {"id": 1, "event": "failed", "text": "..."}                                     an error to tell the user
    {"id": 1, "event": "busy"}           not started: a film is being exported

Every job ends with exactly one of done, cancelled, failed and busy. A line that is not a request gets
{"event": "error", "text": ...}.

One job runs at a time. A preview takes the place of a preview that runs or waits: stale work is dropped, not finished. A
preview first gives a rough frame ("rough": true), made at once from what is at hand without any download, then the real
one. The frame is drawn in one of a few sizes (PREVIEW_SIZES, so that the stored tiles and backgrounds serve windows of
any size) and scaled down to the box. An export is not replaced: until it ends or is cancelled, other jobs get "busy".
Progress comes at most about 20 times a second.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from . import __version__
from .settings import Settings
from .task import Cancelled, Failure, Message, Progress, Task, running

PROTOCOL = 1
PREVIEW_SIZES = ((640, 360), (960, 540), (1280, 720), (1920, 1080))   # a preview is drawn in the smallest that fills the box
PROGRESS_GAP = 0.05                      # s between two events of progress of one stage


def preview_sizes(width: int, height: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """For a box of width x height pixels: the size to draw the frame in and the size to give it to the window in, the
    largest 16:9 frame that fits the box and is not larger than the drawn one."""
    w = max(16, min(int(width), int(height) * 16 // 9))
    drawn = next((s for s in PREVIEW_SIZES if s[0] >= w), PREVIEW_SIZES[-1])
    w = min(w, drawn[0])
    return drawn, (w, max(9, round(w * 9 / 16)))


class Server:
    """The worker's side: reads from inp what the window asks for, runs the jobs in a thread of their own and writes the events to out."""

    def __init__(self, inp, out):
        self.inp, self.out = inp, out
        self.writing, self.lock = threading.Lock(), threading.Condition()
        self.waiting: dict | None = None             # the job to run next
        self.job: dict | None = None                 # the job that runs
        self.task: Task | None = None
        self.closing = False
        self.project = None

    def send(self, event: str, id=None, **fields) -> None:
        line = json.dumps({**({} if id is None else {"id": id}), "event": event, **fields}, ensure_ascii=False)
        with self.writing:
            try:
                self.out.write(line + "\n")
                self.out.flush()
            except (OSError, ValueError):            # nobody reads any more
                pass

    def serve(self) -> None:
        self.send("hello", protocol=PROTOCOL, version=__version__)
        worker = threading.Thread(target=self.work, daemon=True)
        worker.start()
        for line in self.inp:
            if not line.strip():
                continue
            try:
                req = json.loads(line)
                do = req["do"]
            except (ValueError, TypeError, KeyError):
                self.send("error", text="not a request: " + line.strip()[:200])
                continue
            if do == "quit":
                break
            if do == "cancel":
                self.cancel(req.get("id"))
            elif do in ("preview", "export"):
                self.take(req)
            else:
                self.send("error", id=req.get("id"), text=f"unknown request: {do}")
        with self.lock:
            self.closing = True
            self.lock.notify()
        self.cancel(None)
        worker.join(5)                               # a job that waits for a server does not hold the end back

    def take(self, req: dict) -> None:
        with self.lock:
            if any(j is not None and j["do"] == "export" for j in (self.job, self.waiting)):
                return self.send("busy", id=req.get("id"))
            if self.waiting is not None:
                self.send("cancelled", id=self.waiting.get("id"))
            self.waiting = req
            if self.task is not None:
                self.task.cancel()
            self.lock.notify()

    def cancel(self, id) -> None:
        with self.lock:
            if self.waiting is not None and id in (None, self.waiting.get("id")):
                self.send("cancelled", id=self.waiting.get("id"))
                self.waiting = None
            if self.task is not None and id in (None, self.job.get("id")):
                self.task.cancel()

    def work(self) -> None:
        from . import engine                          # the heavy libraries load here, after the hello
        self.project = engine.Project()
        while True:
            with self.lock:
                while self.waiting is None and not self.closing:
                    self.lock.wait()
                if self.closing:
                    return
                req, self.waiting = self.waiting, None
                self.job, self.task = req, Task(self.reporter(req.get("id")))
            try:
                with running(self.task):
                    end = "done", (self.preview if req["do"] == "preview" else self.export)(engine, req)
            except Cancelled:
                end = "cancelled", {}
            except Failure as e:
                end = "failed", {"text": str(e)}
            except Exception as e:  # noqa: BLE001 – the worker goes on with the next job
                import traceback
                end = "failed", {"text": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
            with self.lock:                           # a job that a newer one overtook at its very end still ends as it did
                self.job = self.task = None
            self.send(end[0], id=req.get("id"), **end[1])

    def reporter(self, id):
        last = [None, 0.0]                            # the stage and the time of the last progress sent

        def report(event) -> None:
            if isinstance(event, Message):
                self.send("message", id=id, text=event.text)
            elif isinstance(event, Progress):
                now = time.monotonic()
                if event.stage != last[0] or now - last[1] >= PROGRESS_GAP or (event.total and event.done + 1 >= event.total):
                    last[:] = event.stage, now
                    self.send("progress", id=id, stage=event.stage, done=event.done, total=event.total, text=event.text)
        return report

    @staticmethod
    def settings(req: dict, **changes) -> Settings:
        S = replace(Settings.from_dict(req["settings"]), **changes)
        if S.gpx is None:
            raise ValueError("settings without a GPX file")
        wrong = S.problem()
        if wrong:
            raise Failure(wrong)
        return S

    def preview(self, engine, req: dict) -> dict:
        from PIL import Image
        drawn, shown = preview_sizes(req["width"], req["height"])
        S, file, at = self.settings(req, size=f"{drawn[0]}x{drawn[1]}"), Path(req["file"]), req.get("at")

        def show(frame, rough: bool) -> None:
            im = Image.fromarray(frame)
            if shown != drawn:
                im = im.resize(shown, Image.LANCZOS)
            part = file.with_name(f"{file.stem}.part{file.suffix}")
            im.save(part, compress_level=1)
            os.replace(part, file)                    # the window never reads half a picture
            self.send("frame", id=req.get("id"), file=str(file), width=shown[0], height=shown[1], rough=rough)

        if at is None:
            rough = engine.rough_still(self.project, S)
            if rough is not None:
                show(rough, True)
        p = engine.prepare(self.project, S, film=at is not None)
        show(engine.render_still(p) if at is None else engine.frame_at(p, float(at)), False)
        if req.get("report"):
            self.send("report", id=req.get("id"), report=engine.report(p))
        return {}

    def export(self, engine, req: dict) -> dict:
        S, out = self.settings(req), Path(req["output"])
        p = engine.prepare(self.project, S)
        if req.get("slides"):
            engine.save_slides(p, Path(req["slides"]))
        if p.settings.intro:
            engine.plan_flight(p)
        if req.get("report"):
            self.send("report", id=req.get("id"), report=engine.report(p))
        engine.render_film(p, out)
        return {"output": str(out)}


class Worker:
    """The window's side: a worker process, what is asked of it and its events, each given to on_event (a dict) in a
    thread of this object. When the process ends, on_event gets {"event": "gone", "code": its exit code}. What the process
    writes to standard error (ffmpeg about a film it cannot encode, say) goes where this program's does, or with on_error
    to that function, line by line."""

    def __init__(self, on_event, env: dict | None = None, on_error=None):
        self.on_event, self.next_id = on_event, 0
        self.proc = subprocess.Popen([sys.executable, "-m", "gpxfilm.worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE if on_error else None, text=True, encoding="utf-8", errors="replace", env=env)
        if on_error:
            threading.Thread(target=lambda: [on_error(line) for line in self.proc.stderr], daemon=True).start()
        try:
            hello = json.loads(self.proc.stdout.readline())
            if hello.get("event") != "hello" or hello.get("protocol") != PROTOCOL:
                raise ValueError(hello)
        except ValueError as e:
            self.kill()
            raise RuntimeError(f"the worker process does not speak protocol {PROTOCOL}: {e}") from None
        self.version = hello.get("version")
        threading.Thread(target=self.read, daemon=True).start()

    def read(self) -> None:
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            self.on_event(event)
        self.on_event({"event": "gone", "code": self.proc.wait()})

    def request(self, do: str, **fields) -> int | None:
        """Send a request; for a job, the id its events will carry."""
        id = None
        if do in ("preview", "export"):
            self.next_id += 1
            id = fields["id"] = self.next_id
        try:
            self.proc.stdin.write(json.dumps({"do": do, **fields}, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):                # the process is gone: on_event has heard, or will
            pass
        return id

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self, wait: float = 5.0) -> None:
        """Ask the worker to stop and end; end it by force when it does not in wait seconds."""
        self.request("quit")
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(wait)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self) -> None:
        """End the process at once, whatever it is doing. Safe at any moment: the cache and the film are written whole or
        not at all."""
        self.proc.kill()
        self.proc.wait()


def main() -> None:
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")   # the events alone go to the pipe:
    os.dup2(2, 1)                                     # whatever else writes to standard output (a library, ffmpeg) lands on standard error
    sys.stdout = sys.stderr
    inp = open(0, encoding="utf-8", closefd=False)
    Server(inp, out).serve()
    out.flush()
    os._exit(0)                                       # at once, also when a job still waits for a server


if __name__ == "__main__":
    main()
