# Historical package-management audit

This record describes an audit comparing `49945b14029e09fef608db9ede899377cdb54e11` with merge base `433f7196760e5d76f77ea6d5464ee7f1b602a4ee`.
It is not a current-head release certificate or evidence that later changes passed these checks.
For current contracts and developer setup, see [Package management](../reference/package-management.md).

## Audit findings and repairs

The audit identified dependency constraint conflicts, publication and recovery hazards, and mismatched runtime selection.
At the audited revisions, the repairs included preparing a complete dependency environment before publication, preserving explicit constraints and pins, retaining recoverable tool-store entries after publication failure, and using atomic facts and configuration writes.
Publication recovery recorded the previous configuration and proposed hash and refused to overwrite unrelated edits.
Generation leases protected live readers from collection.
PM completion receipts were correlated with the invoking update rather than an enclosing command.
Nix selected its Python family from `pm/lock.json`; real Nix evaluation and build acceptance remained separate gates.

## Historical verification

The recorded CI run at `a27cd5902a446eb4f1e457e09904d75edc616c2e` reported 46,041 Linux tests passed, none failed, and 492 skipped, without retry-only flakes.
That result applies to that revision, not the present tree.
The historical verification also recorded successful Docker bootstrap builds for both architectures after including the standard-library runtime-path and locking owners required before third-party dependencies are available.
The original [CI run](https://github.com/NousResearch/hermes-agent/actions/runs/34059149746) provides the associated evidence.