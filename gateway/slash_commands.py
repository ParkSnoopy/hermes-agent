"""Gateway slash-command handlers for GatewayRunner: lifted out of ``gateway/run.py`` into a mixin
so ``self._handle_*_command`` keeps resolving via the MRO.  Cohesive clusters live in the sibling
mixins (``slash_commands_model/_session/_status/_goals``); this module keeps the shared helpers plus
the one-off commands.  run.py helpers are imported lazily."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import logging
import os
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Union


from gateway.config import Platform
from gateway.platforms.base import EphemeralReply
from gateway.platforms.event import MessageEvent
from gateway.session import AsyncSessionStore
from gateway.session_transcript import TranscriptReadError
from gateway.slash_commands_goals import GatewayGoalCommandsMixin
from gateway.slash_commands_model import GatewayModelCommandsMixin
from gateway.slash_commands_session import GatewaySessionCommandsMixin
from gateway.slash_commands_login import GatewayLoginCommandsMixin
from gateway.slash_commands_status import GatewayStatusCommandsMixin, history_unreadable
from hermes_cli.config import atomic_config_write, cfg_get
from utils import atomic_json_write, is_truthy_value

logger = logging.getLogger("gateway.run")


# /rollback result keys -> lines for files the safe restore left alone.
_ROLLBACK_SKIP_LINES = (("skipped_user_edits", '↷ Kept your hand-edits: {files}\nUse /rollback <N> --all to restore those too.'),
                        ("skipped_oversize", '↷ Kept (too large for checkpoints, no stored copy to revert to): {files}'),
                        ("failed_deletes", '⚠️ Could not remove (left in place): {files}'))

# /busy input modes -> (status-card description, set-confirmation text).
_BUSY_MODE_BEHAVIOR = {
    "queue": ('queues for next turn', 'Messages will be queued for the next turn while Hermes is busy.'),
    "steer": ('steers into current run (after next tool call)', 'Messages will be steered into the current run (after the next tool call).'),
    "interrupt": ('interrupts current run', 'Messages will interrupt the current run while Hermes is busy.'),
}

# /diff argument -> diff mode (unknown args leave the mode unchanged).
_DIFF_MODE_BY_ARG = {**dict.fromkeys(("staged", "--staged", "cached", "--cached"), "staged"),
                     **dict.fromkeys(("all", "--all", "head"), "all"), "session": "session"}

# /voice subcommand -> stored mode (None = auto-TTS disabled), confirmation text.
_VOICE_MODE_BY_ARG = {
    **dict.fromkeys(("on", "enable"), ("voice_only", "Voice mode enabled.\nI'll reply with voice when you send voice messages.\nUse /voice tts to get voice replies for all messages.")),
    **dict.fromkeys(("off", "disable"), ("off", 'Voice mode disabled. Text-only replies.')),
    "tts": ("all", 'Auto-TTS enabled.\nAll replies will include a voice message.')}

# /footer argument -> new enabled state ("" toggles; anything else is a usage error).
_FOOTER_STATE_BY_ARG = {**dict.fromkeys(("on", "enable", "true", "1"), True),
                        **dict.fromkeys(("off", "disable", "false", "0"), False)}

# /approve modifier tokens -> approval choice (default "once").
_APPROVE_CHOICE_BY_ARG = {**dict.fromkeys(("always", "permanent", "permanently"), "always"),
                          **dict.fromkeys(("session", "ses"), "session")}


_WINDOWS_UPDATE_HELPER = """
import os, subprocess, sys
output_path, exit_code_path, cmd = sys.argv[1], sys.argv[2], sys.argv[3:]
env = dict(os.environ, PYTHONUNBUFFERED="1")
with open(output_path, "wb") as f:
    rc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=env).wait(timeout=3600)
# Temp + replace: the gateway watcher polls the exit code and reads an empty file as failure.
with open(exit_code_path + ".tmp", "w", encoding="utf-8") as f:
    f.write(str(rc))
os.replace(exit_code_path + ".tmp", exit_code_path)
""".strip()


def _nested_dict(root: dict, *keys: str) -> dict:
    """Walk/create ``root[k1][k2]...`` as dicts, replacing any non-dict value on the path."""
    for k in keys:
        if not isinstance(root.get(k), dict):
            root[k] = {}
        root = root[k]
    return root


def _write_raw_config_leaf(config_path: Path, keys: tuple, value) -> None:
    """Set one leaf through a strict raw round-trip. The behavioral read is fail-open (``{}``) and
    expanded, so writing it back wipes the file after a read error and persists ``${VAR}`` values."""
    from hermes_cli.config import read_user_config_raw
    raw = read_user_config_raw(config_path)
    *parents, leaf = keys
    _nested_dict(raw, *parents)[leaf] = value
    atomic_config_write(config_path, raw)


def _preview(text: str, limit: int = 60) -> str:
    return text[:limit] + ("..." if len(text) > limit else "")


def _execute(command: str, **ctx_kwargs):
    """Run *command* through the shared slash executor on the gateway surface."""
    from hermes_cli.slash_exec import CommandContext, execute_command
    return execute_command(command, CommandContext(surface="gateway", **ctx_kwargs))


def _restart_notify_payload(event: MessageEvent) -> dict:
    """Requester routing info so the new gateway process can notify them once back online.
    ``profile`` is persisted so the notice leaves through the requester's own profile bot after the
    restart (a bare platform lookup would resolve the default profile's adapter)."""
    source = event.source
    data = {"platform": source.platform.value if source.platform else None,
            "chat_id": source.chat_id, "chat_type": source.chat_type}
    if source.delivered_via_upstream_relay is True:
        data["delivered_via_upstream_relay"] = True
        data.update({k: getattr(source, k) for k in ("user_id", "scope_id") if getattr(source, k)})
    optional = (("thread_id", source.thread_id), ("message_id", event.message_id),
                ("profile", getattr(source, "profile", None)))
    data.update({k: v for k, v in optional if v})
    return data


def _spawn_detached_update(hermes_cmd, output_path, exit_code_path) -> None:
    """Spawn ``hermes update --gateway`` detached so it survives the gateway restart it may trigger.
    setsid is portable (works where ``systemd-run --user`` lacks a D-Bus session); ``--gateway``
    enables file-based IPC so interactive prompts are forwarded; PYTHONUNBUFFERED lets the gateway
    stream output live.  Windows has no setsid: an inline helper runs the updater as a module under
    this interpreter (not venv\\Scripts\\hermes.exe — that shim holds its own file open, and the
    update must replace it), redirects both outputs to one file and writes the exit code."""
    import shutil
    import subprocess
    if sys.platform == "win32":
        from hermes_cli._subprocess_compat import windows_detach_popen_kwargs
        subprocess.Popen(
            [sys.executable, "-c", _WINDOWS_UPDATE_HELPER, str(output_path), str(exit_code_path),
             sys.executable, "-m", "hermes_cli.main", "update", "--gateway"],
            # detached-spawn: ok — /update runs `hermes update`, which must outlive the gateway it restarts
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **windows_detach_popen_kwargs())
        return
    hermes_cmd_str = " ".join(shlex.quote(part) for part in hermes_cmd)
    update_cmd = (
        f"PYTHONUNBUFFERED=1 {hermes_cmd_str} update --gateway"
        f" > {shlex.quote(str(output_path))} 2>&1; "
        # Avoid `status=$?`: `status` is read-only in zsh and this template is reused in
        # macOS/zsh operator wrappers, so keep it zsh-safe even though bash runs it here.
        # Temp + mv: the gateway watcher polls the exit code and reads an empty file as failure.
        f"rc=$?; printf '%s' \"$rc\" > {shlex.quote(str(exit_code_path) + '.tmp')}"
        f" && mv -f {shlex.quote(str(exit_code_path) + '.tmp')} {shlex.quote(str(exit_code_path))}")
    # Preferred: setsid creates a new session, fully detached; fallback start_new_session=True
    # calls os.setsid() in the child.
    setsid_bin = shutil.which("setsid")
    argv = [setsid_bin, "bash", "-c", update_cmd] if setsid_bin else ["bash", "-c", update_cmd]
    # detached-spawn: ok — /update runs `hermes update`, which must outlive the gateway it restarts
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)



class GatewaySlashCommandsMixin(
    GatewayLoginCommandsMixin,
    GatewayModelCommandsMixin,
    GatewaySessionCommandsMixin,
    GatewayStatusCommandsMixin,
    GatewayGoalCommandsMixin):
    """In-session slash-command handlers for GatewayRunner (plus the helpers the sibling mixins share)."""

    async_session_store: AsyncSessionStore

    # ------------------------------------------------------------------ shared helpers
    def _cached_agent_for(self, session_key: str, *, lockless_fallback: bool = False):
        """Peek the cached AIAgent for *session_key* without evicting it, or None. Entries are
        ``(agent, signature, ...)`` tuples (bare agents from test doubles accepted). Historical callers
        read the cache ONLY under ``_agent_cache_lock`` and got None when a fixture that skipped
        ``__init__`` had no lock; the manual codex ``/compress`` path was the one exception that read
        lock-free (``lockless_fallback=True``)."""
        cache = getattr(self, "_agent_cache", None)
        lock = getattr(self, "_agent_cache_lock", None)
        if cache is None or (lock is None and not lockless_fallback):
            return None
        try:
            if lock:
                with lock:
                    entry = cache.get(session_key)
            else:
                entry = cache.get(session_key)
        except Exception:
            return None
        return (entry[0] if entry else None) if isinstance(entry, (tuple, list)) else entry or None

    def _resident_agent_for(self, session_key: str):
        """The live running agent for *session_key*, else the cached one, else None. The pending
        sentinel (a run that is starting) never counts as a usable agent."""
        from gateway.run import _AGENT_PENDING_SENTINEL
        agent = self._running_agents.get(session_key)
        if agent is not None and agent is not _AGENT_PENDING_SENTINEL:
            return agent
        return self._cached_agent_for(session_key)

    @staticmethod
    def _session_db_unavailable_reply() -> str:
        from hermes_state import format_session_db_unavailable
        return format_session_db_unavailable(prefix='Session database not available')

    def _reply_metadata(self, event: MessageEvent):
        """Thread/reply metadata for an outbound send anchored on *event*."""
        return self._thread_metadata_for_source(event.source, self._reply_anchor_for_event(event))

    def _adapter_and_key_for(self, event: MessageEvent):
        """``(adapter, session_key)`` for the event's source, either None when no source. The source's
        OWN transport (profile-aware, fail-closed) — ``self.adapters`` is the default profile's map."""
        if not event.source:
            return None, None
        return self._delivery_adapter_for(event.source), self._session_key_for_source(event.source)


    def _checkpoint_manager(self, source=None):
        """A CheckpointManager from a local session's frozen policy, else gateway config; None when disabled."""
        from gateway.run import _checkpoint_agent_kwargs, _load_gateway_config
        from gateway.session_authorities import active_authority
        from gateway.session_policy import policy_for_source
        from tools.checkpoint_manager import CheckpointManager
        policy = policy_for_source(self, source) if source is not None else None
        cp = _checkpoint_agent_kwargs(policy.config(active_authority(self), secrets=False) if policy
                                      else _load_gateway_config())
        if not cp["checkpoints_enabled"]:
            return None
        # AIAgent kwargs are ``checkpoint_<field>``; CheckpointManager takes the bare field names.
        fields = {k[len("checkpoint_"):]: v for k, v in cp.items() if k.startswith("checkpoint_")}
        return CheckpointManager(enabled=True, **fields)

    def _write_approval_setter(self, section: str, event: MessageEvent):
        """``set_mode_fn`` for /memory and /skills: persist ``<section>.write_approval``. Raw read is
        correct for the write-back round-trip (merged defaults must not be persisted back to the
        user's file); the cached agent is dropped so the setting takes effect next message."""
        from gateway.run import _gateway_config_home
        # Persist to config (default) unless --session opted out, mirroring the text /model command path
        # above so a picked model survives across sessions like a typed one (#49066).
        from hermes_cli.config import read_user_config_raw
        config_path = _gateway_config_home() / "config.yaml"
        session_key = self._session_key_for_source(event.source)

        def _set_approval(enabled: bool):
            user_config = read_user_config_raw(config_path)
            user_config.setdefault(section, {})["write_approval"] = bool(enabled)
            atomic_config_write(config_path, user_config)
            # Evict any cached agent for this session so the next message rebuilds with the correct
            # session_id end-to-end — mirrors /branch and /reset. Without this, the cached AIAgent (and its
            # memory provider, which cached `_session_id` during initialize()) keeps writing into the wrong
            # session's record. See #6672.
            self._evict_cached_agent(session_key)
        return _set_approval

    async def _deliver_approval_confirmation(self, event: MessageEvent, confirmation_text: str, verb: str):
        """Return *confirmation_text* for normal delivery, or push it on native-streaming adapters
        (WeCom msgtype:"stream"), which need it sent directly with control-lane metadata (reliable
        proactive send, not the finalized reply stream). ``is not True``: mocks auto-create attrs."""
        source = event.source
        adapter = self._delivery_adapter_for(source)  # the receiving bot, not the default profile's
        if adapter:
            adapter.resume_typing_for_chat(source.chat_id)  # agent is about to continue
        if getattr(adapter, "SUPPORTS_NATIVE_STREAMING", False) is not True:
            return confirmation_text
        if adapter:
            try:
                await adapter.send(
                    source.chat_id, confirmation_text, reply_to=event.message_id,
                    metadata={"is_approval_prompt": True, "force_proactive_send": True})
            except Exception as exc:
                logger.warning("Failed to send /%s confirmation to %s: %s", verb, source.chat_id,
                               exc, exc_info=True)
        return None

    def _typed_command_prefix_for(self, platform) -> str:
        """The prefix users can always type to reach Hermes commands (adapter ``typed_command_prefix``,
        default "/"). Slack and Matrix use "!" because typed "/" is blocked/reserved there; their
        adapters rewrite "!command" to "/command"."""
        adapter = self.adapters.get(platform) if getattr(self, "adapters", None) else None
        return getattr(adapter, "typed_command_prefix", "/") if adapter is not None else "/"

    def _terminal_cwd(self) -> str:
        from tools.terminal_scope import terminal_env
        return terminal_env("TERMINAL_CWD", str(Path.home()))

    @staticmethod
    def _display_config_target(event: MessageEvent):
        """``(config.yaml path, platform config key)`` for the per-platform display settings."""
        from gateway.run import _gateway_config_home, _platform_config_key
        return _gateway_config_home() / "config.yaml", _platform_config_key(event.source.platform)

    async def _handle_profile_command(self, event: MessageEvent) -> str:
        """Handle /profile — show the profile serving this source and its home.  On a multiplexed
        gateway the process-level profile is the multiplexer's own ("default" in every chat), so
        with ``multiplex_profiles`` on report ``source.profile`` and resolve home under that
        profile's runtime scope; when off the stamp is ignored, mirroring ``_run_agent``."""
        from hermes_constants import display_hermes_home
        source = getattr(event, "source", None)
        profile_name = display = ""
        if getattr(getattr(self, "config", None), "multiplex_profiles", False):
            profile_name = (getattr(source, "profile", "") or "").strip()
            try:
                from gateway.run import _profile_runtime_scope
                with _profile_runtime_scope(self._resolve_profile_home_for_source(source)):
                    display = display_hermes_home()
            except Exception:
                display = display_hermes_home()

        # Shared executor resolves process-level fallbacks; the multiplexed per-source overrides
        # (when any) ride in via options.
        reply = _execute("profile", options={"profile_name": profile_name, "home_display": display})
        return "\n".join([('👤 **Profile:** `{profile}`').format(profile=reply.data["profile"]),
                          ('📂 **Home:** `{home}`').format(home=reply.data["home"])])

    async def _handle_whoami_command(self, event: MessageEvent) -> str:
        """Handle /whoami — platform, DM-vs-group scope, tier and runnable commands (always allowed)."""
        from gateway.slash_access import policy_for_runner_source
        source = event.source
        policy = policy_for_runner_source(self, source)
        platform = source.platform.value if source and source.platform else "?"
        chat_type = ((source.chat_type if source else "") or "dm").lower()
        scope = ('DM' if chat_type in {"dm", "direct", "private", ""} else 'group/channel')
        user_id = (source.user_id if source else None) or "?"
        head = (f'**You** — {platform} ({scope})\nUser ID: `{user_id}`\n')
        if not policy.enabled:
            return head + 'Tier: unrestricted (no admin list configured for this scope)\nSlash commands: all available'
        if policy.is_admin(user_id):
            return head + 'Tier: **admin**\nSlash commands: all available'
        # Non-admin: floor first (mirrors slash_access._ALWAYS_ALLOWED_FOR_USERS), then operator
        # additions, deduped in order.
        runnable = list(dict.fromkeys(["help", "whoami"] + sorted(policy.user_allowed_commands)))
        runnable_str = ", ".join(f"/{c}" for c in runnable) if runnable else '(none)'
        return head + (f'Tier: user\nSlash commands you can run: {runnable_str}')


    async def _handle_stop_command(self, event: MessageEvent) -> str | EphemeralReply:
        """Handle /stop command - interrupt a running agent.  A truly hung agent (blocked thread
        never checking _interrupt_requested) is caught by the early intercept in _handle_message();
        this handler runs via normal dispatch or as a fallback, and force-cleans the session lock in
        all cases.  The session is preserved so the user can continue."""
        from gateway.run import _AGENT_PENDING_SENTINEL, _INTERRUPT_REASON_STOP
        source = event.source
        session_entry = await self.async_session_store.get_or_create_session(source)
        session_key = session_entry.session_key
        await self._hm_stop_owner_turn(session_key)  # also between turns, before the next claim (N53)

        async def _stop(key: str, invalidation_reason: str) -> None:
            await self._interrupt_and_clear_session(
                key, source, interrupt_reason=_INTERRUPT_REASON_STOP,
                invalidation_reason=invalidation_reason)
        # A turn the app started while the chat was elsewhere still runs on its own key (/resume).
        from gateway.session_detached import chat_running_key
        run_key = chat_running_key(self, session_key, session_entry)
        agent = self._running_agents.get(run_key)
        if agent is _AGENT_PENDING_SENTINEL:  # force-clean the sentinel so the session is unlocked
            await _stop(run_key, "stop_command_pending")
            logger.info("STOP (pending) for session %s — sentinel cleared", run_key)
            return EphemeralReply("⚡ Stopped. The agent hadn't started yet — you can continue this session.")
        if agent:  # force-clean the session lock so a truly hung agent doesn't keep it forever
            await _stop(run_key, "stop_command_handler")
            return EphemeralReply('⚡ Stopped. You can continue this session.')

        # No run under the caller's own key: a live turn in THIS chat may still carry a differently
        # shaped key. One scan feeds both tiers; the chat tier is a superset of the thread-sibling
        # tier (a sibling needs the caller's own thread slot, which satisfies the chat predicate), so
        # it is the set to act on — acting on the sibling subset alone would reply "Stopped" while a
        # same-thread run under a differently shaped key kept going. See `_chat_scoped_run_keys` for
        # the shapes and isolation bounds; both tiers are authorization-gated.
        runs = self._same_chat_runs(source, session_key)
        sibling_keys = self._sibling_thread_run_keys(source, runs)
        fallback_keys = self._chat_scoped_run_keys(source, runs)
        # Reason is per-stop, not per-key: a stop that only ever had thread siblings keeps its own
        # label for hook consumers, anything wider is a chat-scope stop.
        reason = (
            "stop_command_thread_sibling"
            if fallback_keys == sibling_keys
            else "stop_command_chat_scope"
        )
        if fallback_keys and self._is_user_authorized_for_source(source):
            for fallback_key in fallback_keys:
                await _stop(fallback_key, reason)
            logger.info("STOP (%s) by %s — interrupted %d run(s): %s",
                        reason, session_key, len(fallback_keys), ", ".join(fallback_keys))
            return EphemeralReply('⚡ Stopped. You can continue this session.')

        # No running agent anywhere for this scope. Background delegations the session dispatched in an
        # earlier turn still count as "active": stop them; each returns as an interrupted completion.
        from tools.async_delegation import interrupt_for_session
        if interrupt_for_session(session_key=session_key, reason="stop_command",
                                 parent_session_id=str(getattr(session_entry, "session_id", "") or "")):
            return EphemeralReply('⚡ Stopped. You can continue this session.')
        # A platform status indicator can still be stuck —
        # e.g. Slack's persistent assistant.threads.setStatus survives a gateway restart or a turn
        # that died without a final send.
        # Best-effort clear so /stop always dismisses a phantom "is thinking...". See #32295.
        adapter = getattr(self, "adapters", {}).get(source.platform)
        try:
            if adapter and hasattr(adapter, "_stop_typing_with_metadata"):
                await adapter._stop_typing_with_metadata(source.chat_id, self._reply_metadata(event))
        except Exception:
            logger.debug("Failed to clear typing on /stop with no active agent", exc_info=True)
        return 'No active task to stop.'


    async def _handle_restart_command(self, event: MessageEvent) -> str | EphemeralReply | None:
        """Handle /restart command - drain active work, then restart the gateway."""
        from gateway.run import _hermes_home

        if self._restart_requested or self._draining:
            count = self._running_agent_count()
            return (f'⏳ Draining {count} active agent(s) before restart...') if count else EphemeralReply('⏳ Gateway restart already in progress...')

        async def _write_marker(name: str, build, label: str) -> None:
            try:
                await asyncio.to_thread(atomic_json_write, _hermes_home / name, build(), indent=None)
            except Exception as e:
                logger.debug("Failed to write restart %s: %s", label, e)

        def _notify_payload() -> dict:
            data = _restart_notify_payload(event)
            mid = str(event.message_id) if event.message_id is not None else event.source.message_id
            try:
                self._restart_command_source = dataclasses.replace(event.source, message_id=mid)
            except Exception:
                self._restart_command_source = event.source
            return data


        # Save the requester's routing info so the new gateway process can notify them once back.
        await _write_marker(".restart_notify.json", _notify_payload, "notify file")

        active_agents = self._running_agent_count()
        # Idle: teardown is immediate and a RETURNED ack raced the adapter disconnect (lost on a real
        # Bot API round trip, #65057), so deliver it first. Draining: the drain waits, the reply lands.
        from gateway.restart_ack import send_restart_ack
        ack = (f'⏳ Draining {active_agents} active agent(s) before restart...') if active_agents else EphemeralReply("♻ Restarting gateway. If you aren't notified within 60 seconds, restart from the console with `hermes gateway restart`.")
        sent = not active_agents and await send_restart_ack(self, self._delivery_adapter_for(event.source), event, ack)
        # Under a service manager (systemd/launchd) or Docker/Podman, exit 75 so the supervisor /
        # restart policy restarts us — detached setsid+bash fails there (systemd KillMode=mixed kills
        # the cgroup; tini exits with the gateway). The explicit marker covers ``sudo env -i`` wrappers.
        from gateway.restart import is_container_restart_context, is_gateway_supervisor_process
        via_service = is_gateway_supervisor_process() or is_container_restart_context()
        self.request_restart(detached=not via_service, via_service=via_service)
        return None if sent else ack

    async def _handle_autoprune_command(self, event: MessageEvent) -> str:
        """Handle /autoprune — the low-disk auto-prune decision (same text as the CLI)."""
        from hermes_cli.prune_advisor import answered
        reply = _execute("autoprune", args=event.get_command_args()).text
        if event.get_command_args().strip() and answered():  # a failed save or `status` keeps it
            from gateway.run_housekeeping_disk import clear_local_auto_prune_notice
            clear_local_auto_prune_notice(self, event.source)
        return reply

    async def _handle_version_command(self, event: MessageEvent) -> str:
        """Handle /version — show the running Hermes Agent version."""
        return _execute("version").text

    def _catalog_options(self, event: MessageEvent) -> dict:
        """``allowed_commands`` for /help and /commands when the caller is a gated non-admin:
        the slash-access floor + ``user_allowed_commands`` (mirrors /whoami), so the catalog
        never advertises commands ``_check_slash_access`` would refuse. Admins / ungated -> {}."""
        from gateway.slash_access import policy_for_runner_source
        source = event.source
        # Partially-constructed runners (``GatewayRunner.__new__`` in tests) have no ``config``;
        # policy_for_source treats None as ungated.
        policy = policy_for_runner_source(self, source)
        if policy.enabled and not policy.is_admin(source.user_id if source else None):
            return {"allowed_commands": {"help", "whoami", *policy.user_allowed_commands}}
        return {}

    async def _handle_help_command(self, event: MessageEvent) -> str:
        """Handle /help command - list available commands."""
        return _execute("help", options=self._catalog_options(event)).text

    async def _handle_commands_command(self, event: MessageEvent) -> str:
        options = {"page_size": 20, **self._catalog_options(event)}
        reply = _execute("commands", args=event.get_command_args(), options=options)
        return reply.text


    async def _handle_voice_command(self, event: MessageEvent) -> str:
        """Handle /voice [on|off|tts|status] command."""
        args = event.get_command_args().strip().lower()
        chat_id = event.source.chat_id
        # Voice state belongs to the (bot, chat) pair: resolve the adapter that received the
        # command and key the mode by its owning profile so two multiplexed bots in one chat keep
        # independent /voice state.
        # See #75198.
        voice_key = self._voice_key_for_source(event.source)
        adapter = self._delivery_adapter_for(event.source)

        def _set_mode(mode: str) -> None:
            self._voice_mode[voice_key] = mode
            self._save_voice_modes()
            from gateway.session_config import local_voice_transition
            local_voice_transition(event.source, mode)
            if not adapter:
                return
            if mode == "off":
                self._set_adapter_auto_tts_disabled(adapter, chat_id, disabled=True)
            else:
                self._set_adapter_auto_tts_enabled(adapter, chat_id, enabled=True)

        if args in _VOICE_MODE_BY_ARG:
            mode, reply_key = _VOICE_MODE_BY_ARG[args]
            _set_mode(mode)
            return reply_key
        if args in {"channel", "join", "leave"}:
            return "Voice channels are no longer supported."
        if args == "status":
            mode = self._voice_mode.get(voice_key, "off")
            label = {'off': 'Off (text only)', 'voice_only': 'On (voice reply to voice messages)', 'all': 'TTS (voice reply to all messages)'}.get(mode, mode)
            lines = [(f'Voice mode: {label}')]
            return "\n".join(lines)

        # Toggle: off → on, on/all → off
        turning_on = self._voice_mode.get(voice_key, "off") == "off"
        _set_mode("voice_only" if turning_on else "off")
        toggle_line = ('Voice mode enabled.' if turning_on else 'Voice mode disabled.')
        return ('{toggle}\n\n**How /voice works**\n• `/voice on` — voice reply when you send a voice message\n• `/voice tts` — voice reply to *every* message\n• `/voice off` — back to text-only replies\n• `/voice status` — show the current mode\n• `/voice` (no argument) — quick toggle between on and off{channels}').format(toggle=toggle_line, channels="")

    async def _handle_rollback_command(self, event: MessageEvent) -> str:
        """Handle /rollback command — list or restore filesystem checkpoints."""
        from tools.checkpoint_manager import format_checkpoint_list
        mgr = self._checkpoint_manager(event.source)
        if mgr is None:
            return 'Checkpoints are not enabled.\nEnable in config.yaml:\n```\ncheckpoints:\n  enabled: true\n```'
        cwd = self._terminal_cwd()
        # --all / --force: classic full restore, overwriting user edits too.
        tokens = event.get_command_args().strip().split()
        restore_all = any(tok.lower() in ("--all", "--force") for tok in tokens)
        arg = " ".join(tok for tok in tokens if tok.lower() not in ("--all", "--force"))
        # Container-backed session: host checkpoints belong to another tree, so a restore is
        # refused; the bare listing stays visible, prefixed with the reason (same as the CLI).
        reason = mgr.unsupported_backend_reason()
        if reason and arg:
            return reason
        checkpoints = mgr.list_checkpoints(cwd)
        if not arg:
            listing = format_checkpoint_list(checkpoints, cwd)
            return f"{reason}\n{listing}" if reason else listing
        if not checkpoints:
            return (f'No checkpoints found for {cwd}')

        # Restore by number or hash (a short hash can be all digits)
        from tools.checkpoint_refs import resolve_checkpoint_ref
        try:
            target_hash = resolve_checkpoint_ref(checkpoints, arg)
        except ValueError:
            return (f'Invalid checkpoint number. Use 1-{len(checkpoints)}.')
        result = mgr.restore(cwd, target_hash, safe=not restore_all)
        if not result["success"]:
            return ('❌ {error}').format(error=result["error"])
        msg = ('✅ Restored to checkpoint {hash}: {reason}\nA pre-rollback snapshot was saved automatically.').format(hash=result["restored_to"], reason=result["reason"])
        for result_key, template in _ROLLBACK_SKIP_LINES:
            files = result.get(result_key) or []
            if files:
                more = f" (+{len(files) - 5})" if len(files) > 5 else ""
                msg += "\n" + template.format(files=", ".join(files[:5]) + more)
        return msg

    async def _handle_diff_command(self, event: MessageEvent) -> str:
        """Handle /diff — show git changes in the working directory.  Diff body is truncated hard
        here (chat is not a pager); platform senders clamp further."""
        args = [a.lower() for a in event.get_command_args().strip().split()]
        stat_only = bool({"--stat", "stat"} & set(args))
        mode = "working"
        for low in args:
            mode = _DIFF_MODE_BY_ARG.get(low, mode)
        cwd = self._terminal_cwd()
        if mode == "session":
            # Cumulative checkpoint-baseline diff.
            mgr = self._checkpoint_manager(event.source)
            if mgr is None:
                return "Checkpoints are not enabled, so there's no session baseline.\nEnable in config.yaml:\n```\ncheckpoints:\n  enabled: true\n```\nPlain /diff still works — it uses git directly."
            if reason := mgr.unsupported_backend_reason():  # host baseline is not this session's tree
                return reason
            result = await asyncio.to_thread(mgr.session_diff, cwd)
        else:
            from tools.working_diff import collect_working_diff
            result = await asyncio.to_thread(collect_working_diff, cwd, mode)
        if not result.get("success"):
            return ('{error}').format(error=result.get("error") or 'Could not generate diff')
        return self._render_diff_result(result, stat_only)

    def _render_diff_result(self, result: dict, stat_only: bool) -> str:
        """Render a working/session diff result: stat block, untracked list, fenced (truncated) diff."""
        stat = result.get("stat", "")
        diff = result.get("diff", "")
        untracked = result.get("untracked", [])
        if result.get("empty") or (not stat and not diff and not untracked):
            return 'No changes.'
        out: list[str] = []
        if stat:
            out.append(f"```\n{stat}\n```")
        if untracked:
            shown = "\n".join(f"+ {rel}" for rel in untracked[:15])
            more = (f'\n... and {len(untracked) - 15} more') if len(untracked) > 15 else ""
            out.append(f"{'**Untracked:**'}\n```\n{shown}{more}\n```")
        if not stat_only and diff:
            out.append(self._fenced_truncated_diff(diff))
        return "\n\n".join(out)

    @staticmethod
    def _fenced_truncated_diff(diff: str, max_lines: int = 60, max_chars: int = 3000) -> str:
        """Fence a diff body, truncating to messaging-friendly size."""
        diff_lines = diff.splitlines()
        truncated = len(diff_lines) > max_lines
        if truncated:
            diff = "\n".join(diff_lines[:max_lines])
        if len(diff) > max_chars:
            diff = diff[:max_chars]
            truncated = True
        note = ""
        if truncated:
            note = (f'\n... (truncated — {len(diff_lines)} lines total; use /diff --stat for a summary)')
        return f"```diff\n{diff}{note}\n```"

    def _track_background_task(self, coro) -> None:
        """Fire-and-forget *coro*, keeping a strong ref in ``_background_tasks`` until it finishes."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _handle_background_command(self, event: MessageEvent) -> str:
        """Handle /bg <prompt> — run a prompt in a background thread with its own session; the
        result is sent to the same chat without touching the active session's history."""
        prompt = event.get_command_args().strip()
        if not prompt:
            return 'Usage: /bg <prompt>\nExample: /bg Summarize the top HN stories today\n\nRuns the prompt in a separate session. You can keep chatting — the result will appear here when done.'
        task_id = f"bg_{datetime.now().strftime('%H%M%S')}_{os.urandom(3).hex()}"
        self._track_background_task(self._run_background_task(
            prompt, event.source, task_id, event_message_id=self._reply_anchor_for_event(event),
            # Forward image/audio attachments so the background agent can see them.
            media_urls=list(event.media_urls or []), media_types=list(event.media_types or [])))
        return (f'🔄 Background task started: "{_preview(prompt)}"\nTask ID: {task_id}\nYou can keep chatting — results will appear when done.')

    async def _handle_btw_command(self, event: MessageEvent) -> str:
        """Handle /btw <question> — one-shot auxiliary LLM call on a transcript snapshot; live history
        is never touched (alternation + prompt cache intact, current turn keeps running). Unlike /bg,
        which spawns a fresh contextless session."""
        question = event.get_command_args().strip()
        if not question:
            return 'Usage: /btw <question>\nExample: /btw which file was that error in?\n\nAnswers a quick side question about this conversation without interrupting it. For an independent background task, use /bg <prompt>.'
        source = event.source
        session_entry = await self.async_session_store.get_or_create_session(source)
        try:
            history = await self.async_session_store.load_transcript(session_entry.session_id)
        except TranscriptReadError:
            return history_unreadable()
        if not history:
            return 'No conversation yet — send your question as a normal message instead.'
        try:
            model, rt = self._resolve_session_agent_runtime(source=source)
        except Exception:
            model, rt = None, {}
        if not rt.get("api_key"):
            return '❌ Cannot answer side question: no provider credentials configured.'
        main_runtime = {
            "model": model,
            **{k: rt.get(k) for k in ("provider", "base_url", "api_key", "api_mode")},
            "session_id": session_entry.session_id,
        }
        history_snapshot = list(history)
        # Prefer the cache-parity fork when a live cached AIAgent exists: it replays the snapshot
        # against the warm provider prefix cache, giving FULL context at cache-read prices. With no
        # cached agent the cache is cold anyway — answer_side_question's digest fallback handles it.
        try:
            parent_agent = self._cached_agent_for(self._session_key_for_source(source))
        except Exception:
            parent_agent = None
        _thread_metadata = self._reply_metadata(event)
        adapter = self._delivery_adapter_for(source)
        preview = _preview(question)

        async def _run_side_question() -> None:
            from agent.side_question import answer_side_question
            try:
                answer = await asyncio.to_thread(
                    answer_side_question, question, history_snapshot,
                    parent_agent=parent_agent, main_runtime=main_runtime)
                reply = ('💬 /btw: "{preview}"\n\n{answer}').format(preview=preview, answer=answer or "")
            except Exception as e:
                logger.warning("/btw side question failed: %s", e)
                reply = (f'❌ /btw failed: "{preview}"\n{e!s}')
            if adapter is not None:
                await adapter.send(source.chat_id, reply, metadata=_thread_metadata)

        self._track_background_task(_run_side_question())
        return (f'💬 Side question: "{preview}"\nAnswering from a snapshot of this conversation — the current work continues.')

    async def _handle_memory_command(self, event: MessageEvent) -> str:
        """Handle /memory — review pending memory writes + toggle the approval gate. Entries are small
        enough to review inline, so the full flow works on every platform."""
        from hermes_cli.write_approval_commands import handle_pending_subcommand
        from tools import write_approval as wa
        from tools.memory_tool import load_on_disk_store
        # Apply approved writes against a fresh on-disk store (the gateway has no long-lived agent;
        # the store persists to the same MEMORY/USER.md and honors the configured char limits).
        out = handle_pending_subcommand(
            wa.MEMORY, event.get_command_args().strip().split(), memory_store=load_on_disk_store(),
            set_mode_fn=self._write_approval_setter("memory", event))
        return out if out is not None else 'Unknown /memory subcommand. Use: pending, approve <id>, reject <id>, approval <on|off>.'

    async def _handle_skills_command(self, event: MessageEvent) -> str:
        """Handle /skills on the gateway — pending skill-write review only (hub stays CLI-only). Gated
        by ``skills.write_approval`` but still answers when staged writes exist after the gate is off
        (never stranded). ``diff`` is truncated for chat."""
        from hermes_cli.write_approval_commands import handle_pending_subcommand
        from tools import write_approval as wa
        args = event.get_command_args().strip().split()
        sub = args[0].lower() if args else ""
        gate_off = not wa.write_approval_enabled(wa.SKILLS) and sub not in {"approval", "mode"}
        if gate_off and wa.pending_count(wa.SKILLS) == 0:
            return 'Skill write approval is off (skills.write_approval). Enable it with /skills approval on, then review staged writes here with /skills pending.'
        out = handle_pending_subcommand(
            wa.SKILLS, args, set_mode_fn=self._write_approval_setter("skills", event))
        if out is None:
            return 'Unknown /skills subcommand on this platform. Use: pending, approve <id>, reject <id>, diff <id>, approval <on|off>. (Search/install are CLI-only.)'

        # Chat bubbles can't hold a full skill diff — truncate and point at the pending JSON file
        # (NOT `hermes skills diff <name>`, which diffs a bundled skill against its stock version).
        if sub == "diff" and len(out) > 3000:
            pending_id = args[1] if len(args) > 1 else "<id>"
            out = out[:3000] + (f'\n… (truncated — full diff in ~/.hermes/pending/skills/{pending_id}.json)')
        return out

    async def _handle_approvals_command(self, event: MessageEvent) -> str:
        """Show or persist the profile-wide dangerous-command approval mode."""
        from gateway.slash_access import policy_for_runner_source
        from hermes_cli.approval_mode import run_approval_mode_command
        requested = event.get_command_args().strip() or None
        # This mutates profile-wide security policy. The central slash gate can allow selected
        # commands to non-admin users, so enforce admin again at this side-effect boundary.
        # Unconfigured policies remain unrestricted.
        policy = policy_for_runner_source(self, event.source)
        if requested and not policy.is_admin(event.source.user_id):
            return 'Only gateway admins can change the persistent approval mode.'
        # Approval checks load config dynamically; do not evict the cached agent or alter its
        # system prompt/tool schema (prompt-cache prefix is sacred).
        return run_approval_mode_command(requested).message

    async def _handle_yolo_command(self, event: MessageEvent) -> str | EphemeralReply:
        """Handle /yolo — toggle dangerous command approval bypass for this session only. The flag is
        persisted on the routing entry so it survives a gateway restart."""
        from tools.approval_yolo import toggle_session_yolo
        session_key = self._session_key_for_source(event.source)
        persisted, persist = False, None
        if (store := self.session_store) is not None:
            # A first-message /yolo has no routing entry yet; materialize it so the flag has a home.
            persisted = (await self.async_session_store.get_or_create_session(event.source)).yolo is True
            persist = lambda on: store.set_session_yolo(session_key, on)
        # Off the loop: the persist is a routing-store write (thread-safe, like AsyncSessionStore's calls).
        enable = await asyncio.to_thread(toggle_session_yolo, session_key, persisted=persisted, persist=persist)
        return EphemeralReply('⚡ YOLO mode **ON** for this session — all commands auto-approved. Use with caution.' if enable else '⚠️ YOLO mode **OFF** for this session — dangerous commands will require approval.')

    async def _handle_verbose_command(self, event: MessageEvent) -> str:
        """Handle /verbose — cycle tool progress display mode (off → new → all → verbose → log) per
        *current platform*, saved to ``display.platforms.<platform>.tool_progress``. Gated by
        ``display.tool_progress_command`` (default off)."""
        from gateway.run import _load_gateway_config
        from gateway.session_setting_commands import local_live
        config_path, platform_key = self._display_config_target(event)
        live = local_live(self, event.source)
        try:
            user_config = _load_gateway_config()
            gate_enabled = is_truthy_value(cfg_get(user_config, "display", "tool_progress_command"),
                                           default=False)
        except Exception:
            user_config, gate_enabled = {}, False
        # The opt-in gate is for messaging chats; a local session always owned /verbose.
        if not gate_enabled and live is None:
            return 'The `/verbose` command is not enabled for messaging platforms.\n\nEnable it in `config.yaml`:\n```yaml\ndisplay:\n  tool_progress_command: true\n```'
        # Cycle mode (per-platform), reading the current effective mode via the resolver. ``log``
        # (tool calls to a log file instead of the chat) is a messaging mode: a local client renders
        # every tool event itself.
        from gateway.display_config import resolve_display_setting
        cycle = ["off", "new", "all", "verbose"] + ([] if live is not None else ["log"])
        current = (getattr(live, "tool_progress_mode", None) if live is not None else None) or \
            resolve_display_setting(user_config, platform_key, "tool_progress", "all")
        new_mode = cycle[(cycle.index(current if current in cycle else "all") + 1) % len(cycle)]
        if live is not None:
            live.tool_progress_mode = new_mode  # what ``config.get verbose`` and the next tool event read
        description = {'off': '⚙️ Tool progress: **OFF** — no tool activity shown.', 'new': '⚙️ Tool progress: **NEW** — shown when tool changes (preview length: `display.tool_preview_length`, default 40).', 'all': '⚙️ Tool progress: **ALL** — every tool call shown (preview length: `display.tool_preview_length`, default 40).', 'verbose': '⚙️ Tool progress: **VERBOSE** — every tool call with full arguments.', 'log': '⚙️ Tool progress: **LOG** — silent in chat; tool calls written to ~/.hermes/logs/tool_calls.log.'}[new_mode]
        try:
            _write_raw_config_leaf(config_path, ("display", "platforms", platform_key, "tool_progress"), new_mode)
            return f"{description}\n" + (f'_(saved for **{platform_key}** — takes effect on next message)_')
        except Exception as e:
            logger.warning("Failed to save tool_progress mode: %s", e)
            return f"{description}\n" + (f'_(could not save to config: {e})_')

    async def _handle_busy_command(self, event: MessageEvent) -> str | EphemeralReply:
        """Handle /busy — control what happens when messaging while Hermes is working."""
        from gateway.session_setting_commands import local_live, session_busy_mode
        arg = event.get_command_args().strip().lower()
        live = local_live(self, event.source)
        if not arg or arg == "status":
            mode = session_busy_mode(self, live) if live is not None else self._effective_busy_input_mode(event.source)
            behavior = _BUSY_MODE_BEHAVIOR.get(mode, _BUSY_MODE_BEHAVIOR["interrupt"])[0]
            return EphemeralReply(f'**Busy input mode: `{mode}`\nMessages while busy: _{behavior}_\nChange with `/busy queue`, `/busy steer`, or `/busy interrupt`.')
        if arg not in _BUSY_MODE_BEHAVIOR:
            return EphemeralReply(f'Unknown mode `{arg}`. Use `/busy queue`, `/busy steer`, or `/busy interrupt`.')

        # Persist before mutate
        from cli import save_config_value
        if not save_config_value("display.busy_input_mode", arg):
            return EphemeralReply('Busy input mode could not be saved to config. Mode unchanged.')
        if live is not None:
            live.busy_input_mode = arg  # the frozen session does not re-read config.yaml
        profile_name = self._busy_profile_name_for_source(event.source)
        if profile_name:
            from gateway.run import _load_gateway_config
            self._snapshot_profile_busy_modes(profile_name, _load_gateway_config())
        else:
            self._busy_input_mode = arg
            # busy_input_mode is also the source of truth for the text mode — re-derive it so the
            # adapter refresh below doesn't keep a stale value and keep interrupting.
            self._busy_text_mode = self._load_busy_text_mode()

        adapter = self._delivery_adapter_for(event.source)
        if adapter is not None:
            adapter._busy_text_mode = self._effective_busy_text_mode(event.source)
        return EphemeralReply(f'Busy input mode set to **`{arg}`** (saved).\n_{_BUSY_MODE_BEHAVIOR[arg][1]}_')

    async def _handle_footer_command(self, event: MessageEvent) -> str:
        """Handle /footer command — toggle the runtime-metadata footer."""
        from gateway.run import _load_gateway_config, _resolve_gateway_model
        from gateway.runtime_footer import format_runtime_footer, resolve_footer_config
        config_path, platform_key = self._display_config_target(event)
        arg = event.get_command_args().strip().lower()
        try:
            user_config: dict = _load_gateway_config()
        except Exception as e:
            return (f'⚠️ Could not read config.yaml: {e}')
        effective = resolve_footer_config(user_config, platform_key)

        def _state(enabled: bool) -> str:
            return 'ON' if enabled else 'OFF'
        if arg in {"status", "?"}:
            return ('📎 Runtime footer: **{state}**\nFields: `{fields}`\nPlatform: `{platform}`').format(state=_state(effective["enabled"]), fields=", ".join(effective.get("fields") or []), platform=platform_key)
        if arg and arg not in _FOOTER_STATE_BY_ARG:
            return 'Usage: `/footer [on|off|status]`'
        new_state = _FOOTER_STATE_BY_ARG[arg] if arg else not effective["enabled"]
        try:
            # A local session writes the ``cli`` key, as /reasoning show|hide saves: every local
            # surface reads it (gateway.display_config.local_display_config), so the setting holds
            # wherever it was set (T7). A chat's /footer keeps the global key it always wrote.
            leaf = (("display", "platforms", platform_key) if event.source.platform == Platform.LOCAL
                    else ("display",)) + ("runtime_footer", "enabled")
            _write_raw_config_leaf(config_path, leaf, new_state)
        except Exception as e:
            logger.warning("Failed to save runtime_footer.enabled: %s", e)
            return (f'⚠️ Could not save config: {e}')
        example = ""
        if new_state:
            # Show a preview using current agent state if available.
            preview = format_runtime_footer(
                model=_resolve_gateway_model(user_config) or None, context_tokens=0, context_length=None,
                fields=effective.get("fields") or ["model", "context_pct", "cwd"])
            if preview:
                example = (f'\nExample: `{preview}`')
        return (f'📎 Runtime footer: **{_state(new_state)}**{example}\n_(saved globally — takes effect on next message)_')

    async def _handle_reload_mcp_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /reload-mcp — reconnect MCP servers and rebuild the cached agent. Reloading
        invalidates the provider prompt cache (tool schemas live in the system prompt), so it routes
        through slash-confirm; "Always Approve" persists ``approvals.mcp_reload_confirm: false``."""
        session_key = self._session_key_for_source(event.source)
        # Read the gate fresh from disk so a prior "always" click takes effect on the next
        # invocation without restarting the gateway.
        user_config = self._read_user_config()
        approvals = user_config.get("approvals") if isinstance(user_config, dict) else None
        if isinstance(approvals, dict) and not approvals.get("mcp_reload_confirm", True):
            return await self._execute_mcp_reload(event)
        # Route through slash-confirm. The primitive sends the prompt and stores the resume handler;
        # the button/text response triggers ``_resolve_slash_confirm`` which invokes the handler
        # with the chosen outcome.
        async def _on_confirm(choice: str) -> Optional[str]:
            if choice == "cancel":
                return '🟡 /reload-mcp cancelled. MCP tools unchanged.'
            if choice == "always":
                # Persist the opt-out and run the reload.
                try:
                    from cli import save_config_value
                    save_config_value("approvals.mcp_reload_confirm", False)
                    logger.info("User opted out of /reload-mcp confirmation (session=%s)", session_key)
                except Exception as exc:
                    logger.warning("Failed to persist mcp_reload_confirm=false: %s", exc)
            # once / always → run the reload
            result = await self._execute_mcp_reload(event)
            if choice == "always":
                return f"{result}\n\n" + 'ℹ️ Future `/reload-mcp` calls will run without confirmation. Re-enable via `approvals.mcp_reload_confirm: true` in config.yaml.'
            return result
        return await self._request_slash_confirm(
            event=event, command="reload-mcp", title="/reload-mcp",
            message='⚠️ **Confirm /reload-mcp**\n\nReloading MCP servers rebuilds the tool set for this session and **invalidates the provider prompt cache** — the next message will re-send full input tokens.  On long-context or high-reasoning models this can be expensive.\n\nChoose:\n• **Approve Once** — reload now\n• **Always Approve** — reload now and silence this prompt permanently\n• **Cancel** — leave MCP tools unchanged\n\n_Text fallback: reply `/approve`, `/always`, or `/cancel`._', handler=_on_confirm)

    async def _handle_reload_skills_command(self, event: MessageEvent) -> str:
        """Handle /reload-skills — rescan skills dir, queue a note for next turn. Skills are invoked at
        runtime, not baked into the system prompt, so this does NOT clear the prompt cache. The diff
        goes into ``_pending_skills_reload_notes[session_key]``, prepended to the NEXT user message —
        nothing out-of-band, so alternation is preserved."""
        try:
            from agent.skill_commands import reload_skills

            # _run_in_executor_with_context, not a bare hop: the rescan walks
            # get_hermes_home()/skills, a contextvar override under multiplex.
            result = await self._run_in_executor_with_context(reload_skills)
            added, removed = result.get("added", []), result.get("removed", [])  # [{"name", "description"}]
            total = result.get("total", 0)
            # Let adapters refresh platform-side state that cached the skill list at startup (today:
            # Discord /skill autocomplete — otherwise new skills stay invisible and deleted ones
            # error). Adapters without refresh_skill_group are skipped; the in-process reload suffices.
            for adapter in list(self.adapters.values()):
                refresh = getattr(adapter, "refresh_skill_group", None)
                try:
                    maybe = refresh() if callable(refresh) else None
                    if inspect.isawaitable(maybe):
                        await maybe
                except Exception as exc:
                    logger.warning("Adapter %s refresh_skill_group raised: %s",
                                   getattr(adapter, "name", adapter), exc)

            lines = ['🔄 **Skills Reloaded**\n']
            if not added and not removed:
                lines += ['No new skills detected.', (f'\n📚 {total} skill(s) available')]
                return "\n".join(lines)

            def _fmt_line(item: dict) -> str:
                nm, desc = item.get("name", ""), item.get("description", "")
                return ((f'    - {nm}: {desc}') if desc
                        else (f'    - {nm}'))

            # Queue a one-shot note for the next user turn in this session too. Format matches how
            # the system prompt renders pre-existing skills (``    - name: description``) so the
            # model reads the diff in the same shape as its original skill catalog.
            sections = ["[USER INITIATED SKILLS RELOAD:"]
            for heading, note_header, items in (
                ('➕ **Added Skills:**', "Added Skills:", added),
                ('➖ **Removed Skills:**', "Removed Skills:", removed)):
                if items:
                    formatted = [_fmt_line(item) for item in items]
                    lines += [heading] + formatted
                    sections += ["", note_header] + formatted
            lines.append(f'\n📚 {total} skill(s) available')
            sections += ["", "Use skills_list to see the updated catalog.]"]
            session_key = self._session_key_for_source(event.source)
            if not hasattr(self, "_pending_skills_reload_notes"):
                self._pending_skills_reload_notes = {}
            if session_key:
                self._pending_skills_reload_notes[session_key] = "\n".join(sections)
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Skills reload failed: %s", e)
            return (f'❌ Skills reload failed: {e}')

    async def _handle_bundles_command(self, event: MessageEvent) -> str:
        """Handle /bundles — list installed skill bundles (mirrors the CLI handler). Bundles are
        loaded by invoking their own ``/<slug>`` command, not by this one."""
        reply = _execute("bundles")
        if "error" in reply.data:
            logger.warning("Bundles command unavailable: %s", reply.data["error"])
            return reply.text
        bundles = reply.data["bundles"]
        if not bundles:
            return ('No skill bundles installed.\nCreate one on the host with:\n  `hermes bundles create <name> --skill <s1> --skill <s2>`\nDirectory: `{dir}`').format(dir=reply.data["dir"])
        lines = [(f'**Skill Bundles** ({len(bundles)} installed):'), ""]
        for info in bundles:
            skills = info.get("skills", [])
            desc = info.get("description") or (f'Load {len(skills)} skills')
            lines += [('• `/{slug}` — {desc} _({count} skills)_').format(slug=info["slug"], desc=desc, count=len(skills))] + [f"    · {s}" for s in skills]
        return "\n".join(lines + ["", 'Invoke a bundle with `/<slug>` to load all its skills.'])

    def _blocking_approval_or_stale(self, event: MessageEvent, stale_key: str, none_key: str,
                                    resolve_all: bool = False):
        """``(session_key, None, request_ids)`` when an agent thread is blocked on approval and the
        sender may answer the approvals this command selects, else ``(session_key, reply, [])``.
        A pending-approvals entry with no blocked thread is a stale prompt: drop it and say so."""
        from tools.approval import has_blocking_approval
        session_key = self._session_key_for_source(event.source)
        if has_blocking_approval(session_key):
            from gateway.approval_actor import answerable_request_ids, refusal_text
            from gateway.slash_access import policy_for_runner_source
            ids, requester = answerable_request_ids(
                session_key, event.sender_id(), resolve_all=resolve_all,
                admin_ids=policy_for_runner_source(self, event.source).admin_user_ids)
            return (session_key, refusal_text(requester), []) if requester else (session_key, None, ids)
        if session_key in self._pending_approvals:
            self._pending_approvals.pop(session_key)
            return session_key, stale_key, []
        return session_key, none_key, []

    async def _handle_approve_command(self, event: MessageEvent) -> Optional[str]:
        """Handle /approve — unblock waiting agent thread(s). They block inside tools/approval.py;
        signalling the event resumes them so the command executes inline (same flow as the CLI)."""
        from tools.approval import resolve_gateway_approval
        # Args: "all", "all session", "all always", "session", "always" ("always" beats "session").
        args = event.get_command_args().strip().lower().split()
        session_key, stale, request_ids = self._blocking_approval_or_stale(
            event, '⚠️ Approval expired (agent is no longer waiting). Ask the agent to try again.', 'No pending command to approve.', resolve_all="all" in args)
        if stale:
            return stale
        choices = {_APPROVE_CHOICE_BY_ARG[a] for a in args if a in _APPROVE_CHOICE_BY_ARG}
        choice = "always" if "always" in choices else "session" if "session" in choices else "once"
        # Exactly the approvals the sender was checked against, never "the oldest" again.
        count = sum(resolve_gateway_approval(session_key, choice, request_id=rid) for rid in request_ids)
        if not count:
            return 'No pending command to approve.'
        confirmation_text = {'no_pending': 'No pending command to approve.', 'once_singular': '✅ Command approved. The agent is resuming...', 'once_plural': '✅ Commands approved ({count} commands). The agent is resuming...', 'session_singular': '✅ Command approved (pattern approved for this session). The agent is resuming...', 'session_plural': '✅ Commands approved (pattern approved for this session) ({count} commands). The agent is resuming...', 'always_singular': '✅ Command approved (pattern approved permanently). The agent is resuming...', 'always_plural': '✅ Commands approved (pattern approved permanently) ({count} commands). The agent is resuming...'}[f"{choice}_{'plural' if count > 1 else 'singular'}"].format(count=count)
        logger.info("User approved %d dangerous command(s) via /approve (%s)", count, choice)
        return await self._deliver_approval_confirmation(event, confirmation_text, "approve")

    async def _handle_deny_command(self, event: MessageEvent) -> str:
        """Handle /deny — reject pending dangerous command(s) with a definitive BLOCKED result, as in
        the CLI. ``/deny`` denies the oldest; ``/deny all`` denies everything.

        ``/deny <reason>`` (or ``/deny all <reason>``) attaches a one-line reason that is relayed back to
        the agent so it can adapt instead of only hearing "denied". Ported from qwibitai/nanoclaw#2832.
        """
        from tools.approval import resolve_gateway_approval
        # A leading "all" denies every pending command; the rest (or the whole arg string without
        # "all") is the optional deny reason relayed to the agent, capped to a sane one-liner.
        raw_args = event.get_command_args().strip()
        tokens = raw_args.split()
        resolve_all = bool(tokens) and tokens[0].lower() == "all"
        session_key, stale, request_ids = self._blocking_approval_or_stale(
            event, '❌ Command denied (approval was stale).', 'No pending command to deny.', resolve_all=resolve_all)
        if stale:
            return stale
        reason = (raw_args[len(tokens[0]):].strip() if resolve_all else raw_args)[:280].strip()
        count = sum(resolve_gateway_approval(session_key, "deny", reason=reason or None, request_id=rid)
                    for rid in request_ids)
        if not count:
            return 'No pending command to deny.'
        logger.info("User denied %d dangerous command(s) via /deny%s", count,
                    " (with reason)" if reason else "")
        confirmation_text = (f'❌ Commands denied ({count} commands).' if count > 1 else '❌ Command denied.')
        if reason:
            confirmation_text += f' Reason relayed to the agent: "{reason}"'
        return await self._deliver_approval_confirmation(event, confirmation_text, "deny")

    async def _handle_curator_command(self, event: MessageEvent) -> str:
        """Handle /curator — the ``hermes curator`` handler set (status by default), as main's CLI ran
        it in-process; its printed report is the reply. Runs in the executor with the session's
        context so the profile's skills tree and curator state are the ones read and written."""
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from hermes_cli.curator import cli_main
        tokens = shlex.split(event.get_command_args() or "") or ["status"]

        def _run() -> str:
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(out):
                try:
                    cli_main(tokens)
                except SystemExit:
                    pass  # argparse exits on --help/usage errors; its text is the reply
                except Exception as exc:  # health: allow BLE001 -- main's CLI handler: the failure line is the reply
                    print(f'(._.) curator: {exc}')
            return out.getvalue().rstrip()

        return await self._run_in_executor_with_context(_run)

    async def _handle_debug_command(self, event: MessageEvent) -> str:
        """Handle /debug — upload ONLY the summary (system info + log tails), never full logs, to
        protect privacy; ``hermes debug share`` from the CLI does full uploads."""
        from hermes_cli.debug import (_GATEWAY_PRIVACY_NOTICE, _best_effort_sweep_expired_pastes,
                                      _capture_dump, _is_dpaste_url, _schedule_auto_delete,
                                      collect_debug_report, upload_to_pastebin)
        from hermes_cli.debug_redaction import redact_debug_support_text

        wants_local = "local" in {w.lower() for w in (event.get_command_args() or "").split()}
        if wants_local and getattr(event.source, "platform", None) != Platform.LOCAL:
            # From a chat the report can't stay on the machine; `/debug local` must never fall
            # through to the public paste. Bare `/debug` is the upload form.
            return "`/debug local` keeps the report on the machine running Hermes, so it isn't available from a chat. Send `/debug` to upload the summary report."
        if wants_local:
            # A local session's `/debug local` (main's CLI form) asks for the report on this machine:
            # return the redacted report for the client to show, with no network call at all (not
            # even the expired-paste sweep). Uploading stays on the explicit `/debug` form (T7).
            from hermes_cli.debug import collect_share_bundle

            def _collect_local():
                return collect_share_bundle(log_lines=200)["report"]

            return await self._run_in_executor_with_context(_collect_local)

        def _collect_and_upload():  # blocking I/O (dump capture, log reads, uploads) -> thread
            _best_effort_sweep_expired_pastes()
            report = collect_debug_report(log_lines=200, dump_text=_capture_dump())
            try:
                urls = {'Report': upload_to_pastebin(report)}
            except Exception as exc:
                return (f'✗ Failed to upload debug report: {redact_debug_support_text(exc)}')
            _schedule_auto_delete(list(urls.values()))  # paste.rs only; dpaste.com has no delete
            label_width = max(len(k) for k in urls)
            # The 6-hour line is only true for paste.rs; the privacy notice above already states
            # the dpaste.com fallback retention, so drop the line rather than contradict it.
            auto_delete = [] if any(map(_is_dpaste_url, urls.values())) else [
                '⏱ Pastes will auto-delete in 6 hours.']
            return "\n".join([_GATEWAY_PRIVACY_NOTICE, "", '**Debug report uploaded:**', "",
                              *(f"`{label:<{label_width}}`  {url}" for label, url in urls.items()),
                              "", *auto_delete, 'For full log uploads, use `hermes debug share` from the CLI.',
                              'Share these links with the Hermes team for support.'])

        # _run_in_executor_with_context, not a bare hop: this collects the profile's logs/config off
        # ``get_hermes_home()`` and uploads them to a public paste. Losing the contextvar override
        # would publish the DEFAULT profile's diagnostics from another profile's chat.
        return await self._run_in_executor_with_context(_collect_and_upload)

    async def _handle_update_command(self, event: MessageEvent) -> str:
        """Handle /update — spawn ``hermes update`` detached (``setsid``) so it survives the gateway
        restart it may trigger; marker files let this or the next gateway process notify the user."""
        import json
        from gateway.run import _hermes_home, _resolve_hermes_bin
        from hermes_cli.config import is_managed, format_managed_message
        # Block non-messaging platforms (API server, webhooks, ACP); plugin platforms with
        # allow_update_command=True are also allowed.
        src = event.source
        if src.platform not in self._UPDATE_ALLOWED_PLATFORMS:
            try:
                from gateway.platform_registry import platform_registry
                entry = platform_registry.get(src.platform.value)
                if not entry or not entry.allow_update_command:
                    return '✗ /update is only available from messaging platforms. Run `hermes update` from the terminal.'
            except Exception:
                return '✗ /update is only available from messaging platforms. Run `hermes update` from the terminal.'
        if is_managed():
            return f"✗ {format_managed_message('update Hermes Agent')}"

        project_root = Path(__file__).parent.parent.resolve()

        # Not a git-managed install (docker/nix/desktop-app/source): refuse
        # with the steward's own update mechanism instead of git-pulling a
        # tree `hermes update` does not own.
        try:
            from hermes_cli.config import (
                detect_install_method,
                recommended_update_command_for_method,
            )

            method = detect_install_method(project_root)
            if method not in {"git", "unknown"}:
                return (f'✗ `hermes update` does not apply to this install ({method}).\nUpdate with: {recommended_update_command_for_method(method)}')
        except Exception:
            pass  # config unreadable — fall through to the .git check below

        git_dir = project_root / '.git'

        if not git_dir.exists():
            return '✗ Not a git repository — cannot update.'
        hermes_cmd = _resolve_hermes_bin()
        if not hermes_cmd:
            return '✗ Could not locate the `hermes` command. Hermes is running, but the update command could not find the executable on PATH or via the current Python interpreter. Try running `hermes update` manually in your terminal.'
        pending_path = _hermes_home / ".update_pending.json"
        output_path = _hermes_home / ".update_output.txt"
        exit_code_path = _hermes_home / ".update_exit_code"
        pending = {
            "platform": src.platform.value, "chat_id": src.chat_id, "chat_type": src.chat_type,
            "user_id": src.user_id, "session_key": self._session_key_for_source(src),
            "timestamp": datetime.now().isoformat()}
        # ``profile``: the update watcher (possibly the NEXT gateway process) must answer through the
        # requester's own profile bot, not the default profile's adapter for the same platform.
        pending.update({k: v for k, v in (("thread_id", src.thread_id), ("message_id", event.message_id),
                                          ("profile", getattr(src, "profile", None))) if v})
        _tmp_pending = pending_path.with_suffix(".tmp")
        _tmp_pending.write_text(json.dumps(pending), encoding="utf-8")
        _tmp_pending.replace(pending_path)
        exit_code_path.unlink(missing_ok=True)
        try:
            _spawn_detached_update(hermes_cmd, output_path, exit_code_path)
        except Exception as e:
            pending_path.unlink(missing_ok=True)
            exit_code_path.unlink(missing_ok=True)
            return (f'✗ Failed to start update: {e}')
        self._schedule_update_notification_watch()
        return "☤ Starting Hermes update… I'll stream progress here."
