# Hermes CLI Reference

Live sources when anything looks stale: `hermes --help`, `hermes <command> --help`,
https://hermes-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
hermes [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
hermes chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
hermes setup [section]      Wizard (model|terminal|tools|agent)
hermes model                Interactive model/provider picker
hermes fallback [add|remove|list]  Fallback provider chain
hermes config [show|edit|get|set|unset|path|env-path|check|migrate]
hermes login / logout       OAuth sign-in / clear stored auth
hermes doctor [--fix]       Check dependencies and config
hermes status [--full]      Component summary (--full: every section)
```

### Tools & Skills

```
hermes tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

hermes skills list|browse|search QUERY|inspect ID
hermes skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
hermes skills config        Enable/disable skills per platform
hermes skills check|update|uninstall|publish PATH
hermes skills tap add REPO  Add a GitHub repo as a skill source
hermes bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
hermes mcp add NAME (--url or --command) | remove | list | test NAME
hermes mcp catalog | install NAME     Curated catalog install
hermes mcp configure NAME             Toggle tool selection
hermes mcp-serve                      Run Hermes as an MCP server (mcp-serve plugin)
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Sessions

```
hermes sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Webhooks

```
hermes webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
hermes profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
hermes profile rename A B | alias NAME | export NAME | import FILE
hermes profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
hermes auth                 Interactive credential manager
hermes auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
hermes auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
hermes desktop / gui        Native desktop app
hermes dashboard            Web admin panel + embedded chat (--stop / --status)
hermes proxy                OpenAI-compatible local proxy backed by an OAuth provider
hermes portal               Quick setup / sign in via Nous Portal
hermes project              Named multi-folder workspaces (see references/projects-and-folders.md)
hermes skin list|use|set    Switch/tweak skins (see references/themes.md)
hermes pets <verb>          Pet mascots (see references/petdex.md)
hermes memory setup|status|off|reset   Memory provider
hermes secrets bitwarden|onepassword   External secret stores
hermes moa                  Mixture-of-Agents slots
hermes hooks / security / backup / import / checkpoints / console
hermes logs [-f] [errors]   View agent/error logs

hermes plugins / insights / journey / screen
hermes acp                  ACP server (IDE integration)
hermes completion bash|zsh|fish
hermes update / uninstall
```

Plugin- and provider-supplied subcommands (e.g. `hermes photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `hermes config edit` · [Configuration docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `hermes tools list` · [Tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `hermes skills browse` · [Skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `hermes model` · [Providers guide](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `hermes config env-path` · [Env vars reference](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.hermes/logs/gateway.log` (or `hermes logs`) |
| Sessions | `hermes sessions browse` (reads state.db) |
