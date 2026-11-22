"""Sessions: marks per phone, clip slicing, media position estimates."""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .capture import Capture
from .clips import clip_basename, slugify, write_clip
from .ringbuffer import RingBuffer


class SessionError(Exception):
    """A problem to show the person on the page."""


def parse_position(text: str) -> float:
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
        raise ValueError(f"Not a position: {text!r} (use H:MM:SS, MM:SS or seconds)")
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return float(total)


def format_position(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


@dataclass
class Pending:
    pcm: bytes
    truncated: bool
    marked_wall: float
    saved_wall: float


@dataclass
class Session:
    title: str
    meta: dict
    started: float
    buffer: RingBuffer
    capture: object
    sync_position: float | None = None
    sync_wall: float | None = None
    marks: dict = field(default_factory=dict)      # client -> (frame, wall)
    pending: dict = field(default_factory=dict)    # client -> Pending


class Manager:
    def __init__(self, outdir: Path, capture_command: list[str], rate: int = 48000,
                 channels: int = 2, buffer_seconds: float = 600,
                 preroll: float = 5.0, postroll: float = 1.0,
                 limit_hours: float = 4.0, capture_factory=Capture,
                 clock=time.time, sleep=time.sleep):
        self.outdir = Path(outdir)
        self.capture_command = capture_command
        self.rate = rate
        self.channels = channels
        self.frame_bytes = 2 * channels
        self.buffer_seconds = buffer_seconds
        self.preroll = float(preroll)
        self.postroll = float(postroll)
        self.limit_hours = limit_hours
        self._factory = capture_factory
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self.session: Session | None = None

    def _require(self) -> Session:
        if self.session is None:
            raise SessionError("No session running.")
        return self.session

    def start(self, title: str, meta: dict | None = None,
              position: str | None = None) -> None:
        with self._lock:
            if self.session is not None:
                raise SessionError("A session is already running.")
            title = title.strip()
            if not title:
                raise SessionError("Title is required.")
            pos = parse_position(position) if position and position.strip() else None
            buffer = RingBuffer(int(self.buffer_seconds * self.rate), self.frame_bytes)
            capture = self._factory(self.capture_command, buffer)
            capture.start()
            now = self._clock()
            self.session = Session(
                title=title, meta={k: v for k, v in (meta or {}).items() if v},
                started=now, buffer=buffer, capture=capture, sync_position=pos,
                sync_wall=now if pos is not None else None)

    def sync(self, position: str) -> None:
        with self._lock:
            s = self._require()
            s.sync_position = parse_position(position)
            s.sync_wall = self._clock()

    def mark(self, client: str) -> None:
        with self._lock:
            s = self._require()
            s.pending.pop(client, None)
            s.marks[client] = (s.buffer.total_frames, self._clock())

    def end(self, client: str) -> None:
        with self._lock:
            s = self._require()
            if client not in s.marks:
                raise SessionError("Tap Mark first.")
            frame, marked_wall = s.marks.pop(client)
            saved_wall = self._clock()
            save_frame = s.buffer.total_frames
        self._sleep(self.postroll)
        with self._lock:
            if self.session is not s:
                raise SessionError("The session ended before the clip was saved.")
            start = frame - int(self.preroll * self.rate)
            end = min(s.buffer.total_frames, save_frame + int(self.postroll * self.rate))
            pcm, _, truncated = s.buffer.slice(start, end)
            s.pending[client] = Pending(pcm, truncated, marked_wall, saved_wall)

    def finish(self, client: str, text: str, tags: list[str]) -> Path:
        with self._lock:
            s = self._require()
            text = text.strip()
            if not text:
                raise SessionError("Text is required.")
            p = s.pending.get(client)
            if p is None:
                raise SessionError("Nothing to keep: tap Mark, then Save.")
            sidecar = {
                "session": {"title": s.title, "started": _iso(s.started), "meta": s.meta},
                "marked": _iso(p.marked_wall),
                "saved": _iso(p.saved_wall),
                "preroll_s": self.preroll,
                "postroll_s": self.postroll,
            }
            if s.sync_position is not None:
                clip_start = p.marked_wall - self.preroll
                sidecar["media_position"] = format_position(
                    max(0.0, s.sync_position + clip_start - s.sync_wall))
            sidecar.update(text=text, tags=tags, truncated=p.truncated)
            date = datetime.fromtimestamp(p.marked_wall).strftime("%Y-%m-%d")
            basename = clip_basename(self.outdir, date, slugify(s.title))
            path = write_clip(self.outdir, basename, p.pcm, self.rate,
                              self.channels, sidecar)
            del s.pending[client]
            return path

    def discard(self, client: str) -> None:
        with self._lock:
            s = self._require()
            s.marks.pop(client, None)
            s.pending.pop(client, None)

    def stop(self) -> None:
        with self._lock:
            if self.session is not None:
                self.session.capture.stop()
                self.session = None

    def restart_capture(self) -> None:
        with self._lock:
            s = self._require()
            s.capture.stop()
            s.capture = self._factory(self.capture_command, s.buffer)
            s.capture.start()

    def check_limit(self) -> bool:
        with self._lock:
            s = self.session
            if s is not None and self._clock() - s.started > self.limit_hours * 3600:
                self.stop()
                return True
            return False

    def status(self) -> dict:
        with self._lock:
            s = self.session
            if s is None:
                return {"active": False, "title": None,
                        "capture_alive": False, "position": None}
            position = None
            if s.sync_position is not None:
                position = format_position(s.sync_position + self._clock() - s.sync_wall)
            return {"active": True, "title": s.title,
                    "capture_alive": s.capture.alive, "position": position}

    def marked_at(self, client: str) -> str | None:
        with self._lock:
            s = self.session
            if s is None or client not in s.marks:
                return None
            return datetime.fromtimestamp(s.marks[client][1]).strftime("%H:%M:%S")

    def pending_info(self, client: str) -> dict | None:
        with self._lock:
            s = self.session
            if s is None or client not in s.pending:
                return None
            p = s.pending[client]
            secs = len(p.pcm) / self.frame_bytes / self.rate
            return {"seconds": round(secs, 1), "truncated": p.truncated}
