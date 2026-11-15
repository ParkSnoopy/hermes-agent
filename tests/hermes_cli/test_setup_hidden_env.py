"""Setup surfaces ask only for what a platform can't start without.

The knobs in SETUP_HIDDEN_ENV_SUFFIXES are self-configuring (/sethome) or
already correct by default, so they don't belong on a setup form. They must
still be reachable everywhere else — this is a presentation change, not a
feature removal.
"""

from hermes_cli.setup_hidden_env import is_setup_hidden_env


class TestIsSetupHiddenEnv:


    def test_applies_to_plugin_platforms_nobody_enumerated(self):
        """Suffix matching is the point — IRC/SimpleX/ntfy get this for free."""
        for key in (
            "IRC_ALLOW_ALL_USERS",
            "SIMPLEX_HOME_CHANNEL",
            "NTFY_HOME_CHANNEL_NAME",
            "LINE_ALLOW_ALL_USERS",
        ):
            assert is_setup_hidden_env(key), key
