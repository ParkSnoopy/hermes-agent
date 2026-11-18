# Linux source install and update E2E

The source driver exercises an old release's installer and updates its checkout
to a selected target through either `hermes update` or the target installer's
script. It uses a disposable runner and private `HOME`, runtime, and temporary
directories. Do not run it against an existing installation.

The driver serves commits from a local bare clone by redirecting canonical Git
URLs with its own Git configuration. The configured origin stays official;
only transport is redirected. Toolchain and dependency downloads may still use
the network. `--install-ref` chooses the baseline and `--update-ref` chooses
the target (default HEAD). `NEXT` creates a synthetic child of the baseline so
HEAD's own updater can be exercised. Stable-to-stable means both refs are tags.

Each leg verifies the installed commit and selects the published launcher from
that installation, falling back to its legacy venv only when PM is absent. Before
`--version`, the passive observer verifies the launcher's selected Python ABI,
refuses incomplete-update markers, checks PM dependency currency, and verifies
the TUI compiler receipt where those contracts exist. Historical installations
without PM have no compiler-receipt freshness claim. Verification does not
repair or rebuild the installation.

The leg also checks that the user-visible launcher still works, the redirect
remains transport-only, and user state and plugin trees survive the update.
The user-state fixture is created through the CLI with a controlled provider;
plugin preservation uses the disposable fixtures and snapshots in
`e2e-assets/preserve-plugins.sh`. The verifier rejects deletion or modification
of user-owned durable state, while permitting documented additive migrations.

`tests/scripts/test_source_driver.py` covers launcher selection and passive PM
observations; `tests/installation/test_installer_common.py` covers the source
installer helper. These fixtures do not establish a real install/update pass.
The CI workflow owns its matrix, scheduling, and artifact collection. Inspect
the current workflow before selecting a route or dispatching a run.