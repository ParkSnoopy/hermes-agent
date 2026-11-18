---
sidebar_position: 3
title: "Nix & NixOS Setup"
description: "Install and deploy Hermes Agent with Nix, NixOS, and Home Manager"
---

# Nix & NixOS Setup

:::warning Nix support
Nix and NixOS on Linux are supported on a best-effort basis. The flake and NixOS module documented here are maintained on a best-effort basis only. Commits to `main` may break these packages at any point in time.

For a supported setup, use a standard [installation](./installation.md) path.
:::

Hermes Agent ships a Nix flake, a NixOS module, and a Home Manager module.

| Level | Who it's for | What you get |
|-------|-------------|--------------|
| **`nix run` / `nix profile install`** | Nix users on Linux | Pre-built binary with all deps — then use the standard CLI workflow |
| **Home Manager module** | An agent for one person, on a Linux distribution | Declarative configuration and a user service, without root |
| **NixOS module** | NixOS server deployments | Declarative config, hardened systemd service, managed secrets |

:::info What's different from the standard install
The `curl | bash` installer manages Python, Node, and dependencies itself. The Nix flake replaces all of that — every Python dependency is a Nix derivation built by [uv2nix](https://github.com/pyproject-nix/uv2nix), and runtime tools (Node.js, git, ripgrep, ffmpeg) are wrapped into the binary's PATH. There is no runtime pip, no venv activation, no `npm install`.

**For non-NixOS users**, this only changes the install step. Everything after (`hermes setup`, `hermes gateway install`, config editing) works identically to the standard install.

**For NixOS module users**, the entire lifecycle is different: configuration lives in `configuration.nix`, secrets go through sops-nix/agenix, the service is a systemd unit, and CLI config commands are blocked. You manage hermes the same way you manage any other NixOS service.
:::

## Runtime pins

PM's tool lock is also a Nix build input. `nix/npm-pinned.nix` reads the npm
pin, and `nix/pm-packages.nix` exposes matching archives as `pm-NAME` derivations:

```bash
nix build .#pm-ripgrep
```

These outputs unpack the pinned archives. They are not the complete Hermes
wrapper or a guarantee that each archive runs without platform integration.
The application still uses the uv2nix environment and Nix wrapper.

`nix/pythonLock.nix` reads Python's major/minor from `pm/lock.json`.
The uv2nix environment, package overrides, plugin packages, and developer shell
use that interpreter family. If the pinned nixpkgs lacks that family, evaluation
stops instead of selecting a different Python.

Native Nix evaluation and builds remain CI gates. Update through Nix. Do not
repair a Nix store path with pip.

## Prerequisites

- **Nix with flakes enabled** — [Determinate Nix](https://install.determinate.systems) recommended (enables flakes by default)
- **API keys** for the services you want to use (at minimum: an OpenRouter or Anthropic key)

---

## Quick Start (Any Nix User)

No clone needed. Nix fetches, builds, and runs everything:

```bash
# run the tui
nix run github:NousResearch/hermes-agent -- setup
nix run github:NousResearch/hermes-agent -- --tui

# or install it in your profile
nix profile install github:NousResearch/hermes-agent
hermes setup
hermes --tui
```

After `nix profile install`, `hermes`, `hermes-agent`, and `hermes-acp` are on your PATH. From here, the workflow is identical to the [standard installation](./installation.md) — `hermes setup` walks you through provider selection, `hermes gateway install` sets up a systemd user service, and config lives in `~/.hermes/`.

:::warning Messaging platforms (Discord, Telegram, Slack)
The default package includes ALL libraries hermes-agent might need. if you want a smaller variant, check the other flake outputs. 

The `default` package adds ~700 MB to the closure. If you only need messaging platforms, `#messaging` adds just ~33 MB.

:::

<details>
<summary><strong>Running from a local clone</strong></summary>

```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
nix develop
hermes-checkout setup
```

</details>

---

## NixOS Module

The flake exports `nixosModules.default` — a full NixOS service module that declaratively manages user creation, directories, config generation, secrets, documents, and service lifecycle.

:::note
This module needs NixOS. Hermes is an agent for one person. If you want an agent for one person and not a system service, use the [Home Manager module](#home-manager-module). That module runs on NixOS and on each other system that Home Manager supports.
:::

### Add the Flake Input

```nix
# /etc/nixos/flake.nix (or your system flake)
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    hermes-agent.url = "github:NousResearch/hermes-agent";
  };

  outputs = { nixpkgs, hermes-agent, ... }: {
    nixosConfigurations.your-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        hermes-agent.nixosModules.default
        ./configuration.nix
      ];
    };
  };
}
```

### Minimal Configuration

```nix
# configuration.nix
{ config, ... }: {
  services.hermes-agent = {
    enable = true;
    settings.model.default = "anthropic/claude-sonnet-4";
    environmentFiles = [ config.sops.secrets."hermes-env".path ];
    addToSystemPackages = true;
  };
}
```

That's it. `nixos-rebuild switch` creates the `hermes` user, generates `config.yaml`, wires up secrets, and starts the gateway — a long-running service that connects the agent to messaging platforms (Telegram, Discord, etc.) and listens for incoming messages.

:::warning Secrets are required
The `environmentFiles` line above assumes you have [sops-nix](https://github.com/Mic92/sops-nix) or [agenix](https://github.com/ryantm/agenix) configured. The file should contain at least one LLM provider key (e.g., `OPENROUTER_API_KEY=sk-or-...`). See [Secrets Management](#secrets-management) for full setup. If you don't have a secrets manager yet, you can use a plain file as a starting point — just ensure it's not world-readable:

```bash
echo "OPENROUTER_API_KEY=sk-or-your-key" | sudo install -m 0600 -o hermes /dev/stdin /var/lib/hermes/env
```

```nix
services.hermes-agent.environmentFiles = [ "/var/lib/hermes/env" ];
```
:::

:::tip addToSystemPackages
Setting `addToSystemPackages = true` does two things: puts the `hermes` CLI on your system PATH **and** sets `HERMES_HOME` system-wide so the interactive CLI shares state (sessions and skills) with the gateway service. Without it, running `hermes` in your shell creates a separate `~/.hermes/` directory.
:::


### Verify It Works

After `nixos-rebuild switch`, check that the service is running:

```bash
# Check service status
systemctl status hermes-agent

# Watch logs (Ctrl+C to stop)
journalctl -u hermes-agent -f

# If addToSystemPackages is true, test the CLI
hermes --version
hermes config       # shows the generated config
```



---

## Configuration

### Declarative Settings

The `settings` option accepts an arbitrary attrset that is rendered as `config.yaml`. It supports deep merging across multiple module definitions (via `lib.recursiveUpdate`), so you can split config across files:

```nix
# base.nix
services.hermes-agent.settings = {
  model.default = "anthropic/claude-sonnet-4";
  toolsets = [ "all" ];
  terminal = { backend = "local"; timeout = 180; };
};

# personality.nix
services.hermes-agent.settings = {
  display = { compact = false; personality = "kawaii"; };
  memory = { memory_enabled = true; user_profile_enabled = true; };
};
```

Both are deep-merged at evaluation time. Nix-declared keys always win over keys in an existing `config.yaml` on disk, but **user-added keys that Nix doesn't touch are preserved**. This means if the agent or a manual edit adds keys like `skills.disabled` or `streaming.enabled`, they survive `nixos-rebuild switch`.

:::note Model naming
`settings.model.default` uses the model identifier your provider expects. With [OpenRouter](https://openrouter.ai) (the default), these look like `"anthropic/claude-sonnet-4"` or `"google/gemini-3-flash"`. If you're using a provider directly (Anthropic, OpenAI), set `settings.model.base_url` to point at their API and use their native model IDs (e.g., `"claude-sonnet-4-20250514"`). When no `base_url` is set, Hermes defaults to OpenRouter.
:::

:::tip Discovering available config keys
Run `nix build .#configKeys && cat result` to see every leaf config key extracted from Python's `DEFAULT_CONFIG`. You can paste your existing `config.yaml` into the `settings` attrset — the structure maps 1:1.
:::

<details>
<summary><strong>Full example: all commonly customized settings</strong></summary>

```nix
{ config, ... }: {
  services.hermes-agent = {
    enable = true;


    # ── Model ──────────────────────────────────────────────────────────
    settings = {
      model = {
        base_url = "https://openrouter.ai/api/v1";
        default = "anthropic/claude-opus-4.6";
      };
      toolsets = [ "all" ];
      max_turns = 100;
      terminal = { backend = "local"; cwd = "."; timeout = 180; };
      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "google/gemini-3-flash-preview";
      };
      memory = { memory_enabled = true; user_profile_enabled = true; };
      display = { compact = false; personality = "kawaii"; };
      agent = { max_turns = 60; verbose = false; };
    };

    # ── Secrets ────────────────────────────────────────────────────────
    environmentFiles = [ config.sops.secrets."hermes-env".path ];

    # ── Documents ──────────────────────────────────────────────────────
    # USER.md is memory, so it goes to HERMES_HOME. Workspace files use
    # `documents`, and that option needs an explicit `workingDirectory`.
    hermesHomeFiles = {
      "memories/USER.md" = ./documents/USER.md;
    };

    # ── MCP Servers ────────────────────────────────────────────────────
    mcpServers.filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/var/lib/hermes/workspace" ];
    };


    # ── Service tuning ─────────────────────────────────────────────────
    addToSystemPackages = true;
    extraArgs = [ "--verbose" ];
    restart = "always";
    restartSec = 5;
  };
}
```

</details>

### Escape Hatch: Bring Your Own Config

If you'd rather manage `config.yaml` entirely outside Nix, use `configFile`:

```nix
services.hermes-agent.configFile = /etc/hermes/config.yaml;
```

This bypasses `settings` entirely — no merge, no generation. The file is copied as-is to `$HERMES_HOME/config.yaml` on each activation.

### Customization Cheatsheet

Quick reference for the most common things Nix users want to customize:

| I want to... | Option | Example |
|---|---|---|
| Change the LLM model | `settings.model.default` | `"anthropic/claude-sonnet-4"` |
| Use a different provider endpoint | `settings.model.base_url` | `"https://openrouter.ai/api/v1"` |
| Add API keys | `environmentFiles` | `[ config.sops.secrets."hermes-env".path ]` |
| Give the agent an identity | `hermesHomeFiles."SOUL.md"` | `"You are a terse ops assistant."` |
| Add project context to the workspace | `documents."AGENTS.md"` | `./documents/AGENTS.md` |
| Add MCP tool servers | `mcpServers.<name>` | See [MCP Servers](#mcp-servers) |
| Add an optional extra the package does not ship | `extraDependencyGroups` | `[ "google-meet" ]` |
| Make extra tools available to the agent | `extraPackages` | `[ pkgs.pandoc pkgs.imagemagick ]` |
| Override the hermes package | `package` | `inputs.hermes-agent.packages.${system}.default.override { ... }` |
| Change state directory | `stateDir` | `"/opt/hermes"` |
| Set the agent's working directory | `workingDirectory` | `"/home/user/projects"` |

---

## Secrets Management

:::danger Never put API keys in `settings` or `environment`
Values in Nix expressions end up in `/nix/store`, which is world-readable. Always use `environmentFiles` with a secrets manager.
:::

Both `environment` (non-secret vars) and `environmentFiles` (secret files) are merged into `$HERMES_HOME/.env` at activation time (`nixos-rebuild switch`). Hermes reads this file on every startup, so changes take effect with a `systemctl restart hermes-agent`.

### sops-nix

```nix
{
  sops = {
    defaultSopsFile = ./secrets/hermes.yaml;
    age.keyFile = "/home/user/.config/sops/age/keys.txt";
    secrets."hermes-env" = { format = "yaml"; };
  };

  services.hermes-agent.environmentFiles = [
    config.sops.secrets."hermes-env".path
  ];
}
```

The secrets file contains key-value pairs:

```yaml
# secrets/hermes.yaml (encrypted with sops)
hermes-env: |
    OPENROUTER_API_KEY=sk-or-...
    TELEGRAM_BOT_TOKEN=123456:ABC...
    ANTHROPIC_API_KEY=sk-ant-...
```

### agenix

```nix
{
  age.secrets.hermes-env.file = ./secrets/hermes-env.age;

  services.hermes-agent.environmentFiles = [
    config.age.secrets.hermes-env.path
  ];
}
```

### OAuth / Auth Seeding

For platforms requiring OAuth (e.g., Discord), use `authFile` to seed credentials on first deploy:

```nix
{
  services.hermes-agent = {
    authFile = config.sops.secrets."hermes/auth.json".path;
    # authFileForceOverwrite = true;  # overwrite on every activation
  };
}
```

The file is only copied if `auth.json` doesn't already exist (unless `authFileForceOverwrite = true`). Runtime OAuth token refreshes are written to the state directory and preserved across rebuilds.

---

## Documents

Hermes reads files from two directories. Thus there are two options. Use the option for the directory that the file must go into.

`documents` installs into the **working directory** of the agent, which is `workingDirectory`. The agent reads its project context from that workspace:

```nix
{
  services.hermes-agent = {
    # documents needs this option. Read the note below.
    workingDirectory = "/var/lib/hermes/workspace";
    documents = {
      "AGENTS.md" = ./documents/AGENTS.md;   # path reference, copied from Nix store
      "notes/oncall.md" = "Page #infra before restarting anything.";
    };
  };
}
```

:::warning documents needs an explicit workingDirectory
The module refuses `documents` until you set `workingDirectory`. The default of
that option is different on each module. It is your home directory on Home
Manager, and `${stateDir}/workspace` on NixOS. Thus an unset default puts the
files in a directory that you did not select. A directory with the same path as
the default is a correct selection, and it satisfies the rule.
:::

`hermesHomeFiles` installs into **`HERMES_HOME`**. Hermes reads the identity file and the memory files of the agent from that directory. `SOUL.md` and `memories/` work only from there. A `SOUL.md` in `documents` makes a workspace file. Hermes does not load that file as the identity:

```nix
{
  services.hermes-agent.hermesHomeFiles = {
    "SOUL.md" = "You are a helpful AI assistant.";
    "memories/USER.md" = ./documents/USER.md;
  };
}
```

Each value is a string or a path. A key in either option can contain subdirectories, and the module makes the parent directories. Each activation installs the files again.

`hermesHomeFiles` needs no `workingDirectory`, because the module owns the `HERMES_HOME` directory. Most users want `hermesHomeFiles`.

---

## MCP Servers

The `mcpServers` option declaratively configures [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers. Each server uses either **stdio** (local command) or **HTTP** (remote URL) transport.

### Stdio Transport (Local Servers)

```nix
{
  services.hermes-agent.mcpServers = {
    filesystem = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-filesystem" "/var/lib/hermes/workspace" ];
    };
    github = {
      command = "npx";
      args = [ "-y" "@modelcontextprotocol/server-github" ];
      env.GITHUB_PERSONAL_ACCESS_TOKEN = "\${GITHUB_TOKEN}"; # resolved from .env
    };
  };
}
```

:::tip
Environment variables in `env` values are resolved from `$HERMES_HOME/.env` at runtime. Use `environmentFiles` to inject secrets — never put tokens directly in Nix config.
:::

### HTTP Transport (Remote Servers)

```nix
{
  services.hermes-agent.mcpServers.remote-api = {
    url = "https://mcp.example.com/v1/mcp";
    headers.Authorization = "Bearer \${MCP_REMOTE_API_KEY}";
    timeout = 180;
  };
}
```

### HTTP Transport with OAuth

Set `auth = "oauth"` for servers using OAuth 2.1. Hermes implements the full PKCE flow — metadata discovery, dynamic client registration, token exchange, and automatic refresh.

```nix
{
  services.hermes-agent.mcpServers.my-oauth-server = {
    url = "https://mcp.example.com/mcp";
    auth = "oauth";
  };
}
```

Tokens are stored in `$HERMES_HOME/mcp-tokens/<server-name>.json` and persist across restarts and rebuilds.

<details>
<summary><strong>Initial OAuth authorization on headless servers</strong></summary>

The first OAuth authorization requires a browser-based consent flow. In a headless deployment, Hermes prints the authorization URL to stdout/logs instead of opening a browser.

**Option A: Interactive bootstrap** — run the flow once as the service user:

```bash
sudo -u hermes HERMES_HOME=/var/lib/hermes/.hermes \
  hermes mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
```


**Option B: Pre-seed tokens** — complete the flow on a workstation, then copy tokens:

```bash
hermes mcp add my-oauth-server --url https://mcp.example.com/mcp --auth oauth
scp ~/.hermes/mcp-tokens/my-oauth-server{,.client}.json \
    server:/var/lib/hermes/.hermes/mcp-tokens/
# Ensure: chown hermes:hermes, chmod 0600
```

</details>

### Sampling (Server-Initiated LLM Requests)

Some MCP servers can request LLM completions from the agent:

```nix
{
  services.hermes-agent.mcpServers.analysis = {
    command = "npx";
    args = [ "-y" "analysis-server" ];
    sampling = {
      enabled = true;
      model = "google/gemini-3-flash";
      max_tokens_cap = 4096;
      timeout = 30;
      max_rpm = 10;
    };
  };
}
```

---

## Managed Mode

When hermes runs via the NixOS module, the following CLI commands are **blocked** with a descriptive error pointing you to `configuration.nix`:

| Blocked command | Why |
|---|---|
| `hermes setup` | Config is declarative — edit `settings` in your Nix config |
| `hermes config edit` | Config is generated from `settings` |
| `hermes config set <key> <value>` | Config is generated from `settings` |
| `hermes gateway install` | The systemd service is managed by NixOS |
| `hermes gateway uninstall` | The systemd service is managed by NixOS |

This prevents drift between what Nix declares and what's on disk. Detection uses two signals:

1. **The `HERMES_MANAGED` environment variable.** The service sets it, and the gateway process reads it.
2. **The `.managed` marker file** in `HERMES_HOME`. The activation script writes it, and an interactive shell reads it, blocking configuration changes through the CLI.

Both signals hold the name of the system that manages the install. Thus the refusal names the correct rebuild command. The NixOS module gives `sudo nixos-rebuild switch`. The Home Manager module gives `home-manager switch`.

---

## Home Manager Module

The flake also exports `homeManagerModules.default`. Hermes is an agent for one person. The credentials, the memory, the sessions all belong to that person. Thus a user service is the correct shape on a personal machine. It runs on each distribution that Home Manager supports, and not only on NixOS.

The option set is the same set that the NixOS module uses. It is `services.hermes-agent`, with the same `settings`, `environmentFiles`, `documents`, `mcpServers` and `extraPlugins` options. Each example above works here without a change. Only the necessary parts are different:

| | NixOS module | Home Manager module |
|---|---|---|
| Runs as | a system user that you declare, with `user`, `group` and `createUser` | you |
| State directory | `stateDir` and `/.hermes` | `hermesHome`, set directly. The default is `~/.hermes`. |
| Service | `systemd.services` | `systemd.user.services` |
| CLI on the PATH | `addToSystemPackages`, which exports `HERMES_HOME` for the full system | `programs.hermes-agent.enable`, which exports it for your session only |


### Add the Flake Input

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
    hermes-agent.url = "github:NousResearch/hermes-agent";
  };
}
```

Then import the module into your Home Manager configuration. The configuration can be standalone. It can also be under `home-manager.users.<name>` in a NixOS configuration:

```nix
{
  imports = [ hermes-agent.homeManagerModules.default ];

  services.hermes-agent = {
    enable = true;
    gateway.enable = true;
    settings.model.default = "anthropic/claude-sonnet-4";
    environmentFiles = [ config.sops.secrets."hermes-env".path ];
  };
}
```

`home-manager switch` makes `~/.hermes`, writes `config.yaml`, builds `.env` and starts the gateway as a user service.

:::warning Enable linger, or the service stops at logout
CAUTION: Enable linger for your account. Without linger, systemd stops the user manager when your last session ends, and the gateway stops with it. Home Manager cannot set linger, because linger is a property of the account:

```nix
# NixOS
users.users.your-username.linger = true;
```

```bash
# anywhere else
sudo loginctl enable-linger your-username
```

:::

### Verify It Works

```bash
# Linux
systemctl --user status hermes-agent
journalctl --user -u hermes-agent -f

hermes --version
hermes config     # shows the configuration that Nix wrote
```

---


## Plugins

The NixOS module supports declarative plugin installation — no imperative `hermes plugins install` needed.

### Directory Plugins (`extraPlugins`)

For plugins that are just a source tree with `plugin.yaml` + `__init__.py` (e.g., [hermes-lcm](https://github.com/stephenschoettler/hermes-lcm)):

```nix
services.hermes-agent.extraPlugins = [
  (pkgs.fetchFromGitHub {
    owner = "stephenschoettler";
    repo = "hermes-lcm";
    rev = "v0.7.0";
    hash = "sha256-...";
  })
];
```

Plugins are symlinked into `$HERMES_HOME/plugins/` at activation time. Hermes discovers them via its normal directory scan. Removing a plugin from the list and running `nixos-rebuild switch` removes the symlink.

### Entry-Point Plugins (`extraPythonPackages`)

For pip-packaged plugins that register via `[project.entry-points."hermes_agent.plugins"]` (e.g., [rtk-hermes](https://github.com/ogallotti/rtk-hermes)):

```nix
services.hermes-agent.extraPythonPackages = [
  (config.services.hermes-agent.package.python.pkgs.buildPythonPackage {
    pname = "rtk-hermes";
    version = "1.0.0";
    src = pkgs.fetchFromGitHub {
      owner = "ogallotti";
      repo = "rtk-hermes";
      rev = "v1.0.0";
      hash = "sha256-...";
    };
    format = "pyproject";
    build-system = [ config.services.hermes-agent.package.python.pkgs.setuptools ];
  })
];
```

The package's `site-packages` is added to PYTHONPATH in the hermes wrapper. `importlib.metadata` discovers the entry point at session start.

### Optional Dependency Groups (`extraDependencyGroups`)

For optional extras declared in hermes-agent's `pyproject.toml`, use `extraDependencyGroups` to include them in the sealed venv at build time. This is required for any extra the selected package does not already ship — on Nix, runtime installation into the read-only store is not possible.

```nix
# Add Google Meet support on top of what the package ships
services.hermes-agent.extraDependencyGroups = [ "google-meet" ];
```

`extraDependencyGroups` and `extraPythonPackages` **add to** the groups the selected package ships; they never replace them. The `default` package already ships `messaging`, `voice`, `exa` and the other groups in the table below, so naming one of those changes nothing. The same holds for `pkgs.hermes-agent.override { extraDependencyGroups = [ ... ]; }`. For a smaller build, start from a smaller package: `package = inputs.hermes-agent.packages.${system}.minimal;` (or `#messaging`), or `.override { defaultDependencyGroups = [ ]; }` to drop the shipped groups and list only the ones you want.

These groups join the core dependency resolution at build time. Conflicting
requirements can still fail that resolution. The table lists common groups;
`pyproject.toml` is authoritative for the complete list and platform markers.

| Group | What it enables |
|-------|-----------------|
| `messaging` | Discord, Telegram, Slack |
| `matrix` | Matrix/Element (mautrix with encryption; Linux only) |
| `dingtalk` | DingTalk |
| `feishu` | Feishu/Lark |
| `anthropic` | Native Anthropic SDK (not needed via OpenRouter) |
| `bedrock` | AWS Bedrock (boto3) |
| `azure-identity` | Azure Entra ID auth |
| `exa` | Exa web search |
| `firecrawl` | Firecrawl web search |
| `fal` | FAL image generation |

Memory providers that live in the [plugin catalog](../user-guide/features/plugins.md) rather than in the Hermes tree (e.g. Hindsight) are not extras. Install them like any catalog plugin with `hermes plugins install hindsight`, or declaratively via [`extraPlugins`](#directory-plugins-extraplugins) pointing at the plugin's source tree.

Or use the pre-built `#messaging` or `#full` flake packages instead of per-extra configuration (see [Quick Start](#quick-start-any-nix-user)).

**When to use which:**

| Need | Option |
|------|--------|
| Enable a pyproject.toml optional extra | `extraDependencyGroups` |
| Add an external Python plugin not in pyproject.toml | `extraPythonPackages` |
| Add a system binary (pandoc, jq, etc.) | `extraPackages` |
| Add a directory-based plugin source tree | `extraPlugins` |

### Combining Both

A directory plugin with third-party Python dependencies needs both options:

```nix
services.hermes-agent = {
  extraPlugins = [ my-plugin-src ];          # plugin source
  extraPythonPackages = [ config.services.hermes-agent.package.python.pkgs.redis ];  # its Python dep
  extraPackages = [ pkgs.redis ];            # system binary it needs
};
```

### Using the Overlay

External flakes can override the package directly:

```nix
{
  inputs.hermes-agent.url = "github:NousResearch/hermes-agent";
  outputs = { hermes-agent, nixpkgs, ... }: {
    nixpkgs.overlays = [ hermes-agent.overlays.default ];
    # Then:
    #   pkgs.hermes-agent.override { extraPythonPackages = [...]; }
    #   pkgs.hermes-agent.override { extraDependencyGroups = [ "google-meet" ]; }
    # Both add to the groups the default package ships. To drop those:
    #   pkgs.hermes-agent.override { defaultDependencyGroups = [ ]; }
  };
}
```

### Plugin Configuration

Plugins still need to be enabled in `config.yaml`. Add them via the declarative settings:

```nix
services.hermes-agent.settings.plugins.enabled = [
  "hermes-lcm"
  "rtk-rewrite"
];
```

:::note
A build-time collision check prevents plugin packages from shadowing core hermes dependencies. If a plugin provides a package already in the sealed venv, `nixos-rebuild` fails with a clear error.
:::

---

## Development

### Dev Shell

The flake provides an editable Python venv with the `dev` dependency group, running
on the interpreter `pm/lock.json` pins. `HERMES_PYTHON` and `VIRTUAL_ENV` point at it,
and it needs no `PYTHONPATH`. It does not install
Python dependencies into a repository-local `.venv`. The shell also provides
Node.js and runtime tools. Its pnpm hook refreshes JS workspaces when their inputs change.

The shell does not change which `hermes` runs. `hermes` stays your installed Hermes, so you
can keep using it while you work on the checkout. `hermes-checkout` runs this checkout's code
and takes the same arguments. Give it a separate `HERMES_HOME` when a change touches stored
state, because both commands read the same one by default.

```bash
cd hermes-agent
nix develop
"$HERMES_PYTHON" -c "import sys; print(sys.executable); print(sys.version)"
hermes-checkout --version
HERMES_HOME="$PWD/.scratch/home" hermes-checkout chat
```

### direnv (Recommended)

The included `.envrc` activates the dev shell automatically:

```bash
cd hermes-agent
direnv allow    # one-time
# Nix reuses its built Python environment; the pnpm hook checks JS inputs.
```

### Updating the pnpm Lockfile

The flake builds every JavaScript surface from one fixed-output pnpm store whose
hash is in `nix/pnpm-deps-hash`. A `pnpm-lock.yaml` change that adds, removes or
re-resolves a package changes that hash, and every frontend build fails with a
hash mismatch until it is updated. `hermes pm update --pnpm`, a plain
`pnpm install` and the `nix develop` shell hook rewrite the lockfile but not the
hash. From the checkout, run:

```bash
nix run .#update-pnpm-lockfile
```

It brings `pnpm-lock.yaml` up to date with the manifests, rebuilds `.#pnpm-deps`
to take the new hash, writes `nix/pnpm-deps-hash`, and builds `.#tui`. Without Nix, copy the hash the `nix` CI workflow's
`pnpm-deps-hash` job prints.

### Flake Checks

The flake includes build-time verification that runs in CI and locally:

```bash
# Run all checks
nix flake check

# Individual checks
nix build .#checks.x86_64-linux.package-contents   # binaries exist + version
nix build .#checks.x86_64-linux.entry-points-sync  # pyproject.toml ↔ Nix package sync
nix build .#checks.x86_64-linux.cli-commands        # gateway/config subcommands
nix build .#checks.x86_64-linux.managed-guard       # HERMES_MANAGED blocks mutation
nix build .#checks.x86_64-linux.bundled-skills      # skills present in package
nix build .#checks.x86_64-linux.config-roundtrip    # merge script preserves user keys
```

<details>
<summary><strong>What each check verifies</strong></summary>

| Check | What it tests |
|---|---|
| `package-contents` | `hermes` and `hermes-agent` binaries exist and `hermes --version` runs |
| `entry-points-sync` | Every `[project.scripts]` entry in `pyproject.toml` has a wrapped binary in the Nix package |
| `cli-commands` | `hermes --help` exposes `gateway` and `config` subcommands |
| `managed-guard` | `HERMES_MANAGED=true hermes config set ...` prints the NixOS error |
| `bundled-skills` | Skills directory exists, contains SKILL.md files, `HERMES_BUNDLED_SKILLS` is set in wrapper |
| `config-roundtrip` | 7 merge scenarios: fresh install, Nix override, user key preservation, mixed merge, MCP additive merge, nested deep merge, idempotency |

</details>

---

## Options Reference

### Core

| Option | Type | Default | Description |
|---|---|---|---|
| `enable` | `bool` | `false` | Enable the hermes-agent service |
| `package` | `package` | `hermes-agent` | The hermes-agent package to use |
| `user` | `str` | `"hermes"` | System user |
| `group` | `str` | `"hermes"` | System group |
| `createUser` | `bool` | `true` | Auto-create user/group |
| `stateDir` | `str` | `"/var/lib/hermes"` | State directory (`HERMES_HOME` parent) |
| `workingDirectory` | `str` | `"${stateDir}/workspace"` | Agent working directory |
| `addToSystemPackages` | `bool` | `false` | Add `hermes` CLI to system PATH and set `HERMES_HOME` system-wide |

### Configuration

| Option | Type | Default | Description |
|---|---|---|---|
| `settings` | `attrs` (deep-merged) | `{}` | Declarative config rendered as `config.yaml`. Supports arbitrary nesting; multiple definitions are merged via `lib.recursiveUpdate` |
| `configFile` | `null` or `path` | `null` | Path to an existing `config.yaml`. Overrides `settings` entirely if set |

### Secrets & Environment

| Option | Type | Default | Description |
|---|---|---|---|
| `environmentFiles` | `listOf str` | `[]` | Paths to env files with secrets. Merged into `$HERMES_HOME/.env` at activation time |
| `environment` | `attrsOf str` | `{}` | Non-secret env vars. **Visible in Nix store** — do not put secrets here |
| `authFile` | `null` or `path` | `null` | OAuth credentials seed. Only copied on first deploy |
| `authFileForceOverwrite` | `bool` | `false` | Always overwrite `auth.json` from `authFile` on activation |

### Documents

| Option | Type | Default | Description |
|---|---|---|---|
| `documents` | `attrsOf (either str path)` | `{}` | Workspace files. Each key is a path relative to `workingDirectory`. You must set that option to use this one. |
| `hermesHomeFiles` | `attrsOf (either str path)` | `{}` | Files that go into `HERMES_HOME`. `SOUL.md` and `memories/` must be here, or Hermes does not load them. |

### MCP Servers

| Option | Type | Default | Description |
|---|---|---|---|
| `mcpServers` | `attrsOf submodule` | `{}` | MCP server definitions, merged into `settings.mcp_servers` |
| `mcpServers.<name>.command` | `null` or `str` | `null` | Server command (stdio transport) |
| `mcpServers.<name>.args` | `listOf str` | `[]` | Command arguments |
| `mcpServers.<name>.env` | `attrsOf str` | `{}` | Environment variables for the server process |
| `mcpServers.<name>.url` | `null` or `str` | `null` | Server endpoint URL (HTTP/StreamableHTTP transport) |
| `mcpServers.<name>.headers` | `attrsOf str` | `{}` | HTTP headers, e.g. `Authorization` |
| `mcpServers.<name>.auth` | `null` or `"oauth"` | `null` | Authentication method. `"oauth"` enables OAuth 2.1 PKCE |
| `mcpServers.<name>.enabled` | `bool` | `true` | Enable or disable this server |
| `mcpServers.<name>.timeout` | `null` or `int` | `null` | Tool call timeout in seconds (default: 120) |
| `mcpServers.<name>.connect_timeout` | `null` or `int` | `null` | Connection timeout in seconds (default: 60) |
| `mcpServers.<name>.tools` | `null` or `submodule` | `null` | Tool filtering (`include`/`exclude` lists) |
| `mcpServers.<name>.sampling` | `null` or `submodule` | `null` | Sampling config for server-initiated LLM requests |

### Service Behavior

| Option | Type | Default | Description |
|---|---|---|---|
| `extraArgs` | `listOf str` | `[]` | Extra args for `hermes gateway` |
| `extraPackages` | `listOf package` | `[]` | Extra packages available to the agent. Added to the hermes user's per-user profile so terminal commands and skills all see them |
| `extraPlugins` | `listOf package` | `[]` | Directory plugin packages to symlink into `$HERMES_HOME/plugins/`. Each must contain `plugin.yaml` |
| `extraPythonPackages` | `listOf package` | `[]` | Python packages added to PYTHONPATH for entry-point plugin discovery. Use the selected package’s `python.pkgs` |
| `extraDependencyGroups` | `listOf str` | `[]` | pyproject.toml optional extras added to the groups `package` already ships (e.g. `["google-meet"]`). Resolved by uv — no collisions |
| `restart` | `str` | `"always"` | The systemd `Restart=` policy. |
| `restartSec` | `int` | `5` | The systemd `RestartSec=` value. |

### Home Manager only

| Option | Type | Default | Description |
|---|---|---|---|
| `hermesHome` | `str` | `"${config.home.homeDirectory}/.hermes"` | `HERMES_HOME` directly. The NixOS module builds it from `stateDir`. |
| `gateway.enable` | `bool` | `false` | Run the messaging gateway. On the NixOS module the gateway is the service, so that module has no such option. |

### `programs.hermes-agent` (Home Manager only)

Home Manager separates "install this application for me" from "run this
daemon". `services.hermes-agent` keeps the state, the configuration and the
daemons. `programs.hermes-agent` installs what you use and reads
`hermesHome` from the services.

| Option | Type | Default | Description |
|---|---|---|---|
| `enable` | `bool` | `false` | Add the `hermes` CLI to `home.packages`, and export `HERMES_HOME` for your shells |
| `package` | `package` | `services.hermes-agent.package` | The package to install. The default applies `extraPythonPackages` and `extraDependencyGroups` from the services, so both are one build. |
`services.hermes-agent.installPackage` was removed by this split. A
configuration that still sets it gets an error that names the
replacement.


## Directory Layout

### Native Mode

```
/var/lib/hermes/                     # stateDir (owned by hermes:hermes, 0750)
├── .hermes/                         # HERMES_HOME
│   ├── SOUL.md                      # from hermesHomeFiles: the agent identity
│   ├── config.yaml                  # Nix-generated (deep-merged each rebuild)
│   ├── .managed                     # Marker: CLI config mutation blocked
│   ├── .env                         # Merged from environment + environmentFiles
│   ├── auth.json                    # OAuth credentials (seeded, then self-managed)
│   ├── gateway.pid
│   ├── state.db
│   ├── mcp-tokens/                  # OAuth tokens for MCP servers
│   ├── sessions/
│   ├── memories/
│   ├── skills/
│   └── logs/
├── home/                            # Agent HOME
└── workspace/                       # Agent working directory
    ├── AGENTS.md                    # from the documents option
    └── (agent-created files)
```

### Home Manager

```
~/.hermes/                           # hermesHome (HERMES_HOME), 0700
├── SOUL.md                          # from hermesHomeFiles
├── config.yaml                      # written by Nix, merged at each activation
├── .managed                         # marker: names the system that manages this
├── .env                             # written again from environment + environmentFiles
├── auth.json                        # OAuth credentials: seeded, then Hermes owns it
├── memories/  sessions/  skills/  logs/  plugins/
└── (runtime state)

~/                                   # workingDirectory, your home by default
└── AGENTS.md                        # from the documents option
```


## Updating

```bash
# Update the flake input (run from the directory containing flake.nix)
cd /etc/nixos && nix flake update hermes-agent

# Rebuild
sudo nixos-rebuild switch          # for the NixOS module
home-manager switch                # for the Home Manager module
```


---

## Troubleshooting

### Service Logs

```bash
journalctl -u hermes-agent -f
```

### Verify Secrets Are Loaded

If the agent starts but can't authenticate with the LLM provider, check that the `.env` file was merged correctly:

```bash
sudo -u hermes cat /var/lib/hermes/.hermes/.env
```

### Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot save configuration: managed by NixOS` | CLI guards active | Edit `configuration.nix` and `nixos-rebuild switch` |
| `No adapter available for discord` (or telegram/slack) | Messaging deps missing from the sealed Nix venv | Install `#messaging` variant: `nix profile install ...#messaging`. For NixOS module: `extraDependencyGroups = [ "messaging" ]`. Read `journalctl -u hermes-agent` for `InstallError` or `requirements not met` and the underlying cause. |
| Permission denied on `/var/lib/hermes` | State dir is `0750 hermes:hermes` | Use `sudo -u hermes` |
| Tool added via `extraPackages` not found in terminal | Requires `nixos-rebuild switch` to update the per-user profile | Rebuild and restart: `nixos-rebuild switch && systemctl restart hermes-agent` |
