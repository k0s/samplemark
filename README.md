# samplemark

Mark and save audio samples from whatever a Linux machine is playing, using a phone
on the same network.

Start a session on the phone (give it a title), tap **Mark** when a sound or line
you want begins, tap **Save** when it ends and describe it. samplemark keeps a short
rolling recording of the machine's audio output (PipeWire), so the saved clip can
start a few seconds before you tapped.

Status: first version; not yet tried on real hardware. Design: [docs/design.md](docs/design.md).

## Install

    uv sync --no-dev
    uv run samplemark --output ~/samplemark-inbox --host 0.0.0.0

Then open `http://<machine>:8470/` on a phone on the same network. It records only
during a session, only what the machine plays (PipeWire `pw-record`; no microphone).
**There is no authentication:** run it only on a network you trust.

Options (`samplemark --help`) can also be set as `SAMPLEMARK_*` environment
variables. An example systemd user unit is in
[docs/samplemark.service](docs/samplemark.service).

## Tests

    uv sync
    uv run pytest

## Licence

MIT. Clips of films and shows are copyrighted; keep to short excerpts for personal use.
