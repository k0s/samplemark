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
