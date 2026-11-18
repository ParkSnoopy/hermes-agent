"""Frozen historical updater handoff; no desktop daemon is provided."""

from hermes_cli._old_updater import stop_for_relaunch


def _resolve_cua_driver_app_path(*args, **kwargs):
    stop_for_relaunch()


def _validate_cua_driver_app_signature(*args, **kwargs):
    stop_for_relaunch()
