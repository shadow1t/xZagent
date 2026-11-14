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
        rc, out = self._run(["--db", self.db, "ask", self.root, "login bug", "--top", "3"])
        self.assertIn("app/auth.py", out.splitlines()[0])
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
             "params": {"name": "find_files", "arguments": {"task": "login authentication bug", "top_k": 3}}},
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
                        "arguments": {"query": "login authentication bug", "top_k": 3}}},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "call_graph",
                        "arguments": {"file_path": "app/main.py", "symbol_name": "login"}}},
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


if __name__ == "__main__":
    unittest.main()
