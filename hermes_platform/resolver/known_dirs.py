"""Linux directories where tools install outside PATH, grouped by ecosystem."""

from __future__ import annotations


def user_local_bin() -> tuple[str, ...]:
    return ("~/.local/bin",)


def rust_tool_dirs() -> tuple[str, ...]:
    return ("~/.cargo/bin",)


def node_tool_dirs() -> tuple[str, ...]:
    return ("~/.npm-global/bin", "~/.bun/bin", "~/.volta/bin")


def hermes_vendored_dirs() -> tuple[str, ...]:
    return ("~/.hermes/bin",)


def machine_marker_files() -> tuple[str, ...]:
    """Where Hermes records that this OS user has used it."""
    return ("$XDG_STATE_HOME/nous-research/.machine", "~/.local/state/nous-research/.machine")


def hermes_home_globs() -> tuple[str, ...]:
    return ("~/.hermes*",)


def gateway_service_definition_globs() -> tuple[str, ...]:
    """This user's gateway service definitions, each naming the HERMES_HOME it serves."""
    return ("$XDG_CONFIG_HOME/systemd/user/hermes-gateway*.service", "~/.config/systemd/user/hermes-gateway*.service")


def system_gateway_service_definition_globs() -> tuple[str, ...]:
    """Machine-wide gateway services, running as the account their User= names."""
    return ("/etc/systemd/system/hermes-gateway*.service",)


def flatpak_export_dirs() -> tuple[str, ...]:
    """Flatpak launcher directories, system before per-user."""
    return ("/var/lib/flatpak/exports/bin", "~/.local/share/flatpak/exports/bin")


def snap_bin_dirs() -> tuple[str, ...]:
    return ("/snap/bin",)
