# Hermes Agent

A Linux terminal agent with persistent memory, reusable skills, conversation search, and delegated tasks.  
Use the CLI or TUI with a hosted model provider or your own compatible endpoint.  

## Features

- Interactive terminal interfaces with streaming output, conversation history, and slash commands.
- Local command execution, file editing, Python tool orchestration, and browser automation.
- Persistent memory, searchable sessions, and skills compatible with the [Agent Skills standard](https://agentskills.io).
- Isolated subagents for parallel work.
- Configurable model providers, MCP servers, and plugins.

## Install from source

From the root of your Linux source checkout, activate the managed environment and run setup:  

```bash
source ./activate
hermes setup
hermes
```

Activation prepares the pinned Python interpreter and project-managed dependencies.  
The first activation may require network access and a native compiler.  
See [installation](website/docs/getting-started/installation.md) for deployment options.  

## Getting started

```bash
hermes              # Interactive CLI — start a conversation
hermes model        # Choose your LLM provider and model
hermes tools        # Configure which tools are enabled
hermes config set   # Set individual config values
hermes config get   # Print individual config values

hermes setup        # Run the full setup wizard (configures everything at once)
hermes update       # Update the source installation
hermes doctor       # Diagnose any issues
```

Start a conversation with `hermes`, use `/new` for a fresh session, `/model` to switch models, and `/skills` to browse skills.  

## Documentation

- [CLI usage](website/docs/user-guide/cli.md)
- [Configuration](website/docs/user-guide/configuration.md)
- [Model providers](website/docs/integrations/providers.md)
- [Tools and toolsets](website/docs/user-guide/features/tools.md)
- [Skills](website/docs/user-guide/features/skills.md)
- [Memory](website/docs/user-guide/features/memory.md)
- [MCP integration](website/docs/user-guide/features/mcp.md)
- [Security](website/docs/user-guide/security.md)

## Development

See [contributing](CONTRIBUTING.md#development-setup) and the [package-manager workflow](website/docs/reference/package-management.md#developer-workflow) for the development environment and test commands.  

## License

MIT — see [LICENSE](LICENSE).  
