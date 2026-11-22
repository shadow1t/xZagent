import os
import tempfile
import time
import unittest

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)

from xzagent.find import find_matching, recently_modified
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
        rows = recently_modified(self.con, count=3, exclude_exts=".md,.txt")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["path"], "api_v2_billing.py")

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


if __name__ == "__main__":
    unittest.main()