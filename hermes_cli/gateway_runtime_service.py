"""Non-installing service-manager operations for an explicit canonical profile.

The legacy start helpers refresh units, regenerate plists, or fall back to
unmanaged processes. Client startup must not call those helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
import time
from xml.parsers.expat import ExpatError

from gateway.runtime_contract import RuntimeState


class RuntimeStartError(RuntimeError):
    def __init__(self, reason: str, state: RuntimeState = "inaccessible"):
        super().__init__(reason)
        self.reason, self.state = reason, state


def remaining(deadline: float) -> float:
    budget = deadline - time.monotonic()
    if budget <= 0:
        raise TimeoutError
    return budget


def service_suffix(home: Path) -> str:
    """Host-service suffix for the REQUESTED home, never the initiating client's env.

    Same rule as ``hermes_cli.gateway._profile_suffix``: only the platform-native
    default home owns the bare unit name; ``<root>/profiles/<name>`` yields the
    profile name; any other root (Docker, a temp harness) yields a path hash so a
    custom root can never resolve to the production ``hermes-gateway`` unit.
    """
    from hermes_constants import account_default_hermes_home
    from hermes_cli.gateway import _profile_name_from_home
    default = account_default_hermes_home().resolve()
    home = home.resolve()
    if home == default:
        return ""
    root = default
    if not home.is_relative_to(default):
        root = home.parent.parent if home.parent.name == "profiles" else home
    if home != root:
        name = _profile_name_from_home(home, root)
        if name:
            return name
    return hashlib.sha256(str(home).encode()).hexdigest()[:8]


@dataclass(frozen=True)
class ExistingService:
    backend: str
    start_argv: tuple[str, ...]
    running: bool = False


def _run(argv: list[str] | tuple[str, ...], deadline: float, *, encoding: str | None = "utf-8"):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                          text=encoding is not None, encoding=encoding, errors="strict" if encoding else None,
                          timeout=remaining(deadline), check=False)


def _verify_binding(verify, *args, **kwargs):
    try:
        return verify(*args, **kwargs)
    except (ValueError, TypeError, KeyError, AttributeError, ExpatError) as exc:
        reason = str(exc)
        raise RuntimeStartError(reason if reason in {
            "profile_mismatch", "service_account_mismatch", "service_disabled"
        } else "service_identity_unverified") from None


def _exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _systemd(home: Path, deadline: float) -> ExistingService | None:
    from hermes_cli import gateway as gw
    from hermes_cli.gateway_runtime_service_identity import ForeignRoot, SYSTEMD_IDENTITY_PROPERTIES, verify_systemd
    suffix = service_suffix(home)
    unit = f"hermes-gateway{'-' + suffix if suffix else ''}.service"
    paths = (Path.home() / ".config/systemd/user" / unit, Path("/etc/systemd/system") / unit)
    # On non-systemd hosts there is no manager to own transient units. Installed
    # definitions still count: a broken manager is not permission to bypass one.
    # Every Hermes entrypoint ensures the gateway, so a host with no installed unit must not
    # touch the service manager at all (a headless runner has no user bus to answer it).
    # With a unit installed anywhere, both scopes are asked: two claiming the name is a conflict.
    installed = (*paths, Path("/usr/lib/systemd/system") / unit, Path("/lib/systemd/system") / unit)
    if not any(_exists(p) for p in installed):
        return None
    found = []
    for system in (False, True):
        command = gw._systemctl_cmd(system)
        result = _run([*command, "show", unit, "--no-pager", "--all",
                       "--property=LoadState,ActiveState,SubState,UnitFileState," + ",".join(SYSTEMD_IDENTITY_PROPERTIES)], deadline)
        props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if not _exists(paths[int(system)]) and (result.returncode or props.get("LoadState") == "not-found"):
            # Nothing installed in this scope: an unreachable or empty manager there has no
            # unit that could serve or block this home.
            continue
        if result.returncode or props.get("LoadState") != "loaded":
            raise RuntimeStartError("service_manager_unavailable")
        if props.get("UnitFileState") in {"masked", "masked-runtime"}:
            raise RuntimeStartError("service_masked")
        state = props.get("ActiveState")
        if state == "deactivating":
            raise RuntimeStartError("service_draining", "draining")
        if state not in {"inactive", "active", "activating", "reloading", "failed"}:
            raise RuntimeStartError("service_state_unknown")
        if state == "failed":
            raise RuntimeStartError("service_failed")
        environment = _run([*command, "show-environment"], deadline)
        if environment.returncode:
            raise RuntimeStartError("service_identity_unverified")
        try:
            verify_systemd(props, environment.stdout, home, system=system)
        except ForeignRoot:
            # A unit named like ours but pinned to another HOME root belongs to a
            # different install (a sandbox HOME beside a real one); it neither serves
            # nor blocks this home.
            continue
        except ValueError as exc:
            reason = str(exc)
            raise RuntimeStartError(reason if reason in {
                "profile_mismatch", "service_account_mismatch"
            } else "service_identity_unverified") from None
        found.append(ExistingService("systemd", tuple([*command, "--no-ask-password", "start", unit]),
                                     state != "inactive"))
    if len(found) > 1:
        raise RuntimeStartError("service_scope_conflict", "conflict")
    return found[0] if found else None


def discover_existing_gateway_service(home: Path, *, deadline: float) -> ExistingService | None:
    try:
        return _systemd(home, deadline)
    except subprocess.TimeoutExpired:
        raise TimeoutError from None
    except (OSError, ValueError) as exc:
        raise RuntimeStartError("service_manager_unavailable") from exc


def start_existing_gateway_service(service: ExistingService, *, deadline: float) -> None:
    """Request start once; only live control readiness can establish success."""
    if service.running:
        return

    try:
        result = _run(service.start_argv, deadline, encoding=None)
    except subprocess.TimeoutExpired:
        raise TimeoutError from None
    except OSError as exc:
        raise RuntimeStartError("service_start_failed") from exc
    if result.returncode:
        raise RuntimeStartError("service_start_failed")
