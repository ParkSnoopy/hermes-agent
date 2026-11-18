"""Historical updater handoff; no desktop driver is installed here."""

from typing import NoReturn


def install_cua_driver(*args, **kwargs) -> NoReturn:
    from hermes_cli._old_updater import stop_for_relaunch

    stop_for_relaunch()
