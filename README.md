# Hernes Agent

## Install from source

On Linux, install Git, Bash, zsh, and `uv` on your `PATH`. Clone this fork and run:

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
- Omits the native computer-use driver, A2A, X Search, and the bundled email, YouTube, and X skills.
