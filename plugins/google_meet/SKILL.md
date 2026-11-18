---
name: google_meet
description: Join a Google Meet call, capture live captions, and follow up with notes or action items.
version: 0.2.0
platforms:
  - linux
  - macos
metadata:
  hermes:
    tags: [meetings, google-meet, captions, transcription]
---

# google_meet

Use when the user asks the agent to join a Meet, take notes, summarize a meeting, or follow up on action items. The bot is listen-only: it scrapes Google Meet's live captions and cannot speak in the meeting.

## Setup

```bash
hermes plugins enable google_meet
hermes meet install
hermes meet auth                    # optional; avoids guest-lobby wait
hermes meet setup                   # preflight checks
```

For a remote node, enable and install the plugin on the node host, then run `hermes meet node run --display-name my-mac`. Approve the node from the gateway with `hermes meet node approve my-mac ws://<mac-ip>:18789 <token>`, and verify with `hermes meet node ping my-mac`.

## Flow

1. Call `meet_join(url=..., node=...)`; it returns immediately. The only supported mode is `transcribe`.
2. Tell the user in their current channel that a Hermes bot is taking notes. The bot does not announce itself in-call.
3. Poll `meet_status()` for liveness and `meet_transcript(last=20)` for recent captions.
4. Call `meet_leave()` when done, or specify `duration="30m"` at join for automatic departure.
5. Read `meet_transcript()` in full, summarize, and use regular tools for follow-up work.

| Tool | Parameters | Use |
|---|---|---|
| `meet_join` | `url`, `mode?`, `guest_name?`, `duration?`, `headed?`, `node?` | Start caption capture |
| `meet_status` | `node?` | Liveness and progress |
| `meet_transcript` | `last?`, `node?` | Read captions |
| `meet_leave` | `node?` | Stop and finalize the transcript |

Pass a registered `node` name, or `"auto"` for the sole node, to operate remotely; omit it for local operation.

## Limits and safety

- Captions are lossy, especially with overlapping speakers. Guest mode waits in the lobby until admitted; after the configured lobby timeout the bot leaves.
- Only one active meeting per install per location is supported. Windows is unsupported.
- Only `https://meet.google.com/...` URLs are accepted. There is no calendar scanning or automatic dialing.
- Remote nodes use bearer-token authentication. Keep node tokens private.
- Local transcripts live under `$HERMES_HOME/workspace/meetings/<meeting-id>/transcript.txt`; use `meet_transcript(node=...)` for a remote node.
