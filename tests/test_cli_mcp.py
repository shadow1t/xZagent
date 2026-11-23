import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from tests.helpers import make_sample_project  # noqa: F401  (sets sys.path)

from xzagent.cli import main

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project(self.tmp.name)
        self.db = os.path.join(self.root, ".xzagent", "index.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_schema_cli_only_exclude_filters(self):
        import sqlite3
        spec = (("main.sqlite", "alpha_users"), ("other.sqlite", "alien_customers"))
        for fname, tbl in spec:
            con = sqlite3.connect(os.path.join(self.root, fname))
            con.execute("CREATE TABLE %s (id INTEGER PRIMARY KEY, tag TEXT)" % tbl)
            con.commit(); con.close()
        self._run(["--db", self.db, "index", self.root])
        rc, out = self._run(["schema", self.root, "--only", "other.sqlite"])
        self.assertEqual(rc, 0)
        self.assertIn("alien_customers", out)
        self.assertNotIn("alpha_users", out)
        self.assertNotIn("companies", out)     # db/schema.sql filtered out too
        rc, out = self._run(["schema", self.root, "--exclude", "other.sqlite",
                             "--exclude", "migrations"])
        self.assertEqual(rc, 0)
        self.assertIn("alpha_users", out)
        self.assertIn("companies", out)         # schema.sql survives
        self.assertNotIn("alien_customers", out)
        # html output honors the filter as well
        html = os.path.join(self.root, "one.html")
        rc, out = self._run(["schema", self.root, "--only", "main.sqlite",
                             "--html", html])
        self.assertEqual(rc, 0)
        with open(html, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("alpha_users", body)
        self.assertNotIn("alien_customers", body)
        self.assertNotIn("INJECTED_DATA = null", body)  # data sealed in

    def test_index_map_ask_pack(self):
        rc, out = self._run(["--db", self.db, "index", self.root])
        self.assertEqual(rc, 0)
        self.assertIn("Indexed", out)
        rc, out = self._run(["--db", self.db, "map", self.root])
        self.assertIn("app/auth.py", out)
        rc, out = self._run(["--db", self.db, "ask", self.root, "login bug", "--top", "3", "--no-tests"])
        self.assertIn("app/auth.py", out.splitlines()[0])
        self.assertNotIn("test_auth", out)
        out_path = os.path.join(self.tmp.name, "context.md")
        rc, out = self._run(["--db", self.db, "pack", self.root, "login bug", "-o", out_path])
        self.assertEqual(rc, 0)
        with open(out_path) as fh:
            self.assertIn("app/auth.py", fh.read())

    def test_graph_cmd(self):
        self._run(["--db", self.db, "index", self.root])
        rc, out = self._run(["--db", self.db, "graph", self.root, "login"])
        data = json.loads(out)
        self.assertIn("app/main.py", data["callers"])


class TestMcp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mcp_handshake_and_tools(self):
        env = dict(os.environ, PYTHONPATH=os.path.abspath(SRC))
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "find_files", "arguments": {"task": "login authentication bug", "top_k": 3, "no_tests": True}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "call_graph", "arguments": {"symbol": "login"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "file_outline",
                        "arguments": {"path": "app/auth.py"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "file_outline",
                        "arguments": {"path": self.root.replace("\\", "/") + "/app/auth.py"}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "file_outline",
                        "arguments": {"file_path": "app_name/app/auth.py".replace("app_name", self.root.replace("\\", "/").split("/")[-1])}}},
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call",
             "params": {"name": "find_files",
                        "arguments": {"query": "login authentication bug", "top_k": 3, "no_tests": True}}},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "call_graph",
                        "arguments": {"file_path": "app/main.py", "symbol_name": "login"}}},
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
             "params": {"name": "find", "arguments": {"pattern": "api*", "top": 10}}},
            {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
             "params": {"name": "recent_files", "arguments": {"count": 3}}},
            {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
             "params": {"name": "find_files",
                        "arguments": {"task": "user fetch api", "pattern": "web/*", "skip_ext": ".md,.txt"}}},
            {"jsonrpc": "2.0", "id": 13, "method": "tools/call",
             "params": {"name": "find_files",
                        "arguments": {"task": "login authentication", "only_dirs": "app"}}},
            {"jsonrpc": "2.0", "id": 14, "method": "tools/call",
             "params": {"name": "find",
                        "arguments": {"only_dirs": "services", "top": 50}}},
            {"jsonrpc": "2.0", "id": 15, "method": "tools/call",
             "params": {"name": "find_files",
                        "arguments": {"task": "login authentication", "only_ext": ".py"}}},
            {"jsonrpc": "2.0", "id": 16, "method": "tools/call",
             "params": {"name": "find",
                        "arguments": {"pattern": "test_*", "top": 50}}},
            {"jsonrpc": "2.0", "id": 17, "method": "tools/call",
             "params": {"name": "find",
                        "arguments": {"pattern": "test_*", "no_tests": True, "top": 50}}},
        ]
        proc = subprocess.run(
            [sys.executable, "-m", "xzagent", "serve", self.root],
            input="\n".join(json.dumps(m) for m in msgs) + "\n",
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        responses = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
        by_id = {r["id"]: r for r in responses}
        self.assertIn("result", by_id[1])
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "xzagent")
        tool_names = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertIn("find_files", tool_names)
        self.assertIn("context_pack", tool_names)
        self.assertIn("find", tool_names)
        self.assertIn("recent_files", tool_names)
        ranked = json.loads(by_id[3]["result"]["content"][0]["text"])
        self.assertEqual(ranked[0][0], "app/auth.py")
        cg = json.loads(by_id[4]["result"]["content"][0]["text"])
        self.assertIn("app/main.py", cg["callers"])
        outline_rel = json.loads(by_id[5]["result"]["content"][0]["text"])
        outline_abs = json.loads(by_id[6]["result"]["content"][0]["text"])
        names = {r["name"] for r in outline_rel}
        self.assertIn("login", names)
        self.assertEqual(outline_abs, outline_rel)
        outline_prefixed = json.loads(by_id[7]["result"]["content"][0]["text"])
        self.assertEqual(outline_prefixed, outline_rel)
        ranked_q = json.loads(by_id[8]["result"]["content"][0]["text"])
        self.assertEqual(ranked_q[0][0], "app/auth.py")
        cg_alias = json.loads(by_id[9]["result"]["content"][0]["text"])
        self.assertEqual(cg_alias["symbol"], "login")
        self.assertIn("app/main.py", cg_alias["callers"])
        found = json.loads(by_id[10]["result"]["content"][0]["text"])
        self.assertIn("web/api.js", {r["path"] for r in found})
        rf = json.loads(by_id[11]["result"]["content"][0]["text"])
        self.assertEqual(len(rf), 3)
        mtimes = [r["mtime"] for r in rf]
        self.assertEqual(mtimes, sorted(mtimes, reverse=True))
        filtered = json.loads(by_id[12]["result"]["content"][0]["text"])
        self.assertTrue(filtered)
        self.assertTrue(all(p.startswith("web/") for p, _, _ in filtered))
        only_app = json.loads(by_id[13]["result"]["content"][0]["text"])
        self.assertTrue(only_app)
        self.assertTrue(all(p.startswith("app/") for p, _, _ in only_app))
        # 'services' sits at nested depth lib/services/ — only_dirs matches any segment
        nested = json.loads(by_id[14]["result"]["content"][0]["text"])
        self.assertEqual({r["path"] for r in nested},
                         {"lib/services/storage.dart", "lib/services/utils.dart"})
        # only_ext keeps .py files only, whatever the relevance ranking picks
        only_py = json.loads(by_id[15]["result"]["content"][0]["text"])
        self.assertTrue(only_py)
        self.assertTrue(all(p.endswith(".py") for p, _, _ in only_py))
        # without no_tests: the sample test file is findable
        found_tests = json.loads(by_id[16]["result"]["content"][0]["text"])
        self.assertIn("app/test_auth.py", {r["path"] for r in found_tests})
        # with no_tests=true: same pattern returns nothing
        no_tests = json.loads(by_id[17]["result"]["content"][0]["text"])
        self.assertEqual(no_tests, [])


if __name__ == "__main__":
    unittest.main()

class TestSetupCmd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_sample_project(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_setup_writes_both_configs(self):
        rc, out = self._run(["setup", self.root])
        self.assertEqual(rc, 0)
        for rel, client in ((".mcp.json", "claude"),
                            (".cursor/mcp.json", "cursor")):
            with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8") as fh:
                cfg = json.load(fh)
            entry = cfg["mcpServers"]["xzagent"]
            # portable default: no machine paths anywhere, commit-safe
            self.assertEqual(entry["args"], ["serve", "."])
            self.assertEqual(entry["command"], "ctxp")
            self.assertIn(client, out)
        self.assertIn("zero setup", out)
        self.assertIn("portable", out)

    def test_setup_absolute_flag_pins_machine_paths(self):
        rc, out = self._run(["setup", self.root, "--absolute"])
        self.assertEqual(rc, 0)
        with open(os.path.join(self.root, ".mcp.json"), encoding="utf-8") as fh:
            entry = json.load(fh)["mcpServers"]["xzagent"]
        self.assertEqual(entry["args"], ["serve", os.path.abspath(self.root)])
        self.assertIn("do NOT commit", out)

    def test_setup_single_client(self):
        rc, out = self._run(["setup", self.root, "--client", "claude"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".mcp.json")))
        self.assertFalse(os.path.isdir(os.path.join(self.root, ".cursor")))

    def test_setup_keeps_other_servers(self):
        path = os.path.join(self.root, ".mcp.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"other": {"command": "foo"}}}, fh)
        self._run(["setup", self.root, "--client", "claude"])
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertIn("other", cfg["mcpServers"])
        self.assertIn("xzagent", cfg["mcpServers"])

    def test_setup_remove_cleans_up(self):
        self._run(["setup", self.root, "-–client".replace("-–", "--"), "claude"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, ".mcp.json")))
        rc, out = self._run(["setup", self.root, "--client", "claude", "--remove"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.isfile(os.path.join(self.root, ".mcp.json")))

    def test_generic_client_prints_snippet_only(self):
        rc, out = self._run(["setup", self.root, "--client", "generic"])
        self.assertEqual(rc, 0)
        cfg = json.loads(out)
        self.assertIn("xzagent", cfg["mcpServers"])
        self.assertFalse(os.path.isfile(os.path.join(self.root, ".mcp.json")))


class TestSetupGlobalClients(unittest.TestCase):
    """antigravity/windsurf: entries in this MACHINE's global config."""

    def setUp(self):
        self.old_env = {k: os.environ.get(k)
                        for k in ("HOME", "USERPROFILE") if os.environ.get(k)}
        self.home = tempfile.mkdtemp()
        os.environ["HOME"] = self.home
        os.environ["USERPROFILE"] = self.home
        self.projA = make_sample_project(os.path.join(self.home, "proj_a"))
        self.projB = make_sample_project(os.path.join(self.home, "proj_b"))

    def tearDown(self):
        for k, v in list(self.old_env.items()):
            os.environ[k] = v
        for k in ("HOME", "USERPROFILE"):
            if k not in self.old_env:
                os.environ.pop(k, None)
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def _cfg_path(self):
        return os.path.join(self.home, ".gemini", "config",
                            "mcp_config.json")

    def _legacy_cfg_path(self):
        return os.path.join(self.home, ".gemini", "antigravity",
                            "mcp_config.json")

    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(argv)
        return rc, buf.getvalue()

    def test_antigravity_writes_this_machines_global_file(self):
        self.assertFalse(os.path.isfile(self._cfg_path()))
        rc, out = self._run(["setup", self.projA, "--client", "antigravity"])
        self.assertEqual(rc, 0)
        with open(self._cfg_path(), encoding="utf-8") as fh:
            entry = json.load(fh)["mcpServers"]["xzagent"]
        self.assertEqual(entry["args"], ["serve", os.path.abspath(self.projA)])
        self.assertTrue(entry["command"].endswith("ctxp"))
        self.assertIn("never in a commit", out)

    def test_second_project_gets_its_own_key(self):
        self._run(["setup", self.projA, "--client", "antigravity"])
        self._run(["setup", self.projB, "--client", "antigravity"])
        with open(self._cfg_path(), encoding="utf-8") as fh:
            servers = json.load(fh)["mcpServers"]
        self.assertEqual(servers["xzagent"]["args"],
                         ["serve", os.path.abspath(self.projA)])
        self.assertEqual(servers["xzagent_proj_b"]["args"],
                         ["serve", os.path.abspath(self.projB)])
        # re-running project A reuses A's key: still exactly two servers
        self._run(["setup", self.projA, "--client", "antigravity"])
        with open(self._cfg_path(), encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["mcpServers"]), 2)

    def test_fresh_machine_writes_current_path_only(self):
        rc, out = self._run(["setup", self.projA, "--client", "antigravity"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(self._cfg_path()))
        self.assertFalse(os.path.isfile(self._legacy_cfg_path()))

    def test_migrated_machine_prefers_current_over_legacy(self):
        # a migrated app made ~/.gemini/config/, but old ctxp left a
        # file in the legacy spot: the NEW path must win
        os.makedirs(os.path.dirname(self._cfg_path()), exist_ok=True)
        os.makedirs(os.path.dirname(self._legacy_cfg_path()), exist_ok=True)
        with open(self._legacy_cfg_path(), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {"xzagent": {"command": "ctxp",
                                                  "args": ["serve", self.projA]}}},
                      fh)
        self._run(["setup", self.projA, "--client", "antigravity"])
        self.assertTrue(os.path.isfile(self._cfg_path()))
        with open(self._cfg_path(), encoding="utf-8") as fh:
            self.assertIn("xzagent", json.load(fh)["mcpServers"])
        # legacy file untouched by the ADD (cleaned only via --remove)
        with open(self._legacy_cfg_path(), encoding="utf-8") as fh:
            self.assertEqual(len(json.load(fh)["mcpServers"]), 1)

    def test_old_install_still_on_legacy_path_writes_there(self):
        os.makedirs(os.path.dirname(self._legacy_cfg_path()), exist_ok=True)
        rc, out = self._run(["setup", self.projA, "--client", "antigravity"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.isfile(self._cfg_path()))
        self.assertTrue(os.path.isfile(self._legacy_cfg_path()))

    def test_remove_also_cleans_legacy_location(self):
        # simulate an old ctxp write into the legacy path
        os.makedirs(os.path.dirname(self._legacy_cfg_path()), exist_ok=True)
        with open(self._legacy_cfg_path(), "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {
                "xzagent": {"command": "ctxp",
                            "args": ["serve", os.path.abspath(self.projA)]}}}, fh)
        rc, out = self._run(["setup", self.projA, "--client", "antigravity",
                             "--remove"])
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.isfile(self._legacy_cfg_path()))

    def test_remove_takes_only_this_project_out(self):
        self._run(["setup", self.projA, "--client", "antigravity"])
        self._run(["setup", self.projB, "--client", "antigravity"])
        rc, out = self._run(["setup", self.projA, "--client", "antigravity",
                             "--remove"])
        self.assertEqual(rc, 0)
        with open(self._cfg_path(), encoding="utf-8") as fh:
            servers = json.load(fh)["mcpServers"]
        self.assertNotIn("xzagent", servers)
        self.assertIn("xzagent_proj_b", servers)
