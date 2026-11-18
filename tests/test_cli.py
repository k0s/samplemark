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
