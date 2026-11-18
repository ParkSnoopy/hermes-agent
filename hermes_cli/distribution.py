"""Network sources owned by the ParkSnoopy distribution."""

import re

REPOSITORY = "ParkSnoopy/hermes-agent"
REPO_WEB = f"https://github.com/{REPOSITORY}"
REPO_GIT = f"{REPO_WEB}.git"
RAW_MAIN = f"https://raw.githubusercontent.com/{REPOSITORY}/main"
INSTALLER_URL = f"{RAW_MAIN}/scripts/install.sh"
MODEL_CATALOG_URL = f"{RAW_MAIN}/website/static/api/model-catalog.json"
# Generated services are not published by this fork. Leave them offline until
# there is a fork-owned publisher; these are not paths under raw main.
PLUGIN_CATALOG_URL: str | None = None
SKILLS_INDEX_URL: str | None = None
RELEASE_CHANNEL_BASE: str | None = None


def is_distribution_origin(url: str | None) -> bool:
    return bool(url and re.fullmatch(
        rf"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        rf"{re.escape(REPOSITORY)}(?:\.git)?/?",
        url.strip(), re.IGNORECASE,
    ))
