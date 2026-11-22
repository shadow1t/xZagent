import json
import os
import sys

from .graph import call_graph, neighbors
from .index import connect, default_db_path, ensure_index
from .map import repo_map
from .packer import build_pack
from .rank import rank_files

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "repo_map",
        "description": "Compressed map of the whole project: full directory tree (every directory shown, files capped per directory) plus optional per-file key symbols.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "per_dir": {"type": "integer", "default": 25,
                            "description": "Max files shown per directory in the tree (0 = unlimited)"},
                "files": {"type": "boolean", "default": False,
                          "description": "Include the per-file detail list (large output for big projects)"},
            },
        },
    },
    {
        "name": "find_files",
        "description": "Rank the most relevant files for a natural-language task (offline BM25 + graph boost).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What the agent is trying to do or fix"},
                "top_k": {"type": "integer", "default": 8},
            },
            "required": ["task"],
        },
    },
    {
        "name": "file_outline",
        "description": "List classes/functions defined in a file. Path relative to project root (absolute path also accepted).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "call_graph",
        "description": "Where a symbol is defined, who calls it, and what file it lives in.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "neighbors",
        "description": "Files linked to a file via imports or calls. Path relative to project root (absolute also accepted).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "context_pack",
        "description": "Full markdown context pack for a task: relevant files + repo map + excerpts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "top_k": {"type": "integer", "default": 8},
            },
            "required": ["task"],
        },
    },
    {
        "name": "db_schema",
        "description": "Database schema: tables, columns, and foreign-key relations (from .sql files and SQLite DBs in the project).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def normalize_path(root, path):
    """Accept absolute paths, root-prefixed paths, or relative paths; return the indexed relative path."""
    if not path:
        return path
    p = path.replace("\\", "/").strip()
    root_abs = os.path.abspath(root).replace("\\", "/")
    if p.lower().startswith(root_abs.lower()):
        p = p[len(root_abs):]
    p = p.lstrip("/")
    base = os.path.basename(root_abs.rstrip("/"))
    if p.lower() == base.lower():
        return ""
    if p.lower().startswith((base + "/").lower()):
        p = p[len(base) + 1:]
    return p


def _arg(args, *names, default=""):
    for n in names:
        if args.get(n):
            return args[n]
    return default


class Server:
    def __init__(self, root, db_path=None):
        self.root = os.path.abspath(root)
        self.db = ensure_index(self.root, db_path)
        self.con = connect(self.db)

    def call(self, name, args):
        if name == "repo_map":
            per_dir = int(args.get("per_dir", 25) or 0) or None
            return repo_map(self.con, max_per_dir=per_dir, include_files_list=bool(args.get("files", False)))
        if name == "find_files":
            ranked = rank_files(self.con, _arg(args, "task", "query"), top_k=int(args.get("top_k", 8)))
            return json.dumps(ranked, indent=2)
        if name == "file_outline":
            path = normalize_path(self.root, _arg(args, "path", "file_path", "file"))
            rows = self.con.execute(
                "SELECT kind, name, lineno FROM symbols WHERE file=? AND kind IN ('class','function') ORDER BY lineno",
                (path,),
            ).fetchall()
            return json.dumps([dict(r) for r in rows], indent=2)
        if name == "call_graph":
            return json.dumps(call_graph(self.con, _arg(args, "symbol", "symbol_name", "name", "function")), indent=2)
        if name == "neighbors":
            path = normalize_path(self.root, _arg(args, "path", "file_path", "file"))
            return json.dumps(neighbors(self.con, path), indent=2)
        if name == "context_pack":
            return build_pack(self.con, self.root, _arg(args, "task", "query"), top_k=int(args.get("top_k", 8)))
        if name == "db_schema":
            rows = [
                {"name": r["name"], "source": r["source"],
                 "columns": json.loads(r["columns"]), "fks": json.loads(r["fks"])}
                for r in self.con.execute("SELECT name, source, columns, fks FROM db_tables").fetchall()
            ]
            return json.dumps(rows, indent=2)
        raise ValueError("unknown tool: %s" % name)


def serve(root, db_path=None):
    """Minimal MCP stdio server (newline-delimited JSON-RPC 2.0)."""
    server = Server(root, db_path)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue
        method = msg.get("method", "")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "xzagent", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = msg.get("params", {})
                result = {
                    "content": [{"type": "text", "text": server.call(params.get("name", ""), params.get("arguments", {}))}],
                    "isError": False,
                }
            else:
                resp = {"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": "Method not found"}}
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue
        except Exception as exc:
            resp = {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32603, "message": str(exc)}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
        sys.stdout.flush()