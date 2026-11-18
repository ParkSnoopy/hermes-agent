---
title: "Bot Mode"
description: "Use profile-based Bot Chats from the terminal."
---

# Bot Mode

A Bot is a [Hermes profile](./profiles.md), with its own configuration, credentials, memory, skills, and sessions.
Manage profiles with `hermes profile create` and `hermes profile list`; see the [profile command reference](../reference/profile-commands.md).

## Bot-to-bot messaging

The optional Bot Chat protocol uses a session titled exactly `Bot Chat` and a Bot Mode marker in at least one profile's `profile.yaml`.
Set `agent.bot_mode_protocol: true` in the profile configuration, create or resume the named session, and add the marker:

```bash
hermes -p <bot> chat -c "Bot Chat" --create-if-missing
```

```yaml
# ~/.hermes/profiles/<any-bot>/profile.yaml
ui_meta:
  hermes-bots: {}
```

In an eligible Bot Chat, `message_agent` can address a local teammate by profile name.
A queued acknowledgement is not a completed reply; check the delivery outcome before retrying an uncertain request.
Regular chats do not receive this tool.

The retained relay's `bot_mode.envelope_ttl_seconds` setting bounds the age of queued relay envelopes before they expire.

## Groups and group chats

Hosted group events and hooks are described in the [hooks reference](./features/hooks.md).
