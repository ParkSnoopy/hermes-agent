"""Installed identity proofs at inert native supervisor executable boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("case,reason", [
    ("custom", "deadline"), ("named", "deadline"), ("alias", "deadline"),
    ("default", "deadline"),
    ("wrong_home", "profile_mismatch"), ("wrong_user", "service_account_mismatch"),
    ("missing_identity", "service_identity_unverified"),
    ("omitted_empty_exec_lists", "deadline"),
    ("env_file", "service_identity_unverified"), ("dynamic_user", "service_identity_unverified"),
    ("wrong_command", "service_identity_unverified"), ("command_profile", "profile_mismatch"),
    ("dropin_home", "profile_mismatch"), ("manager_home", "profile_mismatch"),
])
def test_ensure_checks_effective_service_binding_before_start(tmp_path, monkeypatch, case, reason):
    from hermes_cli import gateway_runtime as runtime
    from hermes_cli.gateway_runtime_service import service_suffix

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / (".hermes" if case == "default" else "custom root")
    if case == "named":
        home = home / "profiles" / "worker"
    home.mkdir(mode=0o700, parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    suffix = service_suffix(home)
    unit = tmp_path / ".config/systemd/user" / f"hermes-gateway{'-' + suffix if suffix else ''}.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f'[Service]\nExecStart={sys.executable} -m hermes_cli.main gateway run\nEnvironment="HERMES_HOME={home}"\n', encoding="utf-8")
    original = unit.read_bytes()
    configured = home
    if case in {"wrong_home", "dropin_home"}:
        configured = tmp_path / "unrequested"
    if case == "wrong_home":
        unit.write_text(f'[Service]\nEnvironment="HERMES_HOME={configured}"\n', encoding="utf-8")
        original = unit.read_bytes()
    if case == "dropin_home":
        override = unit.parent / (unit.name + '.d') / 'override.conf'
        override.parent.mkdir()
        override.write_text(f'[Service]\nEnvironment="HERMES_HOME={configured}"\n', encoding="utf-8")
    if case == "alias":
        configured = tmp_path / "alias"
        configured.symlink_to(home, target_is_directory=True)
    command = f'{sys.executable} -m hermes_cli.main gateway run'
    if case == "named":
        command += " --profile worker"
    if case == "command_profile":
        command += " --profile stranger"
    if case == "wrong_command":
        command = '/bin/echo hermes_cli.main gateway run'
    props = dict(LoadState="loaded", ActiveState="inactive", SubState="dead", UnitFileState="enabled",
                 User=str(os.getuid()), DynamicUser="no", Environment=f'"HERMES_HOME={configured}" "HERMES_SUPERVISED_CHILD=1"',
                 EnvironmentFiles="", PassEnvironment="", UnsetEnvironment="", PAMName="",
                 RootDirectory="", RootImage="", ExecStartPre="", ExecCondition="",
                 ExecStart=f'{{ path={command.split()[0]} ; argv[]={command} ; ignore_errors=no ; }}',
                 DropInPaths=str(unit.parent / (unit.name + '.d') / 'override.conf') if case == "dropin_home" else "")
    manager_env = ""
    if case == "wrong_user":
        props['User'] = str(os.getuid() + 1)
    if case == "missing_identity":
        props = {k: props[k] for k in ("LoadState", "ActiveState", "SubState", "UnitFileState")}
    if case == "omitted_empty_exec_lists":
        # systemd >= 25x prints nothing for empty exec-command lists and
        # EnvironmentFiles even under --all; absence there means empty, not unknown.
        for key in ("ExecStartPre", "ExecCondition", "EnvironmentFiles"):
            del props[key]
    if case == "env_file":
        env_file = tmp_path / "override.env"
        env_file.write_text(f'HERMES_HOME={tmp_path / "other"}\n', encoding="utf-8")
        props['EnvironmentFiles'] = f'{env_file} (ignore_errors=no)'
    if case == "dynamic_user":
        props['DynamicUser'] = 'yes'
    if case == "manager_home":
        props['Environment'] = 'HERMES_SUPERVISED_CHILD=1'
        manager_env = f'HERMES_HOME={tmp_path / "other"}\n'
    if case == "default":
        # An older default install may rely on the user manager's HOME.
        props['Environment'] = 'HERMES_SUPERVISED_CHILD=1'
        manager_env = f'HOME={tmp_path}\n'
    definition = tmp_path / "effective.json"
    definition.write_text(json.dumps({'props': props, 'system': False, 'env': manager_env}), encoding="utf-8")
    calls = tmp_path / 'calls.jsonl'
    executable = tmp_path / 'inert-supervisor'
    executable.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\n'
        + f'p=Path({str(tmp_path)!r})\nd=json.loads((p/"effective.json").read_text())\n'
        + 'with (p/"calls.jsonl").open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
        + 'if "show-environment" in sys.argv: print(d["env"])\n'
        + 'elif "show" in sys.argv:\n'
        + ' if ("--user" not in sys.argv) == d["system"]: print("\\n".join(k+"="+v for k,v in d["props"].items()))\n'
        + ' else: print("LoadState=not-found")\n'
        + 'elif "start" not in sys.argv: sys.exit(91)\n', encoding='utf-8')
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    from hermes_cli import gateway as gw
    monkeypatch.setattr(gw, "_systemctl_cmd", lambda system=False: [str(executable)] + ([] if system else ["--user"]))
    result = runtime.ensure_gateway_runtime(home, timeout=3.0)
    assert result.reason_code == reason
    commands = [json.loads(line) for line in calls.read_text().splitlines()]
    starts = [cmd for cmd in commands if 'start' in cmd]
    assert len(starts) == (1 if reason == 'deadline' else 0)
    assert all('--no-ask-password' in cmd for cmd in starts)
    assert unit.read_bytes() == original
    assert not (home / 'logs').exists()






















@pytest.mark.platforms("linux")
def test_service_definition_reads_reject_fifo_without_waiting(tmp_path):
    from hermes_cli.gateway_runtime_service_identity import read_definition
    regular = tmp_path / 'regular'
    regular.write_bytes(b'installed definition')
    assert read_definition(regular) == regular.read_bytes()
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError, match='service_identity_unverified'):
        read_definition(fifo)


@pytest.mark.parametrize("outer", ["python", "echo"])
def test_gateway_wrapper_requires_actual_python_entrypoint(outer, tmp_path):
    from hermes_cli.gateway_runtime_service_identity import verify_gateway_argv
    argv = [sys.executable if outer == 'python' else '/bin/echo', '-m', 'hermes_cli.stderr_timestamp', '--error-log', str(tmp_path / 'error.log'), '--', sys.executable, '-m', 'hermes_cli.main', 'gateway', 'run']
    if outer == 'echo':
        with pytest.raises(ValueError, match='service_identity_unverified'):
            verify_gateway_argv(argv, tmp_path)
    else:
        verify_gateway_argv(argv, tmp_path)


def test_android_python_reaches_the_unmanaged_start(tmp_path, monkeypatch):
    """Termux is supported and has no service manager; Python 3.13+ there reports
    sys.platform == "android". Ensure must start the gateway unmanaged, not refuse the platform."""
    from hermes_cli import gateway_runtime as runtime, gateway_runtime_service as service, gateway_runtime_start as start

    home = tmp_path / "profile"
    home.mkdir(mode=0o700)
    spawned = []
    monkeypatch.setattr(service.sys, "platform", "android")
    monkeypatch.setattr(service, "_run", lambda *a, **k: pytest.fail("no supervisor to query on Android"))
    monkeypatch.setattr(runtime, "discover_gateway_endpoint", lambda *a, **k: runtime.GatewayDiscovery("absent"))
    monkeypatch.setattr(start, "spawn_unmanaged_gateway", lambda target, **k: spawned.append(Path(target)))
    result = runtime.ensure_gateway_runtime(home, timeout=0.3)
    assert result.reason_code != "unsupported_supervisor_platform"
    assert spawned == [home.resolve()]


def test_s6_runtime_ensure_starts_the_registered_gateway_slot(tmp_path, monkeypatch):
    """A Docker TUI client starts its s6 gateway instead of probing systemd."""
    from hermes_cli import gateway_runtime_service as service
    from hermes_cli import service_manager

    home = tmp_path / "data"
    home.mkdir()
    scandir = tmp_path / "run-service"
    (scandir / "gateway-default").mkdir(parents=True)
    started = []

    class FakeS6ServiceManager:
        def __init__(self):
            self.scandir = scandir

        def is_running(self, name):
            assert name == "gateway-default"
            return False

        def start(self, name):
            started.append(name)

    monkeypatch.setattr(service_manager, "_s6_running", lambda: True)
    monkeypatch.setattr(service_manager, "S6ServiceManager", FakeS6ServiceManager)
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs: (
        str(home) if str(path).replace("\\", "/") == "/run/s6/container_environment/HERMES_HOME"
        else read_text(path, *args, **kwargs)))
    monkeypatch.setattr(service, "_systemd", lambda *_args: pytest.fail("s6 must not probe systemd"))

    found = service.discover_existing_gateway_service(home, deadline=time.monotonic() + 5)

    assert found is not None and found.backend == "s6"
    service.start_existing_gateway_service(found, deadline=time.monotonic() + 5)
    assert started == ["gateway-default"]


def test_s6_command_timeout_ends_at_the_runtime_deadline(tmp_path, monkeypatch):
    """A hung s6-svstat or s6-svc reaches ensure as its deadline, not an uncaught TimeoutExpired."""
    from hermes_cli import gateway_runtime_service as service
    from hermes_cli import service_manager

    home = tmp_path / "data"
    home.mkdir()
    scandir = tmp_path / "run-service"
    (scandir / "gateway-default").mkdir(parents=True)

    def hang(cmd, *_args, timeout=5, **_kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(service_manager, "_s6_running", lambda: True)
    monkeypatch.setattr(service_manager, "_s6_run", hang)
    monkeypatch.setattr(service_manager.S6ServiceManager, "__init__",
                        lambda self: setattr(self, "scandir", scandir))
    read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs: (
        str(home) if str(path).replace("\\", "/") == "/run/s6/container_environment/HERMES_HOME"
        else read_text(path, *args, **kwargs)))

    with pytest.raises(TimeoutError):
        service.discover_existing_gateway_service(home, deadline=time.monotonic() + 5)
    stopped = service.ExistingService("s6", ("gateway-default",), False)
    with pytest.raises(TimeoutError):
        service.start_existing_gateway_service(stopped, deadline=time.monotonic() + 5)
