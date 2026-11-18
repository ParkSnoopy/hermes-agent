# Hernes Agent

## Install from source

On Linux or WSL2, make Git, Bash, curl, and `uv` available on your `PATH`. Once these installer changes are published on the fork's `main`, download and run the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/ParkSnoopy/hermes-agent/main/scripts/install.sh -o install.sh && bash install.sh
```

The installer places the checkout in `~/.hermes/hermes-agent/` and keeps settings and sessions in `~/.hermes/`. It publishes `hermes` in `~/.local/bin/` if another installation does not own that command. Open a new shell after installation. It skips setup and configuration migration on existing profiles. An explicit `--channel` choice updates that checkout's channel setting. For an unattended new install, pass `--non-interactive` and run `hermes setup` later. The installer does not register a gateway service automatically; interactive Quick Setup may offer to do so.

If that checkout path already belongs to another installation, use `--dir` to choose a new checkout and `--hermes-home` to keep its data separate. The installer does not convert another checkout to this fork. In a side-by-side install, set `HERMES_HOME` to the new profile path and invoke the fork's `.hermes/bin/hermes` from its checkout.

`HERMES_REPO_URL` overrides the install source for development. Self-update is supported only for checkouts that track `ParkSnoopy/hermes-agent`.

For development instead, clone and activate the checkout in zsh:

```zsh
git clone https://github.com/ParkSnoopy/hermes-agent.git
cd hermes-agent
source ./activate
hermes setup
hermes
```

The first activation may need network access and a native compiler. Use `hermes --tui` for the full-screen interface.

## Differences from upstream

- Focuses on terminal chat. The local gateway still owns sessions and runs the agent without a messaging adapter.
- Runs natively without a bundled container deployment. The Docker management skill is still available.
- Uses system `uv` and defaults to the ASCII busy indicator.
- The installer, source updates, model catalogs, and optional skills use `ParkSnoopy/hermes-agent`. The default channel is `main`; `stable` needs a published fork release. Until the fork publishes live catalogs, plugins use the bundled catalog and skill search queries its configured registries directly. Model providers and third-party dependencies still use their own services.
- `hermes debug share --nous` requires `HERMES_DIAGNOSTICS_BASE_URL`. No Nous upload service is selected by default; `hermes debug share --local` does not upload.
- Omits the native computer-use driver, A2A, X Search, and the bundled email, YouTube, and X skills.
