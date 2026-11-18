"""Checkout custody for the updater's children (R2): the ONE spawn policy for every git (and the
Node build) an update starts.

The checkout kernel lock (``update_lock``) must stay held while any process of the update tree
can still write the checkout, and must NOT leak into processes that outlive the update:

* Every updater git call carries ``-c gc.autoDetach=false -c maintenance.auto=false``: git never
  forks a detached gc/maintenance child that would inherit or outlive the lock.
* The lock fd is inherited ONLY by git commands that mutate the worktree, index or refs
  locally (:data:`LOCAL_MUTATORS`, run with ``core.fsmonitor=false`` so no fsmonitor daemon
  starts under them, with no credential helper and no repository hooks). Network/credential commands (fetch,
  ls-remote, credential) and readers run without it: a ``git credential-cache--daemon`` they
  start never holds the checkout. A partial clone's mutator would lazily fetch the objects a move
  needs as a child holding the fd (and start the daemon under it), so before a move the objects
  are fetched without the fd and with the user's helpers (:func:`_prefetch_for_move`); a lazy
  fetch the prefetch missed still runs, just without a helper. A killed fetch that leaves
  ``*.lock`` ref files or ``shallow.lock`` is recovered by the stale-lock rules (``gitlock.release_dead_fetch_locks``
  once its update is proven dead, ``gitlock.clear_stale_git_locks`` by age otherwise).
* The Node build goes through ``pm.progress.run_contained`` (whose Popen it never sees):
  :func:`contained_command` wraps it in a Linux subreaper that holds the lock until every
  descendant has exited. Failure to arm the subreaper refuses the command before it runs.

Every updater git runner (``update_cmd._git_run``, ``update_cmd_git._git_run``,
``update_cmd_stash``, ``update_cmd_check``, ``gitlock``, ``update_cmd_commit``,
``_early_recovery``'s restore) calls :func:`run_git`.
Not covered, on purpose: the read-only release/check readers (``source_releases``,
``source_check``: ``ls-remote``, ``cherry``, ``rev-parse``) run their own probes; they never get
the lock fd and write nothing, so they cannot leak or outlive custody of the checkout.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from collections.abc import Sequence
import itertools

logger = logging.getLogger(__name__)

# No detached child: `git gc --auto` / `maintenance run --auto --detach` would otherwise fork a
# daemonized repack after any command that writes objects (commit, merge, fetch, stash).
GIT_NO_DETACH = ("-c", "gc.autoDetach=false", "-c", "maintenance.auto=false")
# Local mutators only (they hold the lock fd): never start an fsmonitor daemon under it, and no
# credential helper — a promisor lazy fetch under a mutator would start `git
# credential-cache--daemon` holding the fd for its lifetime (900 s by default, m3). No repository
# hooks either (F1): a hook inherits the fd, and one that backgrounds a process (a post-merge
# daemon) kept a completed update's checkout locked until it exited. Per command only: the
# repository's own hook configuration is untouched.
_MUTATOR_CONFIG = ("-c", "core.fsmonitor=false", "-c", "credential.helper=", "-c", f"core.hooksPath={os.devnull}")

# git subcommands that write the worktree, the index or refs on THIS machine. Only these inherit
# the checkout lock fd: if the updater dies mid-command, the checkout stays locked until git exits.
# No `pull` (fetch + merge: its network half must not hold the fd; the updater fetches, then
# merges). `gc` packs refs (and its repack writes the object store); `pack-objects` is the pack
# tidy's merge, whose output the update moves into the object store.
LOCAL_MUTATORS = frozenset({
    "add", "am", "apply", "checkout", "checkout-index", "cherry-pick", "clean", "commit", "gc",
    "merge", "mv", "pack-objects", "read-tree", "rebase", "reset", "restore", "revert", "rm", "stash",
    "switch", "update-index", "update-ref", "symbolic-ref", "tag", "branch", "worktree",
})
# Mutators that move the checkout to another commit: what they read from a promisor remote is
# fetched first, without the fd (_prefetch_for_move).
_MOVES = frozenset({"checkout", "merge", "read-tree", "reset", "switch"})

# Global options before the subcommand that take a separate value argument.
_GLOBAL_WITH_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                                "--config-env", "--super-prefix", "--attr-source", "--list-cmds"})


def git_subcommand(args: Sequence[str]) -> str | None:
    """The subcommand in ``args`` (everything after the git executable), skipping global options."""
    it = iter(args)
    for arg in it:
        if arg in _GLOBAL_WITH_VALUE:
            next(it, None)
        elif not arg.startswith("-"):
            return arg
    return None


def is_local_mutator(args: Sequence[str]) -> bool:
    return git_subcommand(args) in LOCAL_MUTATORS


def git_argv(git_cmd: Sequence[str], args: Sequence[str]) -> list[str]:
    """``git_cmd + args`` with the custody config inserted before the subcommand."""
    git_cmd, args = list(git_cmd), list(args)
    extra = GIT_NO_DETACH + (_MUTATOR_CONFIG if is_local_mutator(git_cmd[1:] + args) else ())
    return [*git_cmd, *extra, *args]


def _held() -> dict | None:
    try:
        from hermes_cli import update_lock
    except Exception:  # health: allow BLE001 -- a torn tree's half-written module raises anything; no updater runs
        return None
    return update_lock._HELD


def _death_signal_preexec():
    """Linux: a ``preexec_fn`` making the child die (SIGKILL) with the updater thread that waits
    on it. For the git children that do NOT hold the lock fd (fetch, ls-remote, readers): a
    killed owner must not leave a fetch rewriting refs under the next lock owner. The ruling's
    stale-lock rules recover a fetch killed mid-write."""
    import ctypes
    import os

    prctl = ctypes.CDLL(None, use_errno=True).prctl  # resolved before fork: the child only calls
    parent = os.getpid()

    def _arm():
        prctl(1, 9, 0, 0, 0)  # PR_SET_PDEATHSIG, SIGKILL
        if os.getppid() != parent:  # the owner died between fork and prctl
            os._exit(137)

    return _arm


def _custody_kwargs(inherit_lock: bool, kwargs: dict) -> dict:
    fds = _lock_fds(inherit_lock)
    if fds:
        kwargs["pass_fds"] = tuple(dict.fromkeys((*kwargs.get("pass_fds", ()), *fds)))
    elif not inherit_lock and _held() is not None and "preexec_fn" not in kwargs:
        arm = _death_signal_preexec()
        if arm is not None:
            kwargs["preexec_fn"] = arm
    return kwargs


def _lock_fds(inherit_lock: bool) -> tuple[int, ...]:
    if not inherit_lock:
        return ()
    from hermes_cli.update_lock import custody_spawn_kwargs

    return tuple(custody_spawn_kwargs().get("pass_fds", ()))


def popen(argv: Sequence[str], *, inherit_lock: bool = False, **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` in the update tree's custody (see the module doc)."""
    return subprocess.Popen(list(argv), **_custody_kwargs(inherit_lock, kwargs))


def run(argv: Sequence[str], *, inherit_lock: bool = False, **kwargs) -> subprocess.CompletedProcess:
    """Run an owned group; timeout/cancellation kills helpers and bounds pipe draining."""
    from hermes_cli._subprocess_compat import kill_and_drain

    timeout = kwargs.pop("timeout", None)
    check = kwargs.pop("check", False)
    input_data = kwargs.pop("input", None)
    if input_data is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    if kwargs.pop("capture_output", False):
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if kwargs.pop("process_group", None) not in (None, 0):
        raise ValueError("updater commands must own their process group")
    if not kwargs.get("start_new_session"):
        kwargs["process_group"] = 0
    proc = popen(argv, inherit_lock=inherit_lock, **kwargs)
    try:
        stdout, stderr = proc.communicate(input=input_data, timeout=timeout)
    except BaseException:
        with contextlib.suppress(Exception):
            kill_and_drain(proc, 1)
        raise
    result = subprocess.CompletedProcess(list(argv), proc.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result


def popen_post_commit(argv: Sequence[str], **kwargs) -> subprocess.Popen:
    """Start a committed update's checkout writer with the inherited lock fd."""
    return subprocess.Popen(list(argv), **_custody_kwargs(True, kwargs))


def run_git(git_cmd: Sequence[str], args: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
    """THE updater git runner: custody config in argv, the lock fd only into local mutators.
    ``kwargs`` are ``subprocess.run``'s."""
    argv = git_argv(git_cmd, args)
    mutator = is_local_mutator(argv[1:])
    if mutator:
        _prefetch_for_move(list(git_cmd), list(args), kwargs)

    return run(argv, inherit_lock=mutator, **kwargs)



def _partial_clone(git_cmd: Sequence[str], kwargs: dict) -> bool:
    """The repository has a promisor remote (``extensions.partialClone``), read from disk."""
    from pathlib import Path

    from hermes_cli.update_lock import _git_common_dir

    cwd = kwargs.get("cwd")
    for flag, value in itertools.pairwise(git_cmd):
        if flag == "-C":
            cwd = Path(cwd or ".") / value
    common = _git_common_dir(Path(cwd or "."))
    try:
        return common is not None and "partialclone" in \
            (common / "config").read_text(encoding="utf-8-sig", errors="replace").lower()
    except OSError:
        return False


def _prefetch_for_move(git_cmd: list[str], args: list[str], kwargs: dict) -> None:
    """Partial clones: fetch what a move to another commit will read — the target's changed trees
    and blobs — WITHOUT the lock fd and with the user's credential helpers, so the mutator (which
    has neither helper nor reason to fetch) never starts a lazy fetch or a credential daemon under
    the fd (m3). ``git diff HEAD <target>`` reads exactly the trees and blobs that differ from the
    checked-out tree; the rest is local. Best effort: a miss only leaves a helper-less lazy fetch."""
    sub = git_subcommand(args)
    if sub not in _MOVES or not _partial_clone(git_cmd, kwargs):
        return
    common = {key: kwargs[key] for key in ("cwd", "env") if key in kwargs}

    def resolve(*query: str, stdin: str | None = None) -> str | None:
        out = run(git_argv(git_cmd, list(query)), capture_output=True, text=True, encoding="utf-8",
                  errors="replace", input=stdin, **({} if stdin is not None else {"stdin": subprocess.DEVNULL}),
                  **common)
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None

    try:
        targets = []
        for arg in args[args.index(sub) + 1:]:
            if arg == "--":
                break
            if not arg.startswith("-"):
                targets.append(resolve("rev-parse", "--verify", "-q", f"{arg}^{{commit}}"))
        targets = [target for target in dict.fromkeys(targets) if target]
        if not targets:
            return
        # An unborn HEAD (a fresh clone) has nothing checked out: diff from the empty tree.
        base = resolve("rev-parse", "--verify", "-q", "HEAD^{commit}") \
            or resolve("hash-object", "-t", "tree", "--stdin", stdin="")
        for target in targets:
            if base and target != base:
                run(git_argv(git_cmd, ["diff", "--binary", "--no-ext-diff", "--no-textconv", base, target]),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **common)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("Could not prefetch the objects for git %s: %s", sub, exc)


# Node marks the donated lock fd close-on-exec,
# so nothing node starts (pnpm, esbuild, sh) keeps the checkout locked: custody of them rests on
# this stdlib parent, which holds the fd until no descendant of the command is left.
# * The command stays in the CALLER's process group: every group kill that stops a build (a
#   Ctrl-C'd completion child's ``killpg``, Desktop's ``kill(-pid)``) reaches node and everything
#   under it that did not start a session of its own. The custodian leaves that group (E): a
#   SIGKILL of the group would otherwise kill it too, and a descendant in a session of its own
#   would go on writing with the checkout lock free. It outlives the kill, settles the tree as
#   below, and only then exits (releasing the fd).
# * Linux: the custodian is a child subreaper, so a descendant orphaned by node's exit (or by any
#   intermediate's) is re-parented to it, never to init. Once node exits it SIGKILLs and reaps
#   its children until none is left; it only signals its own unreaped children, so no pid it
#   kills can have been reused (never a ``killpg`` of an already reaped leader's group).

# * SIGINT/SIGTERM/SIGHUP are forwarded to node (a node still running 10 s later is killed); the
#   descendants are then killed as above.
# * The custodian is NOT the process the runner sees (R4): ``subprocess.run`` SIGKILLs its own
#   child when Ctrl-C reaches the caller, and no group separation survives a direct kill. The
#   runner's child only forks the custodian (own group) and relays signals to it and its exit
#   status back; it leaves the caller's group too, or a terminal's Ctrl-C would reach node twice. Killed, it leaves the custodian to settle: node gets the same 10 s it gets after
#   a forwarded signal (it is usually still cleaning up from the same Ctrl-C), then the tree is
#   killed as above and only then is the fd dropped.
_REAP_TREE = r"""
import os, signal, subprocess, sys, time
KILL = signal.SIGKILL
fds = tuple(int(fd) for fd in sys.argv[1].split(','))
caller = os.getpgrp()
if caller != os.getpid():
    os.setpgid(0, 0)
stand_in = os.getpid()
custodian = os.fork()
if custodian:
    def relay(signum, frame):
        try:
            os.kill(custodian, signum)
        except OSError:
            pass
    for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        signal.signal(getattr(signal, name), relay)
    code = os.waitstatus_to_exitcode(os.waitpid(custodian, 0)[1])
    sys.exit(code if code >= 0 else 128 - code)
os.setpgid(0, 0)
me = os.getpid()
import ctypes
if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
    raise OSError(ctypes.get_errno(), 'could not arm update child subreaper; command not run')

def children():
    found = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % entry, 'rb') as fh:
                stat = fh.read()
            if int(stat[stat.rindex(b')') + 2:].split()[1]) == me:
                found.append(int(entry))
        except (OSError, ValueError, IndexError):
            continue
    return found


def kill(pids):
    for pid in pids:
        try:
            os.kill(pid, KILL)
        except OSError:
            pass

p = subprocess.Popen(sys.argv[2:], pass_fds=fds, process_group=caller)
got = []

def forward(signum, frame):
    got.append(signum)
    try:
        p.send_signal(signum)
    except OSError:
        pass

for name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
    signal.signal(getattr(signal, name), forward)
deadline = None
while True:
    try:
        code = p.wait(timeout=0.5)
        break
    except subprocess.TimeoutExpired:
        pass

    if got or os.getppid() != stand_in:
        deadline = deadline or time.monotonic() + 10
        if time.monotonic() > deadline:
            p.kill()
end = time.monotonic() + 10
while time.monotonic() < end:
    live = children()
    if not live:
        break
    kill(live)
    while True:
        try:
            if not os.waitpid(-1, os.WNOHANG)[0]:
                break
        except ChildProcessError:
            break
    time.sleep(0.02)
sys.exit(code if code >= 0 else 128 - code)
"""


@contextlib.contextmanager
def contained_command(argv: Sequence[str], *, inherit_lock: bool = True, root=None):
    """``(argv, kwargs)`` for a checkout writer started by a runner that hides its Popen (the Node
    build in ``pm.progress.run_contained``). The lock fd (this process's, or one it
    inherited for checkout ``root``), under a launcher that keeps the command in the caller's
    process group and kills every descendant left when it exits (:data:`_REAP_TREE`)."""
    argv = list(argv)
    from hermes_cli.update_lock import checkout_lock_fds

    fds = tuple(checkout_lock_fds(root)) if inherit_lock and root is not None else _lock_fds(inherit_lock)
    if fds:
        argv = [sys.executable, "-I", "-S", "-c", _REAP_TREE, ",".join(map(str, fds)), *argv]
    yield argv, ({"pass_fds": fds} if fds else {})
