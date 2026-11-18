"""Frozen historical updater handoff; no desktop driver is provided."""

from hermes_cli._old_updater import stop_for_relaunch


def cua_driver_runtime_contract_status(*args, **kwargs):
    stop_for_relaunch()


def resolve_cua_driver_cmd(*args, **kwargs):
    stop_for_relaunch()


def cua_driver_update_check(*args, **kwargs):
    stop_for_relaunch()
