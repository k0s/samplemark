# samplemark Implementation Plan

> **For agentic workers:** implement this plan task by task, in order. Steps use
> checkbox (`- [ ]`) syntax — tick them as you go so a resumed session can tell what
> is done. Commit at the end of each task.

**Goal:** A single Python service that keeps a rolling recording of a Linux
machine's audio output and lets phones on the LAN mark, save and describe clips.

**Architecture:** `pw-record` streams raw PCM into an in-memory ring buffer; a
session manager tracks marks per phone and slices the buffer on Save; a FastAPI app
serves one server-rendered page; clips are written atomically as WAV + JSON.

**Tech Stack:** Python ≥ 3.12, FastAPI (`fastapi[standard]`, which brings uvicorn and
python-multipart), Jinja2, stdlib `wave`, pytest + httpx for tests, PipeWire's
`pw-record` at run time.

**Spec:** [design.md](design.md)

## Global Constraints

- Audio: s16le, 48 kHz, stereo from capture; WAV written in the same format.
- Defaults: buffer 600 s, pre-roll 5 s, post-roll 1 s, session limit 4 h, port 8470,
  listen address `127.0.0.1` (deployment sets the LAN address).
- Clip names: `<YYYY-MM-DD>_<session-slug>_<NNN>.wav` and `.json`, written via a
  temporary dot-file and `os.replace`.
- Text is required when keeping a clip.
- No authentication in this version; no config file (flags + `SAMPLEMARK_*` env vars).
- Packaging with `setup.py` (no `pyproject.toml`).
- Tests never need PipeWire: capture is faked or replaced by a Python command.

## Files

```
setup.py                     packaging, entry point
.gitignore
samplemark/__init__.py
samplemark/__main__.py       python -m samplemark
samplemark/ringbuffer.py     RingBuffer: fixed-size PCM buffer indexed by frame count
samplemark/clips.py          slugify, clip_basename, write_clip (WAV + JSON, atomic)
samplemark/capture.py        Capture: runs the capture command, feeds the buffer
samplemark/session.py        positions, Session, Manager (start/sync/mark/end/finish/stop)
samplemark/app.py            create_app(manager): routes + cookies
samplemark/templates/page.html
samplemark/cli.py            flags/env → Manager → uvicorn
tests/conftest.py            FakeCapture, FakeClock, helpers
tests/test_ringbuffer.py
tests/test_clips.py
tests/test_capture.py
tests/test_session.py
tests/test_app.py
tests/test_cli.py
docs/samplemark.service      example systemd user unit
```

## Precondition

The capture proof test on the media PC must pass first: while a film streams in
Firefox, `pw-record -P '{ stream.capture.sink=true }' /tmp/test.wav` for ~10 s plays
back with the film's audio. If it records silence, stop and revisit the design.

---

### Task 1: Scaffold and ring buffer

**Files:**
- Create: `setup.py`, `.gitignore`, `samplemark/__init__.py`, `samplemark/ringbuffer.py`
- Test: `tests/test_ringbuffer.py`

**Interfaces:**
- Produces: `RingBuffer(capacity_frames: int, frame_bytes: int)` with
  `write(data: bytes) -> None`, `total_frames: int` (frames ever written),
  `oldest_frame: int` (property), and
  `slice(start: int, end: int) -> tuple[bytes, int, bool]` returning
  `(pcm, actual_start_frame, truncated)`.

- [ ] **Step 1: Scaffold**

`setup.py`:

```python
from setuptools import find_packages, setup

setup(
    name="samplemark",
    version="0.1.0",
    description="Mark and save audio samples from a Linux machine's audio output",
    license="MIT",
    packages=find_packages(exclude=["tests"]),
    package_data={"samplemark": ["templates/*.html"]},
    python_requires=">=3.12",
    install_requires=["fastapi[standard]>=0.119", "jinja2>=3.1"],
    extras_require={"test": ["pytest", "httpx"]},
    entry_points={"console_scripts": ["samplemark=samplemark.cli:main"]},
)
```

`.gitignore`:

```
.venv/
__pycache__/
*.egg-info/
```

`samplemark/__init__.py`:

```python
"""Mark and save audio samples from a Linux machine's audio output."""
```

Run: `python3 -m venv .venv && .venv/bin/pip install -e '.[test]'`
Expected: installs without error.

- [ ] **Step 2: Write the failing tests**

`tests/test_ringbuffer.py`:

```python
from samplemark.ringbuffer import RingBuffer


def frames(start, count):
    """`count` 4-byte frames whose first byte encodes their index mod 256."""
    return b"".join(bytes([(start + i) % 256, 0, 0, 0]) for i in range(count))


def test_write_and_slice_without_wrap():
    buf = RingBuffer(capacity_frames=10, frame_bytes=4)
    buf.write(frames(0, 6))
    assert buf.total_frames == 6
    assert buf.slice(2, 5) == (frames(2, 3), 2, False)


def test_wraps_and_keeps_newest():
    buf = RingBuffer(capacity_frames=10, frame_bytes=4)
    buf.write(frames(0, 8))
    buf.write(frames(8, 7))
    assert buf.total_frames == 15
    assert buf.oldest_frame == 5
    assert buf.slice(5, 15) == (frames(5, 10), 5, False)


def test_slice_before_oldest_is_truncated():
    buf = RingBuffer(capacity_frames=10, frame_bytes=4)
    buf.write(frames(0, 15))
    pcm, start, truncated = buf.slice(0, 12)
    assert (pcm, start, truncated) == (frames(5, 7), 5, True)


def test_slice_end_clamped_to_written():
    buf = RingBuffer(capacity_frames=10, frame_bytes=4)
    buf.write(frames(0, 4))
    assert buf.slice(1, 99) == (frames(1, 3), 1, False)


def test_partial_frames_are_held_until_complete():
    buf = RingBuffer(capacity_frames=10, frame_bytes=4)
    data = frames(0, 3)
    buf.write(data[:5])
    assert buf.total_frames == 1
    buf.write(data[5:])
    assert buf.total_frames == 3
    assert buf.slice(0, 3)[0] == data


def test_single_write_larger_than_capacity():
    buf = RingBuffer(capacity_frames=4, frame_bytes=4)
    buf.write(frames(0, 9))
    assert buf.total_frames == 9
    assert buf.slice(0, 9) == (frames(5, 4), 5, True)
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_ringbuffer.py -v`
Expected: FAIL (`ModuleNotFoundError: samplemark.ringbuffer`).

- [ ] **Step 4: Implement**

`samplemark/ringbuffer.py`:

```python
"""A fixed-size in-memory PCM buffer indexed by a running frame count."""

import threading


class RingBuffer:
    def __init__(self, capacity_frames: int, frame_bytes: int):
        self.capacity = capacity_frames
        self.frame_bytes = frame_bytes
        self._buf = bytearray(capacity_frames * frame_bytes)
        self._partial = b""
        self._lock = threading.Lock()
        self.total_frames = 0

    @property
    def oldest_frame(self) -> int:
        return max(0, self.total_frames - self.capacity)

    def write(self, data: bytes) -> None:
        data = self._partial + data
        whole = len(data) // self.frame_bytes * self.frame_bytes
        self._partial = data[whole:]
        count = whole // self.frame_bytes
        if count == 0:
            return
        data = data[:whole]
        with self._lock:
            if count > self.capacity:
                skipped = count - self.capacity
                data = data[skipped * self.frame_bytes:]
                self.total_frames += skipped
                count = self.capacity
            start = (self.total_frames % self.capacity) * self.frame_bytes
            first = min(len(data), len(self._buf) - start)
            self._buf[start:start + first] = data[:first]
            self._buf[:len(data) - first] = data[first:]
            self.total_frames += count

    def slice(self, start: int, end: int) -> tuple[bytes, int, bool]:
        with self._lock:
            oldest = self.oldest_frame
            truncated = start < oldest
            start = max(start, oldest)
            end = min(end, self.total_frames)
            if end <= start:
                return b"", start, truncated
            offset = (start % self.capacity) * self.frame_bytes
            length = (end - start) * self.frame_bytes
            if offset + length <= len(self._buf):
                pcm = bytes(self._buf[offset:offset + length])
            else:
                head = self._buf[offset:]
                pcm = bytes(head + self._buf[:length - len(head)])
            return pcm, start, truncated
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_ringbuffer.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add setup.py .gitignore samplemark tests
git commit -m "Ring buffer and package scaffold"
```

---

### Task 2: Clip writer

**Files:**
- Create: `samplemark/clips.py`
- Test: `tests/test_clips.py`

**Interfaces:**
- Produces: `slugify(text: str) -> str`;
  `clip_basename(outdir: Path, date: str, slug: str) -> str` (next free
  `<date>_<slug>_<NNN>`); `write_clip(outdir: Path, basename: str, pcm: bytes,
  rate: int, channels: int, sidecar: dict) -> Path` (returns the WAV path).

- [ ] **Step 1: Write the failing tests**

`tests/test_clips.py`:

```python
import json
import wave

from samplemark.clips import clip_basename, slugify, write_clip


def test_slugify():
    assert slugify("The Shining (1980)") == "the-shining-1980"
    assert slugify("  ¡¿  ") == "untitled"
    assert len(slugify("x" * 100)) == 40


def test_clip_basename_counts_existing(tmp_path):
    assert clip_basename(tmp_path, "2026-11-17", "alien") == "2026-11-17_alien_001"
    (tmp_path / "2026-11-17_alien_001.wav").touch()
    (tmp_path / "2026-11-17_alien_007.wav").touch()
    assert clip_basename(tmp_path, "2026-11-17", "alien") == "2026-11-17_alien_008"


def test_write_clip(tmp_path):
    out = tmp_path / "inbox"
    pcm = b"\x01\x00\x02\x00" * 100
    path = write_clip(out, "2026-11-17_alien_001", pcm, 100, 2, {"text": "hi"})
    assert path == out / "2026-11-17_alien_001.wav"
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, 100)
        assert w.readframes(w.getnframes()) == pcm
    assert json.loads((out / "2026-11-17_alien_001.json").read_text()) == {"text": "hi"}
    assert sorted(p.name for p in out.iterdir()) == [
        "2026-11-17_alien_001.json", "2026-11-17_alien_001.wav"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_clips.py -v`
Expected: FAIL (`ModuleNotFoundError: samplemark.clips`).

- [ ] **Step 3: Implement**

`samplemark/clips.py`:

```python
"""Writing clips: a WAV and a JSON sidecar, each written atomically."""

import json
import os
import re
import wave
from pathlib import Path


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")
    return slug or "untitled"


def clip_basename(outdir: Path, date: str, slug: str) -> str:
    prefix = f"{date}_{slug}_"
    numbers = [0]
    if outdir.is_dir():
        for path in outdir.glob(f"{prefix}*.wav"):
            tail = path.stem[len(prefix):]
            if tail.isdigit():
                numbers.append(int(tail))
    return f"{prefix}{max(numbers) + 1:03d}"


def write_clip(outdir: Path, basename: str, pcm: bytes, rate: int,
               channels: int, sidecar: dict) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    wav_path = outdir / f"{basename}.wav"
    tmp = outdir / f".{basename}.wav.tmp"
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.replace(tmp, wav_path)
    json_tmp = outdir / f".{basename}.json.tmp"
    json_tmp.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n")
    os.replace(json_tmp, outdir / f"{basename}.json")
    return wav_path
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_clips.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add samplemark/clips.py tests/test_clips.py
git commit -m "Clip writer: atomic WAV and JSON sidecar"
```

---

### Task 3: Capture

**Files:**
- Create: `samplemark/capture.py`
- Test: `tests/test_capture.py`

**Interfaces:**
- Consumes: `RingBuffer` (Task 1).
- Produces: `DEFAULT_CAPTURE: str` (shell-style command string);
  `Capture(command: list[str], buffer: RingBuffer, chunk_bytes: int = 19200)` with
  `start() -> None`, `stop() -> None`, `alive: bool` (property).
  Any object with `start`, `stop` and `alive` can stand in (tests use a fake).
  Only an exited command counts as down; a silent stream is not flagged, because a
  suspended sink (nothing playing) legitimately sends nothing. This narrows the
  design's "exits or stalls".

- [ ] **Step 1: Write the failing tests**

`tests/test_capture.py`:

```python
import sys
import time

from samplemark.capture import DEFAULT_CAPTURE, Capture
from samplemark.ringbuffer import RingBuffer


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_reads_command_output_into_buffer():
    payload = bytes(range(256)) * 40
    command = [sys.executable, "-c",
               f"import sys; sys.stdout.buffer.write(bytes(range(256)) * 40)"]
    buf = RingBuffer(capacity_frames=10000, frame_bytes=4)
    cap = Capture(command, buf)
    cap.start()
    assert wait_for(lambda: buf.total_frames == len(payload) // 4)
    assert buf.slice(0, buf.total_frames)[0] == payload
    assert wait_for(lambda: not cap.alive)


def test_stop_terminates_long_running_command():
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    cap = Capture(command, RingBuffer(capacity_frames=10, frame_bytes=4))
    cap.start()
    assert cap.alive
    cap.stop()
    assert not cap.alive


def test_default_command_records_sink_monitor_raw():
    assert DEFAULT_CAPTURE.startswith("pw-record --raw")
    assert "stream.capture.sink=true" in DEFAULT_CAPTURE
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_capture.py -v`
Expected: FAIL (`ModuleNotFoundError: samplemark.capture`).

- [ ] **Step 3: Implement**

`samplemark/capture.py`:

```python
"""Run the capture command and feed its raw PCM into a RingBuffer."""

import subprocess
import threading

from .ringbuffer import RingBuffer

DEFAULT_CAPTURE = ("pw-record --raw --format s16 --rate 48000 --channels 2 "
                   "-P '{ stream.capture.sink=true }' -")


class Capture:
    def __init__(self, command: list[str], buffer: RingBuffer,
                 chunk_bytes: int = 19200):
        self.command = command
        self.buffer = buffer
        self.chunk_bytes = chunk_bytes
        self._proc = None
        self._thread = None

    def start(self) -> None:
        self._proc = subprocess.Popen(self.command, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        stream = self._proc.stdout
        while True:
            data = stream.read1(self.chunk_bytes)
            if not data:
                break
            self.buffer.write(data)

    @property
    def alive(self) -> bool:
        return (self._proc is not None and self._proc.poll() is None
                and self._thread is not None and self._thread.is_alive())

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        if self._thread is not None:
            self._thread.join(timeout=2)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_capture.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add samplemark/capture.py tests/test_capture.py
git commit -m "Capture: stream the capture command into the ring buffer"
```

---

### Task 4: Session manager

**Files:**
- Create: `samplemark/session.py`, `tests/conftest.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `RingBuffer` (Task 1); `slugify`, `clip_basename`, `write_clip`
  (Task 2); `Capture` (Task 3) as the default `capture_factory`.
- Produces:
  - `parse_position(text: str) -> float` (accepts `H:MM:SS`, `MM:SS`, `SS`; raises
    `ValueError`); `format_position(seconds: float) -> str` (`H:MM:SS`).
  - `SessionError(Exception)` — user-facing messages.
  - `Manager(outdir: Path, capture_command: list[str], rate: int = 48000,
    channels: int = 2, buffer_seconds: float = 600, preroll: float = 5.0,
    postroll: float = 1.0, limit_hours: float = 4.0, capture_factory=Capture,
    clock=time.time, sleep=time.sleep)` with methods
    `start(title: str, meta: dict | None = None, position: str | None = None)`,
    `sync(position: str)`, `mark(client: str)`, `end(client: str)`,
    `finish(client: str, marked_by: str, text: str, tags: list[str]) -> Path`,
    `discard(client: str)`, `stop()`, `restart_capture()`,
    `check_limit() -> bool`, `status() -> dict` (keys `active`, `title`,
    `capture_alive`, `position`), `marked_at(client: str) -> str | None`,
    `pending_info(client: str) -> dict | None` (keys `seconds`, `truncated`),
    and attribute `session` (`None` when idle; has `.buffer`, `.capture`).

- [ ] **Step 1: Test helpers**

`tests/conftest.py`:

```python
import pytest

from samplemark.session import Manager

RATE = 100          # frames per second in tests, to keep buffers tiny
FRAME = 4           # s16 stereo


class FakeCapture:
    def __init__(self, command, buffer):
        self.buffer = buffer
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    @property
    def alive(self):
        return self.started and not self.stopped


class FakeClock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def seconds(n):
    """n seconds of test audio."""
    return b"\x01\x00\x01\x00" * int(n * RATE)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_manager(tmp_path, clock):
    def make(**overrides):
        options = dict(outdir=tmp_path / "inbox", capture_command=["unused"],
                       rate=RATE, channels=2, capture_factory=FakeCapture,
                       clock=clock, sleep=lambda s: None)
        options.update(overrides)
        return Manager(**options)
    return make
```

- [ ] **Step 2: Write the failing tests**

`tests/test_session.py`:

```python
import json
import wave
from datetime import datetime

import pytest

from conftest import RATE, seconds
from samplemark.session import SessionError, format_position, parse_position


def test_positions():
    assert parse_position("1:02:03") == 3723
    assert parse_position("12:30") == 750
    assert parse_position(" 45 ") == 45
    with pytest.raises(ValueError):
        parse_position("soon")
    assert format_position(3723.9) == "1:02:03"


def test_full_clip(make_manager, clock, tmp_path):
    m = make_manager()
    m.start("Alien", {"year": "1979", "notes": ""})
    assert m.session.capture.started
    m.session.buffer.write(seconds(10))
    m.mark("phone-a")
    assert m.marked_at("phone-a") is not None
    m.session.buffer.write(seconds(3))
    m.end("phone-a")
    assert m.pending_info("phone-a") == {"seconds": 8.0, "truncated": False}
    path = m.finish("phone-a", "J", "In space no one can hear you scream", ["eerie"])
    date = datetime.fromtimestamp(clock.t).strftime("%Y-%m-%d")
    assert path == tmp_path / "inbox" / f"{date}_alien_001.wav"
    with wave.open(str(path)) as w:
        assert w.getnframes() == 8 * RATE
    sidecar = json.loads(path.with_suffix(".json").read_text())
    assert sidecar["session"]["title"] == "Alien"
    assert sidecar["session"]["meta"] == {"year": "1979"}
    assert sidecar["marked_by"] == "J"
    assert sidecar["text"] == "In space no one can hear you scream"
    assert sidecar["tags"] == ["eerie"]
    assert sidecar["truncated"] is False
    assert (sidecar["preroll_s"], sidecar["postroll_s"]) == (5.0, 1.0)
    assert "media_position" not in sidecar
    assert m.pending_info("phone-a") is None


def test_marks_are_per_phone(make_manager):
    m = make_manager()
    m.start("Alien")
    m.mark("phone-a")
    assert m.marked_at("phone-b") is None
    with pytest.raises(SessionError, match="Mark first"):
        m.end("phone-b")


def test_truncated_when_mark_left_buffer(make_manager):
    m = make_manager(buffer_seconds=3)
    m.start("Alien")
    m.session.buffer.write(seconds(10))
    m.mark("a")
    m.session.buffer.write(seconds(3))
    m.end("a")
    assert m.pending_info("a") == {"seconds": 3.0, "truncated": True}


def test_media_position_from_sync(make_manager, clock):
    m = make_manager()
    clock.t = 1000.0
    m.start("Alien", position="0:10:00")
    clock.t = 1100.0
    m.mark("a")
    m.end("a")
    path = m.finish("a", "J", "line", [])
    assert json.loads(path.with_suffix(".json").read_text())["media_position"] == "0:11:35"
    clock.t = 2000.0
    m.sync("1:00:00")
    clock.t = 2010.0
    assert m.status()["position"] == "1:00:10"


def test_text_required_and_write_failure_keeps_clip(make_manager, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    m = make_manager(outdir=blocker)
    m.start("Alien")
    m.session.buffer.write(seconds(2))
    m.mark("a")
    m.end("a")
    with pytest.raises(SessionError, match="Text is required"):
        m.finish("a", "J", "  ", [])
    with pytest.raises(OSError):
        m.finish("a", "J", "line", [])
    assert m.pending_info("a") is not None


def test_one_session_at_a_time_and_title_required(make_manager):
    m = make_manager()
    with pytest.raises(SessionError, match="Title is required"):
        m.start("  ")
    m.start("Alien")
    with pytest.raises(SessionError, match="already running"):
        m.start("Aliens")


def test_stop_and_limit(make_manager, clock):
    m = make_manager(limit_hours=1)
    m.start("Alien")
    capture = m.session.capture
    assert m.check_limit() is False
    clock.t += 3601
    assert m.check_limit() is True
    assert m.session is None and capture.stopped
    with pytest.raises(SessionError, match="No session"):
        m.mark("a")
    assert m.status() == {"active": False, "title": None,
                          "capture_alive": False, "position": None}


def test_restart_capture_keeps_buffer(make_manager):
    m = make_manager()
    m.start("Alien")
    m.session.buffer.write(seconds(1))
    old = m.session.capture
    old.stop()
    assert m.status()["capture_alive"] is False
    m.restart_capture()
    assert m.session.capture is not old and m.session.capture.alive
    assert m.session.buffer.total_frames == RATE


def test_discard(make_manager):
    m = make_manager()
    m.start("Alien")
    m.mark("a")
    m.discard("a")
    assert m.marked_at("a") is None
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: FAIL (`ModuleNotFoundError: samplemark.session`).

- [ ] **Step 4: Implement**

`samplemark/session.py`:

```python
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

    def finish(self, client: str, marked_by: str, text: str,
               tags: list[str]) -> Path:
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
                "marked_by": marked_by,
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
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_session.py -v`
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add samplemark/session.py tests/conftest.py tests/test_session.py
git commit -m "Session manager: marks per phone, clip slicing, positions"
```

---

### Task 5: Web app and phone page

**Files:**
- Create: `samplemark/app.py`, `samplemark/templates/page.html`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `Manager`, `SessionError` (Task 4).
- Produces: `create_app(manager: Manager) -> FastAPI`. Routes: `GET /`;
  `POST /name` (`name`), `/start` (`title`, `year`, `kind`, `notes`, `position`),
  `/sync` (`position`), `/mark`, `/end`, `/finish` (`text`, `tags`), `/discard`,
  `/stop`, `/restart`. Every POST redirects (303) to `/?msg=…` or `/?error=…`.
  Cookies: `samplemark_client` (random id, set by `GET /`), `samplemark_name`.

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`:

```python
from fastapi.testclient import TestClient

from conftest import seconds
from samplemark.app import create_app


def test_full_flow(make_manager, tmp_path):
    m = make_manager()
    client = TestClient(create_app(m))
    page = client.get("/")
    assert "Your name" in page.text
    assert "samplemark_client" in client.cookies
    assert "Start session" in client.post("/name", data={"name": "J"}).text
    page = client.post("/start", data={"title": "Alien", "year": "1979",
                                       "kind": "film", "notes": "", "position": ""})
    assert ">Mark<" in page.text
    m.session.buffer.write(seconds(10))
    assert "Tap Save when it ends" in client.post("/mark").text
    m.session.buffer.write(seconds(2))
    assert "Keep clip" in client.post("/end").text
    page = client.post("/finish", data={"text": "a line", "tags": "eerie, speaker=Ripley"})
    assert "Saved" in page.text
    wavs = list((tmp_path / "inbox").glob("*.wav"))
    assert len(wavs) == 1
    sidecar = wavs[0].with_suffix(".json").read_text()
    assert '"marked_by": "J"' in sidecar
    assert '"speaker=Ripley"' in sidecar
    assert "Start session" in client.post("/stop").text


def test_errors_are_shown(make_manager):
    client = TestClient(create_app(make_manager()))
    client.get("/")
    client.post("/name", data={"name": "J"})
    assert "No session running." in client.post("/mark").text
    client.post("/start", data={"title": "Alien"})
    assert "Not a position" in client.post("/sync", data={"position": "soon"}).text


def test_capture_down_offers_restart(make_manager):
    m = make_manager()
    client = TestClient(create_app(m))
    client.get("/")
    client.post("/name", data={"name": "J"})
    client.post("/start", data={"title": "Alien"})
    m.session.capture.stop()
    page = client.get("/")
    assert "DOWN" in page.text and "Restart capture" in page.text
    assert "running" in client.post("/restart").text
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL (`ModuleNotFoundError: samplemark.app`).

- [ ] **Step 3: Implement the page**

`samplemark/templates/page.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>samplemark</title>
<style>
body { font-family: sans-serif; max-width: 32rem; margin: auto; padding: 1rem; }
button { font-size: 1.5rem; width: 100%; padding: 1rem; margin: .4rem 0; }
input, textarea { font-size: 1.1rem; width: 100%; box-sizing: border-box; margin: .2rem 0; }
.mark { background: #c33; color: #fff; }
.save { background: #393; color: #fff; }
.msg { background: #eef; padding: .5rem; }
.err { background: #fdd; padding: .5rem; }
</style>
</head>
<body>
<h1>samplemark</h1>
{% if msg %}<p class="msg">{{ msg }}</p>{% endif %}
{% if error %}<p class="err">{{ error }}</p>{% endif %}

{% if not name %}
<form method="post" action="/name">
  <label>Your name <input name="name" required></label>
  <button>OK</button>
</form>

{% elif not status.active %}
<form method="post" action="/start">
  <label>Title <input name="title" required></label>
  <label>Year <input name="year"></label>
  <label>Type <input name="kind" placeholder="film, show, podcast…"></label>
  <label>Notes <textarea name="notes"></textarea></label>
  <label>Current position (optional) <input name="position" placeholder="0:12:30"></label>
  <button>Start session</button>
</form>

{% else %}
<h2>{{ status.title }}</h2>
<p>Capture: {{ "running" if status.capture_alive else "DOWN" }}
{% if status.position %} · position ≈ {{ status.position }}{% endif %}</p>
{% if not status.capture_alive %}
<form method="post" action="/restart"><button>Restart capture</button></form>
{% endif %}

{% if pending %}
<form method="post" action="/finish">
  <p>Clip: {{ pending.seconds }} s{% if pending.truncated %} (start was cut off){% endif %}</p>
  <label>Text <textarea name="text" required></textarea></label>
  <label>Tags (comma-separated; key=value allowed) <input name="tags"></label>
  <button class="save">Keep clip</button>
</form>
<form method="post" action="/discard"><button>Discard</button></form>
{% elif marked %}
<p>Marked at {{ marked }}. Tap Save when it ends.</p>
<form method="post" action="/end"><button class="save">Save</button></form>
<form method="post" action="/discard"><button>Cancel mark</button></form>
{% else %}
<form method="post" action="/mark"><button class="mark">Mark</button></form>
{% endif %}

<form method="post" action="/sync">
  <label>Sync position <input name="position" placeholder="0:43:00" required></label>
  <button>Sync</button>
</form>
<form method="post" action="/stop"><button>Stop session</button></form>
{% endif %}
</body>
</html>
```

- [ ] **Step 4: Implement the app**

`samplemark/app.py`:

```python
"""The phone page: one server-rendered page, POST-redirect-GET."""

import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .session import Manager, SessionError

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
CLIENT_COOKIE = "samplemark_client"
NAME_COOKIE = "samplemark_name"
YEAR = 365 * 24 * 3600

Text = Annotated[str, Form()]


def _back(msg: str | None = None, error: str | None = None) -> RedirectResponse:
    query = urlencode({k: v for k, v in (("msg", msg), ("error", error)) if v})
    return RedirectResponse("/" + (f"?{query}" if query else ""), status_code=303)


def create_app(manager: Manager) -> FastAPI:
    app = FastAPI(title="samplemark")

    def client_of(request: Request) -> str:
        client = request.cookies.get(CLIENT_COOKIE)
        if not client:
            raise SessionError("Reload the page first.")
        return client

    def act(fn, msg: str) -> RedirectResponse:
        try:
            fn()
        except (SessionError, ValueError, OSError) as error:
            return _back(error=str(error))
        return _back(msg=msg)

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request, msg: str | None = None, error: str | None = None):
        if manager.check_limit():
            msg = "Session stopped: time limit reached."
        client = request.cookies.get(CLIENT_COOKIE) or uuid.uuid4().hex
        context = {
            "msg": msg, "error": error,
            "name": request.cookies.get(NAME_COOKIE),
            "status": manager.status(),
            "marked": manager.marked_at(client),
            "pending": manager.pending_info(client),
        }
        response = TEMPLATES.TemplateResponse(request, "page.html", context)
        response.set_cookie(CLIENT_COOKIE, client, max_age=YEAR, samesite="lax")
        return response

    @app.post("/name")
    def name(name: Text):
        response = _back(msg=f"Hello, {name.strip()}.")
        response.set_cookie(NAME_COOKIE, name.strip(), max_age=YEAR, samesite="lax")
        return response

    @app.post("/start")
    def start(title: Text, year: Text = "", kind: Text = "", notes: Text = "",
              position: Text = ""):
        meta = {"year": year.strip(), "type": kind.strip(), "notes": notes.strip()}
        return act(lambda: manager.start(title, meta, position), "Session started.")

    @app.post("/sync")
    def sync(position: Text):
        return act(lambda: manager.sync(position), "Position synced.")

    @app.post("/mark")
    def mark(request: Request):
        return act(lambda: manager.mark(client_of(request)), "Marked.")

    @app.post("/end")
    def end(request: Request):
        return act(lambda: manager.end(client_of(request)), "Clip captured.")

    @app.post("/finish")
    def finish(request: Request, text: Text = "", tags: Text = ""):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        marked_by = request.cookies.get(NAME_COOKIE, "")
        saved = []

        def keep():
            saved.append(manager.finish(client_of(request), marked_by, text, tag_list))

        response = act(keep, "Saved.")
        return response if not saved else _back(msg=f"Saved {saved[0].name}.")

    @app.post("/discard")
    def discard(request: Request):
        return act(lambda: manager.discard(client_of(request)), "Discarded.")

    @app.post("/stop")
    def stop():
        return act(manager.stop, "Session stopped.")

    @app.post("/restart")
    def restart():
        return act(manager.restart_capture, "Capture restarted.")

    return app
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add samplemark/app.py samplemark/templates tests/test_app.py
git commit -m "Phone page: session, mark, save, sync, stop"
```

---

### Task 6: Command line, service example, README

**Files:**
- Create: `samplemark/cli.py`, `samplemark/__main__.py`, `docs/samplemark.service`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Manager` (Task 4), `create_app` (Task 5), `DEFAULT_CAPTURE` (Task 3).
- Produces: `build_parser() -> argparse.ArgumentParser`;
  `main(argv: list[str] | None = None) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
from pathlib import Path

from samplemark.capture import DEFAULT_CAPTURE
from samplemark.cli import build_parser


def test_defaults():
    args = build_parser().parse_args(["--output", "/tmp/inbox"])
    assert args.output == Path("/tmp/inbox")
    assert (args.host, args.port) == ("127.0.0.1", 8470)
    assert (args.buffer_seconds, args.preroll, args.postroll, args.limit_hours) == (
        600.0, 5.0, 1.0, 4.0)
    assert args.capture_command == DEFAULT_CAPTURE


def test_environment(monkeypatch):
    monkeypatch.setenv("SAMPLEMARK_OUTPUT", "/srv/inbox")
    monkeypatch.setenv("SAMPLEMARK_PORT", "9000")
    monkeypatch.setenv("SAMPLEMARK_HOST", "0.0.0.0")
    args = build_parser().parse_args([])
    assert (args.output, args.port, args.host) == (Path("/srv/inbox"), 9000, "0.0.0.0")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL (`ModuleNotFoundError: samplemark.cli`).

- [ ] **Step 3: Implement**

`samplemark/cli.py`:

```python
"""samplemark command line: flags, with SAMPLEMARK_* environment defaults."""

import argparse
import os
import shlex
from pathlib import Path

from .capture import DEFAULT_CAPTURE


def build_parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(
        prog="samplemark",
        description="Mark and save audio samples from this machine's audio output.")
    p.add_argument("--host", default=env("SAMPLEMARK_HOST", "127.0.0.1"),
                   help="listen address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=int(env("SAMPLEMARK_PORT", "8470")))
    p.add_argument("--output", type=Path, default=env("SAMPLEMARK_OUTPUT"),
                   required=env("SAMPLEMARK_OUTPUT") is None,
                   help="directory clips are written to")
    p.add_argument("--buffer-seconds", type=float,
                   default=float(env("SAMPLEMARK_BUFFER_SECONDS", "600")))
    p.add_argument("--preroll", type=float, default=float(env("SAMPLEMARK_PREROLL", "5")))
    p.add_argument("--postroll", type=float, default=float(env("SAMPLEMARK_POSTROLL", "1")))
    p.add_argument("--limit-hours", type=float,
                   default=float(env("SAMPLEMARK_LIMIT_HOURS", "4")))
    p.add_argument("--capture-command",
                   default=env("SAMPLEMARK_CAPTURE_COMMAND", DEFAULT_CAPTURE),
                   help="command writing raw s16le 48 kHz stereo PCM to stdout")
    return p


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from .app import create_app
    from .session import Manager

    args = build_parser().parse_args(argv)
    manager = Manager(outdir=args.output,
                      capture_command=shlex.split(args.capture_command),
                      buffer_seconds=args.buffer_seconds, preroll=args.preroll,
                      postroll=args.postroll, limit_hours=args.limit_hours)
    try:
        uvicorn.run(create_app(manager), host=args.host, port=args.port)
    finally:
        manager.stop()
```

`samplemark/__main__.py`:

```python
from .cli import main

main()
```

`docs/samplemark.service` (example systemd **user** unit; install to
`~/.config/systemd/user/`):

```ini
[Unit]
Description=samplemark: mark and save audio samples
After=pipewire.service

[Service]
Environment=SAMPLEMARK_HOST=0.0.0.0
Environment=SAMPLEMARK_OUTPUT=%h/samplemark-inbox
ExecStart=%h/src/samplemark/.venv/bin/samplemark
Restart=on-failure

[Install]
WantedBy=default.target
```

Append to `README.md`:

````markdown
## Install

    python3 -m venv .venv
    .venv/bin/pip install -e .
    .venv/bin/samplemark --output ~/samplemark-inbox --host 0.0.0.0

Then open `http://<machine>:8470/` on a phone on the same network. It records only
during a session, only what the machine plays (PipeWire `pw-record`; no microphone).
**There is no authentication:** run it only on a network you trust.

Options (`samplemark --help`) can also be set as `SAMPLEMARK_*` environment
variables. An example systemd user unit is in
[docs/samplemark.service](docs/samplemark.service).

## Tests

    .venv/bin/pip install -e '.[test]'
    .venv/bin/pytest

## Licence

MIT. Clips of films and shows are copyrighted; keep to short excerpts for personal use.
````

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass (6 + 3 + 3 + 10 + 3 + 2 = 27).

- [ ] **Step 5: Commit**

```bash
git add samplemark/cli.py samplemark/__main__.py docs/samplemark.service README.md tests/test_cli.py
git commit -m "Command line, example service unit, README usage"
```

---

### Task 7: Verify on the media PC

Manual; needs the media PC's owners' go-ahead, since it records what is playing.
Run as the desktop user (no root).

- [ ] **Step 1: Raw capture to stdout works**

While something plays:
`timeout 3 pw-record --raw --format s16 --rate 48000 --channels 2 -P '{ stream.capture.sink=true }' - | wc -c`
Expected: roughly 192000 bytes per second captured (≈ 400–600 kB). If 0, check
`pw-record --help` for the PipeWire version's raw/stdout options and amend
`DEFAULT_CAPTURE`.

- [ ] **Step 2: Install and run**

```bash
git clone https://github.com/k0s/samplemark ~/src/samplemark
cd ~/src/samplemark && python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/samplemark --host 0.0.0.0 --output /tmp/samplemark-test
```

If a firewall blocks port 8470 from the LAN, note it for the deployment role
rather than changing it by hand.

- [ ] **Step 3: End to end from a phone**

Open `http://<machine>:8470/`, enter a name, start a session with a position, play
a streamed film, Mark → Save → text → Keep clip. Check `/tmp/samplemark-test`: the
WAV plays the line with ~5 s lead-in, and the JSON has the text, `marked_by` and a
plausible `media_position`. Repeat from a second phone at the same time.

- [ ] **Step 4: Record results**

Note what worked and anything amended in the pull request description.
