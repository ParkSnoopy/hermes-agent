---
title: "Give Your Agent Its Own Email Address"
description: "Set up a dedicated mailbox your agent can read and send from using the bundled Himalaya skill, with safety notes"
---

# Give Your Agent Its Own Email Address

A dedicated email address turns your agent into something you (and services) can email: newsletters it summarises, receipts it files, booking confirmations it tracks, and outbound mail it sends on your behalf. This guide sets that up with the bundled [Himalaya email skill](../user-guide/skills/bundled/email/email-himalaya.md), which drives the `himalaya` CLI over IMAP/SMTP from the agent's terminal tools.

:::info Two different email features
This guide is about the agent *operating a mailbox* — reading, searching, composing, and organising mail as part of its tasks.
:::

## 1. Create a dedicated account

Create a fresh mailbox for the agent — never hand it your personal inbox:

- Any IMAP/SMTP provider works: Gmail, Outlook, Fastmail, Migadu, your own domain.
- Enable IMAP in the provider's settings.
- If the provider uses 2FA (Gmail, Outlook), create an **app password** for the agent. For Gmail: enable 2FA, then create one at [App Passwords](https://myaccount.google.com/apppasswords).
- A memorable address helps: `my-agent@yourdomain.com` or similar.

## 2. Install and configure Himalaya

Ask Hermes to do this for you — the skill contains the full procedure — or do it manually:

```bash
# Pre-built binary (Linux/macOS)
curl -sSL https://raw.githubusercontent.com/pimalaya/himalaya/master/install.sh | PREFIX=~/.local sh
himalaya --version
```

Then create `~/.config/himalaya/config.toml` with the account's IMAP/SMTP settings. The skill's `references/configuration.md` covers auth options in detail; a minimal Gmail-style config looks like:

```toml
[accounts.agent]
default = true
email = "my-agent@example.com"
display-name = "My Hermes Agent"

backend.type = "imap"
backend.host = "imap.example.com"
backend.port = 993
backend.login = "my-agent@example.com"
backend.auth.type = "password"
backend.auth.command = "cat ~/.config/himalaya/app-password"

message.send.backend.type = "smtp"
message.send.backend.host = "smtp.example.com"
message.send.backend.port = 587
message.send.backend.encryption.type = "start-tls"
message.send.backend.login = "my-agent@example.com"
message.send.backend.auth.type = "password"
message.send.backend.auth.command = "cat ~/.config/himalaya/app-password"
```

Store the app password in a file readable only by your user (`chmod 600`), or use a secret-manager command instead of `cat`. Verify with:

```bash
himalaya envelope list
```

Once `himalaya` works from your own shell, the agent can use it too — the bundled skill teaches it the commands, so "check the agent inbox and summarise anything new" works in any chat.

## 4. Safety notes

Email is an unauthenticated inbound channel — anyone can write to the agent's address, which makes it a prompt-injection surface:

- **Never let the agent auto-act on unsolicited mail.** Instructions inside an email body are untrusted content, not commands. Include that rule in standing instructions.
- **Confirm before outbound sends.** For workflows where the agent composes mail, have it draft and show you the message before sending, at least until you trust the pattern.
- **Keep the account low-privilege.** Don't attach the agent's address to password resets, banking, or account recovery for anything that matters.
- **Scope the credentials.** An app password for a dedicated mailbox is a small blast radius; your personal account's credentials are not.

## See also

- [Himalaya skill reference](../user-guide/skills/bundled/email/email-himalaya.md) — full command set the agent uses

- [Security](../user-guide/security.md) — the wider prompt-injection and credential-handling picture
