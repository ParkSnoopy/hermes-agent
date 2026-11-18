"""The classic CLI's `/toolsets` and `/platforms` reports as plain lines.

Shared by the classic CLI (`CLIInfoMixin.show_toolsets` / `_show_gateway_status`) and the shared
gateway, which runs the same report for a local session's `slash.exec` (gateway/session_info_reads.py):
one text on every surface, read where the state lives (the session's toolsets, the gateway's
platform config).
"""
from __future__ import annotations



def _box(title: str, width: int) -> list[str]:
    pad = width - len(title)
    return ["+" + "-" * width + "+", "|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|",
            "+" + "-" * width + "+"]


def toolsets_lines(enabled) -> list[str]:
    """``/toolsets``: every toolset, ``(*)`` on the ones *enabled* names."""
    from toolsets import get_all_toolsets, get_toolset_info
    out = ["", *_box('(^_^)b Available Toolsets', 58), ""]
    for name in sorted(get_all_toolsets()):
        info = get_toolset_info(name)
        if info:
            marker = "(*)" if enabled and name in enabled else "   "
            out.append(f"  {marker} {name:<18} [{info['tool_count']:>2} tools] - {info['description']}")
    return [*out, "", f"  {'(*) = currently enabled'}", "",
            ('  ' + format(("Tip: Use 'all' or '*' to enable all toolsets"), '')), ('  ' + format(('Example: python cli.py --toolsets web,terminal'), '')), ""]


def platforms_lines() -> list[str]:
    """``/platforms``: the messaging platforms the profile's gateway config enables."""
    from hermes_constants import display_hermes_home
    from gateway.config import load_gateway_config, Platform
    out = ["", *_box("Gateway Status", 60), ""]
    config_file = f"{display_hermes_home()}/config.yaml"
    try:
        config = load_gateway_config()
        out += [f"  {'Platform Configuration'}", "  " + "-" * 55]
        platform_status = {
            Platform.TELEGRAM: ("Telegram", "TELEGRAM_BOT_TOKEN"),
            Platform.DISCORD: ("Discord", "DISCORD_BOT_TOKEN"),
            Platform.SLACK: ("Slack", "SLACK_BOT_TOKEN"),
            Platform.WHATSAPP: ("WhatsApp", "WHATSAPP_ENABLED")}
        for platform, (name, env_var) in platform_status.items():
            pconfig = config.platforms.get(platform)
            if pconfig and pconfig.enabled:
                home = config.get_home_channel(platform)
                home_str = f" → {home.name}" if home else ""
                out.append(f"    ✓ {name:<12} {'enabled'}{home_str}")
            else:
                out.append(f"    ○ {name:<12} {(f'not configured ({env_var})')}")
        return [*out, "", f"  {'Conversations persist across restarts.'}", "",
                f"  {'To start the gateway:'}", "    python cli.py --gateway", "",
                f"  {(f'Config file: {config_file}')}", ""]
    except Exception as e:  # health: allow BLE001 -- moved unchanged from CLIInfoMixin._show_gateway_status (main): any config load failure prints the setup steps
        return [*out, f"  {(f'Could not load gateway config: {e!s}')}", "",
                f"  {'To configure the gateway:'}", f"    {'Set environment variables:'}",
                "       TELEGRAM_BOT_TOKEN=your_token", "       DISCORD_BOT_TOKEN=your_token",
                f"    {(f'Edit {config_file}')}", ""]
