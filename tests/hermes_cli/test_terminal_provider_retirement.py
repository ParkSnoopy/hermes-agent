"""The plugin surface retains provider registration but not terminal backends."""

from agent.image_gen_provider import ImageGenProvider
from agent import image_gen_registry
from hermes_cli.plugin_isolation import HOST_OBJECT_BASES
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


class _Painter(ImageGenProvider):
    @property
    def name(self):
        return "retirement_probe_painter"

    def generate(self, prompt, aspect_ratio="landscape", **kwargs):
        return {"image": prompt}


def test_retained_provider_registration_is_scoped_and_unloaded(tmp_path):
    manager = PluginManager(scope_key=str(tmp_path / "profile"))
    context = PluginContext(PluginManifest(name="painter", key="painter"), manager)
    painter = _Painter()
    assert context.register_image_gen_provider(painter) is not None
    try:
        assert image_gen_registry.get_provider(painter.name, scope=manager.scope_key) is painter
        assert image_gen_registry.get_provider(painter.name, scope=str(tmp_path / "other")) is None
        assert HOST_OBJECT_BASES["register_image_gen_provider"] == "agent.image_gen_provider:ImageGenProvider"
    finally:
        manager.unload("painter")
    assert image_gen_registry.get_provider(painter.name, scope=manager.scope_key) is None


def test_terminal_provider_registration_is_not_published(tmp_path):
    manager = PluginManager(scope_key=str(tmp_path / "profile"))
    context = PluginContext(PluginManifest(name="probe", key="probe"), manager)
    assert not hasattr(context, "register_terminal_environment_provider")
    assert "register_terminal_environment_provider" not in HOST_OBJECT_BASES
