"""Ctrl+C confirmation for every running ctxp command.

Professional CLI behavior: hitting Ctrl+C once while a command works
asks "Stop now? [y/N]" instead of dumping a traceback. "y" stops the
command with exit code 130; anything else (or just Enter) keeps going.
Hitting Ctrl+C AGAIN, at any moment (even in the same tap-tap burst
or while the question sits on screen), stops immediately.

Why the wakeup socket: a Python signal handler can never nest, so a
second SIGINT that lands while the first handler is still busy (mid
prompt) is deferred forever and never re-raises KeyboardInterrupt.
signal.set_wakeup_fd() is the escape hatch: the C layer writes one
byte into the socket for EVERY delivery, including deferred ones, so
the prompt's select() loop and a quick pre-prompt "pending" check both
catch a second tap reliably.

Windows and non-interactive sessions (pipes, scripts, tests, CI) get
the plain old behavior: no guard installed at all, KeyboardInterrupt
just propagates.
"""
import os
import select
import signal
import socket
import sys

from .tui import pause_progress

ABORT_EXIT_CODE = 130
_PROMPT = "\n  Ctrl+C received. Stop now? [y/N] "
_KEEP_LINE = "  keeping going"


class _Guard:
    def __init__(self, label, old):
        self.label = label
        self.old = old
        self.confirming = False


_state = None   # _Guard while armed; None otherwise
_wake = None    # (r, w) socketpair while armed on POSIX


def _interactive():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def install_guard(label="current command"):
    """Arm the Ctrl+C question around ONE command (interactive POSIX only)."""
    global _state, _wake
    if os.name == "nt" or not _interactive():
        return
    old = signal.signal(signal.SIGINT, handler)
    r, w = socket.socketpair()
    r.setblocking(False)   # _wake_pending() must never block on an empty socket
    w.setblocking(False)   # the C handler must never block on a full socket
    signal.set_wakeup_fd(w.fileno())
    _state = _Guard(label, old)
    _wake = (r, w)


def restore_guard():
    """Disarm the guard and put the default SIGINT behavior back."""
    global _state, _wake
    if _state is not None:
        try:
            signal.signal(signal.SIGINT, _state.old)
        except (ValueError, TypeError, OSError):
            pass
    if _wake is not None:
        try:
            signal.set_wakeup_fd(-1)
        except (ValueError, OSError):
            pass
        for s in _wake:
            try:
                s.close()
            except OSError:
                pass
    _state = None
    _wake = None


def _pop_wake():
    """Consume exactly ONE wakeup byte: this delivery's own."""
    if _wake is None:
        return False
    try:
        return bool(_wake[0].recv(1))
    except (BlockingIOError, OSError):
        return False


def _wake_pending():
    """True when another delivery's byte already waits: a double tap."""
    if _wake is None:
        return False
    while True:
        try:
            return bool(_wake[0].recv(1))
        except BlockingIOError:
            return False
        except OSError:
            return False


def _parse_line(line):
    """First line of a raw stdin read, stripped and lowercased."""
    first = line.split(b"\n")[0].split(b"\r")[0]
    return first.decode("utf-8", "replace").strip().lower()


def _read_confirm():
    """Wait for the y/N answer; a further wakeup byte or EOF stops at once.

    Reads raw stdin so no buffered line editor mid-state pollutes the
    next command, and only returns once the user pressed Enter.
    """
    r, _w = _wake
    line = b""
    while True:
        try:
            ready, _, _ = select.select([sys.stdin, r], [], [])
        except (OSError, ValueError, InterruptedError):
            raise KeyboardInterrupt
        if r in ready:
            raise KeyboardInterrupt   # Ctrl+C again while the question waits
        if sys.stdin in ready:
            try:
                chunk = os.read(sys.stdin.fileno(), 256)
            except OSError:
                raise KeyboardInterrupt
            if not chunk:
                raise KeyboardInterrupt   # EOF: the reader is gone for good
            line += chunk
            if b"\n" in line or b"\r" in line:
                return _parse_line(line)


def handler(signum, frame):
    """First Ctrl+C: ask. Second Ctrl+C (any timing): stop."""
    if _state is None:
        raise KeyboardInterrupt
    if _state.confirming:
        # defensive: re-entrant delivery on exotic runtimes
        raise KeyboardInterrupt
    _state.confirming = True
    pause_progress(True)
    try:
        if _wake is not None:
            _pop_wake()                      # this delivery's own byte
            if _wake_pending():              # a second tap already landed
                raise KeyboardInterrupt
            sys.stdout.write(_PROMPT)
            sys.stdout.flush()
            reply = _read_confirm()
        else:
            reply = input(_PROMPT)           # Windows/non-wakeup fallback
    finally:
        pause_progress(False)
        _state.confirming = False
    if reply and reply.strip().lower() in ("y", "yes"):
        raise KeyboardInterrupt
    print(_KEEP_LINE)


def abort_line(label=None):
    """The single clean line printed when the user confirms stopping."""
    label = label or (_state.label if _state is not None else None)
    tail = " \u00b7 %s" % label if label else ""
    try:
        from .tui import GRAY, WHITE, color_enabled, paint
        if color_enabled():
            head = paint("\u2718 stopped by user", WHITE, bold=True)
            print("%s%s" % (head, paint(tail, GRAY)))
            return
    except Exception:
        pass
    print("stopped by user (%s)" % label if label else "stopped by user")
