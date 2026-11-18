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
