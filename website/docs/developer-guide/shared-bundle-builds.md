# Shared bundle builds

Hermes separates dependency preparation, product builds, and distribution
packaging. The compiler and agent-assembly interfaces live in
[`scripts/build/README.md`](../../../scripts/build/README.md). These are current
interfaces, not proof that every distribution passed native acceptance.

## Providers, products, and distributions

| Layer | Responsibility | Implementation |
|---|---|---|
| Dependency providers | Prepare tools, Python environments, JavaScript dependencies, and native bindings | PM (`pm.build_environment`), `scripts/build/node-deps.mjs`, Nix, and Termux |
| Product builders | Compile icons/TUI or assemble a runnable agent from prepared inputs | `scripts/generate_icons.py` and `scripts/build/` |
| Distribution adapters | Select products and package them for their target | `scripts/bundles/`, `nix/`, and `scripts/termux/` |

Providers retain their package managers and locks. Product builders do not
install missing dependencies or download inputs. The Node dependency provider
uses one locked workspace union. Nix supplies its own Node and Python inputs.
Termux supplies bionic tools and wheels. Shared recipes do not imply identical
bytes, dependency selections, or Python environments across targets.

| Product | Implementation | Inputs |
|---|---|---|
| Icons | `scripts/generate_icons.py` | Artwork and a Python with the runtime dependencies |
| TUI | `scripts/build/tui.mjs` | Prepared TUI workspace |
| Runnable agent | `scripts/build/agent.py` | Code, interpreter, dependencies, independent PM runtime, resources, and selected frontends |

## Source builds and updates

`hermes_cli/source_build.py` selects and prepares the TUI workspace, then invokes its shared compiler.
Current-checkout retries and pulled-checkout updates use `update_cmd_maint._prepare_updated_checkout`: one PM dependency sync followed by frontend compilation in the selected interpreter.
A post-commit build failure remains an owed follow-up; an existing stale product is not a successful build.
PM owns Python dependency selection and the independent manager runtime.

## Native agent preparation and assembly

`hermes pm bundle --out /work/agent-payload --ref HEAD` stages the source snapshot, native tools, application environment, and independent PM runtime.
This command does not compile a frontend.
The shared assembler can consume an explicitly prepared TUI product.

### Split PM Bundle preparation and assembly

PM Bundle uses isolated tool bootstrap and native preparation:

```sh
python -S -B scripts/bundles/native_build.py --source "$PWD" \
  --work "$PWD/.build/payload-job" --cache "$PWD/.cache/payload-inputs" \
  --out "$PWD/build/agent-payload" --ref HEAD --prepare-only
python -S -B scripts/bundles/native_build.py \
  --prepared "$PWD/build/agent-payload.prepared.json"
```

Use a clean checkout at the selected revision. `--ref` defaults to `HEAD`;
`--commit` accepts an exact full SHA instead. Work, cache and output must be
separate; preparation claims a previously absent work directory. Without
`--prepare-only`, the driver also assembles. `--prepared` takes no new selection
or path arguments and validates the native receipt before assembly; it never
bootstraps or repairs dependencies. The receipt and lock are output siblings
(`agent-payload.prepared.json`, `agent-payload.prepare.lock`), not shipped files.
Assembly deliberately supplies no frontend products, as with `hermes pm bundle`.

Native preparation uses build-owned tool and wheel caches.
Restored inputs still undergo provider admission; native compiler prerequisites belong to the host.

For Termux, tools and the wheelhouse are prerequisites:

```sh
python scripts/termux/build.py --repo /work/source --payload /work/termux-payload \
  --out /work/packages --tag vX.Y.Z
```

The Termux driver accepts exactly one of `--tag` or `--commit`. It prepares
the TUI workspace, calls the shared TUI builder, and passes that product to
`build_deb.sh`. It preserves bionic dependency preparation.

The CLI contracts are in `scripts/bundles/native_build.py`,
`pm/cli.py`, and `scripts/termux/build.py`.

## Shared agent and launcher contract

`AgentInputs` supplies explicit paths and a placement mode:

- `contained` keeps runtime inputs inside the native payload.
- `fixed` retains the Termux installation prefix.
- `references` keeps installed code and dependencies in separate Nix store paths.

The assembler derives console commands from `[project.scripts]`. It copies
source-layout resources and selected frontends, or links explicit reference
inputs. It writes the completion manifest after structural assembly. Nix also
receives a command/environment map for its native wrappers.

The launcher implementation lives in `scripts/build/launchers.py`.
`scripts/bundles/payload.py` owns git snapshots, PM tool facts, PM runtime
sealing, and portable link handling. It no longer owns frontend placement or
launcher generation.

Linux launchers use
explicit interpreter, source, and dependency paths. Structural assembly does
not replace target-native launch tests. A manifest alone does not prove that
a relocated or signed artifact runs.

## Independent PM runtime

PM dependencies come from `pm/pyproject.toml` and `pm/uv.lock`, not the
application dependency graph. The application environment and `pm-runtime`
are separate inputs to agent assembly.

Native preparation uses `pm.runtime_stage.stage_runtime`. Termux
uses that function with its offline wheelhouse. Nix uses the independent
`nix/pm-runtime.nix` derivation. Native sealing records the payload interpreter
and PM-only site directory in `pm-runtime.json`.

Resident PM workers run the declared interpreter with `-I -S -B`. They add
only the declared PM dependency directory before the worker script. They do
not borrow application dependencies or add PM packages to the caller's
interpreter. Nix stamps point to their declared PM runtime.
These contracts live in `pm/runtime.py:55–88,171–182` and
`scripts/bundles/payload.py:58–90`.

Ordinary native staging does not scan user plugin trees. It runs provisioning
in a child with temporary home and PM roots plus an explicit persistent build
cache. Live PM retains plugin
admission, generation selection, and transaction state. Both paths use
`pm.environment.PythonEnvironment` for explicit uv environment construction.

## Distribution boundaries and output paths

| Distribution | Selected products | Current output layout |
|---|---|---|
| Nix TUI | TUI | `$out/lib/hermes-tui/{dist/entry.js,package.json}` |
| Nix agent | Agent with TUI references | `$out/bin`, `$out/share/hermes-agent`, `$out/ui-tui`, `manifest.json`, and `command-map.json` |
| Termux | TUI and agent | `OUT/hermes-agent_<version>_aarch64.deb`, installed at `$PREFIX/lib/hermes-agent/` |

Native payloads contain `hermes-agent`, `tools`, `venv`, `pm-runtime`, `bin`,
`uv-cache`, and their manifests/facts. Copied frontend assets live at
`hermes-agent/hermes_cli/tui_dist/`.
The TUI asset directory contains `entry.js` and module-mode package metadata.


Nix retains `fetchPnpmDeps`, uv2nix, platform overrides, and store references.
Its builders run during derivation builds, not evaluation. Per-product source
filters keep frontend, Python, and resource inputs separate. Nix wrappers
consume the assembler's command map and retain their PATH and extra-Python
collision policies.

**Nix wheel policy:** `nix/python.nix:128–135` sets `HERMES_NIX_BUILD=1` only
for the Hermes derivation. `setup.py:34–72` rejects general Hermes wheel/sdist
builds. The shared assembler uses the installed Nix code without another copy.
Other providers use source-layout code and metadata, not a public Hermes wheel.

Termux retains bionic wheel compilation, offline installation, native library
paths, and its fixed prefix. Its installed root contains `app`, `tools`,
`runtime-libs`, `venv`, `pm-runtime`, `bin`, and manifests/facts. APT maintainer
hooks manage the declared CLI symlinks under `$PREFIX/bin` and refuse foreign
conflicts. They do not compile dependencies during installation.

`stage_apt_repo.py` owns repository metadata and signatures. Stable and canary
suites are `hermes-stable` and `hermes-canary`. Package files publish before
signed metadata. [Stable release admission](stable-releases.md) coordinates
acceptance and publication across distributions.

## Public artifact handoffs

Artifact handoffs verify selected files before installation or publication.
Source installation and update acceptance is described in the [Linux source driver guide](../../../tests/install/README.md).

`scripts.releases.handoff fetch --public-base URL` downloads a staged artifact
without R2 credentials. Supply either `--tag TAG --commit SHA` or
`--commit-build SHA`, plus the producer `--name`, destination `--root`, and
optional `--include` selectors. This uses the same receipt identity, path,
size and SHA-256 checks as authenticated handoffs. Tagged and commit-only
archives remain separate; the command does not resolve a mutable latest feed.

Public reads require HTTPS, except for loopback fixture servers. They reject
URL credentials, unsafe paths and redirects. An incomplete or corrupt download
cannot replace an existing verified destination. Receipt files are saved only
after all selected artifacts verify. Selections reject missing or ambiguous matches before installation.

## Cache ownership

PM binary download archives and the native uv wheel cache have different
consumers. They are not interchangeable cleanup targets.

- PM retains completed downloads until package verification and publication.
  Successful installs remove their exact fetch archives. Failures retain
  downloads for retry. Cleanup leaves unrelated partials alone.
- Native staging prunes obsolete entries only in its build-owned tool store.
  It does not prune the user's machine-wide store.
- Native staging retains `uv-cache/` for offline mutable-environment rebuilds.
  It copies extracted wheels, built wheel ZIPs and index/revision metadata, but
  excludes cached sdist `src/` trees (including Rust `target/` outputs) before
  copying. On Linux, uv's wheel entries stay relative symlinks into
  `archive-v0/`, so each unpacked wheel ships once. The provider cache is unchanged; native
  signing reaches the extracted binaries without scanning build-only artifacts.
- PM-runtime and application builds share the provider's persistent uv cache.
  CI restores/saves the provider cache, not the temporary build HOME. Failed builds
  retain completed wheels.
  Plain `uv cache prune` removes dangling entries without discarding offline
  wheel inputs. It does not remove all historical versions or enforce a size cap.
- Frontend `node_modules` is a provider input, not a frontend product.


The native cache behavior is in `scripts/bundles/native.py`.
Removing all caches from a payload can break offline environment reconstruction.

Cache keys are lookup hints, not authorization.
Restored provider inputs must pass admission before use.

## Pinned binary inputs

`python -m scripts.ci.archive_inputs` preserves every HTTPS artifact in
`pm/lock.json`, across all targets, plus the Termux runtime-library and license
pins. `pm/artifact-mirror.json` owns the public mirror location. Object keys
are `upstream/sha256/HASH`, independent of filenames and release tags.

CI requests R2 first. Only 404 permits an upstream download. It verifies the
existing SHA256 before an immutable `If-None-Match: *` upload, then downloads
and verifies the stored object. Corrupt bytes, denied access, and failed
uploads stop the build. Concurrent writers may reuse identical bytes but
cannot replace an existing object.

Archive publication uses scoped credentials; installed clients need only public reads and the pinned hashes.

Installed PM clients, bootstrap installers, and Nix pin consumers use the
primary URL followed by the public mirror if the download is unavailable.
The pinned hash remains binding; no credentials or uploads are needed by
clients. PM keeps per-source resume state and reports attempted URLs.
For Termux files already removed upstream, the CI publisher can recover the
exact bytes from the community Internet Archive after an upstream 404/410.
It never repins to the latest package.

The archive must have no expiration lifecycle rule. Release pruning does
not cover its prefix. This covers PM binary pins and Termux runtime inputs,
not unpinned apt packages, OCI images, language-package registries, or native
SDK archives independently downloaded by build tools.

## Verification boundary

The checks below distinguish product tests from distribution acceptance.
Native payload and Android device execution require their corresponding runtime environments.

Focused helper tests cannot establish all of these requirements:

- Offline frontend compilation from prepared immutable inputs.
- Standalone TUI interaction.
- PM isolation after native payload relocation.
- Retained-cache offline reconstruction of a mutable application environment.

- Actual Nix package execution through reference-based wrappers.
- Fresh network-disabled Termux package installation and Android device behavior.

Use the existing acceptance checks in `tests/install/README.md`,
Nix checks, and `scripts/termux/check_deb.sh` plus
`validate_installed.py`. No build-pass claim follows from this document.
