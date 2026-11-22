import io
import os
import unittest
from contextlib import redirect_stdout

from xzagent.cli import main
from xzagent.tui import CYAN, VIOLET, Progress, bar, clip_left, clip_right, color_enabled, display_width, gradient_rgb, paint, pad_to, visible_len

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)


class TestBarBasics(unittest.TestCase):
    def test_plain_bar_fill_widths(self):
        self.assertEqual(bar(0.0, 4, colored=False), "\u2591" * 4)
        self.assertEqual(bar(1.0, 4, colored=False), "\u2588" * 4)
        self.assertEqual(bar(0.5, 4, colored=False), "\u2588" * 2 + "\u2591" * 2)
        self.assertEqual(bar(0.75, 4, colored=False), "\u2588" * 3 + "\u2591")

    def test_plain_bar_clamps(self):
        self.assertEqual(bar(-1.0, 3, colored=False), "\u2591" * 3)
        self.assertEqual(bar(2.0, 3, colored=False), "\u2588" * 3)


    def test_track_cells_use_visible_gray(self):
        s = bar(0.0, 4, colored=True)
        self.assertIn("38;2;61;68;89", s)   # visible on darwest backgrounds

    def test_colored_bar_has_ansi_and_blocks(self):
        s = bar(1.0, 6, colored=True)
        self.assertIn("\x1b[38;2;", s)
        self.assertEqual(s.count("\u2588"), 6)
        self.assertNotIn("\u2591", s)

    def test_gradient_endpoints(self):
        self.assertEqual(gradient_rgb(0.0), VIOLET)
        self.assertEqual(gradient_rgb(1.0), CYAN)
        mid = gradient_rgb(0.5)
        self.assertTrue(all(min(a, b) <= m <= max(a, b)
                            for m, a, b in zip(mid, VIOLET, CYAN)))

    def test_paint_and_visible_len(self):
        s = paint("abc", VIOLET, bold=True)
        self.assertTrue(s.startswith("\x1b[1m"))
        self.assertIn("abc", s)
        self.assertTrue(s.endswith("\x1b[0m"))
        self.assertEqual(visible_len(s), 3)

    def test_display_width_counts_wide_chars_and_skips_ansi(self):
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("\u4ee3\u7801"), 4)  # 2 CJK chars = 4 cells
        self.assertEqual(display_width(paint("ab", CYAN)), 2)

    def test_clip_right_ellipsizes(self):
        self.assertEqual(clip_right("abcdefgh", 5), "abcd\u2026")
        self.assertEqual(clip_right("short", 10), "short")

    def test_clip_left_keeps_tail(self):
        self.assertEqual(clip_left("really/long/path/file.py", 14), "\u2026/path/file.py")

    def test_pad_to_respects_colored_text(self):
        self.assertEqual(pad_to(paint("ab", CYAN), 5).replace("\x1b[0m", ""),
                        paint("ab", CYAN).replace("\x1b[0m", "") + "   ")


class _FakeTty(object):
    def __init__(self):
        self.chunks = []

    def isatty(self):
        return True

    def write(self, s):
        self.chunks.append(s)

    def flush(self):
        pass


class TestProgress(unittest.TestCase):
    def test_silent_when_not_a_tty(self):
        buf = io.StringIO()  # isatty() -> False
        p = Progress(stream=buf, min_interval=0.0)
        self.assertFalse(p.enabled)
        p.update(1, 10, "indexing")
        p.finish()
        self.assertEqual(buf.getvalue(), "")

    def test_draws_and_erases_on_tty(self):
        env = dict(os.environ, FORCE_COLOR="1")
        old = os.environ
        os.environ.clear()
        os.environ.update(env)
        try:
            ttyp = _FakeTty()
            p = Progress(stream=ttyp, min_interval=0.0)
            self.assertTrue(p.enabled)
            p.update(3, 10, "indexing")
            out = "".join(ttyp.chunks)
            self.assertIn("3/10", out)
            self.assertIn("30%", out)
            self.assertIn("\u2588", out)  # gradient bar present
            self.assertIn("\r", out)      # redraws on the same line
            p.finish()
            out2 = "".join(ttyp.chunks)
            self.assertIn("\x1b[2K", out2)  # erased before the summary
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_spinner_mode_without_total(self):
        ttyp = _FakeTty()
        p = Progress(stream=ttyp, min_interval=0.0)
        p.update(0, 0, "scanning files")
        out = "".join(ttyp.chunks)
        self.assertIn("scanning files", out)
        self.assertNotIn("/", out)


class TestColorEnvAndCliModes(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project(self.tmp.name)
        self.db = os.path.join(self.root, ".xzagent", "index.db")
        self._before = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._before)
        self.tmp.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_no_color_env_disables(self):
        os.environ["NO_COLOR"] = "1"
        self.assertFalse(color_enabled())

    def test_force_color_env_enables(self):
        os.environ["FORCE_COLOR"] = "1"
        self.assertTrue(color_enabled())

    def test_piped_output_stays_plain(self):
        self._run(["--db", self.db, "index", self.root])
        rc, out = self._run(["--db", self.db, "ask", self.root, "login", "--top", "3"])
        self.assertEqual(rc, 0)
        self.assertNotIn("\x1b[", out)
        self.assertIn("app/auth.py", out)

    def test_forced_color_ask_shows_bars(self):
        self._run(["--db", self.db, "index", self.root])
        os.environ["FORCE_COLOR"] = "1"
        rc, out = self._run(["--db", self.db, "ask", self.root, "login", "--top", "3"])
        self.assertEqual(rc, 0)
        self.assertIn("app/auth.py", out)
        self.assertIn("\u2588", out)          # ranking bar blocks in the rows
        self.assertIn("\x1b[38;2;", out)      # truecolor escape present

    def test_forced_color_find_keeps_data(self):
        self._run(["--db", self.db, "index", self.root])
        os.environ["FORCE_COLOR"] = "1"
        rc, out = self._run(["--db", self.db, "find", self.root, "auth*"])
        self.assertEqual(rc, 0)
        self.assertIn("app/auth.py", out.replace("\x1b[0m", ""))

    def test_ask_rows_fit_narrow_terminal(self):
        self._run(["--db", self.db, "index", self.root])
        os.environ["FORCE_COLOR"] = "1"
        os.environ["COLUMNS"] = "70"  # narrow: reasons must clip, rows must not wrap
        rc, out = self._run(["--db", self.db, "ask", self.root, "login", "--top", "3"])
        self.assertEqual(rc, 0)
        from xzagent.tui import display_width
        for line in out.splitlines():
            self.assertLessEqual(display_width(line), 70,
                                 "row wider than terminal: %r" % line)
        self.assertIn("\u2588", out)  # bars still present


if __name__ == "__main__":
    unittest.main()