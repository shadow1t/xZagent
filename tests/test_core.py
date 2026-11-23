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
        # the login test file hijacks the ranking unless tests are excluded
        self.assertIn("app/test_auth.py", paths[:3])
        ranked = rank_files(self.con, "fix login authentication bug", top_k=5, no_tests=True)
        paths = [p for p, _, _ in ranked]
        self.assertNotIn("app/test_auth.py", paths)
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

    def test_db_schema_sql_inline_references(self):
        # Postgres-dump style inline "col INT REFERENCES other(id)" must be
        # extracted too, not only table-level FOREIGN KEY (...) constraints
        from xzagent.schema import parse_sql_schema
        sql = (
            "CREATE TABLE teams (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  -- a line comment\n"
            "  name TEXT\n"
            ");\n"
            "CREATE TABLE players (\n"
            "  id INTEGER PRIMARY KEY,\n"
            "  /* block comment */\n"
            "  team_id INTEGER REFERENCES teams(id),\n"
            "  mentor_id INTEGER NOT NULL REFERENCES players(id),\n"
            "  arena_id INTEGER,\n"
            "  FOREIGN KEY (arena_id) REFERENCES arenas(id)\n"
            ");\n"
        )
        tables = parse_sql_schema(sql)
        self.assertEqual(tables["players"]["fks"], [
            {"column": "team_id", "ref_table": "teams"},
            {"column": "mentor_id", "ref_table": "players"},
            {"column": "arena_id", "ref_table": "arenas"},
        ])
        self.assertEqual(tables["players"]["columns"],
                         ["id", "team_id", "mentor_id", "arena_id"])

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

    def test_schema_html_multi_database_chrome(self):
        # two databases in one project: the HTML must be data-sealed,
        # carry the per-database legend, and inject every source name
        from xzagent.schema import schema_html
        rows = [
            {"name": "users", "source": "app.db",
             "columns": ["id", "name"], "fks": []},
            {"name": "posts", "source": "app.db",
             "columns": ["id", "user_id"],
             "fks": [{"column": "user_id", "ref_table": "users"}]},
            {"name": "customers", "source": "shop.sqlite",
             "columns": ["id", "title"], "fks": []},
            {"name": "orders", "source": "shop.sqlite",
             "columns": ["id", "customer_id", "author_id"],
             "fks": [{"column": "customer_id", "ref_table": "customers"},
                     {"column": "author_id", "ref_table": "users"}]},
        ]
        html = schema_html(rows)
        # data is sealed into the INJECTED_DATA line (the design's own
        # "__DATA__" comment text stays in the shipped template)
        self.assertNotIn("INJECTED_DATA = null", html)
        self.assertIn("INJECTED_DATA = [", html)
        self.assertIn('id="legend"', html)
        self.assertIn("app.db", html)
        self.assertIn("shop.sqlite", html)
        self.assertIn("cross-db FK", html)  # cross-db fk notice
        # HTML-escaping helper ships in the template (names are injected raw)
        self.assertIn("function esc(", html)
        # sidebar group folding: open-all/close-all + collapsed by default
        self.assertIn("foldBtn", html)
        self.assertIn("populateSideGroup", html)
        # 0.2.17: identity = (source, name), duplicate table names must not collide,
        # and double-clicking a legend chip solos one database
        self.assertIn("function keyOf(", html)
        self.assertIn("byKey", html)
        self.assertIn("soloSource", html)
        self.assertIn("createLabelPlacer", html)  # collision-free label culling
        self.assertIn("drawLabelPill", html)      # fk column labels on hot edges
        self.assertIn("resizeCanvas", html)       # canvas backing-store sizing
        self.assertIn("pairMap", html)            # duplicate fk lines merged per pair
        self.assertIn("drawArrowHead", html)      # direction arrowheads on hot edges
        self.assertIn("computeFocusTargets", html)  # hub-centered layout on select
        self.assertIn("computeSphereTargets", html)  # back to sphere positions on reset
        self.assertIn("galaxyNodes", html)        # DB Galaxy orbit for huge corpora
        self.assertIn("autoSpin", html)          # spin guard for large corpora
        # 0.2.26: inside a solo DB (or with a table focused), DB Galaxy draws
        # that DB's TABLES as planets instead of bouncing back to the DB overview
        self.assertIn("renderTableGalaxy", html)
        self.assertIn("galaxyFocusDb", html)

    def test_schema_rows_filter_only_exclude(self):
        from xzagent.schema import filter_schema_rows
        rows = [
            {"name": "users", "source": "app/db.sqlite3", "columns": ["id"], "fks": []},
            {"name": "posts", "source": "app/db.sqlite3", "columns": ["id"], "fks": []},
            {"name": "customers", "source": "data/tenants/company_6.sql", "columns": ["id"], "fks": []},
            {"name": "Order", "source": "shop/migrations/0001_initial.py", "columns": ["id"], "fks": []},
        ]
        only = filter_schema_rows(rows, ["db.sqlite3"], None)
        self.assertEqual(sorted(r["name"] for r in only), ["posts", "users"])
        ex = filter_schema_rows(rows, None, ["migrations"])
        self.assertNotIn("Order", [r["name"] for r in ex])
        both = filter_schema_rows(rows, ["sqlite3", "tenants"], ["db.sqlite3"])
        self.assertEqual([r["name"] for r in both], ["customers"])
        self.assertEqual(filter_schema_rows(rows, None, None), rows)
        # empty string never filters anything out
        self.assertEqual(filter_schema_rows(rows, [""], None), rows)

    def test_postgres_rows_to_tables(self):
        from xzagent.schema import rows_to_tables
        cols = [("users", "id", "integer"), ("users", "company_id", "integer"), ("companies", "id", "integer")]
        fks = [("users", "company_id", "companies")]
        tables = rows_to_tables(cols, fks)
        self.assertEqual(tables["users"]["fks"], [{"column": "company_id", "ref_table": "companies"}])
        self.assertIn("company_id", tables["users"]["columns"])

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


class TestMapLargeProject(unittest.TestCase):
    """repo_map must show every directory regardless of project size."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "big")
        for d in ("ui/platform", "ui/pos", "srv"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
            for i in range(60):
                with open(os.path.join(self.root, d, "f%02d.tsx" % i), "w", encoding="utf-8") as fh:
                    fh.write("export function F%d() {}\n" % i)
        self.db = build_index(self.root)
        self.con = connect(self.db)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_default_map_shows_every_dir_not_every_file(self):
        m = repo_map(self.con)
        for d in ("platform", "pos", "srv", "f00.tsx"):
            self.assertIn(d, m)
        self.assertIn("… (+35 more in this dir)", m)
        self.assertNotIn("more files)", m)  # no global truncation marker

    def test_unlimited_map_lists_every_file(self):
        m = repo_map(self.con, max_per_dir=None)
        self.assertIn("f59.tsx", m)
        self.assertNotIn("… (+", m)

    def test_per_dir_cap_listed_files_section(self):
        m = repo_map(self.con, max_per_dir=5)
        self.assertIn("… (+55 more in this dir)", m)


if __name__ == "__main__":
    unittest.main()