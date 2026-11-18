"""Hermes Agent Uninstaller."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hermes_constants import get_hermes_home

from hermes_cli._startup_fast import project_root_str
from hermes_cli.colors import Colors, color

def _logger(mark: str, col: str):
    return lambda msg: print(f"{color(mark, col)} {msg}")


log_info = _logger("→", Colors.CYAN)
log_success = _logger("✓", Colors.GREEN)
log_warn = _logger("⚠", Colors.YELLOW)


def _print_box(middle: str, col: str) -> None:
    """Print the 3-line framed heading used by the uninstall screens."""
    print(color("┌─────────────────────────────────────────────────────────┐", col, Colors.BOLD))
    print(color(middle, col, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", col, Colors.BOLD))


def _prompt(text: str):
    """``input(text).strip().lower()``; None (after printing "Cancelled.") on Ctrl-C/EOF."""
    try:
        return input(text).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        print("Cancelled.")
        return None


def _cancelled() -> None:
    print()
    print("Uninstall cancelled.")


def _confirm_yes(text: str) -> bool:
    """Ask the user to type ``yes``; False (after the cancel line, unless Ctrl-C/EOF) otherwise."""
    confirm = _prompt(f"Type '{color('yes', Colors.YELLOW)}' {text}: ")
    if confirm != "yes" and confirm is not None:
        _cancelled()
    return confirm == "yes"


def _remove_each(candidates, remove) -> list:
    """Run ``remove(path)`` per candidate, collecting the ones it reports removed; failures become
    the shared ``Could not remove <path>: <err>`` warning."""
    removed = []
    for path in candidates:
        try:
            if remove(path):
                removed.append(path)
        except Exception as e:
            log_warn(f"Could not remove {path}: {e}")
    return removed


def get_project_root() -> Path:
    """Get the project installation directory."""
    return Path(project_root_str())


def code_removal_refusal() -> str | None:
    """Why the uninstaller must not remove this tree's code, or ``None``.

    A git checkout (the install.sh layout)
    is ours to remove. A sealed tree (no ``.git``) belongs to a steward —
    the Nix store, the bundled desktop app, a Docker image — and only the
    steward removes it. Returns the user-facing refusal text for sealed
    trees; the text always points at ``hermes uninstall --data`` for the
    user-data cleanup that IS allowed everywhere.
    """
    from hermes_cli.steward import sealed_steward, steward_uninstall_message

    steward = sealed_steward(get_project_root())
    if steward is None:
        return None
    return steward_uninstall_message(steward)


def _refuse_if_steward_owned() -> None:
    """Exit with the steward's instructions when code removal is forbidden."""
    refusal = code_removal_refusal()
    if refusal is None:
        return
    print()
    print(refusal)
    print()
    raise SystemExit(1)


def find_shell_configs() -> list:
    """Find shell configuration files that might have PATH entries."""
    home = Path.home()
    configs = []
    
    candidates = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
        home / ".zprofile",
    ]
    
    for config in candidates:
        if config.exists():
            configs.append(config)
    
    return configs


def remove_path_from_shell_configs():
    """Remove Hermes PATH entries from shell configuration files."""
    removed_from = []
    for config_path in find_shell_configs():
        try:
            content = config_path.read_text(encoding="utf-8-sig")
            original_content = content
            
            # Remove lines containing hermes-agent or hermes PATH entries
            new_lines = []
            skip_next = False
            
            for line in content.split('\n'):
                # Skip the "# Hermes Agent" comment and following line
                if '# Hermes Agent' in line or '# hermes-agent' in line:
                    skip_next = True
                    continue
                if skip_next and ('hermes' in line.lower() and 'PATH' in line):
                    skip_next = False
                    continue
                skip_next = False
                
                # Remove any PATH line containing hermes
                if 'hermes' in line.lower() and ('PATH=' in line or 'path=' in line.lower()):
                    continue
                    
                new_lines.append(line)
            
            new_content = '\n'.join(new_lines)
            
            # Clean up multiple blank lines
            while '\n\n\n' in new_content:
                new_content = new_content.replace('\n\n\n', '\n\n')
            
            if new_content != original_content:
                from utils import atomic_write_text
                # The user's own rc, never backed up: a bare write_text() truncates before the new
                # content lands and a crash mid-write would leave an empty ~/.zshrc. Atomic replace
                # also follows a symlinked rc; preserve_mode keeps its bits/owner (sudo-run uninstalls).
                atomic_write_text(config_path, new_content, preserve_mode=True)
                removed_from.append(config_path)
        except Exception as e:
            log_warn(f"Could not update {config_path}: {e}")
    return removed_from


def remove_wrapper_script():
    """Remove the hermes wrapper script if it exists."""
    wrapper_paths = [
        Path.home() / ".local" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes-acp",
        Path.home() / ".local" / "bin" / "hermes-agent",
        Path("/usr/local/bin/hermes"),
        Path("/usr/local/bin/hermes-acp"),
        Path("/usr/local/bin/hermes-agent"),
    ]
    
    removed = []
    for wrapper in wrapper_paths:
        if wrapper.exists():
            try:
                # Check if it's our wrapper (contains hermes_cli reference)
                content = wrapper.read_text(encoding="utf-8-sig")
                if 'hermes_cli' in content or 'hermes-agent' in content:
                    wrapper.unlink()
                    removed.append(wrapper)
            except Exception as e:
                log_warn(f"Could not remove {wrapper}: {e}")
    
    return removed


def _node_symlink_candidate_dirs() -> list[Path]:
    """Directories where the installer may have placed node/npm/npx symlinks."""
    dirs: list[Path] = [Path.home() / ".local" / "bin"]
    dirs.append(Path("/usr/local/bin"))  # root FHS installs
    return dirs


def remove_node_symlinks(hermes_home: Path) -> list:
    """Remove the node/npm/npx symlinks the installer placed on PATH.

    Historical POSIX installs (``scripts/install.sh`` before pm owned the
    Node runtime) symlinked node/npm/npx into the same directory as the
    ``hermes`` command:

    - ``/usr/local/bin/`` on root FHS installs (Linux, uid 0)
    - ``~/.local/bin/`` otherwise (the common non-root case)

    We check all candidate directories so that uninstall works regardless of
    how the install was done (e.g. a root FHS install that placed links in
    ``/usr/local/bin``, or an older install that used ``~/.local/bin`` before
    the FHS fix).  Only symlinks that resolve into this Hermes home's ``node``
    directory are removed — links the user has repointed elsewhere (nvm, fnm,
    etc.) are left untouched.
    """
    node_dir = (hermes_home / "node").resolve()

    def _unlink_ours(link: Path) -> bool:
        # Only act on symlinks — never delete a real binary the user put here.
        if not link.is_symlink():
            return False
        target = link.resolve()
        if target != node_dir and node_dir not in target.parents:
            return False
        link.unlink()
        return True

    candidates = (bin_dir / name for name in ("node", "npm", "npx") for bin_dir in _node_symlink_candidate_dirs())
    return _remove_each(candidates, _unlink_ours)


def uninstall_gateway_service():
    """Remove owned systemd services and standalone gateway processes."""
    stopped_something = False

    # 1. Kill any standalone gateway processes
    try:
        from hermes_cli.gateway import kill_gateway_processes, find_gateway_pids
        killed = kill_gateway_processes() if find_gateway_pids() else 0
        if killed:
            log_success(f"Killed {killed} running gateway process(es)")
            stopped_something = True
    except Exception as e:
        log_warn(f"Could not check for gateway processes: {e}")

    # Termux/Android has no systemd — nothing left to do.
    if os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in os.getenv("PREFIX", ""):
        return stopped_something

    # 2. Remove owned systemd units in both scopes.
    try:
        stopped_something = _remove_systemd_gateway() or stopped_something
    except Exception as e:
        log_warn(f"Could not check systemd gateway services: {e}")
    return stopped_something


def _remove_systemd_gateway() -> bool:
    """Linux: uninstall systemd services (both user and system scopes)."""
    from hermes_cli.gateway import (
        _systemctl_cmd, _systemd_unit_belongs_to_current_home, get_service_name, get_systemd_unit_path,
    )
    svc_name = get_service_name()
    removed_any = False
    for is_system, scope in ((False, "user"), (True, "system")):
        unit_path = get_systemd_unit_path(system=is_system)
        if not unit_path.exists() or not _systemd_unit_belongs_to_current_home(is_system):
            continue
        try:
            if is_system and os.geteuid() != 0:  # windows-footgun: ok — Linux-only systemd path
                log_warn(f"System gateway service exists at {unit_path} but needs sudo to remove")
                continue
            cmd = _systemctl_cmd(is_system)
            for verb in ("stop", "disable"):
                subprocess.run(cmd + [verb, svc_name], capture_output=True, check=False)
            unit_path.unlink()
            subprocess.run(cmd + ["daemon-reload"], capture_output=True, check=False)
            log_success(f"Removed {scope} gateway service ({unit_path})")
            removed_any = True
        except Exception as e:
            log_warn(f"Could not remove {scope} gateway service: {e}")
    return removed_any


def _xdg_leftover_paths(full_uninstall: bool) -> tuple[list[Path], list[Path]]:
    """The XDG cache/data dirs an install scatters outside HERMES_HOME.

    Returns (removable, data_preserved): the cache dir is always removable;
    the XDG data dir is user data, so it is only removed on a full uninstall
    and otherwise reported as intentionally preserved.
    """
    home = Path.home()
    cache = home / ".cache" / "hermes"
    data = home / ".local" / "share" / "hermes"
    removable = [cache] if cache.exists() else []
    if data.exists() and full_uninstall:
        removable.append(data)
    preserved = [data] if data.exists() and not full_uninstall else []
    return removable, preserved


def remove_desktop_app_leftovers(*, full_uninstall: bool) -> list[Path]:
    """Remove XDG cache and, on full uninstall, XDG data leftovers."""
    removable, _ = _xdg_leftover_paths(full_uninstall=full_uninstall)
    return _remove_each((t for t in removable if t.exists()), _remove_path_or_tree)


def _remove_path_or_tree(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        return True
    except OSError:
        return False


def remove_legacy_runtime_trees(hermes_home: Path) -> list[Path]:
    """Delete managed-runtime trees a PRE-SPLIT install left in HERMES_HOME.

    Runtime artifacts are install-scoped now, so the current locations go
    away with ``rmtree(project_root)``. But a checkout OUTSIDE the home
    (the common case: ``~/src/hermes-agent``) used to put its node/uv
    under ``$HERMES_HOME`` — that tree survives removing the checkout and
    survives "keep my data" uninstalls, because it is not data.

    Only the exact managed layout is removed: ``node/`` (a tree the
    installer owned wholesale) and ``bin/uv`` (the single binary, NOT the
    whole ``bin/`` dir — a user's own scripts can live there). Profile
    state is never touched.
    """
    removed: list[Path] = []

    node_tree = hermes_home / "node"
    if node_tree.is_dir():
        try:
            shutil.rmtree(node_tree, ignore_errors=False)
            removed.append(node_tree)
        except Exception as e:
            log_warn(f"Could not remove {node_tree}: {e}")

    for uv_name in ("uv", "uv.exe"):
        uv_binary = hermes_home / "bin" / uv_name
        if uv_binary.is_file():
            try:
                uv_binary.unlink()
                removed.append(uv_binary)
            except Exception as e:
                log_warn(f"Could not remove {uv_binary}: {e}")

    return removed


def _is_default_hermes_home(hermes_home: Path) -> bool:
    """Return True when ``hermes_home`` points at the default (non-profile) root."""
    try:
        from hermes_constants import get_default_hermes_root
        return hermes_home.resolve() == get_default_hermes_root().resolve()
    except Exception:
        return False


def _discover_named_profiles():
    """``ProfileInfo`` for every non-default profile; ``[]`` when profile support is unavailable."""
    try:
        from hermes_cli.profiles import list_profiles
    except Exception:
        return []
    try:
        return [p for p in list_profiles() if not getattr(p, "is_default", False)]
    except Exception as e:
        log_warn(f"Could not enumerate profiles: {e}")
        return []


def _uninstall_profile(profile) -> None:
    """Fully uninstall a named profile: stop its gateway, remove its alias, wipe its home. Shells
    out to ``hermes -p <name> gateway stop|uninstall`` because service names / unit paths derive
    from the current HERMES_HOME and can't be switched in-process."""
    name = profile.name
    log_info(f"Uninstalling profile '{name}'...")

    # 1. Gateway service, via `python -m hermes_cli.main` (the `hermes` wrapper may be half-gone).
    hermes_invocation = [sys.executable, "-P", "-m", "hermes_cli.main", "--profile", name]
    for subcmd in ("stop", "uninstall"):
        try:
            subprocess.run(
                hermes_invocation + ["gateway", subcmd], capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=60, check=False)
        except subprocess.TimeoutExpired:
            log_warn(f"  Gateway {subcmd} timed out for '{name}'")
        except Exception as e:
            log_warn(f"  Could not run gateway {subcmd} for '{name}': {e}")

    # 2. Remove the wrapper alias script at ~/.local/bin/<name> (if any).
    alias_path = getattr(profile, "alias_path", None)
    if alias_path and alias_path.exists():
        try:
            alias_path.unlink()
            log_success(f"  Removed alias {alias_path}")
        except Exception as e:
            log_warn(f"  Could not remove alias {alias_path}: {e}")
    # 3. Wipe the profile's HERMES_HOME directory.
    _rmtree_step(profile.path, indent="  ", fully=False)


def run_data_uninstall(args):
    """Erase this home's data, preserving installed runtimes and sibling profiles."""
    from hermes_cli.data_cleanup import plan_data_removal, remove_data

    home = get_hermes_home()
    try:
        plan = plan_data_removal(home, get_project_root())
    except (OSError, ValueError, RuntimeError) as exc:
        log_warn(str(exc))
        raise SystemExit(1) from exc
    if not plan.remove:
        print(f"No Hermes user data found in {home}.")
        return
    print("Data-only removal: installed code and other profiles stay intact.")
    print("Will remove:")
    for path in plan.remove:
        print(f"  {path}")
    if plan.keep:
        print("Kept intact:")
        for path in plan.keep:
            print(f"  {path}")
    if bool(getattr(args, "dry_run", False)):
        print("Dry run: no data or processes changed.")
        return
    if not bool(getattr(args, "yes", False)) and not _confirm_yes("to remove this home's data"):
        return
    from hermes_cli.data_cleanup_holders import quiescent_home

    try:
        with quiescent_home(plan.home):
            removed, failed = remove_data(plan)
    except (OSError, ValueError, RuntimeError) as exc:
        log_warn(f"No data removed: {exc}")
        raise SystemExit(1) from exc
    for path in removed:
        log_success(f"Removed {path}")
    if failed:
        print("Hermes data was only partially removed. Surviving targets:")
        for path, reason in failed:
            log_warn(f"{path}: {reason}")
        raise SystemExit(1)
    log_success("Hermes data removed.")


def run_uninstall(args):
    """
    Run the uninstall process.
    
    Options:
    - Full uninstall: removes code + ~/.hermes/ (configs, data, logs)
    - Keep data: removes code but keeps ~/.hermes/ for future reinstall
    """
    _refuse_if_steward_owned()

    project_root = get_project_root()
    hermes_home = get_hermes_home()

    full_flag = bool(getattr(args, "full", False))
    if bool(getattr(args, "dry_run", False)):
        _print_uninstall_dry_run(
            project_root=project_root, hermes_home=hermes_home, full_uninstall=full_flag)
        return

    # Named profiles (only when uninstalling from the default root) are offered for cleanup too,
    # instead of leaving zombie HERMES_HOMEs and systemd units behind.
    is_default_profile = _is_default_hermes_home(hermes_home)
    named_profiles = _discover_named_profiles() if is_default_profile else []

    # ``--yes`` (the desktop app's detached cleanup script): no prompts; ``--full`` = full wipe.
    # Named profiles are NOT auto-removed here — too destructive a default for an unattended run.
    if bool(getattr(args, "yes", False)):
        _perform_uninstall(
            project_root=project_root, hermes_home=hermes_home, full_uninstall=full_flag,
            remove_profiles=False, named_profiles=named_profiles)
        return

    print()
    _print_box("│            ☤ Hermes Agent Uninstaller                  │", Colors.MAGENTA)
    print()

    # Show what will be affected
    print(color("Current Installation:", Colors.CYAN, Colors.BOLD))
    print(f"  Code:    {project_root}")
    print(f"  Config:  {hermes_home / 'config.yaml'}")
    print(f"  Secrets: {hermes_home / '.env'}")
    print(f"  Data:    {hermes_home / 'cron/'}, {hermes_home / 'sessions/'}, {hermes_home / 'logs/'}")
    print()

    if named_profiles:
        print(color("Other profiles detected:", Colors.CYAN, Colors.BOLD))
        for p in named_profiles:
            print(f"  • {p.name}{' (gateway running)' if getattr(p, 'gateway_running', False) else ''}: {p.path}")
        print()

    # Ask for confirmation
    print(color("Uninstall Options:", Colors.YELLOW, Colors.BOLD))
    print()
    print("  1) " + color("Keep data", Colors.GREEN) + " - Remove code only, keep configs/sessions/logs")
    print("     (Recommended - you can reinstall later with your settings intact)")
    print()
    print("  2) " + color("Full uninstall", Colors.RED) + " - Remove everything including all data")
    print("     (Warning: This deletes all configs, sessions, and logs permanently)")
    print()
    print("  3) " + color("Cancel", Colors.CYAN) + " - Don't uninstall")
    print()

    choice = _prompt(color("Select option [1/2/3]: ", Colors.BOLD))
    if choice is None:
        return

    if choice in {"3", "c", "cancel", "q", "quit", "n", "no"}:
        _cancelled()
        return

    full_uninstall = (choice == "2")

    # Full uninstall from the default profile: offer to remove named profiles too (gateway
    # services, alias wrappers, HERMES_HOME dirs) — otherwise they leave zombie services behind.
    remove_profiles = False
    n_profiles = len(named_profiles)
    profile_names = ", ".join(p.name for p in named_profiles)
    if full_uninstall and named_profiles:
        print()
        print(color("Other profiles will NOT be removed by default.", Colors.YELLOW))
        print(f"Found {n_profiles} named profile(s): {profile_names}")
        print()
        resp = _prompt(color(f"Also stop and remove these {n_profiles} profile(s)? [y/N]: ", Colors.BOLD))
        if resp is None:
            return
        remove_profiles = resp in {"y", "yes"}

    # Final confirmation
    print()
    if full_uninstall:
        print(color("⚠️  WARNING: This will permanently delete ALL Hermes data!", Colors.RED, Colors.BOLD))
        print(color("   Including: configs, API keys, sessions, scheduled jobs, logs", Colors.RED))
        if remove_profiles:
            print(color(f"   Plus {n_profiles} profile(s): {profile_names}", Colors.RED))
    else:
        print("This will remove the Hermes code but keep your configuration and data.")

    print()
    if not _confirm_yes("to confirm"):
        return

    _perform_uninstall(
        project_root=project_root, hermes_home=hermes_home, full_uninstall=full_uninstall,
        remove_profiles=remove_profiles, named_profiles=named_profiles)


def _print_uninstall_dry_run(*, project_root: Path, hermes_home: Path, full_uninstall: bool) -> None:
    """Print the uninstall plan without stopping services or deleting files."""
    print()
    print(color("Dry run: no files, services, or environment entries will be changed.", Colors.CYAN, Colors.BOLD))
    print()
    print(color("Would inspect/remove:", Colors.YELLOW, Colors.BOLD))
    print("  • Gateway services and standalone gateway processes")
    print("  • Hermes PATH entries from shell configs")
    print("  • Hermes wrapper scripts and Hermes-managed node/npm/npx symlinks")
    print(f"  • Code checkout: {project_root}")
    if not full_uninstall:
        print(f"  • Keep Hermes config/data: {hermes_home}")
    else:
        print(f"  • Hermes config/data: {hermes_home}")
        profiles = _discover_named_profiles() if _is_default_hermes_home(hermes_home) else []
        if profiles:
            print("  • Named profiles (interactive uninstall asks before removing):")
            for prof in profiles:
                print(f"    - {prof.name}: {prof.path}")
    print()


def _remove_step(label: str, remove, success_fmt: str, none_msg: str) -> None:
    """Announce ``label``, run ``remove()``, log one success line per removed item (or ``none_msg``)."""
    log_info(label)
    removed = remove()
    for item in removed:
        log_success(success_fmt.format(item))
    if not removed:
        log_info(none_msg)


def _rmtree_step(path: Path, *, indent: str = "", fully: bool = True) -> None:
    """Best-effort ``rmtree`` with the shared success/warning lines."""
    try:
        if path.exists():
            shutil.rmtree(path)
            log_success(f"{indent}Removed {path}")
    except Exception as e:
        log_warn(f"{indent}Could not {'fully ' if fully else ''}remove {path}: {e}")
        if fully:
            log_info("You may need to manually remove it")


def _perform_uninstall(
    *,
    project_root: Path,
    hermes_home: Path,
    full_uninstall: bool,
    remove_profiles: bool,
    named_profiles: list) -> None:
    """Remove gateway, shell entries, runtime and code; optionally remove data."""
    print()
    print(color("Uninstalling...", Colors.CYAN, Colors.BOLD))
    print()
    # 1. Stop and uninstall gateway service + kill standalone processes
    log_info("Checking for running gateway...")
    if not uninstall_gateway_service():
        log_info("No gateway service or processes found")

    # 2-3. Remove shell PATH entries, wrappers and owned node symlinks.
    for label, remove, success_fmt, none_msg in (
        ("Removing PATH entries from shell configs...",
         remove_path_from_shell_configs, "Updated {}", "No PATH entries found to remove in shell rc files"),
        ("Removing hermes command...", remove_wrapper_script, "Removed {}", "No wrapper script found"),
        ("Removing Hermes-managed node/npm/npx symlinks...",
         lambda: remove_node_symlinks(hermes_home), "Removed {}", "No Hermes-managed node/npm/npx symlinks found"),
    ):
        _remove_step(label, remove, success_fmt, none_msg)

    # 4. Remove installation directory (code) — we may be running from inside it.
    log_info("Removing installation directory...")
    _rmtree_step(project_root)
    # 4c. Remove managed-runtime trees a PRE-SPLIT install left in
    #     HERMES_HOME. Current installs keep these inside the checkout, so
    #     step 4 already removed them — but a checkout outside the home
    #     (~/src/hermes-agent) used to leave its node/uv behind, surviving
    #     both the checkout removal and a "keep my data" uninstall. They
    #     are install tooling, not data, so removing them is correct in
    #     either mode.
    log_info("Removing managed runtime trees...")
    removed_runtimes = remove_legacy_runtime_trees(hermes_home)
    if removed_runtimes:
        for path in removed_runtimes:
            log_success(f"Removed {path}")
    else:
        log_info("No legacy runtime trees to remove")

    # 5. Optionally remove ~/.hermes/ data directory (and named profiles)
    if full_uninstall:
        # 5a. Named profiles' homes live under <default>/profiles/ (swept by the rmtree below),
        #     but their services + alias scripts live OUTSIDE the default root.
        for prof in named_profiles if remove_profiles else ():
            _uninstall_profile(prof)
        log_info("Removing configuration and data...")
        _rmtree_step(hermes_home)
    else:
        log_info(f"Keeping configuration and data in {hermes_home}")

    print()
    _print_box("│              ✓ Uninstall Complete!                      │", Colors.GREEN)
    print()

    if not full_uninstall:
        print(color("Your configuration and data have been preserved:", Colors.CYAN))
        print(f"  {hermes_home}/")

        _, preserved = _xdg_leftover_paths(full_uninstall=False)
        for path in preserved:
            print(f"  {path}/")
        print()
        print("To reinstall later with your existing settings:")
        print(color("  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash", Colors.DIM))
        print()

    print(color("Reload your shell to complete the process:", Colors.YELLOW))
    print("  source ~/.bashrc  # or ~/.zshrc")
    print()
    print("Thank you for using Hermes Agent! ☤")
    print()


class _UninstallArgs:
    """Lightweight args namespace for the module entrypoint below."""

    def __init__(self, *, mode: str):
        self.full = mode == "full"
        self.data = mode == "data"
        self.yes = True  # the module entrypoint is always non-interactive



def main(argv=None) -> int:
    """Route non-interactive code or data removal through the same safeguards."""
    import argparse
    parser = argparse.ArgumentParser(prog="python -m hermes_cli.uninstall")
    parser.add_argument(
        "--mode",
        choices=["lite", "full", "data"],
        required=True,
        help="lite = remove agent, keep data; "
        "full = everything; data = user data only, keep code",
    )
    ns = parser.parse_args(argv)
    args = _UninstallArgs(mode=ns.mode)

    if args.data:
        run_data_uninstall(args)
    else:
        run_uninstall(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
