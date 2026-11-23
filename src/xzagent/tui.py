"""Tiny terminal-UX helpers: ANSI colors, gradient ranking bars and a live
indexing progress bar.

Pure stdlib, zero dependencies, and it degrades gracefully: when the output
stream is not a TTY (piped output, captured by tests, consumed by scripts or
the MCP server) or when NO_COLOR is set, every helper here either turns off
or falls back to the plain machine-friendly plain-text format the CLI has
always produced.
"""
import os
import re
import sys
import threading
import time

VIOLET = (139, 92, 246)   # #8b5cf6
CYAN = (34, 211, 238)     # #22d3ee
GREEN = (126, 231, 135)   # #7ee787
WHITE = (230, 237, 243)   # #e6edf3
GRAY = (139, 148, 158)    # #8b949e
BLUE = (121, 192, 255)    # #79c0ff
TRACK = (61, 68, 89)      # #3d4459: empty bar cells, visible on any dark bg

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
_ERASE = "\x1b[2K"

_SPINNER = ["\u280b", "\u2819", "\u2839", "\u2838", "\u283c", "\u2834",
             "\u2826", "\u2827", "\u2807", "\u280f"]
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_vt_ready = False
_SPIN_PAUSE = False   # frozen while the Ctrl+C y/N question owns the line


def pause_progress(paused):
    """Freeze/thaw every live spinner (the Ctrl+C question needs the line)."""
    global _SPIN_PAUSE
    _SPIN_PAUSE = bool(paused)


def _enable_windows_ansi():
    """Best-effort VT processing for legacy Windows consoles (no-op elsewhere)."""
    global _vt_ready
    if not _vt_ready and sys.platform == "win32":
        os.system("")  # known trick: flips ConHost into VT mode on Win10+
    _vt_ready = True


def color_enabled(stream=None):
    """True if the stream should get ANSI colors.

    FORCE_COLOR=1 overrides everything (CI screenshots, demos).
    NO_COLOR (any value) forces plain text, per https://no-color.org.
    Otherwise colors only go to an interactive TTY.
    """
    if os.environ.get("FORCE_COLOR"):
        return True
    _enable_windows_ansi()
    if "NO_COLOR" in os.environ:
        return False
    if os.name == "posix" and os.environ.get("TERM", "") in ("", "dumb"):
        return False
    stream = stream or sys.stdout
    try:
        return stream.isatty()
    except Exception:
        return False


def paint(text, rgb, bold=False):
    """Wrap text in a 24-bit foreground-color escape (visible widely)."""
    prefix = BOLD if bold else ""
    r, g, b = rgb
    return "%s\x1b[38;2;%d;%d;%dm%s%s" % (prefix, r, g, b, text, RESET)


def gradient_rgb(t):
    """Interpolate the brand gradient VIOLET -> CYAN for t in [0, 1]."""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return tuple(round(VIOLET[i] + (CYAN[i] - VIOLET[i]) * t) for i in range(3))


def bar(frac, width=12, colored=True):
    """Rounded line bar in the modern Rust/indicatif style.

    bar(1.0, 4, False) == '━━━╸', bar(0.5, 4, False) == '━╸╌╌': filled
    cells run the violet -> cyan gradient and end in a rounded head '\u2578',
    the remainder of the bar is a dotted track '\u254c' in dim gray, so the
    full column width is always visible on any dark background.
    """
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    filled = max(0, min(width, int(round(width * frac))))
    if not colored:
        if filled <= 0:
            return "\u254c" * width
        return "\u2501" * (filled - 1) + "\u2578" + "\u254c" * (width - filled)
    out = []
    for i in range(width):
        if i < filled:
            ch = "\u2578" if i == filled - 1 else "\u2501"
            rgb = gradient_rgb(i / float(width - 1)) if width > 1 else VIOLET
            out.append(paint(ch, rgb))
        else:
            out.append(paint("\u254c", TRACK))
    return "".join(out)


def visible_len(text):
    """Length of text without ANSI escapes (for alignment of colored lines)."""
    return len(_ANSI_RE.sub("", text))


def display_width(text):
    """Printed width ignoring ANSI escapes; East-Asian chars count as 2 cells."""
    import unicodedata
    w = 0
    for ch in _ANSI_RE.sub("", text):
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def pad_to(text, width):
    """Left-aligned text padded to a display width (ANSI-aware)."""
    return text + " " * max(width - display_width(text), 0)


def clip_right(text, width):
    """Truncate the tail of text with a visible ellipsis to fit `width` cells."""
    import unicodedata
    if display_width(text) <= width:
        return text
    out, w = [], 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if w + cw > width - 1:
            break
        out.append(ch)
        w += cw
    return "".join(out) + "\u2026"


def clip_left(text, width):
    """Keep the tail of text (long file paths), prefix a visible ellipsis."""
    if display_width(text) <= width:
        return text
    return "\u2026" + text[len(text) - (width - 1):]


class Progress:
    """Live one-line progress/spinner on ttys, drawn on stderr.

    Streams stay clean: writes go to stderr, so piped stdout output (scripts,
    tests, MCP, `ctxp ask | grep`) is byte-for-byte what it always was.
    Silent entirely when stderr is not a TTY.

    A tiny daemon ticker keeps the spinner breathing while work is in
    flight, so the CLI never looks frozen even if a single huge file or a
    slow phase produces no callback events for a while.
    """

    def __init__(self, stream=None, min_interval=0.05, tick=0.1):
        self.stream = stream if stream is not None else sys.stderr
        self.min_interval = min_interval
        self.tick = tick
        self.colors = color_enabled(self.stream)
        try:
            self.enabled = bool(self.stream.isatty())
        except Exception:
            self.enabled = False
        self._frame = 0
        self._last = 0.0
        self._state = None          # (done, total, note)
        self._vlen = 0
        self._drawn = False
        self._lock = threading.Lock()
        self._stop = None
        self._thread = None

    def update(self, done, total, note=None):
        """Report progress; safe to call for every single file (throttled).

        total==0 (falsy) means an indeterminate phase: the title is the note.
        """
        if not self.enabled:
            return
        with self._lock:
            self._state = (done, total, note)
            self._maybe_draw()

    def start(self):
        """Launch the guaranteed-animation ticker thread (idempotent)."""
        if not self.enabled or self._thread is not None:
            return
        self._stop = threading.Event()

        def loop():
            while not self._stop.wait(self.tick):
                if _SPIN_PAUSE:
                    continue
                with self._lock:
                    if self._state is not None:
                        self._maybe_draw(force=True)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        self._thread = t

    def finish(self):
        """Stop the ticker and erase the progress line (no-op if never drawn)."""
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            if self.enabled and self._drawn:
                try:
                    self.stream.write("\r" + _ERASE + " " * self._vlen + "\r" + _ERASE)
                    self.stream.flush()
                except Exception:
                    pass
            self._drawn = False
            self._state = None
            self._vlen = 0

    def _maybe_draw(self, force=False):
        now = time.monotonic()
        if not force and now - self._last < self.min_interval:
            return
        self._last = now
        self._frame = (self._frame + 1) % len(_SPINNER)
        done, total, note = self._state
        if not total:
            line = "  %s %s ..." % (_SPINNER[self._frame], note or "working")
        else:
            pct = int(done * 100 // total)
            line = "  %s %s %5d/%d  %s %3d%%" % (
                _SPINNER[self._frame], note or "indexing", done, total,
                bar(done / float(total), 18, colored=self.colors), pct)
        pad = " " * max(self._vlen - visible_len(line), 0)
        try:
            self.stream.write("\r" + _ERASE + line + pad)
            self.stream.flush()
        except Exception:
            pass
        self._vlen = visible_len(line)
        self._drawn = True