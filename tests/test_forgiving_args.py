"""Forgiving positional order: `ctxp find "tmp_*"` inside the project.

ROOT is a positional, so a lone pattern/query/symbol naturally lands in
it. The CLI should do the obvious right thing instead of a traceback:
treat a non-directory as the pattern against the current directory,
accept swapped orders, and reject truly bad roots with one clean line.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)

from xzagent.cli import main


class TestForgivingPositionals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.root)
        for rel, content in {
            "app.py": "def main():\n    pass\n",
            "api_v2.py": "class ApiV2:\n    pass\n",
            "tmp_probe.py": "def t():\n    pass\n",
        }.items():
            with open(os.path.join(self.root, rel), "w", encoding="utf-8") as fh:
                fh.write(content)
        self.other = os.path.join(self.tmp.name, "other")
        os.makedirs(self.other)
        with open(os.path.join(self.other, "other_file.py"), "w",
                  encoding="utf-8") as fh:
            fh.write("x = 1\n")
        self.db = os.path.join(self.tmp.name, "index.db")
        self._pwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._pwd)

    def _run(self, argv, cwd=None):
        buf = io.StringIO()
        if cwd:
            os.chdir(cwd)
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            with redirect_stdout(buf):
                rc = main(argv)
        return rc, buf.getvalue()

    def test_find_lone_pattern_from_inside_project(self):
        """`ctxp find "tmp_*"` with cwd = the project: no root needed."""
        rc, out = self._run(["--db", self.db, "find", "tmp_*"], cwd=self.root)
        self.assertEqual(rc, 0)
        self.assertIn("tmp_probe.py", out)
        self.assertIn("[not indexed]", out)

    def test_find_lone_existing_dir_stays_a_root(self):
        """`ctxp find OTHER_DIR` keeps the old meaning: a root, no pattern."""
        rc, out = self._run(["--db", self.db, "find", self.other, "--recent"],
                            cwd=self.root)
        self.assertEqual(rc, 0)
        self.assertIn("other_file.py", out)
        self.assertNotIn("[not indexed]", out)

    def test_find_root_then_pattern_still_works(self):
        rc, out = self._run(["--db", self.db, "find", self.root, "tmp_*"])
        self.assertEqual(rc, 0)
        self.assertIn("tmp_probe.py", out)

    def test_find_swapped_order_both_given(self):
        """`ctxp find "tmp_*" .` (pattern before the root) also works."""
        rc, out = self._run(["--db", self.db, "find", "tmp_*", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("tmp_probe.py", out)

    def test_invalid_root_is_a_clean_one_liner(self):
        """No traceback, no WinError 123: a professional one-line error."""
        with self.assertRaises(SystemExit) as ctx:
            self._run(["--db", self.db, "find", "no_such_dir_xyz", "*"])
        self.assertIn("is not a directory", str(ctx.exception))
        self.assertIn("no_such_dir_xyz", str(ctx.exception))

    def test_ask_task_only_from_inside_project(self):
        """`ctxp ask "task"` with cwd = the project."""
        rc, out = self._run(["--db", self.db, "ask", "api handler logic"],
                            cwd=self.root)
        self.assertEqual(rc, 0)

    def test_ask_reversed_old_order_still_works(self):
        """`ctxp ask ROOT "query"` (the old documented order)."""
        rc, out = self._run(["--db", self.db, "ask", self.root, "api_v2"])
        self.assertEqual(rc, 0)

    def test_pack_task_only_from_inside_project(self):
        rc, out = self._run(["--db", self.db, "pack", "api_v2", "-o",
                             os.path.join(self.tmp.name, "ctx.md")],
                            cwd=self.root)
        self.assertEqual(rc, 0)

    def test_graph_lone_symbol_from_inside_project(self):
        rc, out = self._run(["--db", self.db, "graph", "main"], cwd=self.root)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()