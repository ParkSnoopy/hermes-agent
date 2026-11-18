"""Linux headless-screen status, lifecycle, installation, and lease privacy.

``display.status`` reports runtime + lease; ``display.start`` / ``display.stop`` manage the Xvnc/Xfce
process. ``display.install`` runs the distro package install on the gateway host: sudo
privilege is asked for through the masked ``display.install.sudo`` server→client request (same ``_ask``
bridge as the terminal tool's sudo prompt), stdout streams as ``display.install.log`` and the run ends with
``display.install.done`` carrying a fresh status snapshot. Every handler is profile-scoped so a multiplexed gateway answers for the bot
the pane is looking at. Lease transitions fan out as the global ``display.lease`` event so every
connected client repaints (badge on the bot row, red border on the viewer, agent handoff prompt).

Bodies are rebound onto server.py's globals (method_ctx.bind_module) and reference them bare.
"""

import logging
import threading


from .method_ctx import HandlerRegistry, bind_module

logger = logging.getLogger(__name__)
_registry = HandlerRegistry()
method = _registry.method
_profile_scoped = _registry.profile_scoped

_DISPLAY_ERR = 5300
_lease_listener_installed = threading.Event()


def _display_snapshot() -> dict:
    from hermes_constants import hermes_home_key
    from tools.bot_desktop import lease as _bd_lease, runtime as _bd_runtime
    st = _bd_runtime.status().as_dict()
    st.pop("image_switch", None)
    return {**st, "lease": _bd_lease.public_view(_bd_lease.get()), "profile_key": hermes_home_key()}


def _install_lease_listener() -> None:
    """Once per process: broadcast every lease change to all connected clients."""
    if _lease_listener_installed.is_set():
        return
    from tools.bot_desktop import lease as _bd_lease

    def _on_change(profile_key: str, lease) -> None:
        _broadcast_global_event("display.lease", {"profile_key": profile_key, "lease": _bd_lease.public_view(lease)})
    _bd_lease.on_change(_on_change)
    _lease_listener_installed.set()  # only once the subscription exists, or a failed import would silence every client


@method("display.status")
@_profile_scoped
def _(rid, params: dict) -> dict:
    _install_lease_listener()
    try:
        return _ok(rid, _display_snapshot())
    except Exception as e:
        return _err(rid, _DISPLAY_ERR, str(e))


@method("display.thumbnail")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """One JPEG grab of the bot's screen (``data_url``: null while stopped). Read-only: no lease change.
    Suppressed while a human holds the lease — the frame may show what they are typing — and when
    the lease moved DURING the grab: a takeover racing the framebuffer read means the frame may
    already be the human's session, so it is dropped rather than shipped to every client."""
    try:
        from tools.bot_desktop import lease as _bd_lease
        before = _bd_lease.get()
        if before.holder == _bd_lease.HUMAN:
            return _ok(rid, {"data_url": None, "suppressed": "human_has_control"})
        from tools.bot_desktop.thumbnail import thumbnail_data_url
        data_url = thumbnail_data_url()
        if _bd_lease.get().epoch != before.epoch:
            return _ok(rid, {"data_url": None, "suppressed": "human_has_control"})
        return _ok(rid, {"data_url": data_url})
    except Exception as e:
        return _err(rid, _DISPLAY_ERR, str(e))


@method("display.start")
@_profile_scoped
def _(rid, params: dict) -> dict:
    _install_lease_listener()
    from tools.bot_desktop import runtime as _bd_runtime
    try:
        _bd_runtime.start()
        return _ok(rid, _display_snapshot())
    except Exception as e:
        return _err(rid, _DISPLAY_ERR, str(e))


@method("display.stop")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Stopping kills the screen under whoever is on it, so it obeys the same rule as a bare
    display.lease.release: refused while a human holds unless the caller says ``force``."""
    from tools.bot_desktop import lease as _bd_lease, runtime as _bd_runtime
    force = bool(params.get("force"))
    # Refusal and release are ONE lease transition: a takeover landing between a separate human_holds()
    # check and the release would be acknowledged to the human and then silently revoked here.
    if _bd_lease.release(unless_human=not force).holder == _bd_lease.HUMAN:
        return _err(rid, _DISPLAY_ERR, "a human holds this screen; pass force: true to stop it anyway",
                    data={"code": "viewer_mismatch"})
    try:
        stopped = _bd_runtime.stop()
        return _ok(rid, {**_display_snapshot(), "stopped": stopped})
    except Exception as e:
        return _err(rid, _DISPLAY_ERR, str(e))


@method("display.install")
@_profile_scoped
def _(rid, params: dict) -> dict:
    """Start the package install in the background; the renderer follows ``display.install.log`` /
    ``display.install.done``. Refused while one is already running for this profile."""
    from hermes_constants import hermes_home_key
    from tools.bot_desktop import install as _bd_install, runtime as _bd_runtime
    if not _bd_runtime.is_supported_host():
        return _err(rid, _DISPLAY_ERR, "Bot Desktop runs on Linux gateway hosts only")

    if _bd_runtime.install_command() is None:
        return _err(rid, _DISPLAY_ERR, "no supported package manager (apt-get, dnf, pacman) on this host")
    profile_key = hermes_home_key()

    def _ask_password() -> str:
        # App-level card, no session: it reaches the connection that clicked Install through the
        # transport copy_context() carries below. A client-supplied session_id could route the
        # masked password card into another window's chat, so none is accepted.
        return _ask("display.install.sudo", "", {"profile_key": profile_key}, timeout=300)

    def _line(text: str) -> None:
        _broadcast_global_event("display.install.log", {"profile_key": profile_key, "line": text})

    # The worker thread inherits NO context: the caller's transport (so the sudo card reaches the
    # CLIENT that clicked Install) and the profile scope `_profile_scoped` installed (so status, lock
    # and events all speak for the requested profile) are carried across with copy_context().
    import contextvars
    ctx = contextvars.copy_context()

    def _run() -> None:
        try:
            code = _bd_install.install_packages(ask_password=_ask_password, on_line=_line, claimed=True)
        except Exception as e:
            _line(f"install failed: {e}")
            code = 1
        _broadcast_global_event("display.install.done", {"profile_key": profile_key, "code": code,
                                                         "status": _display_snapshot()})

    try:
        _bd_install.claim()  # atomic: two fast clicks cannot both start a package manager
    except _bd_install.InstallBusy as e:
        return _err(rid, _DISPLAY_ERR, str(e))
    threading.Thread(target=ctx.run, args=(_run,), name=f"bot-desktop-install:{profile_key}", daemon=True).start()
    return _ok(rid, {"started": True, "command": _bd_runtime.install_command(), "profile_key": profile_key})


def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
