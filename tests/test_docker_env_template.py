"""The container's first-boot environment template must enter its build context."""

import fnmatch
from pathlib import Path


DOCKERIGNORE = Path(__file__).resolve().parents[1] / ".dockerignore"


def test_dockerignore_keeps_env_example_template() -> None:
    """The first-boot seed copies /opt/hermes/.env.example into HERMES_HOME.

    .env.* also matches the template, so its re-include must come after
    exclusion rules, with no later rule excluding it again.
    """
    lines = [line.strip() for line in DOCKERIGNORE.read_text().splitlines()]
    rules = [line for line in lines if line and not line.startswith("#")]
    included = True
    for rule in rules:
        pattern = rule.removeprefix("!")
        if not pattern.endswith("/") and fnmatch.fnmatchcase(".env.example", pattern):
            included = rule.startswith("!")
    assert included, (
        ".env.example must survive .dockerignore — docker/stage2-hook.sh "
        "seeds HERMES_HOME/.env from it on first boot"
    )
