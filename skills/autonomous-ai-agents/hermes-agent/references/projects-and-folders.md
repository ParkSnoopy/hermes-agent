# Projects and folders (`hermes project`)

A **Project** is a named workspace that can span several folders — a repo, a monorepo, a
scratch directory, a sibling repo you always open together. Projects are the named
workspaces the desktop sidebar groups chats by.

Projects are **not** git concepts and not config keys: they live in their own SQLite store,
one per profile (`$HERMES_HOME/projects.db`; `~/.hermes` by default), created on first use. `hermes -p <profile> project list` shows another
profile's projects.

## Command surface

`hermes project <sub> --help` is authoritative for flags.

| Subcommand | What it does |
|---|---|
| `create <name> [folders...]` | New project. First folder becomes the primary; `--slug`, `--primary PATH`, `--description`, `--icon`, `--color`, `--use` (make it active). |
| `list` (alias `ls`) | Projects, oldest first; `*` marks the active one; `--all` includes archived. |
| `show <project>` | Slug, id, name, about/primary, folder list (`*` = primary). |
| `add-folder <project> <path>` | Add a folder; `--label`, `--primary` (make it primary). |
| `remove-folder <project> <path>` | Drop a folder (repoints primary if it was primary). |
| `rename <project> <name>` | Change the display name. The slug does **not** change. |
| `set-primary <project> <path>` | Primary folder; the path must already be in the project. |
| `use [<project>]` | Set the active project; with no argument, clear it. |
| `archive <project>` / `restore <project>` | Hide / unhide a project (reversible; nothing on disk moves). |

`<project>` is an id (`p_1a2b3c4d`, exact) or a slug (any case). The CLI does **not**
accept the display name; get the slug from `hermes project list`.

## Semantics worth knowing

- **Slug** — derived from the name (lowercased, runs of non-alphanumerics collapse to `-`,
  capped at 64 chars) unless `--slug` is given. An invalid `--slug` exits **2**. A slug that
  is already taken gets a suffix (`team-docs-2`, …), an explicit `--slug` included, so read
  the slug back from the `Created project <slug>` line.
- **Folders** — stored as absolute paths; `~`, relative paths and trailing slashes are
  normalized on every command. Paths are not checked or created. The primary is the first
  folder given, or `--primary`; it anchors the folder a desktop chat moves to.
- **One project per primary folder** — `create` refuses a primary path that is already the
  primary of another (non-archived) project (`folder already belongs to project '<slug>'
  …`). Two projects owning one path produce duplicate sidebar subtrees.
- **Active project** — one per-profile pointer that `use` sets and clears: the `*` in
  `hermes project list` and the highlighted project in the desktop sidebar. It is a marker,
  not a scope: chat workspaces do not follow it.
- **Archived projects** are hidden from `list` (use `--all`) and stop claiming their folders
  for path → project resolution; they still resolve by id or slug.
- **Path → project resolution** — a folder owns itself and everything under it, and the
  longest matching folder wins, so a nested project resolves to the innermost one.
- **Deleting** — the CLI has no delete; `archive` is the reversible way to retire a project.
  The desktop app's project menu deletes the project record permanently (folders on disk and
  chats are kept).
- **Exit codes** — `0` ok; `1` no such project, or the path is not a folder of that project
  (`remove-folder`, `set-primary`); `2` invalid input (bad slug, empty name,
  duplicate primary folder, bad arguments).

## What projects actually drive

- **Desktop sidebar grouping.** A chat's workspace (its cwd) resolves to the owning project
  by the path rule above, and the TUI status line and desktop status bar name it. The
  sidebar also shows automatic groups for repos and folders that are not projects (absent
  from `hermes project list`); setting a color or icon on one turns it into a real project.
- **The `desktop_project` tool** (toolset `project`, folded in by the desktop/TUI gateway;
  not in the default CLI toolsets). `action: create|switch|list` with `name` /
  `path`; unlike the CLI it also matches display names, and archived projects. In a live
  desktop/TUI chat, `create` and `switch` move *that chat's* workspace to the project's
  primary folder and the sidebar follows — the intended way to move a chat between projects,
  not `cd`. The tool itself leaves the active-project pointer (the shared sidebar selection)
  alone, though the desktop sidebar following the chat you are viewing can select the new
  project; a project with no folders cannot move the chat, so it sets the pointer instead. A
  primary folder that does not exist on disk is skipped silently: the call succeeds but the
  chat stays put. `create` with a `path` that is already another project's primary reuses
  that project instead of failing.
## Common tasks

```bash
# Make a project for a repo and start using it
hermes project create "Hermes Agent" ~/Work/hermes-agent --use

# A workspace spanning two repos (first given = primary)
hermes project create "Payments" ~/Work/payments-api ~/Work/payments-web

# Add a folder later / move the primary
hermes project add-folder payments ~/Work/payments-infra --label infra
hermes project set-primary payments ~/Work/payments-web

# What is active (archived included), and what a project contains
hermes project list --all
hermes project show payments

# Retire one without losing it
hermes project archive payments        # hidden from list / path resolution
hermes project restore payments

```

## Troubleshooting

- **`no such project: <Name>`** — the CLI takes an id or slug, not the display name.
- **"switched project but this chat didn't move"** — `hermes project use` only moves the
  pointer. Switch from inside the chat (the `desktop_project` tool), or start the chat in the
  project's folder; check that the primary folder exists.
- **`create` refused: folder already belongs to project X** — the folder is already X's
  primary (possibly an automatic sidebar group that was given a color or icon). Use X
  (`hermes project show X`, `rename` it if the name is wrong) rather than adding the folder
  to a second project.
- **A project's folders look "unprojected"** — the project is archived;
  `hermes project restore <project>`.
- **Two profiles show different projects** — expected: `projects.db` is per profile.

## See also

- `references/cli-reference.md` — `hermes project` in the CLI command map.
- `references/project-context-files.md` — a different feature: `AGENTS.md` / `.hermes.md` /
  `CLAUDE.md` instruction files that load into a session.
- https://hermes-agent.nousresearch.com/docs/reference/cli-commands#hermes-project
