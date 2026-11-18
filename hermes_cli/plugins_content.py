"""Content registrars on ``PluginContext``: plugin skills.

Bound into the class body (``from hermes_cli.plugins_content import ...``), so they are ordinary
``ctx.register_*`` methods and get the same abandoned-load guard as every other registrar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from hermes_cli.plugins_ledger import PluginRegistration
from hermes_cli.plugins_loader import _serialized_replacement

__all__ = ["register_skill"]


def plugin_skill_owners(manager, qualified: str) -> set[str]:
    """The names ``plugins.enabled``/``plugins.disabled`` may use for the plugin that registered
    *qualified*: its key and manifest name. A portable plugin's skill prefix is a slug of its name
    (``foo.bar`` -> ``foo-bar``), so the prefix alone can name a different plugin."""
    entry = manager._plugin_skills.get(qualified) or {}
    owners = {entry.get("plugin_key"), entry.get("plugin_name")} - {None, ""}
    return owners or {qualified.partition(":")[0]}


@_serialized_replacement
def register_skill(
    self, name: str, path: Path, description: str = "",
    frontmatter: Optional[Mapping[str, Any]] = None,
) -> PluginRegistration:
    """Register a read-only skill resolvable as ``'<plugin_name>:<name>'`` via ``skill_view()``,
    listed by ``skills_list`` and in the system prompt's ``<available_skills>`` while the plugin
    is enabled. Not copied into ``~/.hermes/skills/``. Raises ``ValueError`` (``':'``/invalid
    chars) or ``FileNotFoundError``."""
    from agent.skill_utils import _NAMESPACE_RE
    from hermes_cli.plugins_manifest import _portable_slug
    if ":" in name:
        raise ValueError(f"Skill name '{name}' must not contain ':' (the namespace is derived from the "
                         f"plugin name '{self.manifest.name}' automatically).")
    if not name or not _NAMESPACE_RE.match(name):
        raise ValueError(f"Invalid skill name '{name}'. Must match [a-zA-Z0-9_-]+.")
    # Plugin register() helpers commonly pass the SKILL.md location as str
    # (PluginManifest.path is stored as str); the registry and find_plugin_skill()
    # promise a Path downstream.
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"SKILL.md not found at {path}")
    namespace = _portable_slug(self.manifest.name) if self.manifest.portable else self.manifest.name
    qualified = f"{namespace}:{name}"
    existing = self._manager._plugin_skills.get(qualified)
    # Native-over-native keeps last-wins replacement; a clash involving a portable plugin is refused
    # either way round, so one plugin's instructions never load under another's name.
    if existing and (self.manifest.portable
                     or (existing.get("portable") and existing.get("plugin_key") != self.plugin_id)):
        raise ValueError(f"Plugin skill '{qualified}' is already registered by plugin '{existing.get('plugin_key')}'")
    entry = {
        "path": path, "plugin": namespace, "plugin_key": self.plugin_id, "plugin_name": self.manifest.name,
        "portable": self.manifest.portable, "bare_name": name,
        "description": description, "frontmatter": dict(frontmatter or {}),
    }
    return self._register_entry("skill", qualified, self._manager._plugin_skills, entry,
                                "Plugin %s registered skill: %s", qualified)

