"""Shared setup for e2e tests."""

from hermes_cli.version_info import _reset_version_info_cache

# E2E subprocesses resolve the checkout's real identity, not the root fixture's seed.
_reset_version_info_cache()