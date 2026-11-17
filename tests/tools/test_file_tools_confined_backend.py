"""Verify local file operations continue to use the host filesystem."""

import json

import pytest

from tools import terminal_tool
from tools.environments.local import LocalEnvironment
from tools.file_tools import clear_file_ops_cache
from tools.registry import registry

# The host fast path is POSIX-only (``_native_read_enabled``); Windows always takes the shell path.
pytestmark = pytest.mark.platforms("posix")


class _MountedViewEnv(LocalEnvironment):
    """Commands see ``real`` at ``view``; the host sees only ``real``."""

    def __init__(self, view: str, real: str):
        super().__init__(cwd=real, timeout=30, persistent=False)
        self._view, self._real = view, real

    def execute(self, command, cwd="", **kwargs):
        result = super().execute(command.replace(self._view, self._real),
                                 cwd=cwd.replace(self._view, self._real), **kwargs)
        result["output"] = result.get("output", "").replace(self._real, self._view)
        return result




def _dispatch(name, task_id, **arguments):
    return json.loads(registry.dispatch(name, arguments, task_id=task_id))


@pytest.fixture
def mounted(tmp_path, monkeypatch, request):
    """``(env, view, real, task_id)`` with the env active for the task; ``a.txt`` holds two lines."""
    monkeypatch.delenv("HERMES_NATIVE_FILE_READ", raising=False)  # the fast path at its default: on
    view, real = tmp_path / "view", tmp_path / "real"
    real.mkdir()
    (real / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    env = request.param(str(view), str(real))
    task_id = f"confined-{request.node.name}"
    monkeypatch.setitem(terminal_tool._active_environments, terminal_tool._resolve_task_id(task_id), env)
    yield env, view, real, task_id
    clear_file_ops_cache()
    env.cleanup()




@pytest.mark.parametrize("mounted", [_MountedViewEnv], indirect=True)
def test_host_backend_keeps_the_host_fast_path(mounted):
    _env, view, real, task = mounted

    read = _dispatch("read_file", task, path=str(view / "a.txt"))
    assert "alpha" not in json.dumps(read), read

    written = _dispatch("write_file", task, path=str(view / "b.txt"), content="gamma\n")
    assert "error" not in written, written
    assert (view / "b.txt").read_text(encoding="utf-8") == "gamma\n"
    assert not (real / "b.txt").exists()
