# Stable release admission and promotion

`Stable Release` is the release gate. A successful builder alone is not a
stable release. Stable releases use attempt refs as locks and final tags as
publish receipts; neither tag is a workflow trigger. Canary builds have their
own scheduled workflow.

Canaries retain the exact `v<stable>+canary.<YYYYMMDDTHHMMSSZ>` annotated tag.
`scripts/release.py --canary --publish` creates or resumes its GitHub draft and
dispatches `termux-release.yml` from the repository's default branch. Admission
requires a repository maintainer, the exact remote tag object and commit, and
ancestry on that branch. Admission refuses a canary older than the newest
published canary.
After the Termux payload and public APT checks pass, the workflow revalidates the
receipt, publishes the prerelease, and reads it back. Failed or skipped payload
jobs leave the draft unpublished. Stable and canary source subscriptions read
published GitHub releases directly; neither needs a graphical package manifest.
Canaries are ordered by their UTC tag stamp, not by SemVer build precedence.

The committed project version is always `0.0.0`. Release jobs derive the payload
version from the admitted ref and stamp isolated build trees. Do not bump version
files on `main`.

## Order

1. Refresh `origin/main` and the remote attempt and marker refs (`rc.*` and
   `abandoned-rc.*`). Derive the next SemVer from the published release family
   seeded at `0.21.5` when no release exists: the newest published non-prerelease GitHub release with a `vX.Y.Z` tag is authoritative.
   Source-only releases spend their versions even when no package channel moves.
   Attempts do not move the
   version line. A cut whose next version already has a final `vX.Y.Z` tag is
   refused until that publication finishes.
2. Push an annotated `rc.<N>-vX.Y.Z` attempt ref atomically, create one
   non-prerelease GitHub draft on it, and dispatch `Stable Release` from the
   default branch with that ref as its `tag` input. The attempt number comes from the existing attempt refs of that version.
   The claim message binds its commit, attempt number, autopublish policy,
   lane set (see [Lanes](#lanes)), and
   one monotonically allocated epoch. That epoch is the release date and native
   packaging clock for every matrix leg and retry. One outstanding attempt, of
   any version, blocks a new `release`.
3. Admit the exact remote annotated tag-object SHA, its peeled commit, and that
   commit's ancestry on `origin/main`. The run itself must be the default-branch
   dispatch of `Stable Release`. Its workflows and release scripts come from
   `main`, so its Actions caches share one scope across attempts. `GITHUB_SHA` is
   `main`'s tip, never the commit under test. Every lane that tests or builds the
   payload is passed the admitted commit, and the commit-threading lint in
   `tests/ci/test_stable_release_commit_threading.py` enforces that.
4. Run the lanes the claim records: by default source CI, Nix, PM bundle acceptance, and install/update acceptance.
   Add the Termux build lane explicitly when publishing an APT package.
5. Push immutable versioned R2 artifacts from the tested bytes. Do not rebuild for publication.
6. Create the annotated final `vMAJOR.MINOR.PATCH` receipt at publish, not at
   green. It binds the winning attempt's ref, object, commit, archive prefix,
   candidate-manifest SHA256 (`null` when the claim skipped bundles), and autopublish policy.
7. The stable publication controller resolves releases oldest first. It creates
   `vX.Y.Z`, retargets the still-draft release onto it, strips the warning
   blocks, makes the release public as the last call, then verifies and promotes
   the receipt-bound APT packages for the selected channels.

A sole green draft waits unless its claim selected autopublish. A later green
claim flushes all contiguous earlier green drafts in order. An older running claim
blocks newer publication. A burned attempt is skipped, and its version is cut
again after `abandon`. Failed, cancelled, missing, and unexpectedly skipped
requirements remain red.

R2, APT, and GitHub do not support one cross-service
transaction. The controller is therefore idempotent: after a partial failure,
run it again and let every mutation verify its current state before continuing.
The final tag is the custody receipt. Never rebuild or replace accepted bytes to
repair a pointer.

Termux promotion replaces `releases/termux/stable/release-candidates.json` and the stable APT indexes on the R2 public origin.
The GitHub release remains the source-installation update authority, independently of APT publication.

## Run, publish, or abandon a stable release

Start from an exact commit already on remote `main`:

```sh
python scripts/release.py release --commit "$(git rev-parse origin/main)" --bump patch --remote origin
```

`--bump` defaults to `patch`; pass `minor` or `major` only when that change is
intentional. The selected commit must descend from the newest published
`vX.Y.Z`. With no published tag, any commit already on remote `main` is
accepted. An abandoned attempt puts no constraint on the next cut; it was often
abandoned because its commit is bad. `release` also refuses while any attempt,
of any version, is outstanding. The refusal prints the attempt's workflow run
URL, the `abandon` command, and the rerun command before it raises. In an
interactive terminal it then asks `Abandon <ref> and cut a new attempt? [y/N]`;
`y` abandons the attempt and cuts the next one, and the default is no. Anywhere
stdin or stdout is not a terminal it raises without asking.

The attempt ref is a public lock, not a SemVer prerelease. It reads
`rc.1-v0.21.5`, not `v0.21.5-rc.1`. The package version stays plain `0.21.5`.
`parse_attempt_ref` in `scripts/releases/versioning.py` is the one grammar for
the shape.

The draft body carries a fenced warning block at the top and at the bottom: do
not publish the release from the GitHub UI. Publishing it by hand skips the
`vX.Y.Z` receipt tag and the APT channel.
Published releases are immutable, so a hand-published release cannot
be fixed afterwards, and it blocks the pipeline: the attempt then has neither a
marker ref nor a final tag, so `release` refuses it as outstanding and
`abandon` refuses it because it is published. The publication pass strips both
fenced blocks while the release is still a draft.

Add `--autopublish` to publish immediately when the claim becomes the oldest
green release. Without it, the release stays a draft until an explicit publish
or a later green claim forces ordered resolution.

### Lanes

A stable release is a set of lanes. A build lane produces something users get;
a test lane is a suite that gates the release. `release` starts from the
default set and edits it:

```sh
# The default: everything except Termux
python scripts/release.py release --commit "$(git rev-parse origin/main)" --remote origin
# Only the Termux package, no standalone test lanes
python scripts/release.py release --commit "$(git rev-parse origin/main)" --only termux --remote origin
# Everything, plus Termux, without the install/update E2E
python scripts/release.py release --commit "$(git rev-parse origin/main)" --with termux --without install-e2e --remote origin
```

`--only` replaces the default set, `--with` adds lanes and `--without`
removes them. Each takes comma-separated names and can repeat. `release --help`
lists every lane.

| Lane | Default | What it controls |
|---|---|---|
| `termux` | off | Termux (Android aarch64) APT package |
| `ci`, `nix`, `pm-bundle`, `install-e2e` | on | the standalone suites |
| `termux-checks` | on | Termux verification; runs only with `termux` |

Lanes are independent switches. A job runs only when every lane it needs is on
(`JOB_LANES` in `scripts/releases/lanes.py`), so a test lane for something that
is not built does nothing.
For example, `termux-checks` with `termux` off runs no Termux verification.

The lane set is written into the claim message (`"lanes": [...]`), never passed
as a workflow input. `admit` reads it from the claim and emits two outputs:
`lanes` (one flag per lane) and `jobs` (one run flag per lane-controlled job).
Every such job's `if` reads its own `jobs` entry, so the lane table is the only
place that says what runs. A recovery rerun cannot change it. To change a lane,
`abandon` the attempt and cut again.

The gates stay strict. `scripts.releases.stable gate` reads the lanes and
requires each job they remove to report `skipped`, and every other gated job to
report `success`. A job that ran although a lane removes it also blocks the
release.
Claims require an explicit, duplicate-free `lanes` list and `lanesRevision: 3`.
Old flag sets, revisions, and retired lane names are rejected instead of translated.

What a release publishes follows what it built:


- **Packages.** With the Termux lane off there is no candidate manifest,
  and the final tag records `candidateManifestSha256: null`.
- **APT.** Only a release with `termux` selected advances the Termux APT channel.
  Other releases leave its indexes and packages unchanged.
- **Finished.** Every selected channel must name the accepted release: APT through its candidate head.
  A release that moves no channel is finished once it is public.
  The sequencer uses these markers to recover incomplete publication without repeating completed channel moves.

The claim push is the atomic version lock. Two callers may derive the same next
version, but only one push wins; the loser reports the winning tagger, time, and
commit. A rejected or abandoned claim remains spent. To publish or abandon:

```sh
python scripts/release.py publish --version 0.21.5 --remote origin
python scripts/release.py abandon --version 0.21.5 --remote origin
```

`publish` performs a synchronous supersession preflight, then dispatches the same
ordered controller used by automatic recovery. It refuses a known burned version
below a newer published release. `abandon` force-cancels the attempt's
in-progress `Stable Release` runs, deletes the draft when one exists,
writes an `abandoned-rc.<N>-vX.Y.Z` marker ref, and keeps the attempt ref. The
marker is the record of abandonment; the attempt ref is never deleted. The
version is not spent, so the next cut is `rc.<N+1>-vX.Y.Z`.

Do not manually dispatch `Stable Release` with a final tag. Recovery keeps the
original claim ref, object SHA, commit, draft database ID, autopublish policy,
and lane set.

## Failure and recovery

When a stable run fails, its completion event starts `Stable Release
Publication`, which at once reruns only the failed jobs in the same GitHub
Actions run. There is no backoff and no schedule. The pass applies only the
oldest unresolved retry, because GitHub keeps only one pending run in the shared
stable-release concurrency group. The rerun waits in that group until the pass
ends. At most two retries are admitted (run attempts 2 and 3). After attempt 3
fails, the claim is burned and the sequencer may resolve later claims. A lost
retry request, or a crash between publication mutations, is recovered by the
next failure event or by dispatching `Stable Release Publication` by hand.

A newly pushed claim with no observed workflow run remains unresolved for one
hour. This grace window covers the non-atomic draft and dispatch steps. After the
hour, a still-unstarted claim is derived as burned. No claim is burned during the
normal creation window.

Termux handoffs live in the immutable R2 tag archive. The archive
key is the attempt ref: `releases/tag/rc.<N>-vX.Y.Z/`. `releases/tag/vX.Y.Z/`
is never written. Each producer writes a `handoff-<target>.json` receipt
containing the tag, commit, paths, sizes, and SHA256 digests.
Candidate assembly emits
one pinned `release-candidates.json`; consumers verify its digest and do not
re-upload the packages. Its digest is recorded nowhere before publish: the
publication pass hashes the archive copy and writes that hash into the final
tag.

The Termux candidate job stages packages and metadata under
`releases/tag/<attempt ref>/` before acceptance. No stable feed is written at
this stage. The stable APT pool path also carries the attempt ref:
`releases/termux/stable/pool/<attempt ref>/<c>/<name>.deb`. The pool upload is
immutable with a one-year cache header, so a recut of the same version writes a
different pool key.
Immutable uploads accept an existing object only when its bytes match. If an
archive object, final-tag digest, or read-back differs, stop
recovery rather than replacing the accepted candidate.

Candidate manifests use schema `3` and bind the admitted claim epoch, exact source commit, attempt archive, package hashes, and Termux package identity.
Validation rejects other schemas, unexpected fields or targets, invalid epochs, and package versions that do not match the admitted release.
APT promotion verifies the published final receipt and the archived manifest digest before reading or publishing package bytes.
The package acceptance and runtime identity checks remain part of their selected retained lanes.

The draft stays on the attempt ref until publish. Deleting it is `abandon`, and
it does not burn the version. The annotated final tag binds the GitHub release
database ID admitted with the claim. Publish edits the release only while it is
still a draft: it creates `vX.Y.Z`, retargets the release onto it, strips the
warning blocks from the body, and reads the tag and body back. Making the
release public is the last call. Published releases are immutable, so nothing
can retarget or re-tag one afterwards.


APT indexes stay on the previous accepted package until promotion.

The reusable `termux-release.yml` workflow owns the Termux build and signed APT handoff.
Its `termux_upgrade_from_tag` input selects an exact published Termux handoff when explicitly requested.
Stable calls that workflow only when its claim selects the Termux lane.
Every gate and candidate starts after `admit`, and `acceptance` joins the selected requirements before publication.

## Tag namespaces and receipts

Before relying on attempt refs as locks, apply a repository ruleset for
`refs/tags/v*`, `refs/tags/rc.*`, and `refs/tags/abandoned-rc.*` that restricts
creation to organization administrators and the release integration and blocks
update and deletion. The ruleset is not applied yet; until an admin applies it,
these locks are honor-system — anyone with push access can delete a marker ref
or move a receipt tag. Attempt refs, marker refs, and final tags are immutable.


## Dynamic channels in R2

Source subscriptions remain independent of graphical applications.
`stable` resolves a published GitHub release, and `main` follows the Git branch without an R2 record.
Named preview subscriptions resolve validated R2 channel records through `hermes_cli/source_releases.py` and `hermes_cli/release_channels.py`.
Repository authority, exact source commits, and downgrade guards remain enforced before changing a checkout.
These readers are retained CLI functionality, not a reason to restore retired native builders or migration handlers.

## Explicit exclusions and policy

- Install/update E2E remains selected by default.
  A claim without `install-e2e` requires that job to be skipped rather than reporting it as passed.
- PR-only history, label and diff review checks do not apply to a stable tag.
  All applicable source CI jobs still run, regardless of changed paths.
- OSV vulnerability findings retain their existing advisory policy. Required
  scanner execution failures are failures, not advisory findings.
- PM bundles, Nix and install/update checks remain required when their lanes are selected.
- Housekeeping, autofix, comment and skills-index/deploy workflows are not
  release acceptance suites.

## Implementation ownership

Actions owns job ordering, runner selection, permissions and environments.
Python owns shared release admission, manifests, artifact hashes, publication
and channel promotion under `scripts/releases/` and `scripts/bundles/`.
The retained Termux workflow and build driver consume those shared release facts.
Gate jobs use only Python's standard library;
they do not install the application or the JS workspace to report a verdict.

`scripts/releases/versioning.py` resolves the published GitHub stable identity.
`scripts/releases/sequencer.py` orders publication and verifies completion of all selected channels.
`scripts/releases/termux.py` admits APT promotion from the stable controller and binds it to the final receipt.
`scripts/bundles/release_artifacts.py` validates the accepted `.deb` bytes and publishes the Termux pool, signed indexes, and candidate head without rebuilding packages.

Signing and publication credentials stay in their protected job environments.
The source CI call does not inherit deployment secrets. Configure the existing
Termux signing credentials before running this pipeline.
