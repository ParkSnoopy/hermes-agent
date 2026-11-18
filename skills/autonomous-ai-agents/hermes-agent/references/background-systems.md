# Durable & Background Systems

Four systems run alongside the main conversation loop. Quick reference
here; full developer notes live in `AGENTS.md`, user-facing docs under
`website/docs/user-guide/features/`.

### Delegation (`delegate_task`)

Spawn a subagent with an isolated context + terminal session.

- **Single:** `delegate_task(goal, context)`.
- **Batch:** `delegate_task(tasks=[{goal, ...}, ...])` runs children in
  parallel, capped by `delegation.max_concurrent_children` (default 3).
- **Background:** `delegate_task(background=true)` returns a handle
  immediately and keeps the parent loop going; the child's result
  re-enters the conversation as a new turn when it finishes.
- **Roles:** `leaf` (default; cannot re-delegate) vs `orchestrator`
  (can spawn its own workers, bounded by `delegation.max_spawn_depth`).
- **Not durable.** A backgrounded child is still process-local — if the
  parent process exits, the child is lost. For work that must outlive
  the process, use
  `terminal(background=True, notify_on_complete=True)`.

Config: `delegation.*` in `config.yaml`.

### Curator (skill lifecycle)

Background maintenance for agent-created skills. Tracks usage, marks
idle skills stale, archives stale ones, keeps a pre-run tar.gz backup
so nothing is lost.

- **CLI:** `hermes curator <verb>` — `status`, `usage`, `run`, `pause`,
  `resume`, `pin`, `unpin`, `archive`, `restore`, `list-archived`, `prune`,
  `backup`, `rollback`.
- **Slash:** `/curator <subcommand>` mirrors the CLI.
- **Scope:** only touches skills with `created_by: "agent"` provenance.
  Bundled + hub-installed skills are off-limits. **Never deletes** —
  max destructive action is archive. Pinned skills are exempt from
  every auto-transition and every LLM review pass.
- **Cost:** the deterministic inactivity/prune sweep runs for free. The
  aux-model "consolidate overlapping skills into umbrellas" pass is
  **off by default** — opt in with `curator.consolidate: true` or
  `hermes curator run --consolidate`. Routine background curation costs
  zero tokens.
- **Telemetry:** sidecar at `~/.hermes/skills/.usage.json` holds
  per-skill `use_count`, `view_count`, `patch_count`,
  `last_activity_at`, `state`, `pinned`.

Config: `curator.*` (`enabled`, `interval_hours`, `min_idle_hours`,
`stale_after_days`, `archive_after_days`, `backup.*`).
User docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/curator
