# samplemark design

Status: draft for review, 2026-11-17. Nothing is built yet.

## Purpose

While watching a film (or anything else) played by a Linux media PC, either of two
people can mark a line or sound on their phone and save it as a clip, without
pausing. Clips land in an output directory as WAV files with a JSON sidecar, ready
to be reviewed and catalogued later. samplemark only records; it does not catalogue,
publish or edit.

## Flow

1. **Start session.** Title is required. Optional: other details (year, type,
   free-form notes) and the **media's current position** (e.g. `0:12:30`).
2. **Mark.** The clip starts here, minus a pre-roll (default 5 s), because people
   tap after the line has begun.
3. **Save.** The clip ends here, plus a short post-roll (default 1 s). A form asks
   for the clip's details, all optional: **text** (the words spoken, or a
   description), which is what later searches use, and tags. (Text was required
   until 2026-11-22.) Then back to Mark.
4. Repeat Mark → Save as often as wanted.
5. **Stop.** Recording ends and the buffer is discarded. A session also stops by
   itself after a limit (default 4 h), in case nobody taps Stop.

Each phone has its own Mark (kept per browser, by cookie), so two people can mark
overlapping moments. Who marked a clip isn't recorded (dropped 2026-11-22: a name
page before every first use cost more than the attribution was worth).

### Media position

If a position was given, each clip records an estimated media position: the last
known position plus the wall-clock time since. **Sync** on the page lets anyone
re-enter the current position at any time, for example after a pause. Pauses are
not detected; a clip after an unsynced pause has a wrong estimate. The estimate is a
convenience — clips are found by their text.

## Architecture

One Python process, run as a systemd user service on the media PC.

```
pw-record (sink monitor) ──raw PCM──▶ ring buffer ◀── web app (phone page)
                                           │
                                     Save: slice ──▶ WAV + JSON in output dir
```

- **Capture.** During a session only, samplemark runs
  `pw-record -P '{ stream.capture.sink=true }'` writing raw PCM (s16le, 48 kHz,
  stereo) to stdout: the default sink's monitor, i.e. what the machine is playing.
  No microphone is used. The command is configurable, so a different PipeWire
  target (a USB audio input, later) or a test source can be substituted.
- **Ring buffer.** A fixed-size in-memory buffer of the last N seconds (default
  600 s ≈ 115 MB at 48 kHz stereo 16-bit), indexed by a running frame count so a
  wall-clock time maps to a buffer position.
- **Web app.** FastAPI, one server-rendered HTML page with large buttons; plain
  forms, little or no JavaScript. Shows session state, the current Mark, capture
  health, and a Sync control.
- **Save.** Slices the buffer from Mark − pre-roll to Save + post-roll and writes
  `<date>_<session-slug>_<n>.wav` and `.json` to the output directory, atomically
  (write to a temporary name, then rename) so a sync tool or web server never sees
  a half-written file.

### Sidecar

```json
{
  "session": {"title": "...", "started": "2026-11-17T20:31:05-08:00", "meta": {}},
  "marked": "2026-11-17T21:02:14-08:00",
  "saved": "2026-11-17T21:02:22-08:00",
  "preroll_s": 5, "postroll_s": 1,
  "media_position": "0:43:09",
  "text": "...",
  "tags": [],
  "truncated": false
}
```

`media_position` is omitted when no position was given. `truncated` is true when the
Mark had already fallen out of the buffer and the clip starts at the oldest audio
held.

## Configuration

Command-line flags, with environment-variable equivalents for the service unit:
listen address and port, output directory, buffer seconds, pre-roll, post-roll,
session limit, capture command. No config file in the first version.

## Errors

- **Capture exits or stalls.** The page shows "capture down" and offers restart;
  the session and its metadata survive. Audio during the gap is missing.
- **Write fails** (disk full, directory missing). The page shows the error; the
  Mark and buffer are kept so Save can be retried.
- **Mark too old.** Clip is saved from the oldest audio held, flagged `truncated`.
- **Service restart.** The buffer and any open Mark are lost; sessions are not
  persisted in the first version.

## Security

Proof of concept: **no authentication.** It is meant to listen only on a trusted
home network; anyone who can reach the port can start sessions and save clips.
Hardening (a shared token or similar) is planned. The output directory receives
only WAV and JSON files written by samplemark; if it is published by a web server,
that server should serve it as static files, never render templates from it.

## Testing

- Ring buffer and slicing: unit tests on synthetic PCM, including wrap-around and
  truncation.
- Capture: tests substitute a command that streams a WAV file, so no PipeWire is
  needed.
- Web app: FastAPI's test client through a full session (start, mark, save, stop).
- End to end, by hand on the media PC: play something, mark, save, listen.

## Deployment

An Ansible role installs it and its systemd user unit. The role lives with the
rest of the deployment configuration, not in this repo.

## Not in the first version

- Pause detection (from PipeWire stream state) or reading the player's position
  through a browser extension.
- Sources not played by the machine itself (e.g. a receiver's audio through a USB
  input); possible later by changing the capture target.
- Authentication.
- Cataloguing, review, publishing.

## Open questions

- Does a streaming service's audio actually reach the sink monitor when played in
  Firefox? To be proved by test before building (video is DRM-protected; audio
  normally passes through the mixer).

## Note on use

Clips of films and shows are copyrighted material; samplemark is intended for short
excerpts for personal use. Respect the terms of what you record.
