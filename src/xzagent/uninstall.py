"""ctxp uninstall: remove the xzagent CLI from this machine.

Pip removes the package and the ctxp / python -m xzagent entry points.
A y/N confirmation runs first (--yes skips it). Nothing belonging to a
project is ever touched: each project's .xzagent/ index is plain
project data, MCP client configs belong to their projects (clean them
with `ctxp setup --remove` inside that project). --purge also removes
the global ~/.xzagent config dir (saved update source, update locks).

Windows: the running ctxp.exe cannot be deleted by itself, exactly
like an update. Same machinery: rename the live exe aside and uninstall
right there; if even the rename is refused, hand the uninstall to a
detached interpreter (python -m xzagent __update-child uninstall ...)
that finishes and reports once this process is gone.
"""
import os
import subprocess
import sys

from . import __version__

REINSTALL_HINT = "pip install git+https://github.com/shadow1t/xZagent.git"


def _print_plan(purge):
    print("This removes from this machine:")
    print("  - the xzagent package and its ctxp commands (pip)")
    if purge:
        print("  - the global config dir (~/.xzagent: saved update source)")
    print("Project data stays untouched:")
    print("  - each project's .xzagent/ index (plain project files)")
    print("  - MCP client configs (clean a project's config with")
    print("    'ctxp setup --remove' inside that project)")
    print("")


def _success(purge):
    from .tui import GRAY, GREEN, WHITE, color_enabled, paint
    if color_enabled():
        print("%s%s%s%s" % (
            paint("\u2718", GREEN),
            paint(" Uninstalled xzagent", WHITE),
            paint(" %s" % __version__, GRAY),
            paint("  \u00b7 project data untouched", GRAY)))
    else:
        print("Uninstalled xzagent %s (project data untouched)" % __version__)
    print("reinstall any time: %s" % REINSTALL_HINT)
    return 0


def _fail(what, output):
    print("error: %s" % what)
    for ln in (output or "").splitlines():
        if ln.strip():
            print("  " + ln)
    return 1


def _rmtree_config_dir():
    import shutil

    from .selfupdate import config_dir
    d = config_dir()
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    return d


def _spawn_handoff(spec):
    child = [sys.executable, "-m", "xzagent", "__update-child", spec,
             str(os.getpid()), "uninstall"]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if flags:      # Windows: new process group, immune to Ctrl+C noise
        subprocess.Popen(child, creationflags=flags)
    else:          # POSIX (env-override demos/tests): survive the parent
        subprocess.Popen(child, start_new_session=True)


def uninstall(yes=False, purge=False, runner=None, asker=None, retire=None,
              spawn_child=None, rmtree=None):
    """Entry point of `ctxp uninstall`. Returns a process exit code.

    Everything slow or external is injectable so tests exercise the
    full flow without pip touching the machine.
    """
    from .selfupdate import _is_windows, _needs_handoff, _retire_running_exe, _run
    runner = runner or _run
    asker = asker or (lambda prompt: input(prompt))
    retire = retire if retire is not None else _retire_running_exe
    spawn_child = spawn_child or _spawn_handoff
    rmtree = rmtree or _rmtree_config_dir

    _print_plan(purge)
    if not yes:
        reply = asker("Proceed? [y/N] ")
        if (reply or "").strip().lower() not in ("y", "yes"):
            print("cancelled \u00b7 nothing removed")
            return 0

    if _needs_handoff():
        if _is_windows() and retire():
            pass  # exe renamed aside: uninstall right here, one command
        else:
            # the running exe is locked and could not be renamed: hand the
            # uninstall to a detached child that finishes once we exit
            spawn_child("uninstall-purge" if purge else "uninstall")
            print("Uninstall in progress (the result prints here in a few seconds)")
            return 0

    rc, out = runner([sys.executable, "-m", "pip", "uninstall", "-y", "xzagent"])
    if rc != 0:
        return _fail("pip uninstall failed", out)
    if purge:
        rmtree()
    return _success(purge)


def child_uninstall(purge=False, ppid=None, runner=None, rmtree=None):
    """The detached handoff body: pip uninstall once the parent died."""
    import time

    from .selfupdate import _run
    runner = runner or _run
    rmtree = rmtree or _rmtree_config_dir
    if ppid is None:
        ppid = os.getppid()
    for _ in range(240):  # wait up to a minute, exactly like child_finish
        try:
            os.kill(int(ppid), 0)
        except (OSError, ValueError):
            break
        time.sleep(0.25)
    rc, out = runner([sys.executable, "-m", "pip", "uninstall", "-y", "xzagent"])
    if rc != 0:
        return _fail("pip uninstall failed", out)
    if purge:
        rmtree()
    return _success(purge)
