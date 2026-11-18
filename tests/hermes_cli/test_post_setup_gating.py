"""Tests for the post_setup install-state gate in `_toolset_needs_configuration_prompt`.

Regression coverage for the cua-driver silent-no-op bug (issue #22737).

When a no-key provider's only install side-effect is a `post_setup` hook
(cua-driver, etc.), the gate function used to fall through to the
`_toolset_has_keys` catch-all, which returned True for any provider with
empty `env_vars` — causing `hermes tools` to write the toolset to config
and exit `✓ Saved` without ever invoking the post_setup install. These
tests pin the new predicate-aware behaviour so the regression doesn't
sneak back in.
"""

from __future__ import annotations



class TestBrowserBackendPrompt:
    """`_toolset_needs_configuration_prompt` for the browser toolset keys on `browser.cloud_provider`
    (written by every Browser Automation row), so a configured browser never re-prompts."""


    def test_browser_cloud_provider_set_skips_provider_picker(self, monkeypatch, tmp_path):
        from hermes_cli import tools_config

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        config = {"browser": {"cloud_provider": "local"}}
        assert tools_config._toolset_needs_configuration_prompt("browser", config) is False


    def test_browser_empty_still_prompts(self, monkeypatch, tmp_path):
        from hermes_cli import tools_config

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        config = {"browser": None}
        assert tools_config._toolset_needs_configuration_prompt("browser", config) is True


class TestBrowserBackendPromptThroughLoader:
    """The browser gate must hold through the real config loader.

    `load_config()` merges ``DEFAULT_CONFIG``; a selection key the defaults filled in would suppress the
    picker everywhere. These pin the behaviour against the merged dict a real ``hermes tools`` run feeds
    the gate.
    """

    def _home(self, tmp_path, body: str):
        (tmp_path / "config.yaml").write_text(body, encoding="utf-8")
        return tmp_path

    def test_unset_browser_still_prompts(self, monkeypatch, tmp_path):
        from hermes_cli import tools_config
        from hermes_cli.config import load_config

        monkeypatch.setenv("HERMES_HOME", str(self._home(tmp_path, "cli: {}\n")))
        config = load_config()
        assert tools_config._toolset_needs_configuration_prompt("browser", config) is True

    def test_explicit_cloud_provider_skips_prompt(self, monkeypatch, tmp_path):
        from hermes_cli import tools_config
        from hermes_cli.config import load_config

        monkeypatch.setenv("HERMES_HOME", str(self._home(tmp_path, "browser:\n  cloud_provider: browser-use\n")))
        config = load_config()
        assert tools_config._toolset_needs_configuration_prompt("browser", config) is False
