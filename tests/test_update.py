import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)

from xzagent.cli import main
from xzagent.selfupdate import (DEFAULT_GIT_URL, child_finish, config_path,
                                detect_clone, resolve_source,
                                selfupdate as run_selfupdate)


class _TmpCfg(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(os.environ,
                                    {"XZAGENT_CONFIG_DIR": self.tmp.name})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self.tmp.cleanup)  # not cleanup(): pass, don't call

    # run from a neutral directory so detect_clone() never finds the repo
    # the tests themselves live in
    def _chdir_tmp(self):
        neutral = os.path.join(self.tmp.name, "neutral")
        os.makedirs(neutral, exist_ok=True)
        old = os.getcwd()
        os.chdir(neutral)
        self.addCleanup(os.chdir, old)

    def _read_cfg(self):
        with open(config_path(), encoding="utf-8") as fh:
            return json.load(fh)

    def _run_plain(self, argv):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            with redirect_stdout(buf):
                rc = main(argv)
        return rc, buf.getvalue()


def _fake_run(rc=0, out="", calls=None):
    def runner(cmd, note=None, progress=None):
        if calls is not None:
            calls.append(list(cmd))
        if progress is not None and note:
            progress(0, 0, note)
        return rc, out
    return runner


def _fake_installed(version):
    return mock.patch("xzagent.selfupdate._installed_version",
                      return_value=version)


class TestResolveSource(_TmpCfg):
    def _clone(self, name="clone"):
        root = os.path.join(self.tmp.name, name)
        os.makedirs(root)
        with open(os.path.join(root, "pyproject.toml"), "w",
                  encoding="utf-8") as fh:
            fh.write('[project]\nname = "xzagent"\nversion = "0.0.1"\n')
        return root

    def test_explicit_dir_wins(self):
        d = self._clone()
        self.assertEqual(resolve_source(d), ("path", d))

    def test_missing_dir_is_an_error(self):
        with self.assertRaises(SystemExit):
            resolve_source(os.path.join(self.tmp.name, "nope"))

    def test_detect_clone_from_subdirectory(self):
        d = self._clone()
        sub = os.path.join(d, "src", "xzagent")
        os.makedirs(sub)
        self.assertEqual(detect_clone(sub), d)

    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    @_fake_installed("9.9.9")
    def test_saved_source_is_remembered(self, _v, _h):
        d = self._clone("saved")
        rc, out = self._run_plain(["update", d])
        self.assertEqual(rc, 0)
        # plain `ctxp update` in a neutral dir now resolves to the saved clone
        self._chdir_tmp()
        self.assertEqual(resolve_source(), ("path", d))

    def test_neutral_dir_falls_back_to_git_url(self):
        self._chdir_tmp()
        self.assertEqual(resolve_source(), ("git", DEFAULT_GIT_URL))

    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    @_fake_installed("9.9.9")
    def test_git_mode_is_remembered_too(self, _v, _h):
        self._chdir_tmp()
        rc, out = self._run_plain(["update"])
        self.assertEqual(rc, 0)
        cfg = self._read_cfg()["update_source"]
        self.assertEqual(cfg, {"kind": "git", "value": DEFAULT_GIT_URL})


class TestSelfupdatePosix(_TmpCfg):
    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    @_fake_installed("9.9.9")
    def test_posix_flow_installs_and_reports(self, _v, _h):
        self._chdir_tmp()
        rc, out = self._run_plain(["update"])
        self.assertEqual(rc, 0)
        self.assertIn("Updated xzagent", out)
        self.assertIn("9.9.9", out)
        # git mode: the plain report names the GitHub source
        self.assertIn(DEFAULT_GIT_URL, out)

    def test_pip_invocation_shape(self):
        """Direct call: pip gets --force-reinstall/--no-deps/--no-cache-dir."""
        self._chdir_tmp()
        calls = []
        with mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok", calls)), \
                mock.patch("xzagent.selfupdate._needs_handoff",
                           return_value=False):
            rc = run_selfupdate()
        self.assertEqual(rc, 0)
        pip = [c for c in calls if c[1:3] == ["-m", "pip"]]
        self.assertEqual(len(pip), 1)
        self.assertIn("install", pip[0])
        for flag in ("--force-reinstall", "--no-deps", "--no-cache-dir"):
            self.assertIn(flag, pip[0])
        self.assertTrue(pip[0][-1].startswith("git+"))

    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    @_fake_installed("9.9.9")
    def test_progress_cb_gets_phase_notes(self, _v, _h):
        """progress is progress(done, total, note) — same signature the
        indexer uses; cmd_update passes Progress.update straight in."""
        self._chdir_tmp()
        notes = []

        def cb(done, total, note):
            notes.append((done, total, note))

        rc = run_selfupdate(progress=cb)
        self.assertEqual(rc, 0)
        self.assertIn("installing via pip", [n[2] for n in notes])

    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    @_fake_installed("9.9.9")
    def test_posix_path_mode_pulls_then_installs(self, _v, _h):
        root = os.path.join(self.tmp.name, "clone")
        os.makedirs(os.path.join(root, ".git"))
        calls = []
        with mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok", calls)):
            rc, out = self._run_plain(["update", root])
        self.assertEqual(rc, 0)
        git = [c for c in calls if c and c[0] == "git"]
        self.assertEqual(git[0][:2], ["git", "-C"])
        self.assertEqual(git[0][-1], "--ff-only")
        pip = [c for c in calls if c[1:3] == ["-m", "pip"]]
        self.assertEqual(pip[0][-1], root)  # installs FROM the clone folder

    @mock.patch("xzagent.selfupdate._run", _fake_run(1, "boom\nERROR: nope"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    def test_pip_failure_exits_nonzero(self, _h):
        self._chdir_tmp()
        rc, out = self._run_plain(["update"])
        self.assertEqual(rc, 1)
        self.assertIn("error: pip install failed", out)
        self.assertIn("ERROR: nope", out)
        self.assertIn("ctxp update", out)

    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    def test_git_pull_failure_aborts(self, _h):
        root = os.path.join(self.tmp.name, "clone2")
        os.makedirs(os.path.join(root, ".git"))

        def runner(cmd, note=None, progress=None):
            if cmd and cmd[0] == "git":
                return 1, "merge conflict"
            return 0, "ok"

        self.patched = mock.patch("xzagent.selfupdate._run", runner)
        self.patched.start()
        self.addCleanup(self.patched.stop)
        rc, out = self._run_plain(["update", root])
        self.assertEqual(rc, 1)
        self.assertIn("error: git pull failed", out)
        self.assertIn("merge conflict", out)

    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    @_fake_installed("9.9.9")
    def test_no_pull_skips_git(self, _v, _h):
        root = os.path.join(self.tmp.name, "clone3")
        os.makedirs(os.path.join(root, ".git"))
        calls = []
        with mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok", calls)):
            rc, out = self._run_plain(["update", root, "--no-pull"])
        self.assertEqual(rc, 0)
        self.assertEqual([c for c in calls if c and c[0] == "git"], [])


class TestWindowsHandoffAndChild(_TmpCfg):
    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=True)
    def test_windows_spawns_child_and_exits(self, _h):
        self._chdir_tmp()
        spawned = {}
        with mock.patch("xzagent.selfupdate.subprocess.Popen",
                        side_effect=lambda a, **k: spawned.setdefault("argv", a)):
            rc, out = self._run_plain(["update"])
        self.assertEqual(rc, 0)
        self.assertIn("Update in progress", out)
        argv = spawned["argv"]
        self.assertEqual(argv[1:4], ["-m", "xzagent", "__update-child"])
        self.assertTrue(argv[4].startswith("git+"))
        self.assertEqual(argv[5], str(os.getpid()))

    @_fake_installed("9.9.9")
    def test_child_finishes_after_parent_gone(self, _v):
        calls = []
        with mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok", calls)):
            # pid 999999999 does not exist: the wait loop breaks immediately
            rc = child_finish("git+https://example/repo.git", "999999999")
        self.assertEqual(rc, 0)
        self.assertTrue(calls and calls[0][1:3] == ["-m", "pip"])
        self.assertEqual(calls[0][-1], "git+https://example/repo.git")

    @mock.patch("xzagent.selfupdate._run", _fake_run(1, "ERROR: bad"))
    def test_child_reports_failure(self):
        rc = child_finish("/some/spec", "999999999")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

class TestRunWithDrivesProgressCallback(_TmpCfg):
    """Regression: `ctxp update` crashed with AttributeError because _run()
    called progress.update(...) while progress is the same
    progress(done, total, note) hook every other phase uses. The pull
    path only runs when the source is a real git clone, which is why it
    survived stubbed tests and surfaced on a real Windows machine."""

    def test_run_drives_progress_callback(self):
        from xzagent.selfupdate import _run
        notes = []

        def cb(done, total, note):
            notes.append((done, total, note))

        with mock.patch("xzagent.selfupdate.subprocess.run") as sr:
            sr.return_value = mock.Mock(returncode=0, stdout="ok")
            rc, out = _run(["git", "pull"], note="pulling latest changes",
                           progress=cb)
        self.assertEqual(rc, 0)
        self.assertEqual(notes, [(0, 0, "pulling latest changes")])

    def test_real_subprocess_reaches_the_callback(self):
        """Same bug, but through a REAL spawned process on PATH."""
        import os
        import shutil
        import stat
        import tempfile
        binroot = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, binroot, True)
        script = os.path.join(binroot, "git")
        with open(script, "w", encoding="utf-8") as fh:
            import sys
            fh.write("#!%s\nprint('Already up to date.')\n" % sys.executable)
        os.chmod(script, 0o755)
        cur_path = os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": binroot + os.pathsep
                                          + cur_path}):
            from xzagent.selfupdate import _pull_latest
            notes = []

            def cb(done, total, note):
                notes.append(note)

            root = os.path.join(self.tmp.name, "aclone")
            os.makedirs(os.path.join(root, ".git"))
            ok, out = _pull_latest(root, progress=cb)
        self.assertTrue(ok)
        self.assertIn("Already up to date.", out)
        self.assertEqual(notes, ["pulling latest changes"])

class TestHandoffOverrideAndLabels(_TmpCfg):
    @mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
    def test_env_override_forces_handoff(self):
        """XZAGENT_UPDATE_HANDOFF=1 exercises the Windows flow anywhere."""
        self._chdir_tmp()
        spawned = {}
        with mock.patch.dict(os.environ,
                             {"XZAGENT_UPDATE_HANDOFF": "1"}), \
                mock.patch("xzagent.selfupdate.subprocess.Popen",
                           side_effect=lambda a, **k:
                           spawned.setdefault("argv", a)):
            rc, out = self._run_plain(["update"])
        self.assertEqual(rc, 0)
        self.assertIn("Update in progress", out)
        argv = spawned["argv"]
        self.assertEqual(argv[1:4], ["-m", "xzagent", "__update-child"])
        self.assertTrue(argv[4].startswith("git+"))  # spec
        self.assertTrue(argv[5].isdigit())           # ppid
        self.assertIn("main", argv[6])               # label

    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    def test_child_report_uses_label(self, _h):
        self._chdir_tmp()
        with mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok")), \
                mock.patch("xzagent.selfupdate._installed_version",
                           return_value="9.9.9"), \
                mock.patch("xzagent.selfupdate._report",
                           return_value=None) as rep:
            rc = child_finish("/some/spec", "999999999", "mylabel")
        self.assertEqual(rc, 0)
        rep.assert_called_once()
        self.assertEqual(rep.call_args[0][2], "mylabel")

    def test_version_has_one_source_of_truth(self):
        """pyproject reads the version from xzagent.__version__, so pip
        wheels and `ctxp --version` can never disagree again."""
        import xzagent
        with open(os.path.join(os.path.dirname(xzagent.__file__), "..",
                               "..", "pyproject.toml"),
                  encoding="utf-8") as fh:
            pp = fh.read()
        self.assertIn('dynamic = ["version"]', pp)
        self.assertIn('xzagent.__version__', pp)
        self.assertNotIn('version = "0.', pp)

class TestUpdateLock(_TmpCfg):
    """Two ctxp updates racing each other must not double-install."""

    def test_lock_is_exclusive_then_released(self):
        from xzagent.selfupdate import (_acquire_update_lock,
                                        _release_update_lock)
        self.assertTrue(_acquire_update_lock())
        self.assertFalse(_acquire_update_lock())   # a second updater stands down
        _release_update_lock()
        self.assertTrue(_acquire_update_lock())    # and can run afterwards

    def test_release_without_lock_is_harmless(self):
        from xzagent.selfupdate import _release_update_lock
        _release_update_lock()  # no exception

    def test_stale_lock_is_taken_over(self):
        import time as _t
        from xzagent.selfupdate import (_acquire_update_lock,
                                        _release_update_lock,
                                        _update_lock_path)
        self.assertTrue(_acquire_update_lock())
        old = _t.time() - 700                    # crashed updater, 11 min ago
        os.utime(_update_lock_path(), (old, old))
        self.assertTrue(_acquire_update_lock())  # takeover, not a deadlock
        _release_update_lock()

    @mock.patch("xzagent.selfupdate._needs_handoff", return_value=False)
    def test_second_update_is_a_noop(self, _h):
        """While one updater holds the lock, another prints and exits 0."""
        from xzagent.selfupdate import (_acquire_update_lock,
                                        _release_update_lock)
        self._chdir_tmp()
        self.assertTrue(_acquire_update_lock())
        self.addCleanup(_release_update_lock)
        self.patched = mock.patch("xzagent.selfupdate._run", _fake_run(0, "ok"))
        self.patched.start()
        self.addCleanup(self.patched.stop)
        rc, out = self._run_plain(["update"])
        self.assertEqual(rc, 0)                      # no failure, just stood down
        self.assertIn("already in progress", out)

    def test_child_finish_honors_the_lock(self):
        """A second spawned child must stand down while the lock is held."""
        from xzagent.selfupdate import (_acquire_update_lock,
                                        _release_update_lock)
        self.assertTrue(_acquire_update_lock())
        self.addCleanup(_release_update_lock)
        calls = []

        def _spy(cmd, note=None, progress=None):
            calls.append(cmd)
            return 0, "ok"

        with mock.patch("xzagent.selfupdate._run", _spy):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = child_finish("/some/spec", "999999999")
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])            # pip never ran: lock was held
        self.assertIn("already in progress", out.getvalue())
