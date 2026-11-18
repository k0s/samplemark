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
