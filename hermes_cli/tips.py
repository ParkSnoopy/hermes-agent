"""English discovery tips and composer suggestions."""

import random


TIPS = (
    '/bg <prompt> runs a task in a separate session while your current one stays free; /btw <question> answers a side question about this conversation without interrupting it.',
    '/branch forks the current session so you can explore a different direction without losing progress.',
    '/compress manually compresses conversation context when things get long.',
    '/rollback lists filesystem checkpoints — restore files the agent modified to any prior state.',
    '/rollback diff 2 previews what changed since checkpoint 2 without restoring anything.',
    '/rollback 2 src/file.py restores a single file from a specific checkpoint.',
    '/title "my project" names your session — resume it later with /resume or hermes -c.',
    '/resume picks up where you left off in a previously named session.',
    '/queue <prompt> queues a message for the next turn without interrupting the current one.',
    '/undo removes the last user/assistant exchange from the conversation.',
    "/retry resends your last message — useful when the agent's response wasn't quite right.",
    '/verbose cycles tool progress display: off → new → all → verbose.',
    "/reasoning high increases the model's thinking depth. /reasoning show displays the reasoning.",
    '/fast toggles priority processing for faster API responses (provider-dependent).',
    '/yolo skips all dangerous command approval prompts for the rest of the session.',
    '/model lets you switch models mid-session — try /model sonnet or /model gpt-5.',
    '/model --global changes your default model permanently.',
    '/personality pirate sets a fun personality — 14 built-in options from kawaii to shakespeare.',
    '/skin changes the CLI theme — try ares, mono, slate, poseidon, or charizard.',
    '/statusbar toggles a persistent bar showing model, tokens, context fill %, cost, and duration.',
    '/tools disable browser temporarily removes browser tools for the current session.',
    '/browser connect attaches browser tools to your running Chromium-family browser via CDP.',
    '/plugins lists installed plugins and their status.',
)

COMPOSER_PLACEHOLDERS = (
    'Ask anything, or type / for commands…',
    "Summarize what's in this folder",
    'Draft a reply to the last email in my inbox',
    'Plan a feature, then build it step by step',
    'Find and fix a failing test',
    'Research this topic and write me a brief',
    'What changed in this repo recently?',
    'Turn these notes into a to-do list',
    'Explain this error and how to fix it',
    'Set a reminder or schedule a recurring task',
    'Type / to browse commands, or Ctrl+P for the palette',
)

def get_random_tip(exclude_recent: int = 0) -> str:
    """Return a random discovery tip."""
    return random.choice(TIPS)


def get_random_composer_placeholder() -> str:
    """Return a random example task for the empty composer."""
    return random.choice(COMPOSER_PLACEHOLDERS)
