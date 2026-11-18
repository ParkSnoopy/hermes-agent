# Running updater compatibility

An updater can retain old Python modules after replacing its checkout. Its next
lazy import reads the new files. Current-tree tests alone cannot protect that
boundary.

The supported baseline is v0.23.1. `old_updater_surface.json` retains a
conservative history-plus-tree superset, not an exact baseline audit. Remove
older-only requirements only after checking the release's actual post-swap and
failure paths. The three `hermes_cli.plugin_compat` stubs were removed because
the v0.23.1 source has no caller; historical stats still record the old audit.

The original union also covers updaters from v0.21.6 on that import from the
new tree after the swap (a Ctrl-C after the commit point, a lost completion,
the parent restarting the gateways it paused, a shallow-checkout count). The
names those post-swap paths reach in each shipped release are added here, so a
rename cannot break an update halfway. They come from a call-graph walk of
each release tag from its post-swap entry points, not from the history audit
above.

The historical audit requires a full clone and an explicit pre-PM cutoff.
It is useful as diagnostic evidence, but regenerating the supported freeze from
it would restore retired requirements. Use the existing JSON's
`stats.history.history_ref` (or `stats.history_ref` in history-only output):

```sh
python3 scripts/audit-old-updater-imports.py --ref PRE_PM_COMMIT --json
```

The walker resolves `--ref` once. The JSON records that exact commit, the
history roots, discovered entrypoints, selected paths, and parse recoveries.
It inventories all reachable commits, including merge parents. It follows
historical filenames, deleted helpers, extraction imports, and rename edges.
A shallow clone cannot regenerate the file. Shallow CI resolves every frozen
bare name against the current tree and checks historical completeness; it does
not require fresh-tree imports to be a subset of the freeze. An update-flow
change alone is not grounds for regeneration or for advancing the cutoff.

The surface deliberately over-approximates reachability. Matching update
entrypoints and same-named functions can include unrelated callers. A historical
name alone does not prove a supported release needs it; examine actual release
callers and recovery paths before removing an entry.
The resolver requires module-scope bindings, not names inside functions or
classes. It does not execute module bodies or prove every conditional export.

## Runtime behavior

Retired dependency entrypoints hand off to the fresh absolute-path updater
child, wait, then exit with its status. Returning `None` can activate old pip
fallback code. The old parent must not import PM, reload live modules, install,
or download. Other shims retain conservative return shapes. Current application
code must use the live implementation, not these historical exports.

`old_updater_support.py` supplies the broad export tests' shared child-process
seam: only the isolated takeover command is accepted, and the real temporary
request/result bridge still runs. Each test resets the parent result cache so
one export cannot pass just because another already handed off. The tests retain
historical arguments and return shapes, and forbid old-parent fallbacks.
`tests/hermes_cli/test_old_updater_takeover.py` separately executes a real child
to prove waiting, status propagation and no reentry; these export probes do not
replace that integration coverage.

A fresh source launch checks PM's existing successful dependency facts. It
synchronizes stale state and switches to the managed interpreter before
activating application dependencies. No new incomplete-marker protocol is used.

`old_updater_dependencies.py` retains historical caller functions with their
lazy imports intact. The shim tests execute those callers against the new tree.
`tests/pm/test_source_update_launch.py` exercises real worker publication,
failed-build retention, and fresh-process bootstrap.

## Manual review of dynamic edges

Complete history enumeration is not a complete Python call-graph proof. The
JSON retains `unresolved_dynamic` rather than silently dropping those edges.
The reviewed categories are:

- `hermes_constants` reloads: fixed first-party module, present and audited.
  Reload execution still depends on the running interpreter and process state.
- `managed_scope`, `main_dashboard`, and browser module objects: fixed modules
  passed between helpers. The modules exist. Arbitrary attribute dispatch is
  not a statically proven contract.
- `managed_uv` and `_subprocess_compat`: the historical Windows installer calls
  `_subprocess_compat.run`. `REVIEWED_DYNAMIC_LOADS` retains that named call with
  its witness commit, including in history-only output at that cutoff.
  Its shim stops before invoking an installer.
- PM operations and build operations: fixed dispatch modules recorded by the
  retained union's tree half. New PM imports do not extend the historical
  contract. Diagnostic tree audits still inspect these modules; PM registry
  import names can also come from package definitions outside the checkout.
- Plugin configuration hooks and deferred tool registration: import targets
  depend on enabled plugins and runtime manifests. A core-tree freeze cannot
  enumerate names supplied by independently installed plugins.

These limits remain visible in the generated file. The static gate proves that
its recorded imports still resolve structurally. Real updater and launch tests
prove the exercised runtime paths, not every historical platform combination.
