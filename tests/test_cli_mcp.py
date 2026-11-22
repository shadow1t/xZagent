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
