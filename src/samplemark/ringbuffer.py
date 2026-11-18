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
