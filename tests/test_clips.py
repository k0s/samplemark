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
