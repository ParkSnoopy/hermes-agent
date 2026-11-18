"""Linux dashboard process discovery and update lifecycle."""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from hermes_cli.process_identity import _hermes_home_for_pid, _normalized_home_for_compare

BACKEND_LOCK_NAME = "backend.lock.json"


def _scan_dashboard_processes(*, exclude_pids: set[int] | None = None) -> list[tuple[int, str]]:
    from hermes_cli.process_identity import _hermes_holder_subcommand, ledger_entries

    excluded = {os.getpid(), *(exclude_pids or ())}
    found = {}
    try:
        result = subprocess.run(["ps", "-A", "-o", "pid=,command="], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    pid = int(parts[0])
                    if pid not in excluded and _hermes_holder_subcommand(parts[1]) in ("dashboard", "serve"):
                        found[pid] = parts[1]
    except (OSError, subprocess.TimeoutExpired):
        pass
    with contextlib.suppress(Exception):
        for entry in ledger_entries():
            pid = entry.get("pid")
            if type(pid) is int and pid not in excluded and entry.get("purpose") in ("dashboard", "serve"):
                found.setdefault(pid, str(entry.get("argv") or ""))
    return list(found.items())


def _ledger_serve_binds() -> dict[int, tuple[str, int]]:
    from hermes_cli.process_identity import ledger_entries
    result = {}
    with contextlib.suppress(Exception):
        for entry in ledger_entries():
            pid, port = entry.get("pid"), entry.get("port")
            if entry.get("purpose") in ("serve", "dashboard") and type(pid) is int and type(port) is int and port > 0:
                result[pid] = (str(entry.get("host") or ""), port)
    return result


def _caller_ancestor_pids() -> set[int]:
    try:
        import psutil
        return {p.pid for p in psutil.Process().parents()}
    except Exception:
        ancestors = set()
        current = os.getpid()
        while current > 1 and current not in ancestors:
            ancestors.add(current)
            try:
                lines = Path(f"/proc/{current}/status").read_text().splitlines()
                current = int(next(line.split()[1] for line in lines if line.startswith("PPid:")))
            except (OSError, ValueError, StopIteration, IndexError):
                raise RuntimeError("cannot establish caller ancestry") from None
        ancestors.discard(os.getpid())
        return ancestors


def _is_caller_wrapper_shell(pid: int, ancestors: set[int]) -> bool:
    return pid in ancestors


def _pids_owned_by_hermes_home(pids: list[int], home: str) -> list[int]:
    target = _normalized_home_for_compare(home)
    return [pid for pid in pids if (owner := _hermes_home_for_pid(pid, require_launch_identity=True))
            and _normalized_home_for_compare(owner) == target]


def _profile_flag(argv: list[str]) -> str:
    for i, token in enumerate(argv):
        if token in ("--profile", "-p") and i + 1 < len(argv):
            return argv[i + 1]
        if token.startswith("--profile="):
            return token.partition("=")[2]
    return "default"


def _filter_dashboard_respawn_candidates(candidates: list[tuple[int, list[str], str | None]], *,
                                         own_home: str | None = None) -> list[list[str]]:
    if own_home is None:
        from hermes_constants import get_hermes_home
        own_home = str(get_hermes_home())
    target = _normalized_home_for_compare(own_home) if own_home else ""
    selected, seen_commands, seen_profiles = [], set(), set()
    for _, argv, home in candidates:
        if not argv or (target and home and _normalized_home_for_compare(home) != target):
            continue
        idx = next((i for i, token in enumerate(argv) if token in ("serve", "dashboard")), None)
        if idx is None:
            continue
        tail = argv[idx:]
        if any(token == "--port=0" or (token == "--port" and i + 1 < len(tail) and tail[i + 1] == "0")
               for i, token in enumerate(tail)):
            continue
        profile = _profile_flag(argv[:idx])
        key = (profile, *tail)
        owner = _normalized_home_for_compare(home) if home else profile
        if key in seen_commands or owner in seen_profiles:
            continue
        seen_commands.add(key)
        seen_profiles.add(owner)
        selected.append(list(argv))
    return selected


def read_valid_backend_lock(lock_path: Path) -> dict | None:
    ownership = lock_path.parent.name
    hexdigits = set("0123456789abcdef")
    def hex_value(value, length):
        return isinstance(value, str) and len(value) == length and not set(value) - hexdigits
    if not hex_value(ownership, 32):
        return None
    try:
        with lock_path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            return None
        row = json.loads(data)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(row, dict) or row.get("schemaVersion") != 2 or row.get("protocolVersion") != 1:
        return None
    if row.get("ownershipId") != ownership or not hex_value(row.get("spawnNonce"), 16) or not hex_value(row.get("tokenFingerprint"), 32):
        return None
    if type(row.get("pid")) is not int or not 0 < row["pid"] <= 4194304 or type(row.get("port")) is not int or not 0 <= row["port"] <= 65535:
        return None
    if any(not isinstance(row.get(key), str) or len(row[key]) > 1024 for key in ("profile", "hermesPath", "hermesHome", "logPath", "startedAt")):
        return None
    return row if row["logPath"].endswith(f"/{ownership}/{row['spawnNonce']}.log") else None


def _lock_owned_serve_pids() -> set[int]:
    from hermes_constants import get_default_hermes_root, get_process_hermes_home
    owned = set()
    for root in {get_default_hermes_root(), get_process_hermes_home()}:
        try:
            entries = list((root / "desktop-ssh").iterdir())
        except OSError:
            continue
        for entry in entries:
            if lock := read_valid_backend_lock(entry / BACKEND_LOCK_NAME):
                owned.add(lock["pid"])
    return owned


def _kill_pids_posix(pids: list[int], killed: list[int], failed: list[tuple[int, str]], *,
                     expected_start_times: dict[int, int]) -> None:
    """Signal captured incarnations only; leave detached descendants and caller ancestry alone."""
    import psutil
    from gateway.status import get_process_start_time
    protected = _caller_ancestor_pids() | {os.getpid()}
    snapshots = {}
    for pid in pids:
        try:
            if (pid in protected or expected_start_times.get(pid) is None
                    or get_process_start_time(pid) != expected_start_times[pid]):
                failed.append((pid, "process identity changed or protected"))
                continue
            root = psutil.Process(pid)
            snapshots[pid] = (root, root.create_time(), [])
            pending = list(root.children())
            while pending:
                child = pending.pop()
                if child.pid in protected:
                    continue
                try:
                    if os.getsid(child.pid) == child.pid:
                        continue
                    snapshots[pid][2].append((child, child.create_time()))
                    pending.extend(child.children())
                except (OSError, psutil.Error):
                    continue
        except psutil.Error as exc:
            failed.append((pid, str(exc)))
    def send(proc, birth, sig):
        try:
            if proc.pid in expected_start_times and get_process_start_time(proc.pid) != expected_start_times[proc.pid]:
                raise ProcessLookupError("process identity changed")
            if psutil.Process(proc.pid).create_time() == birth:
                proc.send_signal(sig)
        except (psutil.NoSuchProcess, ProcessLookupError):
            return
    def kill_group(root, birth, children):
        # A live captured member pins the original group. Never signal the
        # updater's group, or a numeric pgid whose ownership cannot be proved.
        for member, member_birth in [(root, birth), *children]:
            try:
                if (member.is_running() and psutil.Process(member.pid).create_time() == member_birth
                        and os.getpgid(member.pid) == root.pid and root.pid != os.getpgrp()):
                    os.killpg(root.pid, signal.SIGKILL)
                    return
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
    for pid, (root, birth, _) in snapshots.items():
        try:
            if get_process_start_time(pid) != expected_start_times[pid]:
                failed.append((pid, "process identity changed"))
                continue
            send(root, birth, signal.SIGTERM)
        except (OSError, psutil.Error) as exc:
            failed.append((pid, str(exc)))
    for pid, (root, birth, children) in snapshots.items():
        if any(p == pid for p, _ in failed):
            continue
        try:
            root.wait(timeout=5)
        except psutil.TimeoutExpired:
            try:
                kill_group(root, birth, children)
                send(root, birth, signal.SIGKILL)
                root.wait(timeout=2)
            except (OSError, psutil.Error) as exc:
                failed.append((pid, str(exc)))
                continue
        except psutil.NoSuchProcess:
            pass
        try:
            kill_group(root, birth, children)
        except (OSError, psutil.Error) as exc:
            failed.append((pid, str(exc)))
            continue
        for child, child_birth in children:
            with contextlib.suppress(OSError, psutil.Error):
                send(child, child_birth, signal.SIGTERM)
        _, alive = psutil.wait_procs([child for child, _ in children], timeout=2)
        for child in alive:
            birth = next(b for c, b in children if c.pid == child.pid)
            with contextlib.suppress(OSError, psutil.Error):
                send(child, birth, signal.SIGKILL)
        _, alive = psutil.wait_procs(alive, timeout=2)
        if alive:
            failed.append((pid, "descendants still alive after SIGKILL"))
        else:
            killed.append(pid)


def _kill_stale_dashboard_processes(reason: str = "update", *, restart_managed: bool = False,
                                    already_restarted_units: set[str] | None = None,
                                    scope_home: str | None = None) -> dict[str, list]:
    from hermes_cli import main_dashboard as dash
    from gateway.status import get_process_start_time
    excluded = _lock_owned_serve_pids() if restart_managed else set()
    for part in os.environ.get("HERMES_DESKTOP_CHILD_PID", "").split(","):
        with contextlib.suppress(ValueError):
            excluded.add(int(part))
    pids = dash._find_stale_dashboard_pids(exclude_pids=excluded, scope_home=scope_home)
    candidates = []
    for pid in pids:
        start = get_process_start_time(pid)
        if start is None:
            continue
        home = _hermes_home_for_pid(pid, require_launch_identity=True)
        if not home or (scope_home and _normalized_home_for_compare(home) != _normalized_home_for_compare(scope_home)):
            continue
        unit = dash._get_systemd_service_for_pid(pid)
        if unit and unit.removesuffix(".service") in (already_restarted_units or set()):
            continue
        candidates.append((pid, start, unit, dash._get_pid_cgroup_path(pid), dash._dashboard_cmdline_for_pid(pid), home))
    killed, failed, unrecovered = [], [], []
    manual = []
    for pid, start, unit, cgroup, argv, home in candidates:
        if get_process_start_time(pid) != start:
            failed.append((pid, "process identity changed"))
            continue
        _kill_pids_posix([pid], killed, failed, expected_start_times={pid: start})
        if pid not in killed:
            continue
        if restart_managed and unit:
            if not dash._try_restart_systemd_service(unit, cgroup):
                unrecovered.append(pid)
        elif restart_managed and argv:
            manual.append((pid, argv, home))
        else:
            unrecovered.append(pid)
    commands = _filter_dashboard_respawn_candidates(manual, own_home=scope_home) if manual else []
    failed_commands = dash._respawn_dashboard_processes(commands) if commands else []
    unrecovered.extend(pid for pid, argv, _ in manual if argv not in commands or argv in failed_commands)
    return {"matched": [row[0] for row in candidates], "killed": killed, "failed": failed, "unrecovered": unrecovered}
