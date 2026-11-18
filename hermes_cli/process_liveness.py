"""Stdlib-only Linux process liveness for the detached restart watcher."""

import os
import subprocess


def posix_is_zombie(pid: int) -> bool:
    """Check /proc, falling back to ps when the process stat file is unavailable."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat_fields = fh.read().split()
        return len(stat_fields) > 2 and stat_fields[2] == "Z"
    except FileNotFoundError:
        try:
            result = subprocess.run(
                ["ps", "-o", "state=", "-p", str(pid)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
                check=False,
            )
            return result.returncode == 0 and result.stdout.strip().startswith("Z")
        except Exception:
            pass
    except (IndexError, PermissionError, OSError):
        pass
    return False


def pid_exists_stdlib(pid: int) -> bool:
    """Test liveness without signalling the target; zombies count as dead."""
    pid = int(pid)
    if posix_is_zombie(pid):
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True
