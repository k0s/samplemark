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
