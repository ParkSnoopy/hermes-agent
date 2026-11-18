"""Profile delete/rename: remove the old name's system-scope supervisor registration.

Every registration is keyed on the profile name and starts ``--profile <old>`` with
``HERMES_HOME`` pinned to a directory that the delete removed or the rename moved, so a
survivor crash-loops at the next boot.
Callers bind the profile's home (``set_hermes_home_override``) before calling so the
name-deriving helpers resolve THAT profile, never the ambient one.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def owned_by_profile(definition: Path, profile_dir: Path) -> bool:
    """False (one-line notice) when *definition* pins another HERMES_HOME. Unit names derive from the
    profile name, and a scratch ``HOME``'s ``profiles/<name>`` resolves the ACCOUNT's real
    ``hermes-gateway-<name>`` unit (the unit dir is account-anchored): delete/rename then disabled,
    stopped and unlinked a live gateway of another install. Same owner rule as
    ``gateway_service_owner.definition_belongs_to_home``."""
    from hermes_cli.gateway_service_owner import _resolve, pinned_home
    raw = pinned_home(definition)
    if raw is None or _resolve(raw) == _resolve(profile_dir):
        return True
    print(f"↷ {definition} runs HERMES_HOME={raw}, not {profile_dir}: another install's service, left alone")
    return False


def remove_system_systemd_unit(profile_dir: Path) -> bool:
    """Remove ``/etc/systemd/system/hermes-gateway-<profile>.service`` when root; otherwise
    name the leftover and the exact command, because the profile dir the ``gateway uninstall
    --system`` verb would need to resolve the name from is about to vanish."""
    from hermes_cli import gateway
    svc_name = gateway.get_service_name()
    unit = gateway._SYSTEM_UNIT_DIR / f"{svc_name}.service"
    if not unit.exists() or not owned_by_profile(unit, profile_dir):
        return False
    if os.geteuid() != 0:
        print(f"⚠ System service {svc_name} remains at {unit}; it will restart the removed profile at boot.")
        print(f"  Remove it with: sudo systemctl disable --now {svc_name} && sudo rm {unit} && sudo systemctl daemon-reload")
        return False
    subprocess.run(["systemctl", "disable", svc_name], capture_output=True, check=False, timeout=30)
    stopped = subprocess.run(["systemctl", "stop", svc_name], capture_output=True, check=False, timeout=30)
    unit.unlink(missing_ok=True)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True, check=False, timeout=30)
    if getattr(stopped, "returncode", 0) != 0:
        # The unit file is gone (it must not resurrect the removed profile at boot), but the
        # gateway it supervised may still be running under the old name.
        print(f"⚠ System service {svc_name} unit removed, but `systemctl stop` exited {stopped.returncode}; "
              f"its gateway may still be running. Stop it with: sudo systemctl stop {svc_name}")
        return True
    print(f"✓ System service {svc_name} removed")
    return True
