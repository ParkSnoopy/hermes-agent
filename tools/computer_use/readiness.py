"""Computer Use readiness from the selected Linux driver or plugin backend."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import suppress
from typing import Any, Optional


_RUNTIME_PLATFORMS = frozenset({"linux"})


def _run(binary: str, *args: str, timeout: float) -> subprocess.CompletedProcess:
    from tools.computer_use.cua_backend import sanitized_cua_driver_env

    return subprocess.run([binary, *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, env=sanitized_cua_driver_env(), stdin=subprocess.DEVNULL, check=False)

def _json_out(binary: str, *args: str, timeout: float) -> Any:
    """Run ``binary args`` and parse stdout as JSON (``None`` on empty output)."""
    raw = (_run(binary, *args, timeout=timeout).stdout or "").strip()
    return json.loads(raw) if raw else None

def _doctor(binary: str) -> Optional[dict[str, Any]]:
    """``cua-driver doctor --json`` → ``{ok, checks:[{label,status,message}]}`` (None on any failure)."""
    try:
        data = _json_out(binary, "doctor", "--json", timeout=12)
    except Exception:
        data = None
    if not isinstance(data, dict):
        return None
    checks = [{k: str(p.get(k, "")) for k in ("label", "status", "message")} for p in data.get("probes", []) if isinstance(p, dict)]
    return {"ok": bool(data.get("ok")), "checks": checks}

def computer_use_status(driver_cmd: Optional[str] = None) -> dict[str, Any]:
    """Normalize driver health; ``ready=None`` means a missing binary or failed probe."""
    from plugins.computer_use import DEFAULT_BACKEND, configured_backend_name, get_active_provider
    from tools.computer_use.cua_backend_driver import resolve_cua_driver_cmd  # same resolver as the tool itself
    plat, name = sys.platform, configured_backend_name()
    binary = resolve_cua_driver_cmd(driver_cmd) if name == DEFAULT_BACKEND else None
    out: dict[str, Any] = {"platform": plat, "platform_supported": plat in _RUNTIME_PLATFORMS,
                           "installed": bool(binary), "version": None, "ready": None,
                           "checks": [], "source": None, "error": None}
    if name != DEFAULT_BACKEND:  # another driver is selected: cua-driver probes do not apply
        out.update(platform_supported=True)
        try:
            provider = get_active_provider()
        except LookupError as e:
            out["error"] = str(e)
            return out
        out.update(installed=True, version=f"{provider.display_name} (computer_use.backend: {provider.name})",
                   ready=provider.is_available())
        return out
    if not binary:
        return out
    with suppress(Exception):
        out["version"] = (_run(binary, "--version", timeout=5).stdout or "").strip() or None
    doctor = _doctor(binary)
    if doctor is not None:
        out["checks"] = doctor["checks"]
        out["ready"] = doctor["ok"]
    return out
