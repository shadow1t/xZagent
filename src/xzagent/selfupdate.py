"""ctxp update: reinstall the CLI in place, from its source.

Replaces the manual developer loop (git pull, pip uninstall, pip install
--no-cache-dir) with a single command. Source resolution order:

1. explicit folder argument (your xZagent clone)
2. the source saved from the last successful update (~/.xzagent/config.json)
3. an xZagent checkout found walking up from the current directory
4. the GitHub repository itself (pip fetches git+https, no clone needed)

The remembered source makes plain `ctxp update` work from anywhere.

On Windows the running ctxp.exe is locked by the OS and cannot be
OVERWRITTEN while we are alive, but a running exe CAN be renamed:
ctxp update renames the live exe aside (ctxp.exe.oldN), pip installs
the fresh exe at the original path, and the whole update runs inside
the one visible command: live spinner, result line, prompt returns
when it truly finished. Only when the rename fails (rare, AV meddling)
does it fall back to the older handoff: a detached interpreter
(python -m xzagent __update-child) waits for THIS process to exit,
reinstalls, and prints into the same console. Leftover .oldN files
are swept at the next update. On every other platform pip runs
in-process from the start. Piped/non-tty sessions stay plain and quiet.
"""
import json
import os
import subprocess
import sys
import time

from . import __version__
from .tui import CYAN, GRAY, GREEN, Progress, WHITE, color_enabled, paint

DEFAULT_GIT_URL = "https://github.com/shadow1t/xZagent.git"
PIP_ARGS = ("install", "--force-reinstall", "--no-deps", "--no-cache-dir")


def config_dir():
    return (os.environ.get("XZAGENT_CONFIG_DIR")
            or os.path.join(os.path.expanduser("~"), ".xzagent"))


def config_path():
    return os.path.join(config_dir(), "config.json")


def _load_cfg():
    try:
        with open(config_path(), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_source(kind, value):
    """Remember what the user updates from, so plain `ctxp update` works."""
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump({"update_source": {"kind": kind, "value": value}}, fh, indent=2)
    except OSError:
        pass  # the config is a convenience, never a failure


def detect_clone(start="."):
    """The xZagent source tree containing this project, if cwd is inside it."""
    d = os.path.abspath(start)
    while True:
        try:
            with open(os.path.join(d, "pyproject.toml"), encoding="utf-8") as fh:
                if 'name = "xzagent"' in fh.read():
                    return d
        except OSError:
            pass
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def resolve_source(path_arg=None):
    """Returns (kind, value): 'path' + folder, or 'git' + repo URL."""
    if path_arg:
        d = os.path.abspath(path_arg)
        if not os.path.isdir(d):
            raise SystemExit("error: %s is not a directory" % path_arg)
        return "path", d
    saved = _load_cfg().get("update_source") or {}
    if saved.get("kind") == "path" and os.path.isdir(saved.get("value") or ""):
        return "path", saved["value"]
    if saved.get("kind") == "git" and saved.get("value"):
        return "git", saved["value"]
    clone = detect_clone(os.getcwd())
    if clone:
        return "path", clone
    return "git", DEFAULT_GIT_URL


def _pip_spec(kind, value):
    return value if kind == "path" else "git+%s@main" % value


def _run(cmd, note=None, progress=None):
    """Quietly run a command; returns (returncode, combined output)."""
    if progress is not None and note:
        progress(0, 0, note)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        return p.returncode, (p.stdout or "").strip()
    except OSError as e:
        return 127, str(e)


def _is_windows():
    """True on Windows; XZAGENT_UPDATE_INPLACE=1 simulates it anywhere.

    Lets the Windows in-place rename flow be exercised end to end in
    demos and tests on POSIX (where os.name never says nt).
    """
    if os.environ.get("XZAGENT_UPDATE_INPLACE") == "1":
        return True
    return os.name == "nt"


def _needs_handoff():
    """True when the running console script would be locked during reinstall.

    XZAGENT_UPDATE_HANDOFF=1 forces the Windows-style handoff on any
    platform so the flow can be exercised end to end in demos and tests.
    """
    if os.environ.get("XZAGENT_UPDATE_HANDOFF") == "1":
        return True
    return os.name == "nt"


def _installed_version():
    """Version the freshly installed CLI reports (clean env, no PYTHONPATH)."""
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    try:
        p = subprocess.run(
            [sys.executable, "-c",
             "from xzagent import __version__; print(__version__)"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env)
        if p.returncode == 0:
            return (p.stdout or "").strip() or None
    except OSError:
        pass
    return None


def _report(old, new, label):
    if color_enabled():
        head = paint("\u2714", GREEN)
        if new and new != old:
            print("%s %s %s %s %s  %s%s" % (
                head, paint("Updated", WHITE),
                paint("xzagent", WHITE), paint(old, GRAY),
                paint("\u2192 " + new, GREEN, bold=True),
                paint("\u00b7 from ", GRAY), paint(label, GRAY)))
        elif new:
            print("%s %s %s %s  %s%s" % (
                head, paint("Reinstalled", WHITE), paint("xzagent", WHITE),
                paint(new, GREEN, bold=True),
                paint("\u00b7 from ", GRAY), paint(label, GRAY)))
        else:
            print("%s %s%s%s%s" % (
                head, paint("Updated. Run ", WHITE),
                paint("ctxp --version", CYAN),
                paint(" to confirm.", WHITE), paint("  \u00b7 from " + label, GRAY)))
        return
    if new and new != old:
        print("Updated xzagent %s -> %s (from %s)" % (old, new, label))
    elif new:
        print("Reinstalled xzagent %s (from %s)" % (new, label))
    else:
        print("Updated xzagent (from %s). Run ctxp --version to confirm." % label)


def _fail(what, output):
    print("error: %s" % what)
    tail = [ln for ln in (output or "").splitlines() if ln.strip()][-6:]
    for ln in tail:
        print("  " + ln)
    print("  fix the problem above, then run: ctxp update")


def _pull_latest(value, progress=None):
    """git pull in the clone; returns (ok, output). Skipped without .git."""
    if not os.path.isdir(os.path.join(value, ".git")):
        return True, ""
    rc, out = _run(["git", "-C", value, "pull", "--ff-only"],
                   note="pulling latest changes", progress=progress)
    return rc == 0, out


def _update_lock_path():
    return os.path.join(config_dir(), "update.lock")


def _acquire_update_lock(max_age=600):
    """One updater at a time.

    Two concurrent `ctxp update` runs (double-entered command, editor
    terminal racing a scheduled one) would point two pip processes at
    the same install dir at once. The first updater takes an exclusive
    lock; the second stands down. A lock older than max_age seconds is
    stale (crashed updater) and is taken over.
    """
    def _try():
        fd = os.open(_update_lock_path(),
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return True
    try:
        os.makedirs(config_dir(), exist_ok=True)
        return _try()
    except FileExistsError:
        try:
            age = time.time() - os.stat(_update_lock_path()).st_mtime
        except OSError:
            return False  # vanished between try and stat: unlucky timing
        if age > max_age:
            try:
                os.unlink(_update_lock_path())
            except OSError:
                return False
            try:
                return _try()
            except FileExistsError:
                return False
        return False


def _release_update_lock():
    try:
        os.unlink(_update_lock_path())
    except OSError:
        pass


def _already_running_line():
    if color_enabled():
        return "%s%s%s%s" % (paint("\u2714", GREEN),
                             paint(" an update is already in progress",
                                   WHITE),
                             paint(" \u00b7 ", GRAY),
                             paint("letting it finish", GRAY))
    return "Update already in progress (another ctxp update owns it); nothing to do"


def _entry_exe_candidates():
    """Likely installed locations of the ctxp console-script exe."""
    cands = []
    try:
        import sysconfig
        d = sysconfig.get_path("scripts")
        if d:
            cands.append(os.path.join(d, "ctxp.exe"))
    except Exception:
        pass
    if sys.prefix:
        cands.append(os.path.join(sys.prefix, "Scripts", "ctxp.exe"))
    try:
        import site
        if site.USER_BASE and os.name == "nt":
            cands.append(os.path.join(site.USER_BASE,
                                      "Python%d%d" % sys.version_info[:2],
                                      "Scripts", "ctxp.exe"))
    except Exception:
        pass
    out = []
    for c in cands:
        if os.path.isfile(c) and c not in out:
            out.append(c)
    return out


def _retired_exe_dirs():
    dirs = [os.path.dirname(c) for c in _entry_exe_candidates()]
    try:
        import sysconfig
        d = sysconfig.get_path("scripts")
        if d and os.path.isdir(d):
            dirs.append(d)
    except Exception:
        pass
    return [d for d in dict.fromkeys(dirs) if d and os.path.isdir(d)]


def _sweep_retired_exes():
    """Delete leftover ctxp.exe.oldN from previous updates (best effort)."""
    for d in _retired_exe_dirs():
        for i in range(4):
            try:
                os.unlink(os.path.join(d, "ctxp.exe.old%d" % i))
            except OSError:
                pass


def _retire_running_exe(candidates=None):
    """Windows in-place self-update: rename the locked exe aside.

    A running exe cannot be overWRITTEN on Windows, but it CAN be
    renamed. Renaming the live ctxp.exe aside frees the original path
    while the running launcher keeps executing under its new name,
    so pip can install the fresh exe in place WITHOUT waiting for the
    command to exit. That kills the whole handoff dance: no child
    process printing behind PowerShell's back, no phantom updates after
    the prompt returns, no Enter press needed to see the input line.

    Returns True when the rename landed (caller should update in
    place); False means fall back to the detached-child handoff.
    """
    cands = candidates if candidates is not None else _entry_exe_candidates()
    for exe in cands:
        if not os.path.isfile(exe):
            continue
        for i in range(4):
            old = os.path.join(os.path.dirname(exe), "ctxp.exe.old%d" % i)
            if os.path.isfile(old):
                continue
            try:
                os.rename(exe, old)
                return True
            except OSError:
                return False  # AV/permission meddling: handoff fallback
    return False


def selfupdate(path_arg=None, no_pull=False, progress=None):
    """Entry point of `ctxp update`. Returns a process exit code."""
    kind, value = resolve_source(path_arg)
    _save_source(kind, value)
    spec = _pip_spec(kind, value)
    label = value if kind == "path" else "%s@main" % value

    if kind == "path" and not no_pull:
        ok, out = _pull_latest(value, progress)
        if not ok:
            _fail("git pull failed in %s" % value, out)
            return 1

    in_place = not _needs_handoff()
    if not in_place and _is_windows():
        # the running exe is locked, but it can be RENAMED: retire it
        # aside and reinstall in front of the user, one clean command
        _sweep_retired_exes()
        in_place = _retire_running_exe()
    if not in_place:
        # Windows locks the running ctxp.exe (rename could not free it):
        # spawn a fresh interpreter,
        # exit now, and let it reinstall + report once we are gone. The
        # child keeps the console alive with its own live progress line.
        child = [sys.executable, "-m", "xzagent", "__update-child", spec,
                 str(os.getpid()), label]
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            if flags:  # Windows: new process group, immune to Ctrl+C noise
                subprocess.Popen(child, creationflags=flags)
            else:      # POSIX (env-override demos/tests): survive SIGHUP,
                       # like a Windows child survives its parent exit
                subprocess.Popen(child, start_new_session=True)
        except OSError as e:
            _fail("could not start the updater", str(e))
            return 1
        if progress is not None:
            progress(0, 0, "handing off to the updater")
        if color_enabled():
            print("%s%s%s%s" % (
                paint("\u2714 ", GREEN), paint("Update in progress", WHITE),
                paint(" \u00b7 ", GRAY),
                paint("the result prints here in a few seconds", GRAY)))
        else:
            print("Update in progress (the result prints here in a few seconds)")
        return 0

    if progress is not None:
        progress(0, 0, "installing via pip")
    if not _acquire_update_lock():
        print(_already_running_line())
        return 0
    try:
        rc, out = _run([sys.executable, "-m", "pip", *PIP_ARGS, spec])
    finally:
        _release_update_lock()
    if rc != 0:
        _fail("pip install failed", out)
        return 1
    _report(__version__, _installed_version(), label)
    return 0


def child_finish(spec, ppid, label=None):
    """Runs in the handoff interpreter after the parent has exited."""
    for _ in range(240):  # wait up to one minute for the parent to die
        try:
            os.kill(int(ppid), 0)
        except (OSError, ValueError):
            break
        time.sleep(0.25)
    # the install is the slow part: keep a live progress line going in the
    # user's console, exactly like every other long ctxp phase
    prog = Progress()
    prog.start()
    if not _acquire_update_lock():
        prog.finish()
        print(_already_running_line())
        return 0
    try:
        rc, out = _run([sys.executable, "-m", "pip", *PIP_ARGS, spec],
                       note="installing via pip",
                       progress=prog.update if prog.enabled else None)
    finally:
        prog.finish()
        _release_update_lock()
    if rc != 0:
        _fail("pip install failed", out)
        return 1
    _report(__version__, _installed_version(), label or spec)
    return 0