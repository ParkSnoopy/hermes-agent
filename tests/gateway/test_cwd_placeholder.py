"""Unit tests for gateway.cwd_placeholder.resolve_placeholder_terminal_cwd."""

from gateway.cwd_placeholder import resolve_placeholder_terminal_cwd


class TestResolvePlaceholderTerminalCwd:
    def test_local_placeholder_uses_messaging_cwd(self):
        assert resolve_placeholder_terminal_cwd(
            configured_cwd=".",
            messaging_cwd="/home/user/project",
            home_fallback="/home/user",
        ) == "/home/user/project"

    def test_local_placeholder_defaults_to_home(self):
        assert resolve_placeholder_terminal_cwd(
            configured_cwd=".",
            messaging_cwd=None,
            home_fallback="/home/user",
        ) == "/home/user"


