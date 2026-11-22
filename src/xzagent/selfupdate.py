"""ctxp update: reinstall the CLI in place, from its source.

Replaces the manual developer loop (git pull, pip uninstall, pip install
--no-cache-dir) with a single command. Source resolution order:

1. explicit folder argument (your xZagent clone)
2. the source saved from the last successful update (~/.xzagent/config.json)
3. an xZagent checkout found walking up from the current directory
4. the GitHub repository itself (pip fetches git+https, no clone needed)

The remembered source makes plain `ctxp update` work from anywhere.

On Windows the running ctxp.exe is locked by the OS, so pip cannot
overwrite it while we are alive: the heavy step is handed off to a fresh
interpreter (python -m xzagent __update-child) that waits for THIS
process to exit, reinstalls, and prints the result into the same console.
On every other platform pip runs in-process and the result prints
immediately. Piped/non-tty sessions stay plain and quiet.
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

    if _needs_handoff():
        # Windows locks the running ctxp.exe: spawn a fresh interpreter,
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
    rc, out = _run([sys.executable, "-m", "pip", *PIP_ARGS, spec])
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
    try:
        rc, out = _run([sys.executable, "-m", "pip", *PIP_ARGS, spec],
                       note="installing via pip",
                       progress=prog.update if prog.enabled else None)
    finally:
        prog.finish()
    if rc != 0:
        _fail("pip install failed", out)
        return 1
    _report(__version__, _installed_version(), label or spec)
    return 0