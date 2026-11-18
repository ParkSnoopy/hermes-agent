# google_meet plugin

Let the Hermes agent join a Google Meet call, capture captions, and follow up afterwards. The bot is listen-only.

## What ships

| Version | What | Status |
|---|---|---|
| v1 | Caption capture: Playwright joins Meet and saves Google-provided captions to a transcript | ✓ ships by default |
| v3 | Remote node host: run the bot on a different machine than the gateway | ✓ opt in with `node='<name>'` |

## Architecture

```
┌─ gateway (Linux box, where hermes runs) ────────────────────────────┐
│                                                                      │
│   agent → meet_join(url, node='my-node')                          │
│         │                                                            │
│         └─ NodeClient ─── ws ────┐                                   │
│                                  │                                   │
└──────────────────────────────────┼───────────────────────────────────┘
                                   │ wss (token auth)
                                   ▼
┌─ node host (signed-in Chrome lives here)              ───────────────┐
│                                                                      │
│   NodeServer (from `hermes meet node run`)                           │
│     │                                                                │
│     ├─ start_bot → process_manager.start() → spawns meet_bot         │
│     │                                                                │
│     └─ meet_bot (Playwright)                                         │
│        ├─ Chromium → meet.google.com                                 │
│        └─ caption scraper → transcript.txt                           │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

Without v3: the whole right column runs on the gateway machine.

## Files

| Path | Purpose |
|---|---|
| `plugin.yaml` | manifest |
| `__init__.py` | `register(ctx)` — registers 4 tools + `on_session_end` hook + `hermes meet` CLI |
| `meet_bot.py` | Playwright bot subprocess (standalone, `python -m plugins.google_meet.meet_bot`) |
| `process_manager.py` | local bot lifecycle |
| `tools.py` | agent-facing tools + node-routing helper |
| `cli.py` | `hermes meet setup / auth / join / status / transcript / stop / node ...` |
| `node/protocol.py` | v3: message envelope + validation |
| `node/registry.py` | v3: `$HERMES_HOME/workspace/meetings/nodes.json` |
| `node/server.py` | v3: `NodeServer` (runs on host machine) |
| `node/client.py` | v3: `NodeClient` (used by tool handlers + CLI on gateway) |
| `node/cli.py` | v3: `hermes meet node {run,list,approve,remove,status,ping}` |
| `SKILL.md` | agent usage guide |

## Local quick start

```bash
hermes plugins enable google_meet
hermes meet install                                      # pip + Chromium
hermes meet setup                                        # preflight
hermes meet auth                                         # optional
hermes meet join https://meet.google.com/abc-defg-hij    # transcribe
```

## Remote node host

On the Linux node machine with a signed-in Chrome:
```bash
hermes plugins enable google_meet
hermes meet install
hermes meet node run --display-name my-node --host 0.0.0.0 --port 18789
# prints the bearer token on first run; copy it
```

On the gateway:
```bash
hermes meet node approve my-node ws://<node-ip>:18789 <token>
hermes meet node ping my-node
# now any meet_* tool call accepts node='my-node' (or 'auto')
```

## Safety

- URL gate: only `https://meet.google.com/abc-defg-hij`, `/new`, `/lookup/<id>`.
- No calendar scanning, no auto-dial, no auto-consent announcement.
- Node server uses bearer-token auth; no key exchange, no TLS termination
  built in — run it on a LAN or behind a reverse proxy you trust.
- One active meeting per (gateway, node) pair. A second `meet_join` leaves the first.

## Out of scope

- **Calendar scanning** — deliberately not implemented. Join URLs must be explicit.
- **Multi-tenant node sharing** — a node serves one gateway at a time.
- **Non-Linux platforms** — the plugin only registers on Linux.
- **In-call speech and raw meeting audio** — the bot only captures Meet captions.
