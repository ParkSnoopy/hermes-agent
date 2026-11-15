---
sidebar_position: 4
title: "Buzz Integration"
description: "Connect Hermes Agent to Buzz through ACP"
---

# Buzz Integration

[Buzz](https://github.com/block/buzz) is Block's open-source, self-hostable workspace where humans and AI agents share the same channels. It is built on Nostr: every message is a signed event on a relay you own, and every participant — human or agent — is a keypair.

Hermes integrates with Buzz through ACP. Pick by where Hermes runs:

| | ① Desktop runtime | ② Relay bridge (ACP) |
|---|---|---|
| **What it is** | Buzz Desktop spawns Hermes locally as a managed harness | Buzz's `buzz-acp` bridges a channel to `hermes acp` over stdio |
| **Hermes runs** | On your desktop, launched by Buzz | On a server, launched by `buzz-acp` |
| **Best for** | Trying Hermes inside Buzz Desktop with zero config | A hosted agent identity when Buzz owns the transport |
| **Inbound** | ACP stdio | ACP stdio (via relay WebSocket) |
| **Setup** | Automatic discovery | `buzz-acp` env vars |

## ① Buzz Desktop managed runtime

Buzz Desktop ships Hermes as a preset runtime. With Hermes installed the normal way, open **Settings → Runtimes** and Hermes appears automatically — discovery resolves the `hermes-acp` launcher on your login-shell PATH, which the installer writes to `~/.local/bin` (and `hermes update` self-heals on older installs).

Full setup, troubleshooting, and the security posture (Buzz auto-approves tool permissions — keep agents owner-only): **[ACP Host Integration → Buzz Desktop](../user-guide/features/acp.md#buzz-desktop)**

## ② Relay bridge (buzz-acp + ACP)

For a hosted Hermes identity that joins Buzz *channels* while Buzz's own harness owns the transport:

```text
Buzz relay <-- WebSocket --> buzz-acp <-- ACP over stdio --> Hermes Agent
```

The spawned Hermes uses the same config, credentials, memory, and skills as `hermes` on that host. Key minting, channel discovery, owner-only telemetry (`BUZZ_ACP_RELAY_OBSERVER`), and headless-permission guidance: **[ACP Host Integration → Buzz channels (relay bridge)](../user-guide/features/acp.md#buzz-channels-relay-bridge)**


## Which one should I use?

- **Just exploring, Buzz Desktop user** → ① works out of the box.
- **Running a community relay and want an agent identity managed by Buzz** → ②.


## Credits

The Buzz integration was built with the community: @SHL0MS (PATH launcher + Desktop security audit), @NYTEMODEONLY (relay-bridge docs), @rob-coco (platform adapter), @ScaleLeanChris (Nostr WebSocket transport + NIP-42/BIP-340 signing), and @jethac (multi-agent verification).
