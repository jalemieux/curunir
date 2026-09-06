---
name: webcam
description: "Use when the user asks what the camera / webcam sees, to take a snapshot or picture of the room, to check on something visible from the camera ('is anyone at the desk?', 'is the door closed?', 'what does the camera show right now?'), or on a schedule to watch a scene. Captures one frame from the attached webcam and describes it with the vision model."
---

# Webcam

Take a snapshot from the webcam attached to this instance and describe what it
shows. One command does both: `snapshot.py` in this skill directory captures a
frame with ffmpeg, saves it under `context/workspace/generated/`, and sends it
to the vision model (`VISION_MODEL`) with your question as the prompt. You never
look at the image yourself — you read the description it prints.

Run it from the repo root via the `bash` tool.

## Basic use

Pass the user's actual question as `--prompt` so the description answers it
instead of cataloguing the whole scene:

```bash
python skills/webcam/snapshot.py --prompt "Is anyone sitting at the desk?"
```

Output on success:

```json
{"path": "context/workspace/generated/webcam-2026-09-06_141502.jpg",
 "device": "/dev/video0",
 "model": "gemini/gemini-2.5-flash",
 "description": "A home office with an empty chair ..."}
```

If the user only asked for a picture, omit `--prompt` and you get a general
description.

## Deliver the photo — required final step

The user is on a chat channel and cannot see a filesystem path. After a
successful capture, call `attach` with the printed `path` so they receive the
actual image alongside your summary of the description:

```
attach(path="context/workspace/generated/webcam-2026-09-06_141502.jpg", name="webcam-2026-09-06_141502.jpg")
```

Then answer the user's question in your own words from the `description`. Say
what the model saw; do not embellish beyond it. If the description is vague or
uncertain, say so.

## Options

- `--no-describe` — capture only (e.g. the user just wants the photo).
- `--resolution 1280x720` — request a specific frame size from the camera.
- `--warmup N` — frames to discard while auto-exposure settles (default 10).
  Raise it if snapshots come out dark.
- `--device` — override `WEBCAM_DEVICE`. A `/dev/videoN` path is read via v4l2;
  an `http://`/`rtsp://` URL is read as a stream (IP cameras).

## Failures

On failure the script prints `{"error": ..., "hint": ...}` and exits 1. Read the
`error` and report it plainly; do not retry in a loop.

- `camera device /dev/video0 not found` — the container was started without the
  `docker-compose.webcam.yml` override, or the camera is unplugged. Operator fix.
- `ffmpeg failed to capture ... Permission denied` — the container user is not
  in the device's group; the override's `group_add` needs the host's video GID.
- `no vision-capable model configured` — `VISION_MODEL` is unset. Operator fix.
- `vision model ... failed` — provider error; the photo was still saved, so
  attach it and tell the user the description step failed.

## Scheduled watching

This skill works under the `schedule` tool. A prompt like "take a snapshot and
tell me whether the garage door is open" runs unattended: capture, describe,
attach, and report. Keep scheduled prompts specific so the description stays
short.

## Privacy

Only take a snapshot when asked or when a schedule the user created calls for
it. Never capture speculatively, and do not describe people in more detail than
the question requires.
