"""``hermes computer-use`` subcommand parser."""

from __future__ import annotations

import sys

from hermes_cli.subcommands._shared import add_json_flag


def _exit_if_plugin_backend(*, doctor: bool = False) -> None:
    """cua-driver checks do not apply when ``computer_use.backend`` selects another backend: report it
    (running its own ``doctor()`` when asked and it ships one) and exit; return for the built-in."""
    from plugins.computer_use import DEFAULT_BACKEND, configured_backend_name, get_active_provider
    if configured_backend_name() == DEFAULT_BACKEND:
        return
    try:
        provider = get_active_provider()
    except LookupError as e:
        print(f"Computer Use: {e}")
        sys.exit(2)
    if doctor and (rc := provider.doctor()) is not None:
        sys.exit(rc)
    ok = provider.is_available()
    print(f"Computer Use backend: {provider.name} ({provider.display_name}) — "
          f"{'available' if ok else 'not available'}. cua-driver checks do not apply to this backend.")
    sys.exit(0 if ok else 1)


def _cu_install(args) -> int:
    from hermes_cli.tools_config_cua import install_cua_driver
    return 0 if install_cua_driver(upgrade=bool(getattr(args, "upgrade", False))) else 1


def _cu_status(args) -> int:
    if getattr(args, "json", False):
        import json
        from tools.computer_use.readiness import computer_use_status

        status = computer_use_status()
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0 if status["ready"] else 1
    _exit_if_plugin_backend()
    import os as _os
    from hermes_cli.tools_config_cua import _cua_driver_contract_status, _cua_version_summary
    from tools.computer_use.cua_backend_driver import resolve_cua_driver_cmd

    path = resolve_cua_driver_cmd()
    override = _os.environ.get("HERMES_CUA_DRIVER_CMD", "").strip()
    if not path:
        print("cua-driver: not installed")
        print("  Run: hermes computer-use install")
        return 1
    contract = _cua_driver_contract_status(path)
    version = _cua_version_summary(contract.get("version") or "")
    # Name the override here too. Without it the operator is told to repair an
    # install that `hermes computer-use install` will (correctly) refuse to touch,
    # with nothing pointing at the env var that actually selected the binary.
    origin = " [custom binary from HERMES_CUA_DRIVER_CMD]" if override else ""
    print(f"cua-driver: installed at {path}{origin}" + (f" ({version})" if version else ""))
    if not contract.get("ready"):
        print("  ⚠ Repair required: " + (contract.get("reason") or "runtime contract is incomplete"))
        if override:
            print(
                "    Update the binary selected by HERMES_CUA_DRIVER_CMD, or unset "
                "the override and run: hermes computer-use install --upgrade")
        else:
            print("    Run: hermes computer-use install")
        return 1
    rc = 0
    if sys.platform == "linux":  # hand-written daemon units: dead `serve` is invisible to the binary contract (#114748)
        from tools.computer_use.cua_backend import cua_daemon_listening
        from tools.computer_use.doctor import cua_daemon_units
        for _kind, unit, _target, serve, socket in cua_daemon_units():
            if serve and cua_daemon_listening(path, socket) is False:
                print(f"  ✗ Daemon unit {unit}: no `cua-driver serve` is listening on {socket or 'the default socket'}")
                print(f"    Check: systemctl --user status {unit}  (reinstalling the driver does not start it)")
                rc = 1
    print("  ✓ Runtime contract ready (externally managed)." if override
          else "  ✓ Runtime contract ready (Hermes PM pin).")
    return rc


def _cu_doctor(args) -> None:
    _exit_if_plugin_backend(doctor=True)
    from tools.computer_use.doctor import run_doctor
    sys.exit(run_doctor(
        include=list(getattr(args, "include", []) or []),
        skip=list(getattr(args, "skip", []) or []),
        json_output=bool(getattr(args, "json", False))))


def build_computer_use_parser(subparsers) -> None:
    """Attach the ``computer-use`` subcommand to ``subparsers``."""
    computer_use_parser = subparsers.add_parser(
        "computer-use", help="Manage the Computer Use (cua-driver) backend on Linux",
        description="Install or check the cua-driver binary used by the\n"
            "`computer_use` toolset on Linux.\n\n"
            "Use `hermes computer-use install` to prepare the pinned\n"
            "cua-driver package and host integration. This is equivalent to the\n"
            "post-setup hook that `hermes tools` runs when you first\n"
            "enable the Computer Use toolset, and is a stable target\n"
            "for re-running the install if it didn't fire (e.g. when\n"
            "toggling the toolset on a returning-user setup).\n\n"
            "Use `hermes computer-use doctor` to run cua-driver's\n"
            "`health_report` MCP tool and surface its check matrix\n"
            "(version, platform support, capture, ...)\n"
            "in human-readable form.")
    computer_use_sub = computer_use_parser.add_subparsers(dest="computer_use_action")

    computer_use_install = computer_use_sub.add_parser(
        "install", help="Install or repair the cua-driver binary on Linux")
    computer_use_install.add_argument(
        "--upgrade", action="store_true",
        help="Reconcile cua-driver with Hermes' pinned PM package and repair host setup.")
    computer_use_status = computer_use_sub.add_parser(
        "status", help="Check the selected cua-driver and its runtime contract")
    add_json_flag(computer_use_status, "Emit normalized driver readiness as JSON.")
    computer_use_doctor = computer_use_sub.add_parser(
        "doctor", help="Run cua-driver `health_report` and surface the check matrix",
        description="Drive cua-driver's stable `health_report` MCP tool and render\n"
            "its check matrix (driver health, version,\n"
            "platform support, screenshot probe, …) as human-readable\n"
            "output. cua-driver owns the health model; this command stays\n"
            "thin so new checks added upstream surface here without code\n"
            "changes. Exits 0 when overall=ok, 1 when degraded/failed, 2\n"
            "when the binary is missing or unreachable.")
    computer_use_doctor.add_argument(
        "--include", action="append", default=[], metavar="CHECK",
        help="Run only the listed checks. Repeat for multiple "
            "(e.g. --include binary_version --include screen_capture_capability). "
            "Unknown names are reported by cua-driver.")
    computer_use_doctor.add_argument(
        "--skip", action="append", default=[], metavar="CHECK",
        help="Skip the listed checks. Repeat for multiple. Wins over --include.")
    add_json_flag(computer_use_doctor,
                  "Emit the raw structured payload as JSON (same shape as `tools/call`).")

    from hermes_cli.subcommands.computer_use_screen import build_screen_parser
    build_screen_parser(computer_use_sub, add_json_flag)

    def _cu_screen(args):
        return args.screen_func(args)

    _actions = {"install": _cu_install, "status": _cu_status, "doctor": _cu_doctor,
                "screen": _cu_screen}

    def cmd_computer_use(args):
        handler = _actions.get(getattr(args, "computer_use_action", None))
        if handler is not None:
            return handler(args)
        computer_use_parser.print_help()  # no subcommand → help

    computer_use_parser.set_defaults(func=cmd_computer_use)
