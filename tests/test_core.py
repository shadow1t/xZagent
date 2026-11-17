import os
import tempfile
import unittest

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)

from xzagent.gitignore import GitignoreMatcher
from xzagent.graph import build_edges, call_graph, neighbors
from xzagent.index import build_index, connect
from xzagent.map import repo_map
from xzagent.packer import build_pack
from xzagent.rank import rank_files
from xzagent.scanner import scan
from xzagent.symbols import extract


class TestGitignore(unittest.TestCase):
    def test_dir_and_glob(self):
        m = GitignoreMatcher()
        m.add_gitignore("/r", "ignored_dir/\n*.log\n!keep.log\n")
        self.assertTrue(m.is_ignored("/r", "/r/ignored_dir", True))
        self.assertTrue(m.is_ignored("/r", "/r/ignored_dir/x.py", False))
        self.assertTrue(m.is_ignored("/r", "/r/a/debug.log", False))
        self.assertFalse(m.is_ignored("/r", "/r/a/keep.log", False))
        self.assertFalse(m.is_ignored("/r", "/r/a/main.py", False))


class TestScannerSymbols(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_respects_gitignore(self):
        paths = {f.path for f in scan(self.root)}
        self.assertIn("app/auth.py", paths)
        self.assertIn("web/script.js", paths)
        self.assertNotIn("ignored_dir/secret.py", paths)
        self.assertNotIn("debug.log", paths)

    def test_python_symbols(self):
        with open(os.path.join(self.root, "app/auth.py")) as fh:
            symbols, imports, calls, summary = extract(fh.read(), "python")
        names = {n for _, n, _ in symbols}
        self.assertIn("login", names)
        self.assertIn("logout", names)
        self.assertEqual(summary, "Authentication module.")

    def test_js_symbols(self):
        with open(os.path.join(self.root, "web/api.js")) as fh:
            symbols, imports, calls, _ = extract(fh.read(), "javascript")
        self.assertIn("fetchUser", {n for _, n, _ in symbols})
        with open(os.path.join(self.root, "web/script.js")) as fh:
            js_imports = extract(fh.read(), "javascript")[1]
        self.assertTrue(any(m == "./api" for m, _ in js_imports))


class TestIndexRankGraph(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project(self.tmp.name)
        self.db = build_index(self.root)
        self.con = connect(self.db)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_rank_finds_auth_for_login_task(self):
        ranked = rank_files(self.con, "fix login authentication bug", top_k=5)
        paths = [p for p, _, _ in ranked]
        self.assertIn("app/auth.py", paths[:3])
        self.assertEqual(paths[0], "app/auth.py")

    def test_rank_finds_js_for_render_task(self):
        ranked = rank_files(self.con, "render user profile in frontend", top_k=5)
        paths = [p for p, _, _ in ranked]
        self.assertTrue(any(p.startswith("web/") for p in paths[:3]))

    def test_dart_symbols_and_imports(self):
        with open(os.path.join(self.root, "lib/main.dart")) as fh:
            symbols, imports, calls, _ = extract(fh.read(), "dart")
        names = {n for _, n, _ in symbols}
        self.assertIn("MyApp", names)
        self.assertIn("main", names)
        self.assertIn("package:app_name/services/storage.dart", {m for m, _ in imports})
        self.assertIn("services/utils.dart", {m for m, _ in imports})

    def test_dart_uppercase_calls_excluded(self):
        snippet = "void main() {\n  ChangeNotifierProvider(create: (_) => 1) {}\n}\n"
        symbols, _, _, _ = extract(snippet, "dart")
        names = {n for _, n, _ in symbols}
        self.assertIn("main", names)
        self.assertNotIn("ChangeNotifierProvider", names)

    def test_dart_keywords_not_symbols(self):
        with open(os.path.join(self.root, "lib/main.dart")) as fh:
            symbols, _, _, _ = extract(fh.read(), "dart")
        fn_names = {n for k, n, _ in symbols if k == "function"}
        self.assertIn("main", fn_names)
        self.assertNotIn("if", fn_names)

    def test_db_schema_sql(self):
        from xzagent.schema import parse_sql_schema
        with open(os.path.join(self.root, "db/schema.sql"), encoding="utf-8") as fh:
            tables = parse_sql_schema(fh.read())
        self.assertIn("users", tables)
        self.assertIn("company_id", tables["users"]["columns"])
        self.assertEqual(tables["users"]["fks"], [{"column": "company_id", "ref_table": "companies"}])
        rows = self.con.execute("SELECT name, columns, fks FROM db_tables").fetchall()
        names = {r["name"] for r in rows}
        self.assertIn("companies", names)
        self.assertIn("users", names)

    def test_db_schema_django_migration_and_html(self):
        from xzagent.schema import parse_django_migrations, schema_html
        with open(os.path.join(self.root, "shop/migrations/0001_initial.py"), encoding="utf-8") as fh:
            tables = parse_django_migrations(fh.read())
        self.assertIn("Order", tables)
        self.assertIn("total", tables["Order"]["columns"])
        self.assertEqual(tables["Order"]["fks"], [{"column": "customer_id", "ref_table": "Customer"}])
        rows = [dict(r) for r in self.con.execute("SELECT name, source, columns, fks FROM db_tables").fetchall()]
        import json as _json
        rows = [{"name": r["name"], "source": r["source"], "columns": _json.loads(r["columns"]), "fks": _json.loads(r["fks"])} for r in rows]
        html = schema_html(rows)
        self.assertIn("Order", html)
        self.assertIn("<svg", html)

    def test_ensure_index_rebuilds_on_change(self):
        from xzagent.index import ensure_index
        db = build_index(self.root)
        self.assertEqual(ensure_index(self.root, db), db)
        with open(os.path.join(self.root, "app/utils.py"), "a", encoding="utf-8") as fh:
            fh.write("\n\ndef new_fn():\n    pass\n")
        ensure_index(self.root, db)
        con = connect(db)
        names = {r["name"] for r in con.execute("SELECT name FROM symbols WHERE file='app/utils.py'")}
        con.close()
        self.assertIn("new_fn", names)

    def test_code_version_triggers_rebuild(self):
        from unittest import mock

        import xzagent.index as ix
        db = build_index(self.root)
        with mock.patch.object(ix, "_code_version", return_value="v-old"):
            ix.build_index(self.root, db)
        ix.ensure_index(self.root, db)
        con = connect(db)
        v = con.execute("SELECT v FROM meta WHERE k='code_version'").fetchone()["v"]
        con.close()
        self.assertNotEqual(v, "v-old")

    def test_dart_import_edges_and_call_graph(self):
        n = neighbors(self.con, "lib/main.dart")
        others = {x["other"] for x in n if x["kind"] == "import"}
        self.assertIn("lib/services/storage.dart", others)
        self.assertIn("lib/services/utils.dart", others)
        cg = call_graph(self.con, "saveFile")
        self.assertIn("lib/services/storage.dart", [d["file"] for d in cg["definitions"]])

    def test_import_edges(self):
        n = neighbors(self.con, "app/main.py")
        others = {x["other"] for x in n if x["kind"] == "import"}
        self.assertIn("app/auth.py", others)
        self.assertIn("app/utils.py", others)

    def test_js_import_edge(self):
        n = neighbors(self.con, "web/script.js")
        self.assertIn("web/api.js", {x["other"] for x in n})

    def test_call_graph_references(self):
        cg = call_graph(self.con, "login")
        self.assertIn("app/auth.py", cg["references"])
        self.assertIn("app/main.py", cg["references"])

    def test_call_graph(self):
        cg = call_graph(self.con, "login")
        self.assertIn("app/auth.py", [d["file"] for d in cg["definitions"]])
        self.assertIn("app/main.py", cg["callers"])

    def test_path_match_ranks_first(self):
        ranked = rank_files(self.con, "web api fetch", top_k=5)
        self.assertEqual(ranked[0][0], "web/api.js")

    def test_snake_and_camel_boundaries(self):
        ranked = rank_files(self.con, "prefix", top_k=5)
        self.assertIn("app/utils.py", [p for p, _, _ in ranked])
        ranked = rank_files(self.con, "fix", top_k=10)
        self.assertNotIn("app/utils.py", [p for p, _, _ in ranked])
        ranked = rank_files(self.con, "fetchUser", top_k=5)
        self.assertEqual(ranked[0][0], "web/api.js")

    def test_stopwords_ignored(self):
        from xzagent.rank import _tokens
        self.assertNotIn("in", _tokens("fix the bug in login"))
        self.assertIn("login", _tokens("fix the bug in login"))

    def test_word_boundary_no_false_positive(self):
        ranked = rank_files(self.con, "fix", top_k=10)
        paths = [p for p, _, _ in ranked]
        self.assertNotIn("app/utils.py", paths)
        ranked = rank_files(self.con, "prefix", top_k=10)
        self.assertIn("app/utils.py", [p for p, _, _ in ranked])

    def test_repo_map_and_pack(self):
        m = repo_map(self.con)
        self.assertIn("app/auth.py", m)
        self.assertIn("login", m)
        pack = build_pack(self.con, self.root, "login bug", top_k=3)
        self.assertIn("app/auth.py", pack)
        self.assertIn("Context Pack", pack)


if __name__ == "__main__":
    unittest.main()
