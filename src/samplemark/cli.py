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
