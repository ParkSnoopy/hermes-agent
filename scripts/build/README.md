# Shared product builders

These modules compile frontends and assemble a runnable agent from prepared
inputs. They do not replace pnpm, uv, PM, or Nix.
[Shared bundle builds](../../website/docs/developer-guide/shared-bundle-builds.md) describes the
providers and distribution adapters.

This reference describes the current interfaces, not completed artifact
acceptance. A successful parser or helper test does not prove a distribution
build or a target-native runtime.

## Ownership

| Layer | Owns | Examples |
|---|---|---|
| Dependency provider | Tools, locked dependencies, native libraries and build environments | `node-deps.mjs`, PM Python operations, Nix `fetchPnpmDeps`/uv2nix, Termux wheelhouse |
| Product builder | Compilation or application assembly from prepared inputs | `tui.mjs`, `agent.py`, `../generate_icons.py` |
| Distribution adapter | Product selection, target preparation, package layout, signing and publication | `../bundles/`, `../../Dockerfile`, `../../nix/`, `../termux/` |

`node-deps.mjs` and `pm.build_environment` prepare dependencies. Unlike the product
builders, they can access package registries. There is no universal installer,
all-products dispatcher, or cross-platform Python environment.

## Prepared JavaScript workspace

Run commands from the repository root. Replace the absolute example paths with
build-owned paths.

```sh
node scripts/build/node-deps.mjs --source /work/source \
  --workspace ui-tui
```

`--source` and at least one `--workspace` are required. A workspace is its
importer path in `pnpm-lock.yaml` (`ui-tui`); an unknown path
fails. The provider deduplicates the selection and runs one root
`pnpm install --frozen-lockfile --trust-lockfile --prod=false` filtered to the
selected workspaces and their workspace dependencies. The root project is always
included. It includes development and optional dependencies. pnpm enforces the
root `engines` pin; the provider enforces the exact `pm/lock.json` pnpm version
(`pmOnFail: ignore` keeps any pnpm pin out of the lockfile). pnpm runs dependency
build scripts only for the packages `allowBuilds` in `pnpm-workspace.yaml` names
(`strictDepBuilds` fails the install on any other). The lockfile is trusted
here: the `minimumReleaseAge` policy is verified in CI, where it is written.
Every install starts from empty trees, which also clears a `node_modules` tree that
npm installed. The previous trees are moved to `.cache/node-deps-previous` and deleted
only once the install succeeds; a failed install puts them
back, and the run after a killed one recovers them. Both generations are on disk while
pnpm runs. A tree that cannot be moved (a mount point, or a `.cache` on another
filesystem) is deleted in place as before and cannot be restored. Runs in one checkout
are serialised by `.cache/node-deps.lock` (`node-deps-lock.mjs`, with its heartbeat,
watcher and relay programs beside it). Its owner refreshes the lock's mtime every
second (a failed refresh is retried on the next beat), and a lock that nobody has
refreshed for 30 s is broken, whatever pid, host or PID namespace it names; the run that
breaks it replaces it in one rename and waits 2 s before touching the trees. An owner
stopped for longer (Ctrl-Z) loses the lock. pnpm runs under a
watcher outside the job (a session of its own) that re-reads
the lock every 250 ms and kills the child's process tree once the lock is no longer its
run's, so a stopped pnpm is killed while still stopped and never resumes into the new
owner's trees; pnpm itself stays in the job, so Ctrl-C and Ctrl-Z reach it as before, and
on POSIX a SIGTERM aimed at node-deps alone is passed on to it. Between those children, the run
re-reads the lock before every move, restore, delete and receipt write and exits without
touching the trees once it is no longer its own. What is left: a run stopped (or a
container frozen, watcher included) between one of those checks and the step after it can,
once resumed, do that one step in the new owner's trees; a freeze that also stops the
watcher lets the resumed pnpm write for up to one poll (250 ms) before it is killed; and a
process pnpm started that left its tree (daemonized) is not killed; nor is pnpm when
node-deps, its relay and the watcher are all SIGKILLed at once (`pkill -9 node`), and it
keeps writing into whatever tree the next owner certifies. `hermes update`, or
deleting `node_modules`, repairs a tree left that way. A live owner is waited for at most
10 minutes; then the run fails. Downloads retry with pnpm's defaults, except for a source
launch (`hermes --tui`), which retries once unless the user set a count
where pnpm 12 reads one (`pnpm_config_fetch_retries`, or `fetchRetries` in pnpm's
`config.yaml` or `pnpm-workspace.yaml`; pnpm 12 ignores `fetch-retries` in an npmrc and
`npm_config_fetch_retries`), so an unreachable registry fails it in about 10 s instead of 70 s.
`--pnpm ARGS...` instead runs the same resolved pnpm with `ARGS`.

The prepared source needs these inputs:

- Root `package.json`, `pnpm-lock.yaml`, and `pnpm-workspace.yaml`.
- Selected workspace manifests and their `workspace:` dependency sources.
- Source files and configuration for the selected product.
- Resolved dependencies: each workspace's `node_modules` links into the root
  `node_modules/.pnpm` virtual store; root `node_modules` holds only root
  dependencies.

Request the complete workspace union once. A later, narrower install can
remove dependencies that another product needs. This provider modifies the
prepared workspace. It does not build a frontend.
Nix supplies dependencies through `fetchPnpmDeps` (its fixed-output hash is `nix/pnpm-deps-hash`) instead of this command.

`--reuse` opts into reusing a completed dependency install. The receipt lives inside that tree
and matches the lockfile, `pnpm-workspace.yaml`, local package manifests,
project `.npmrc`, `pnpm_config_*`/`npm_config_*` settings, Node/pnpm versions,
OS/architecture, and the install arguments (the exact workspace union). It also
checks pnpm's installed-tree lock (`node_modules/.pnpm/lock.yaml`) and the
manifest of every package in the virtual store.
A missing or mismatched receipt runs `pnpm install` again. A failed install leaves
the previous trees and their receipt, which is reused only for the inputs it names;
the receipt is written only when pnpm's installed-tree lock holds this lockfile's
entries. Omit `--reuse` to always run the install.
Source launchers add `--no-install` when PM's lazy-install policy is disabled.
That mode still reuses a matching completed receipt, but rejects stale or missing
dependencies before mutating the tree. Explicit build/update operations may install.
The receipt does not validate arbitrary edits inside installed packages and
never skips product compilation. CI saves the prepared tree before packaging
can mutate it, and before unrelated build/signing failures can discard it.

## TUI product

```sh
node scripts/build/tui.mjs --source /work/source --out /work/products/tui
node scripts/build/freshness.mjs --source /work/source --product tui --out /work/products/tui/dist
```

The TUI builder requires `--source` and `--out`.  
Its output contains `dist/entry.js`, gateway/clipboard Python helpers in `dist/scripts/`, and module-mode `package.json` metadata.  
The exported function is `buildTui`.  
Source development can use `pnpm --dir ui-tui run build` with existing workspace dependencies.  

The compiler resolves dependencies from the supplied workspace and does not install tools or dependencies.  
It writes `hermes-build.json` inside `dist/` with the source/build input identity and output inventory.  
`freshness.mjs` validates this receipt without provisioning dependencies and returns a JSON boolean.  
Missing receipts, changed inputs, missing dependencies, and damaged outputs are stale.  
Dependency-provider receipts and product receipts have separate owners.  

Compilation uses a private output directory next to the destination.  
A successful build replaces the destination.  
A failed build preserves the previous output and reports failure.  
The previous output's presence does not prove that the latest build succeeded.  
Arbitrary existing outputs require the `.hermes-product` marker.  
Files, symlinks, and protected source/input paths are rejected.  

### Icons

The artwork generator remains available for website and other retained asset consumers:  

```sh
python scripts/generate_icons.py --source /work/source --out /work/products/icons
python scripts/generate_icons.py --source /work/source --out /work/products/icons --check
```

Use a runtime Python with the generator's dependencies.  
The generator reads artwork from `SOURCE/assets` and writes declared paths beneath `OUT`.  
`--check` regenerates targets in memory and validates image properties, not byte-for-byte equality.  

## Python dependency provider

```sh
python -m pm.build_env --source /work/source \
  --python /work/tools/python --out /work/venv --sealed \
  --extra all
```

| Argument | Contract |
|---|---|
| `--source`, `--out` | Required prepared source and fresh environment destination |
| `--python` | Optional build interpreter; otherwise PM selects its pinned Python |
| `--cache` | Optional build cache directory; otherwise PM selects its cache |
| `--group` | Repeatable build/test dependency-group selection |
| `--sealed` | Prune build-time editable and virtualenv marker `.pth` files |
| `--extra` | Repeatable extra selection |
| `--all-extras` | Select all extras instead of `--extra` |
| `--no-install-project` | Exclude the root application install, but retain workspace-member installation |
| `--offline` | Prohibit uv network access. Required artifacts must already be available |

`OUT` must not exist. Failure removes this invocation's environment, not a
pre-existing environment. Success prints its Python executable. The CLI is an
explicit build request. The Python
function `pm.build_environment` accepts the same semantic inputs and an optional
explicit build `env` mapping. It returns the same executable as a `Path`.

PM owns pinned installer acquisition, environment creation, frozen workspace
sync, dependency checks, and failure cleanup. Callers never resolve or pass a uv
executable. It preserves project policy, uses the supplied interpreter, and
disables interpreter downloads. It does not discover user plugins or publish a
live PM selection. Nix retains its declarative dependency provider. Termux builds
native wheels separately, then uses PM's requirements-environment operation with
an explicit bionic interpreter and an offline wheelhouse. The build cache remains
available after this call.

Other build adapters use the same command with `--requirements FILE` (or repeated
`--requirement SPEC`) for a caller-owned dependency list, `--manager-runtime` for
the independent PM graph, `--check-lock` for non-mutating CI lock validation, and
`--export-requirements FILE` for marker-preserving frozen export. Cache teardown
uses `python -m pm.build_env --prune-cache --cache PATH`; add `--ci` only when the
cache will not be packaged for offline installation.

Native bundle staging keeps its HOME and PM state temporary, but not its uv
cache. `scripts.bundles.stage --cache PATH` (or `hermes pm bundle --cache PATH`)
selects the persistent cache explicitly. Direct staging also accepts the
provider's `UV_CACHE_DIR`; otherwise it uses the output parent's `.uv-cache`.
The PM runtime and application dependency builds receive this same cache.
General PM staging uses `setup-pm`, which restores and saves it automatically on
success. The packaged `uv-cache/` is a copy, not the writable build cache.

## Runnable agent assembly

```sh
python -m scripts.build.agent --inputs /work/agent-inputs.json --out /work/agent
```

The only builder arguments are `--inputs` and `--out`. Python entrypoints also
accept argparse's `-h`/`--help`. The library interface is
`assemble(AgentInputs(...), out)`. The input JSON rejects unknown fields.

### Input fields

All supplied filesystem input paths must be absolute and exist. `repo` and
`bin_dir` are output-relative names, not filesystem inputs.

| Field | Required | Meaning |
|---|---|---|
| `project` | Yes | `pyproject.toml` with static project metadata and `[project.scripts]` |
| `code` | Yes | Prepared source tree, or installed code root for reference placement |
| `repo` | Yes | Code/resource location beneath `OUT`, such as `hermes-agent`, `.`, or `share/hermes-agent` |
| `placement` | Yes | `contained`, `fixed`, or `references` |
| `target` | Yes | Linux targets: `linux-x64`, `linux-arm64`, their `-musl` variants, or `linux-arm64-bionic` |
| `python` | Yes | Prepared target interpreter file |
| `site_packages` | Yes | Prepared application dependency directory |
| `environment` | Yes | Prepared application environment root |
| `pm_runtime` | Yes | Independent PM runtime directory with `pm-runtime.json` |
| `bin_dir` | No | One output-relative directory name. Default: `bin` |
| `tools` | No | Prepared runtime tool directory. The manifest defaults to `tools` if omitted |
| `command_dir` | For `references` | Directory of prepared commands declared by the project |
| `resources` | No | Resource-name to directory mapping |
| `frontends` | No | `tui` product path |
| `ref` | No | Source identity copied to the completion manifest |
| `stamp` | No | Prepared install stamp copied to `OUT/repo/install-stamp.json` |
| `features` | No | Prepared feature inventory copied to `OUT/enabled-features.json` |
| `env` | No | Additional environment values in the reference-placement command map |

Recognized resource names are `skills`, `optional-skills`, `plugins`,
and `optional-mcps`. Distribution adapters supply the required resources.
The generic assembler does not infer a missing resource mapping. TUI inputs
require `dist/entry.js` and `package.json`.

The PM marker supplies `python` and `sitePackages` paths relative to its runtime
directory, or absolute store references. These paths must resolve to real
inputs. If a supplied stamp declares `nix` or `docker`, its `pmRuntime` must
match the supplied PM runtime. The assembler does not create that environment.

### Placement and output

- **`contained`:** The provider prepares the interpreter, dependencies, PM
  runtime, and tools inside `OUT`. Assembly copies source/resources, plants
  frontends, and calls the portable link helper.
- **`fixed`:** The provider owns final-prefix preparation. Assembly copies or
  reuses source/resources and generates launchers without portable relocation.
  Docker and Termux use this placement.
- **`references`:** Assembly references installed code and commands without
  copying Python code or replacing wheel metadata. It links explicit resources
  and frontends, then emits `command-map.json`. Nix creates its native wrappers
  from this map.

Source-layout placement writes project distribution metadata without building
a Hermes wheel. It also writes `site_packages/hermes-agent.pth` with a relative
code path. Source-layout placement therefore requires an output-owned dependency
directory, including in `fixed` mode. Reference placement does not write this file.

Commands derive from `[project.scripts]`, not a second command
list. Linux launchers use the supplied runtime paths.
A target label alone does not prove ABI compatibility.

For copied frontends, the assembler places TUI files at `OUT/repo/hermes_cli/tui_dist/`.
Reference placement links TUI at `OUT/ui-tui` and records its environment binding.

After structural assembly, `manifest.json` records `schema`, `target`, `repo`,
`venv`, `store`, `launchers`, and `runtime`, plus `ref` when supplied. Its
`runtime` contains `repoDir`, `toolsDir`, `storePython`, `sitePackages`, and
`commands`. Reference placement also emits command sources, destinations,
entrypoints, and environment values in `command-map.json`.

Agent assembly modifies its output in place. It removes old completion records
before work and writes the manifest last. This is not the frontend builder's
atomic-directory publication contract. A failed assembly can leave partial
files. The manifest is not evidence of a target-native launch or signed-package
acceptance.

## Implementation references

These locations define the interfaces described here:

| Contract | Source |
|---|---|
| Frontend arguments and publication | `frontend-common.mjs:31–79` |
| TUI product and development output | `tui.mjs:25–103` |
| Locked workspace union | `node-deps.mjs` (`dependencyInputs`, `prepareNodeDependencies`) |
| Checkout lock and child supervision | `node-deps-lock.mjs`, `node-deps-heartbeat.mjs`, `node-deps-watcher.mjs`, `node-deps-relay.mjs` |
| Python provider | `../../pm/operations.py`, `../../pm/environment.py` |
| Agent input fields and checks | `inputs.py:30–115` |
| Agent assembly and outputs | `agent.py:76–168` |
| Launcher implementation | `launchers.py` |

## Verification still required

Parser checks and source inspection establish the documented call shapes.
They do not establish offline compilation, cache reuse, or runtime success.
Artifact acceptance still needs these checks:

- Real frontend builds from immutable prepared inputs without network access.
- Standalone TUI interaction.
- Agent CLI, ACP, plugins, and catalogs from an unrelated working directory.
- Native payload relocation and offline mutable-environment reconstruction.
- Docker runtime probes as its non-root user and layer-content inspection.
- Actual Nix builds and commands through store-reference wrappers.
- Fresh network-disabled bionic installation and Android device acceptance.

Docker/Nix build execution belongs to the distribution verification work, not
this documentation pass. No build-pass claim follows from this reference.
