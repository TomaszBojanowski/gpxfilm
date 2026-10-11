"""A piece of work of the engine as its caller sees it: the events it reports (messages and progress), the way to stop it,
and the error it ends with when it cannot go on. A light module, like settings.py.

The engine does not print and does not end the program. It reports to the task it runs in (see running), and whoever
started the task decides what to do with the events: the command prints them, a window shows them.
"""
from __future__ import annotations

import contextlib
import contextvars
import sys
import threading
import time
from dataclasses import dataclass

STAGES = ("terrain", "map", "osm", "background", "frames", "encoding")   # what a longer activity is busy with


class Failure(Exception):
    """The work cannot go on, for a reason to tell the user: the message is written for them."""


class Cancelled(BaseException):
    """The work was stopped on request. Not an Exception, so that code that carries on after any error does not swallow it."""


@dataclass(frozen=True)
class Message:
    """A line for the user: what the program is doing, a warning, a result."""
    text: str


@dataclass(frozen=True)
class Progress:
    """How far a longer activity is: its stage (one of STAGES), how many of how many steps are done (tiles, frames; total 0
    when the length is not known) and a short description."""
    stage: str
    done: int = 0
    total: int = 0
    text: str = ""

    @property
    def fraction(self) -> float | None:
        return self.done / self.total if self.total else None


class Task:
    """One piece of work: where its events go and whether it has been asked to stop."""

    def __init__(self, report=None, stop: threading.Event | None = None):
        self.report = report or _to_stderr
        self.stop = stop or threading.Event()

    def cancel(self) -> None:
        self.stop.set()


def _to_stderr(event) -> None:
    if isinstance(event, Message):
        print(event.text, file=sys.stderr, flush=True)


class Console:
    """What the command does with the events: messages go to standard error, and so does the progress of the frames, as a
    percentage about 25 times in a film."""

    def __init__(self):
        self.start = None

    def __call__(self, event) -> None:
        if isinstance(event, Message):
            print(event.text, file=sys.stderr, flush=True)
        elif event.stage == "frames" and event.total and event.done % max(1, event.total // 25) == 0:
            self.start = self.start or time.time()
            print(f"  {100 * event.done // event.total:3d}%  ({time.time() - self.start:.0f} s)", file=sys.stderr, flush=True)


_task: contextvars.ContextVar[Task | None] = contextvars.ContextVar("gpxfilm_task", default=None)
_NO_TASK = Task()                        # outside any task: messages go to standard error, nothing asks to stop


@contextlib.contextmanager
def running(task: Task):
    """Run the engine inside task: its events go to the task, and check() stops it when the task is cancelled."""
    token = _task.set(task)
    try:
        yield task
    finally:
        _task.reset(token)


@contextlib.contextmanager
def muted():
    """Run a block whose messages and progress nobody gets; the task around it still stops it."""
    with running(Task(lambda event: None, (_task.get() or _NO_TASK).stop)) as task:
        yield task


def log(text: str) -> None:
    (_task.get() or _NO_TASK).report(Message(text))


def progress(stage: str, done: int = 0, total: int = 0, text: str = "") -> None:
    (_task.get() or _NO_TASK).report(Progress(stage, done, total, text))


def check() -> None:
    """Stop here if the task was cancelled. Called at every tile and every frame."""
    if (_task.get() or _NO_TASK).stop.is_set():
        raise Cancelled()


def rest(seconds: float) -> None:
    """Wait seconds, or stop at once when the task is cancelled in that time. Every pause of the engine is taken here, so
    that none of them (up to 30 s when a server asks for it) holds a stop back."""
    if (_task.get() or _NO_TASK).stop.wait(seconds):
        raise Cancelled()
