import os
import tempfile
import time
import unittest

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)

from xzagent.find import find_matching, is_test_file, recently_modified
from xzagent.index import build_index, connect
from xzagent.rank import rank_files
from xzagent.scanner import scan


def make_versioned_project(root):
    files = {
        "api_v1_legacy.py": "def legacy_api():\n    return 1\n",
        "api_v2_client.py": "class ApiV2Client:\n    def fetch_orders(self):\n        return []\n",
        "api_v2_billing.py": "class ApiV2Billing:\n    def charge(self):\n        return True\n",
        "client_api_v2.py": "def make_client():\n    return None\n",
        "docs/guide.md": "# Billing guide\nCharge flow uses billing.\n",
        "docs/notes.txt": "notes about billing\n",
        "generated/numbers.py": "def generated_numbers():\n    return [1, 2]\n",
        "test_billing.py": "def test_billing():\n    return 'billing flow'\n",
        "saas/tests_flows.py": "def run_billing_flow_test():\n    return True\n",
        "workflows/tests/test_flow.py": "def test_flow():\n    return 'billing'\n",
        "web/billing.test.tsx": "test('billing flow renders', () => {})\n",
        "CheckoutTests.java": "public class CheckoutTests {\n    void testBilling() {}\n}\n",
        "README.md": "# project\napi overview\n",
    }
    offsets = {"api_v2_billing.py": 100000, "api_v2_client.py": 50000,
               "api_v1_legacy.py": 200, "docs/guide.md": 100}
    now = time.time()
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        if rel in offsets:
            when = now + offsets[rel]
            os.utime(path, (when, when))


class TestFindAndFilters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "versioned")
        os.makedirs(self.root)
        make_versioned_project(self.root)
        self.db = build_index(self.root)
        self.con = connect(self.db)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_find_prefix_glob(self):
        paths = {r["path"] for r in find_matching(self.con, pattern="api_v2*")}
        self.assertEqual(paths, {"api_v2_billing.py", "api_v2_client.py"})

    def test_find_suffix_glob(self):
        paths = [r["path"] for r in find_matching(self.con, pattern="*_v2.py")]
        self.assertEqual(paths, ["client_api_v2.py"])

    def test_find_multiple_patterns(self):
        paths = {r["path"] for r in find_matching(self.con, pattern="api_v2_client*, *legacy*")}
        self.assertEqual(paths, {"api_v2_client.py", "api_v1_legacy.py"})

    def test_find_language_and_ext_filters(self):
        paths = {r["path"] for r in find_matching(self.con, ext="md")}
        self.assertEqual(paths, {"docs/guide.md", "README.md"})

    def test_find_symbol_filter(self):
        rows = find_matching(self.con, symbol="ApiV2*")
        self.assertEqual({r["path"] for r in rows}, {"api_v2_billing.py", "api_v2_client.py"})
        self.assertTrue(all(r["symbols"] for r in rows))

    def test_find_exclusions(self):
        paths = {r["path"] for r in find_matching(self.con, exclude_exts=".md,.txt", exclude_dirs="generated")}
        self.assertNotIn("docs/guide.md", paths)
        self.assertNotIn("docs/notes.txt", paths)
        self.assertNotIn("generated/numbers.py", paths)
        self.assertIn("api_v2_client.py", paths)

    def test_find_recent_sort(self):
        rows = find_matching(self.con, pattern="api_v2*", recent=True)
        self.assertEqual(rows[0]["path"], "api_v2_billing.py")
        self.assertTrue(all(r["modified"] for r in rows))

    def test_recently_modified(self):
        rows = recently_modified(self.con, count=3, exclude_exts=".md,.txt", no_tests=True)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["path"], "api_v2_billing.py")

    def test_find_only_exts(self):
        rows = find_matching(self.con, only_exts=".py")
        self.assertTrue(rows)
        self.assertTrue(all(r["ext"] == ".py" for r in rows))
        self.assertNotIn("docs/guide.md", {r["path"] for r in rows})
        # alias equivalence: only_exts == ext
        self.assertEqual({r["path"] for r in rows},
                         {r["path"] for r in find_matching(self.con, ext=".py")})
        # 'py' without the dot works too
        self.assertEqual(len(find_matching(self.con, only_exts="py")), len(rows))
        # composing only_* filters: intersection that matches nothing is []
        self.assertEqual(find_matching(self.con, only_exts=".md", only_dirs="nomatch"), [])

    def test_recently_modified_only_exts(self):
        rows = recently_modified(self.con, count=10, only_exts=".md")
        self.assertTrue(rows)
        self.assertTrue(all(r["path"].endswith(".md") for r in rows))

    def test_rank_only_exts(self):
        ranked = rank_files(self.con, "guide billing notes api", only_exts=".md", top_k=0)
        self.assertTrue(ranked)
        self.assertTrue(all(p.endswith(".md") for p, _, _ in ranked))
        ranked = rank_files(self.con, "api", only_exts=".tsx,.dart", top_k=0)
        self.assertEqual(ranked, [])

    def test_is_test_file_heuristic(self):
        cases = {
            "test_billing.py": True, "tests/test_charge.py": True,
            "workflows/tests/test_flow.py": True, "saas/tests_flows.py": True,
            "web/billing.test.tsx": True, "web/billing.spec.ts": True,
            "CheckoutTests.java": True, "conftest.py": True,
            "__tests__/unit.ts": True, "cypress/e2e/a.ts": True,
            "payment_test.go": True, "spec/models_spec.rb": True,
            # false-positive traps: no underscore, no .test./.spec. dot split
            "latest.py": False, "contest.py": False, "api_v2_client.py": False,
            "docs/guide.md": False, "TESTING_NOTES": False,
        }
        for path, expected in cases.items():
            self.assertEqual(is_test_file(path), expected, path)

    def test_find_no_tests(self):
        all_paths = {r["path"] for r in find_matching(self.con, ext="py")}
        self.assertIn("test_billing.py", all_paths)
        self.assertIn("saas/tests_flows.py", all_paths)
        kept = {r["path"] for r in find_matching(self.con, ext="py", no_tests=True)}
        for gone in ("test_billing.py", "saas/tests_flows.py",
                     "workflows/tests/test_flow.py"):
            self.assertNotIn(gone, kept)
        self.assertIn("api_v2_client.py", kept)
        # other extensions too: jest and java tests vanish
        kept_all = {r["path"] for r in find_matching(self.con, no_tests=True)}
        self.assertNotIn("web/billing.test.tsx", kept_all)
        self.assertNotIn("CheckoutTests.java", kept_all)

    def test_rank_no_tests(self):
        ranked = rank_files(self.con, "billing flow test", top_k=0)
        self.assertTrue(any(is_test_file(p) for p, _, _ in ranked))
        ranked = rank_files(self.con, "billing flow test", top_k=0, no_tests=True)
        self.assertTrue(ranked)
        self.assertTrue(all(not is_test_file(p) for p, _, _ in ranked))

    def test_recently_modified_no_tests(self):
        rows = recently_modified(self.con, count=100)
        self.assertTrue(any(is_test_file(r["path"]) for r in rows))
        rows = recently_modified(self.con, count=100, no_tests=True)
        self.assertTrue(rows)
        self.assertTrue(all(not is_test_file(r["path"]) for r in rows))
        # freshest non-test file in the fixture (offset gives it the newest mtime)
        self.assertEqual(rows[0]["path"], "api_v2_billing.py")

    def test_find_only_dirs(self):
        rows = find_matching(self.con, only_dirs="docs")
        self.assertEqual({r["path"] for r in rows}, {"docs/guide.md", "docs/notes.txt"})
        rows = find_matching(self.con, only_dirs="docs,generated")
        self.assertEqual({r["path"] for r in rows},
                         {"docs/guide.md", "docs/notes.txt", "generated/numbers.py"})
        rows = find_matching(self.con, only_dirs="docs", exclude_exts=".txt")
        self.assertEqual({r["path"] for r in rows}, {"docs/guide.md"})
        self.assertEqual(find_matching(self.con, only_dirs="nope"), [])

    def test_recently_modified_only_dirs(self):
        rows = recently_modified(self.con, count=10, only_dirs="docs")
        self.assertTrue(rows)
        self.assertTrue(all(r["path"].startswith("docs/") for r in rows))

    def test_rank_only_dirs(self):
        ranked = rank_files(self.con, "guide billing notes", only_dirs="docs", top_k=0)
        self.assertTrue(ranked)
        self.assertTrue(all(p.startswith("docs/") for p, _, _ in ranked))
        ranked = rank_files(self.con, "api", only_dirs="nomatch", top_k=0)
        self.assertEqual(ranked, [])

    def test_rank_exclude_dirs_and_exts(self):
        ranked = rank_files(self.con, "guide billing charge", exclude_exts=".md,.txt")
        self.assertTrue(all(p != "docs/guide.md" for p, _, _ in ranked))
        ranked = rank_files(self.con, "generated numbers", exclude_dirs="generated")
        self.assertTrue(all(p != "generated/numbers.py" for p, _, _ in ranked))

    def test_rank_pattern_restricts(self):
        ranked = rank_files(self.con, "api", pattern="api_v2*", top_k=0)
        self.assertTrue(ranked)
        self.assertTrue(all(p.startswith("api_v2") for p, _, _ in ranked))

    def test_rank_recent_bias_prefers_newest(self):
        # both api_v2 files score equally for this query; the newest must win
        ranked = rank_files(self.con, "api v2", recent_bias=True, top_k=0)
        self.assertEqual(ranked[0][0], "api_v2_billing.py")

    def test_rank_only_lang(self):
        ranked = rank_files(self.con, "billing guide", only_lang="python", top_k=0)
        self.assertTrue(all(not p.endswith(".md") for p, _, _ in ranked))

    def test_rank_top_zero_returns_all(self):
        ranked = rank_files(self.con, "api", top_k=0)
        self.assertGreater(len(ranked), 3)

    def test_xzagentignore_honored_at_index_time(self):
        root2 = os.path.join(self.tmp.name, "ignored")
        os.makedirs(root2)
        for rel, content in {
            "a.py": "def keep():\n    return True\n",
            "noise.md": "# noise\n",
            "generated/data.py": "def data():\n    return []\n",
        }.items():
            path = os.path.join(root2, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        with open(os.path.join(root2, ".xzagentignore"), "w", encoding="utf-8") as fh:
            fh.write("*.md\ngenerated/\n")
        paths = {f.path for f in scan(root2)}
        self.assertIn("a.py", paths)
        self.assertNotIn("noise.md", paths)
        self.assertNotIn("generated/data.py", paths)
        con2 = connect(build_index(root2))
        self.assertNotIn("README-placeholder", [r["path"] for r in con2.execute("SELECT path FROM files")])
        self.assertFalse(con2.execute("SELECT 1 FROM files WHERE path='noise.md'").fetchone())
        con2.close()


class TestDefaultNoiseExclusions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self, root, files):
        for rel, content in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)

    def test_noise_names_skipped_by_default(self):
        root = os.path.join(self.tmp.name, "noisy")
        os.makedirs(root)
        self._make(root, {
            "app.py": "def keep():\n    return True\n",
            "tmp_debug_probe.py": "def t():\n    pass\n",
            "temp_thing.py": "def t():\n    pass\n",
            "backup_bak.py": "def t():\n    pass\n",
            "app.min.js": "var a=1\n",
            "package-lock.json": "{}\n",
            "run.log": "noise\n",
            "migrations/0001_initial.py": "def up():\n    pass\n",
            "__snapshots__/a.test.js.snap": "x\n",
            "node_modules/lib/index.js": "var x=2\n",
        })
        paths = {f.path for f in scan(root)}
        self.assertIn("app.py", paths)
        for gone in ("tmp_debug_probe.py", "temp_thing.py", "backup_bak.py",
                     "app.min.js", "package-lock.json", "run.log",
                     "migrations/0001_initial.py", "__snapshots__/a.test.js.snap",
                     "node_modules/lib/index.js"):
            self.assertNotIn(gone, paths)

    def test_negation_reincludes_defaults(self):
        root = os.path.join(self.tmp.name, "negated")
        os.makedirs(root)
        self._make(root, {
            "app.py": "def keep():\n    return True\n",
            "tmp_skip.py": "def t():\n    pass\n",
            "tmp_keep.py": "def t():\n    pass\n",
            "migrations/0001_deep.py": "def up():\n    pass\n",
            ".xzagentignore": "!migrations/\n!tmp_keep.py\n",
        })
        paths = {f.path for f in scan(root)}
        self.assertIn("app.py", paths)
        self.assertIn("tmp_keep.py", paths)
        self.assertIn("migrations/0001_deep.py", paths)
        self.assertNotIn("tmp_skip.py", paths)

    def test_migrations_still_feed_db_schema(self):
        root = os.path.join(self.tmp.name, "schema")
        os.makedirs(root)
        self._make(root, {
            "models.py": "class Order:\n    pass\n",
            "migrations/0001_initial.py": (
                "from django.db import migrations, models\n\n"
                "class Migration(migrations.Migration):\n"
                "    operations = [\n"
                "        migrations.CreateModel(\n"
                "            name='Purchase',\n"
                "            fields=[('id', models.AutoField(primary_key=True)),],\n"
                "        ),\n"
                "    ]\n"
            ),
        })
        con = connect(build_index(root))
        names = {r["name"] for r in con.execute("SELECT name FROM db_tables")}
        paths = {r["path"] for r in con.execute("SELECT path FROM files")}
        con.close()
        self.assertIn("Purchase", names)
        self.assertNotIn("migrations/0001_initial.py", paths)


class TestIndexSkipFlags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "flags")
        os.makedirs(self.root)
        for rel, content in {
            "app.py": "def main():\n    pass\n",
            "tmp_probe.py": "def t():\n    pass\n",
            "migrations/0001_initial.py": "def up():\n    pass\n",
            "nomad/config.py": "def c():\n    pass\n",
            "docs/guide.md": "# guide\n",
        }.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        self.db = os.path.join(self.root, ".xzagent", "index.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _paths(self):
        con = connect(self.db)
        paths = {r["path"] for r in con.execute("SELECT path FROM files")}
        con.close()
        return paths

    def _run(self, argv):
        import io
        from contextlib import redirect_stdout

        from xzagent.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(argv)
        return buf.getvalue()

    def test_scan_flags(self):
        paths = {f.path for f in scan(self.root)}
        self.assertNotIn("tmp_probe.py", paths)
        paths = {f.path for f in scan(self.root, no_default_skips=True)}
        self.assertIn("tmp_probe.py", paths)
        self.assertIn("migrations/0001_initial.py", paths)
        paths = {f.path for f in scan(self.root, extra_ignores=["nomad/"])}
        self.assertNotIn("nomad/config.py", paths)
        self.assertIn("app.py", paths)

    def test_cli_index_all_is_sticky(self):
        self._run(["--db", self.db, "--index-all", "index", self.root])
        paths = self._paths()
        self.assertIn("tmp_probe.py", paths)
        self.assertIn("migrations/0001_initial.py", paths)
        # a later plain run keeps the stored config (no rebuild churn)
        self._run(["--db", self.db, "index", self.root])
        self.assertIn("tmp_probe.py", self._paths())

    def test_cli_skip_patterns(self):
        self._run(["--db", self.db, "--skip", "nomad/", "--skip", "*.md", "index", self.root])
        paths = self._paths()
        self.assertNotIn("nomad/config.py", paths)
        self.assertNotIn("docs/guide.md", paths)
        self.assertIn("app.py", paths)
        self.assertNotIn("tmp_probe.py", paths)  # defaults still active

    def test_cli_default_skips_resets_index_all(self):
        self._run(["--db", self.db, "--index-all", "index", self.root])
        self.assertIn("tmp_probe.py", self._paths())
        self._run(["--db", self.db, "--default-skips", "index", self.root])
        self.assertNotIn("tmp_probe.py", self._paths())

    def test_global_flags_after_subcommand(self):
        # --index-all / --skip given AFTER the subcommand must be honored
        self._run(["--db", self.db, "index", self.root, "--index-all", "--skip", "nomad/"])
        paths = self._paths()
        self.assertIn("tmp_probe.py", paths)
        self.assertIn("migrations/0001_initial.py", paths)
        self.assertNotIn("nomad/config.py", paths)
        # --reindex / --default-skips after the subcommand trigger a rebuild
        self._run(["--db", self.db, "map", self.root, "--reindex", "--default-skips"])
        paths = self._paths()
        self.assertNotIn("tmp_probe.py", paths)
        self.assertIn("docs/guide.md", paths)  # defaults restored, docs still indexed

    def test_global_flags_mixed_positions(self):
        # global flags may be split around the subcommand; --skip appends on both sides
        self._run(["--db", self.db, "--skip", "*.md", "index", self.root, "--skip", "nomad/"])
        paths = self._paths()
        self.assertNotIn("docs/guide.md", paths)
        self.assertNotIn("nomad/config.py", paths)
        self.assertIn("app.py", paths)


class TestFindDiskFallback(unittest.TestCase):
    """ctxp find (pattern search) also matches noise-skipped files ON DISK.

    tmp_*/lockfiles/minified files never enter the index, but find is an
    EXPLICIT targeting command: naming a pattern means the user wants
    those files. Rows found only on disk are marked '[not indexed]'.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "diskfb")
        os.makedirs(self.root)
        for rel, content in {
            "app.py": "def main():\n    pass\n",
            "api_v2.py": "class ApiV2:\n    pass\n",
            "tmp_probe.py": "def t():\n    pass\n",
            "temp_scratch.md": "scratch\n",
            "package-lock.json": "{}\n",
            "logs/tmp_secret.py": "def s():\n    pass\n",
            "migrations/0001_initial.py": "def up():\n    pass\n",
            "nomad/config.py": "def c():\n    pass\n",
            ".gitignore": "ignored_dir/\n",
            "ignored_dir/tmp_hidden.py": "def h():\n    pass\n",
        }.items():
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        self.db = os.path.join(self.root, ".xzagent", "index.db")
        self._run(["--db", self.db, "index", self.root])

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        import io
        from contextlib import redirect_stdout

        from xzagent.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(argv)
        return buf.getvalue()

    def test_find_on_disk_unit(self):
        from xzagent.find import find_on_disk
        rows = {r["path"]: r for r in find_on_disk(self.root, pattern="tmp_*")}
        self.assertIn("tmp_probe.py", rows)
        self.assertIn("logs/tmp_hidden.py" if False else "logs/tmp_secret.py", rows)
        self.assertTrue(all(r["on_disk"] for r in rows.values()))
        self.assertTrue(rows["tmp_probe.py"]["mtime"] > 0)
        self.assertEqual(rows["tmp_probe.py"]["lang"], "python")
        # gitignored and built-in noise dirs stay hidden on disk too
        self.assertNotIn("ignored_dir/tmp_hidden.py", rows)
        self.assertNotIn("migrations/0001_initial.py", rows)  # migrations/ is a dir skip

    def test_cli_shows_noise_matches_marked(self):
        out = self._run(["--db", self.db, "find", self.root, "tmp_*"])
        self.assertIn("tmp_probe.py", out)
        self.assertIn("[not indexed]", out)
        # package-lock.json matches a different pattern and shows too
        out = self._run(["--db", self.db, "find", self.root, "*lock*"])
        self.assertIn("package-lock.json", out)
        self.assertIn("[not indexed]", out)

    def test_cli_merges_index_and_disk_rows(self):
        out = self._run(["--db", self.db, "find", self.root, "*.py"])
        self.assertIn("app.py", out)
        self.assertNotIn("app.py  [not indexed]", out)       # indexed: no marker
        self.assertIn("tmp_probe.py  [not indexed]", out)    # noise: marked
        self.assertIn("nomad/config.py", out)

    def test_cli_disk_pass_honors_filters(self):
        # --skip-dirs hides disk rows in those dirs
        out = self._run(["--db", self.db, "find", self.root, "tmp_*", "--skip-dirs", "logs"])
        self.assertNotIn("tmp_secret.py", out)
        self.assertIn("tmp_probe.py", out)
        # --only-dirs keeps only disk rows under the dir
        out = self._run(["--db", self.db, "find", self.root, "tmp_*", "--only-dirs", "logs"])
        self.assertIn("tmp_secret.py", out)
        self.assertNotIn("tmp_probe.py  [not indexed]", out)
        # --skip patterns travel to the disk walk as extra ignores
        out = self._run(["--db", self.db, "--skip", "logs/", "find", self.root, "tmp_*"])
        self.assertNotIn("tmp_secret.py", out)
        # --no-tests applies to disk rows as well
        out = self._run(["--db", self.db, "find", self.root, "tmp_*", "--no-tests"])
        self.assertIn("tmp_probe.py", out)  # tmp_probe is not a test file

    def test_cli_no_marker_after_index_all(self):
        self._run(["--db", self.db, "--index-all", "index", self.root, "--reindex"])
        out = self._run(["--db", self.db, "find", self.root, "tmp_*"])
        self.assertIn("tmp_probe.py", out)
        self.assertNotIn("[not indexed]", out)

    def test_cli_symbol_search_skips_disk_pass(self):
        # symbols need the index: no marker rows when --symbol is used
        out = self._run(["--db", self.db, "find", self.root, "*", "--symbol", "ApiV2"])
        self.assertIn("api_v2.py", out)
        self.assertNotIn("[not indexed]", out)

    def test_cli_recent_sorts_merged_rows(self):
        # touch the noise file so it is the newest thing on disk
        import time as _t
        p = os.path.join(self.root, "tmp_probe.py")
        when = _t.time() + 500
        os.utime(p, (when, when))
        out = self._run(["--db", self.db, "find", self.root, "*.py", "--recent"])
        first = [ln for ln in out.splitlines() if ln.strip()][0]
        self.assertIn("tmp_probe.py", first)


if __name__ == "__main__":
    unittest.main()