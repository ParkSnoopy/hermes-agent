"""One user's machine across one real ``hermes update``, observed once per column.

A manual gateway and a manual dashboard run in one shared-PID-namespace sandbox.
The scenario runs once per column and records what the user would see; each property
is its own test over that record, gated on its own issue. The expensive part (stage, boot, update,
settle) is paid once per column, not once per property.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.e2e.core._pending_fixes import known_failure, known_gate
from tests.e2e.core.upgrade import _helpers as H
from tests.e2e.core.upgrade.handoff import _handoff as X
from tests.fakes.fake_llm_provider import FakeLLMServer, Text

_PORT_IN_USE = re.compile(r"address already in use|port \d+ is (already )?in use|errno 98", re.IGNORECASE)
REPLY = "handoff reply"

# Open issues behind a property, per column (a fix PR deletes its entry). Each pattern matches only the
# verdict text its bug produces; any other failure of the same property propagates.
# A dashboard started from a shell that runs under a systemd service (every GitHub-hosted job runs in
# hosted-compute-agent.service) sits in that unit's cgroup; the update restarts the unit instead of
# respawning the dashboard. Fixed at HEAD by #124940; the N-1 column keeps this gate until a release
# carrying the fix is N-1.
_FOREIGN_UNIT_GATE = (r"the update restarted the systemd unit \S+ the dashboard was started under",
                      "gated on #124938: the update restarts the systemd unit whose cgroup a manual dashboard "
                      "was started in instead of respawning the dashboard")
DASHBOARD_GATES = {"n1": [_FOREIGN_UNIT_GATE]}

# ``hermes update``'s own line for each systemd unit it restarted (or tried to) after stopping a dashboard.
_UNIT_RESTART = re.compile(r"✓ restarted systemd service (\S+\.service)|⚠ (\S+\.service): systemctl restart returned")


@contextlib.contextmanager
def _gated(entries):
    """``known_failure`` over several open issues: the one whose pattern the failure matches xfails it."""
    with contextlib.ExitStack() as stack:
        for pattern, reason in entries:
            stack.enter_context(known_failure(pattern, reason))
        yield


class Model:
    """The fake provider replies to chat turns."""

    def __call__(self, rec: dict):
        return Text(REPLY)


# -- small views over the sandbox -------------------------------------------------------------------


def _dashboard_health(port: int) -> dict | None:
    status, body = X.http("GET", f"http://127.0.0.1:{port}/api/health", key=None, timeout=5)
    return body if status == 200 and isinstance(body, dict) else None


_LISTENER_PY = r"""
import os, sys
port = int(sys.argv[1]); inodes = set()
for table in ("/proc/net/tcp", "/proc/net/tcp6"):
    try:
        rows = open(table).read().splitlines()[1:]
    except OSError:
        continue
    for row in rows:
        f = row.split()
        if f[3] == "0A" and int(f[1].rsplit(":", 1)[1], 16) == port:
            inodes.add(f[9])
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
            if link.startswith("socket:[") and link[8:-1] in inodes:
                print(pid); sys.exit(0)
    except OSError:
        pass
"""


def listener_pid(inst: X.Install, port: int) -> int | None:
    """The sandbox PID owning the LISTEN socket on ``port`` (``/proc/net/tcp`` inode -> ``/proc/<pid>/fd``)."""
    cp = inst.host.run([sys.executable, "-c", _LISTENER_PY, str(port)], timeout=60, quiet=True)
    return int(cp.stdout.strip()) if cp.stdout.strip().isdigit() else None


def dashboards(inst: X.Install) -> list[dict]:
    """Top-level ``hermes dashboard`` processes (a dashboard's own helpers are not a second one)."""
    out = [p for p in inst.host.procs()
           if p["state"] not in ("Z", "X") and "dashboard" in p["cmdline"] and "update" not in p["cmdline"]
           and any("hermes" in a for a in p["cmdline"])]
    pids = {p["pid"] for p in out}
    return [p for p in out if p["ppid"] not in pids]




def _quiet_wait(pred, *, timeout: float, what: str, interval: float = 1.0):
    try:
        return X.wait_for(pred, timeout=timeout, what=what, interval=interval)
    except AssertionError:
        return None


# -- the scenario ---------------------------------------------------------------------------------


def run(column: str, root: Path) -> SimpleNamespace:
    """Boot the fleet, update, let everything settle, and record what the user would see."""
    o = SimpleNamespace(column=column)
    model = Model()
    with FakeLLMServer(model) as srv, X.cell(column, root, srv.base_url) as inst:
        before = X.start_gateway(inst)
        o.old_pid = before["pid"]
        with X.FileWatch(inst.hermes_home / "gateway.pid") as watch:
            assert X.pid_file_pid(inst) == o.old_pid, f"premise: gateway.pid names the gateway: {watch.render()}"
            status, body = X.chat(inst.port, "hello before the update")
            assert status == 200 and X.reply_text(body) == REPLY, f"premise: a turn works before: {body}"

            o.dash_port = X.free_port()
            inst.spawn("dashboard", "dashboard", "--no-open", "--host", "127.0.0.1", "--port", str(o.dash_port))
            try:  # the first ``hermes dashboard`` builds the web UI, as it does for the user
                X.wait_for(lambda: _dashboard_health(o.dash_port), timeout=600, what="the dashboard /api/health")
            except AssertionError as exc:
                raise AssertionError(f"premise: the dashboard never came up: {exc}\n{inst.diagnostics()}") from None
            o.dash_old = listener_pid(inst, o.dash_port)
            assert o.dash_old, "premise: nothing listens on the dashboard port"


            o.boots = len(X.gateway_starts(inst))
            o.target = X.publish_target(inst)

            o.up = inst.update()
            o.sha_after = inst.sha()
            o.layout_after = {rel: (inst.checkout / rel).is_file() for rel in X.NEXT_MOVE}

            o.ident = X.settle_gateway(inst, o.old_pid)
            o.chat_after = X.chat(inst.port, "hello after the update") if o.ident else (0, "")

            gone_since: list[float] = []

            def dash_back():
                pid = listener_pid(inst, o.dash_port)
                if pid and pid != o.dash_old and _dashboard_health(o.dash_port):
                    return pid
                # No dashboard process at all for 20s: the respawn died (or never happened); stop waiting.
                if dashboards(inst):
                    gone_since.clear()
                elif not gone_since:
                    gone_since.append(time.monotonic())
                return -1 if gone_since and time.monotonic() - gone_since[0] > 20 else None
            back = _quiet_wait(dash_back, timeout=240, what="a new dashboard on its port")
            o.dash_new = back if back and back > 0 else None


            o.gateways = X.gateway_pids(inst)
            o.status = inst.cli("gateway", "status", timeout=120)

            time.sleep(2)  # let the pid-file watch see what the status probes did
        o.pid_history = watch.history
        o.relaunch = X.relaunch_verdict(inst, o.old_pid, o.boots, o.ident, o.up)
        o.restart_wait = X.restart_wait_verdict(inst)
        o.pid_file = X.pid_file_verdict(inst, o.ident["pid"], watch.history) if o.ident else "no gateway"
        o.dash_listener = listener_pid(inst, o.dash_port)
        o.dash_roots = dashboards(inst)
        restart_log = inst.hermes_home / "logs" / "dashboard-restart.log"
        o.dash_restarts = restart_log.read_text(errors="replace") if restart_log.exists() else ""

        inst.logs.append(restart_log)
        # bwrap keeps the caller's cgroup: the sandbox's processes (the dashboard too) live in ours.
        o.cgroup = Path("/proc/self/cgroup").read_text(errors="replace").strip() if sys.platform == "linux" else ""
        o.diag = (inst.diagnostics(o.up) + f"\n--- cgroup of the sandbox ---\n{o.cgroup}"
                  f"\n--- gateway.pid history ---\n{watch.render()}")
    return o


# -- the properties -------------------------------------------------------------------------------


class HandoffProperties:
    """Every property of the hand-off, over one recorded scenario per column (``column`` on the
    subclass). Each test is independent: a failure in one subsystem never hides another's."""

    column: str = ""

    @pytest.fixture(scope="class")
    def fleet(self):
        if self.column == "n1" and not X.refs().base:
            pytest.skip("no release tag reachable (shallow checkout)")
        base = os.environ.get("TMPDIR") or tempfile.gettempdir()
        root = Path(tempfile.mkdtemp(prefix="ho", dir=base))  # short: AF_UNIX paths under $HERMES_HOME
        try:
            yield run(self.column, root)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _relaunched(self, o) -> None:
        with known_gate(X.RELAUNCH_GATES, o.column):
            assert not o.relaunch, f"{o.relaunch}\n{o.diag}"

    def test_update_moves_the_checkout(self, fleet):
        assert fleet.sha_after == fleet.target, f"the update did not move the checkout\n{fleet.diag}"
        if fleet.column == "head":
            old, new = X.NEXT_MOVE
            assert fleet.layout_after == {old: False, new: True}, (
                f"NEXT moved {old} to {new}; the checkout has {fleet.layout_after}\n{fleet.diag}")

    def test_restart_wait_ends_within_its_cap(self, fleet):
        """A gateway that will not drain is stopped at ``agent.restart_after_turn_timeout``: the update
        moves on in minutes and the report names what held it, instead of the job timing out."""
        assert not fleet.restart_wait, f"{fleet.restart_wait}\n{fleet.diag}"

    def test_gateway_is_relaunched_once_on_the_new_commit(self, fleet):
        o = fleet
        self._relaunched(o)
        assert [p["pid"] for p in o.gateways] == [o.ident["pid"]], f"expected exactly one gateway: {o.gateways}\n{o.diag}"
        status, body = o.chat_after
        assert status == 200 and X.reply_text(body) == REPLY, f"no turn after the update: {body}\n{o.diag}"

    def test_update_exit_status_matches_what_came_back(self, fleet):
        """Nothing is reported as success when it failed, and a failure exit names something real."""
        o = fleet
        out = o.up.stdout + o.up.stderr
        assert X.TRACEBACK not in out, f"the update crashed\n{o.diag}"
        down = [name for name, ok in (("the gateway", o.ident), ("the dashboard", o.dash_new)) if not ok]
        if o.up.returncode == 0:
            assert not down, f"the update exited 0 though {' and '.join(down)} did not come back\n{o.diag}"
        else:
            assert down, (f"the update exited {o.up.returncode} though the gateway and the dashboard came back"
                          f"\n{o.diag}")

    def test_gateway_pid_file_and_status_find_the_relaunched_gateway(self, fleet):
        o = fleet
        self._relaunched(o)
        assert not o.pid_file, f"{o.pid_file}\n{o.diag}"
        assert f"Gateway is running (PID: {o.ident['pid']})" in o.status.stdout, (
            f"`hermes gateway status` cannot find the relaunched gateway:\n{H.describe(o.status)}\n{o.diag}")

    def test_dashboard_is_back_on_its_port(self, fleet):
        o = fleet
        out = o.up.stdout + o.up.stderr
        assert "TypeError" not in out, f"the update tripped over the dashboard\n{o.diag}"
        with _gated(DASHBOARD_GATES.get(o.column, ())):
            assert o.dash_new is not None, f"{dashboard_verdict(o)}\n{o.diag}"
        assert len(o.dash_roots) == 1, f"expected exactly one dashboard: {o.dash_roots}\n{o.diag}"
        assert o.dash_listener == o.dash_new, f"the dashboard on port {o.dash_port} churned\n{o.diag}"
        assert not _PORT_IN_USE.search(o.dash_restarts), f"the respawned dashboard hit port-in-use\n{o.diag}"


def dashboard_verdict(o) -> str:
    """Why no new dashboard serves its port, in words a gate can key on."""
    if re.search(r"^SyntaxError", o.dash_restarts, re.MULTILINE) and "restarted:" in o.up.stdout:
        return (f"the respawned dashboard died parsing its launcher: the update replayed the pre-update argv and "
                f"Python read a shell script (SyntaxError in logs/dashboard-restart.log); port {o.dash_port} is dark")
    # The sandbox runs no Hermes unit, so any unit the dashboard stop restarts is the one the test runner
    # (and so the hand-started dashboard) happens to live in.
    unit = next((a or b for a, b in _UNIT_RESTART.findall(o.up.stdout)), None)
    if unit and not o.dash_restarts:
        return (f"the update restarted the systemd unit {unit} the dashboard was started under (cgroup "
                f"{o.cgroup}) instead of respawning the dashboard; port {o.dash_port} is dark")
    return f"no new dashboard serves port {o.dash_port} (old pid {o.dash_old}, listener now {o.dash_listener})"
