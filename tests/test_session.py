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
