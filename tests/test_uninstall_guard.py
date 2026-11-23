"""Tests for the Ctrl+C confirmation guard, ctxp uninstall, and the
professional help surfaces (v0.2.33)."""
import io
import os
import signal
import socket
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from xzagent import selfupdate, uninstall as uninst
from xzagent import interrupt
from xzagent.cli import (ABORT_EXIT_CODE, _get_subparser, build_parser,
                         cmd_help, cmd_uninstall, main)


def _run_module(fn, *a, **kw):
    """Run a function, capturing everything it prints to stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


def _arm(label="ctxp test"):
    """Arm the guard state by hand (no real signal wiring)."""
    interrupt._state = interrupt._Guard(label, signal.default_int_handler)


def _arm_wake():
    """Arm with a real wakeup socketpair; fully cleaned up afterwards."""
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    interrupt._wake = (a, b)

    def _cleanup():
        a.close()
        b.close()
        interrupt._wake = None
        interrupt._state = None
    return a, b, _cleanup


class TestCtrlCGuard(unittest.TestCase):

    def tearDown(self):
        interrupt.restore_guard()
        interrupt._state = None
        interrupt._wake = None

    def test_n_answer_keeps_going(self):
        _arm()
        with mock.patch("builtins.input", return_value="n"):
            out, printed = _run_module(interrupt.handler, signal.SIGINT, None)
        # the question itself goes through input(prompt) to the real tty,
        # never through a captured pipe: only the resume line is printed
        self.assertIn("keeping going", printed)

    def test_enter_keeps_going(self):
        _arm()
        with mock.patch("builtins.input", return_value=""):
            _run_module(interrupt.handler, signal.SIGINT, None)
        # reaching this line means: no KeyboardInterrupt raised

    def test_y_answer_stops(self):
        _arm("ctxp update")
        with mock.patch("builtins.input", return_value="y"):
            with self.assertRaises(KeyboardInterrupt):
                interrupt.handler(signal.SIGINT, None)

    def test_yes_word_stops(self):
        _arm()
        with mock.patch("builtins.input", return_value="YES"):
            with self.assertRaises(KeyboardInterrupt):
                interrupt.handler(signal.SIGINT, None)

    def test_double_tap_stops_without_asking(self):
        # two taps landing together: two wakeup bytes sit in the socket
        # before the handler even starts. It must stop instead of asking.
        a, b, cleanup = _arm_wake()
        self.addCleanup(cleanup)
        _arm()
        b.send(b"\x00\x00")
        buf = io.StringIO()
        with redirect_stdout(buf):
            with mock.patch("builtins.input") as inp:
                with self.assertRaises(KeyboardInterrupt):
                    interrupt.handler(signal.SIGINT, None)
        inp.assert_not_called()
        self.assertNotIn("Stop now?", buf.getvalue())

    def test_single_tap_only_consumes_its_own_byte(self):
        # one tap: exactly one byte in the socket; the handler pops it
        # and (finding nothing pending) proceeds to ask the question
        a, b, cleanup = _arm_wake()
        self.addCleanup(cleanup)
        _arm()
        b.send(b"\x00")
        with mock.patch.object(interrupt, "_read_confirm", return_value="n"):
            with mock.patch.object(interrupt, "pause_progress"):
                _run_module(interrupt.handler, signal.SIGINT, None)
        # and the wakeup socket is empty again: no leaked byte defuses
        # the NEXT double tap
        self.assertFalse(interrupt._wake_pending())

    def test_new_byte_wakes_read_confirm(self):
        # a tap while the question sits on screen: _read_confirm must
        # see the fresh wakeup byte and abort instead of waiting
        a, b, cleanup = _arm_wake()
        self.addCleanup(cleanup)
        b.send(b"\x00")
        with mock.patch.object(interrupt.select, "select", return_value=[a]):
            with self.assertRaises(KeyboardInterrupt):
                interrupt._read_confirm()

    def test_eof_at_prompt_stops(self):
        a, b, cleanup = _arm_wake()
        self.addCleanup(cleanup)
        devnull = open(os.devnull, "rb")

        class _F:
            def fileno(self):
                return devnull.fileno()
        try:
            with mock.patch.object(sys, "stdin", _F()):
                with self.assertRaises(KeyboardInterrupt):
                    interrupt._read_confirm()
        finally:
            devnull.close()

    def test_parse_line_takes_first_enter_terminated_line(self):
        self.assertEqual(interrupt._parse_line(b"y\njunk"), "y")
        self.assertEqual(interrupt._parse_line(b"  NO\r\nx"), "no")
        self.assertEqual(interrupt._parse_line(b"\n"), "")

    def test_spinner_frozen_during_question(self):
        _arm()
        with mock.patch.object(interrupt, "pause_progress") as pp:
            with mock.patch("builtins.input", return_value="n"):
                _run_module(interrupt.handler, signal.SIGINT, None)
        self.assertEqual([c[0][0] for c in pp.call_args_list], [True, False])

    def test_handler_without_state_raises(self):
        interrupt._state = None
        with self.assertRaises(KeyboardInterrupt):
            interrupt.handler(signal.SIGINT, None)

    def test_noninteractive_guard_not_installed(self):
        class _Notty:
            def isatty(self):
                return False
            def fileno(self):
                raise OSError("no fd")

        fake_in, fake_out = _Notty(), _Notty()
        for stdin_tty, stdout_tty in ((False, True), (True, False),
                                      (False, False)):
            class _In(_Notty):
                def isatty(self):
                    return stdin_tty

            class _Out(_Notty):
                def isatty(self):
                    return stdout_tty
            with mock.patch.object(sys, "stdin", _In()), \
                 mock.patch.object(sys, "stdout", _Out()):
                interrupt.install_guard("x")
            self.assertIsNone(interrupt._state)

    def test_restore_guard_cleans_state(self):
        a, b, cleanup = _arm_wake()
        _arm()
        interrupt.restore_guard()
        self.assertIsNone(interrupt._state)
        self.assertIsNone(interrupt._wake)
        with self.assertRaises(OSError):
            a.recv(1)

    def test_abort_line_plain(self):
        interrupt._state = None
        printed = _run_module(interrupt.abort_line, None)[1]
        self.assertIn("stopped by user", printed)

    def test_abort_line_with_label(self):
        interrupt._state = None
        printed = _run_module(interrupt.abort_line, "ctxp update")[1]
        self.assertIn("stopped by user", printed)
        self.assertIn("ctxp update", printed)


class TestUninstall(unittest.TestCase):

    def _runner(self, rc=0, out="Successfully uninstalled xzagent"):
        calls = []

        def runner(cmd):
            calls.append(cmd)
            return rc, out
        return calls, runner

    def test_cancel_removes_nothing(self):
        calls, runner = self._runner()
        rc, printed = _run_module(
            uninst.uninstall, yes=False, asker=lambda p: "n", runner=runner)
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])
        self.assertIn("cancelled", printed)
        self.assertIn("untouched", printed)

    def test_success_line_and_pip_command(self):
        calls, runner = self._runner()
        rm = []
        rc, printed = _run_module(
            uninst.uninstall, yes=True, purge=False, runner=runner,
            rmtree=lambda: rm.append(1))
        self.assertEqual(rc, 0)
        self.assertEqual(
            calls, [[sys.executable, "-m", "pip", "uninstall", "-y", "xzagent"]])
        self.assertEqual(rm, [])          # no purge: config dir stays
        self.assertIn("Uninstalled xzagent", printed)
        self.assertIn("pip install git+https://github.com/shadow1t/xZagent.git",
                      printed)

    def test_purge_removes_config(self):
        calls, runner = self._runner()
        rm = []
        rc, printed = _run_module(
            uninst.uninstall, yes=True, purge=True, runner=runner,
            rmtree=lambda: rm.append(1))
        self.assertEqual(rc, 0)
        self.assertEqual(rm, [1])         # purge: config dir removed
        self.assertIn("~/.xzagent", printed)

    def test_pip_failure_reports_tail(self):
        calls, runner = self._runner(rc=1, out="WARNING: skipping\nboom")
        rm = []
        rc, printed = _run_module(
            uninst.uninstall, yes=True, runner=runner,
            rmtree=lambda: rm.append(1))
        self.assertEqual(rc, 1)
        self.assertIn("pip uninstall failed", printed)
        self.assertIn("boom", printed)
        self.assertEqual(rm, [])

    def test_windows_handoff_spawns_child(self):
        calls, runner = self._runner()
        specs = []
        with mock.patch.dict(os.environ, {"XZAGENT_UPDATE_HANDOFF": "1"}):
            rc, printed = _run_module(
                uninst.uninstall, yes=True, runner=runner,
                spawn_child=lambda spec: specs.append(spec))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])       # pip runs in the child, not here
        self.assertEqual(specs, ["uninstall"])
        self.assertIn("Uninstall in progress", printed)

    def test_windows_retire_then_uninstall_in_place(self):
        calls, runner = self._runner()
        specs = []
        with mock.patch.dict(os.environ, {"XZAGENT_UPDATE_HANDOFF": "1",
                                          "XZAGENT_UPDATE_INPLACE": "1"}):
            rc, printed = _run_module(
                uninst.uninstall, yes=True, runner=runner,
                retire=lambda: True,
                spawn_child=lambda spec: specs.append(spec))
        self.assertEqual(rc, 0)
        self.assertEqual(specs, [])       # exe was renamed aside: no handoff
        self.assertEqual(
            calls, [[sys.executable, "-m", "pip", "uninstall", "-y", "xzagent"]])

    def test_child_uninstall_runs_pip_for_dead_parent(self):
        calls, runner = self._runner()
        rm = []
        rc, printed = _run_module(
            uninst.child_uninstall, purge=True, ppid="999999999",
            runner=runner, rmtree=lambda: rm.append(1))
        self.assertEqual(rc, 0)
        self.assertEqual(rm, [1])
        self.assertIn("Uninstalled xzagent", printed)


class TestWiring(unittest.TestCase):

    def test_selfupdate_child_finish_routes_uninstall(self):
        seen = []
        with mock.patch.object(uninst, "child_uninstall",
                               side_effect=lambda purge=False, ppid=None:
                               seen.append((purge, ppid)) or 0):
            self.assertEqual(selfupdate.child_finish("uninstall", "321"), 0)
            self.assertEqual(selfupdate.child_finish("uninstall-purge", "322"), 0)
        self.assertEqual(seen, [(False, "321"), (True, "322")])

    def test_main_update_child_routes_spec(self):
        seen = []
        with mock.patch("xzagent.cli.child_finish",
                       side_effect=lambda spec, ppid, label=None:
                       seen.append((spec, ppid)) or 0):
            rc = main(["__update-child", "uninstall", "123"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("uninstall", "123")])

    def test_cli_passes_uninstall_flags(self):
        args = build_parser().parse_args(["uninstall", "--yes", "--purge"])
        self.assertTrue(args.yes)
        self.assertTrue(args.purge)
        self.assertIs(args.func, cmd_uninstall)

    def test_main_installs_guard_around_commands(self):
        with mock.patch("xzagent.cli.cmd_map", return_value=0), \
             mock.patch("xzagent.cli.install_guard") as ig, \
             mock.patch("xzagent.cli.restore_guard") as rg:
            rc = main(["map", "."])
        self.assertEqual(rc, 0)
        ig.assert_called_once_with("map")
        rg.assert_called_once()

    def test_main_serve_never_installs_guard(self):
        with mock.patch("xzagent.cli.cmd_serve", return_value=0), \
             mock.patch("xzagent.cli.install_guard") as ig, \
             mock.patch("xzagent.cli.restore_guard") as rg:
            rc = main(["serve", "."])
        self.assertEqual(rc, 0)
        ig.assert_not_called()
        rg.assert_not_called()

    def test_main_keyboardinterrupt_exits_130(self):
        def boom(args):
            raise KeyboardInterrupt
        with mock.patch("xzagent.cli.cmd_map", side_effect=boom), \
             mock.patch("xzagent.cli.install_guard"), \
             mock.patch("xzagent.cli.restore_guard"), \
             mock.patch("xzagent.cli.abort_line") as al:
            rc = main(["map", "."])
        self.assertEqual(rc, ABORT_EXIT_CODE)
        al.assert_called_once_with("map")


class TestHelpSurface(unittest.TestCase):

    def test_every_command_has_examples_in_its_help(self):
        for name in ("index", "map", "ask", "find", "pack", "graph", "schema",
                     "setup", "update", "uninstall", "help"):
            sub = _get_subparser(name)
            self.assertIsNotNone(sub, name)
            self.assertIn("Examples:", sub.format_help(), name)
        # serve has no example lines by design; its help shows the
        # client-launch flow instead
        self.assertIn("launch it through an MCP client",
                      _get_subparser("serve").format_help())

    def test_bare_help_is_the_curated_overview(self):
        rc, printed = _run_module(
            cmd_help, type("A", (), {"topic": None})())
        self.assertEqual(rc, 0)
        for marker in ("HOW THE COMMANDS FIT TOGETHER", "TYPICAL WORKFLOW",
                       "THE FASTEST WAY TO LEARN A COMMAND", "--reindex",
                       "uninstall", "Ctrl+C"):
            self.assertIn(marker, printed)

    def test_dash_h_shows_the_same_overview(self):
        printed = build_parser().format_help()
        for marker in ("HOW THE COMMANDS FIT TOGETHER", "TYPICAL WORKFLOW",
                       "NICE TO KNOW"):
            self.assertIn(marker, printed)

    def test_topic_help_for_find(self):
        rc, printed = _run_module(
            cmd_help, type("A", (), {"topic": "find"})())
        self.assertEqual(rc, 0)
        self.assertIn("--symbol", printed)
        self.assertIn("--only-dirs", printed)
        self.assertIn("Examples:", printed)
        # identical to the subparser's own -h: no drift between surfaces
        self.assertEqual(printed, _get_subparser("find").format_help())

    def test_unknown_topic_is_a_clean_error(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()):
                cmd_help(type("A", (), {"topic": "nosuch"})())
        # a message string exit: printed to stderr, shell sees exit 1
        self.assertIsInstance(ctx.exception.code, str)
        self.assertIn("unknown command", ctx.exception.code)

    def test_uninstall_help_mentions_data_safety(self):
        printed = _get_subparser("uninstall").format_help()
        self.assertIn("Project data is never touched", printed)
        self.assertIn("--purge", printed)
        self.assertIn("git+https://github.com/shadow1t/xZagent.git", printed)

    def test_help_command_parses(self):
        args = build_parser().parse_args(["help", "find"])
        self.assertEqual(args.topic, "find")


if __name__ == "__main__":
    unittest.main()
