import json
import os
import sys

from .find import find_matching
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
        "description": "Rank the most relevant files for a natural-language task (offline BM25 + graph boost). "
                       "Use for 'where should I make this change' questions. "
                       "Restrict with 'pattern' (target only files like api_v2*), 'only_lang', 'only_dirs' (e.g. api,ui keeps files under those directories), "
                       "'only_ext' (e.g. .py,.tsx keeps only real code files), "
                       "no_tests=true (drop test_*, *_test.*, *.spec.* files and tests/ dirs), "
                       "'skip_ext' (e.g. .md,.txt), 'skip_dirs' (e.g. docs,tests), "
                       "and prefer the most recently edited matches with recent=true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What the agent is trying to do or fix (use English code keywords)"},
                "query": {"type": "string", "description": "Alias of task"},
                "top_k": {"type": "integer", "default": 15, "description": "Max results returned (0 = all matches)"},
                "pattern": {"type": "string", "description": "Glob(s) the candidate files must match: 'api_v2*', '*Dialog*.tsx', 'web/**' (basename or full path)"},
                "only_lang": {"type": "string", "description": "Comma-separated languages to keep: 'python', 'typescript,tsx'"},
                "only_dirs": {"type": "string", "description": "Comma-separated directory names files MUST live under (any depth): 'api,ui'"},
                "only_ext": {"type": "string", "description": "Comma-separated extensions files MUST have: '.py,.tsx'"},
                "skip_ext": {"type": "string", "description": "Comma-separated extensions to skip: '.md,.txt,.log'"},
                "skip_dirs": {"type": "string", "description": "Comma-separated directory names to skip: 'docs,tests,generated'"},
                "recent": {"type": "boolean", "default": False, "description": "Prefer the most recently modified matches (continue-where-I-left-off tasks)"},
                "no_tests": {"type": "boolean", "default": False,
                             "description": "Exclude test files: test_*, tests_*, *_test.*, *.test.*, *.spec.*, *Test.java, conftest.py and tests/, __tests__/, spec/, e2e/, cypress/ directories"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "find",
        "description": "Find files by NAME/PATH pattern, not content relevance: 'api_v2*' (versioned API files), "
                       "'*Dialog*.tsx', 'docs/**', with optional language, extension, defining-symbol, directory "
                       "and exclusion filters. Use this BEFORE find_files when the task names specific file names "
                       "or a versioned subset, so you target api_v2 files instead of every api file. "
                       "recent=true sorts by last modified.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Comma-separated globs matched against basename and full path: 'api_v2*', 'client_*_v2', '*Handler.py'"},
                "lang": {"type": "string", "description": "Comma-separated languages: 'python', 'typescript,tsx'"},
                "ext": {"type": "string", "description": "Comma-separated extensions: '.tsx,.ts'"},
                "symbol": {"type": "string", "description": "Only files defining a class/function matching this glob: 'Dialog*', '*Handler'"},
                "only_dirs": {"type": "string", "description": "Comma-separated directory names files MUST live under (any depth): 'api,ui'"},
                "only_ext": {"type": "string", "description": "Comma-separated extensions files MUST have: '.tsx,.ts' (alias of ext)"},
                "skip_ext": {"type": "string", "description": "Comma-separated extensions to skip: '.md,.txt'"},
                "skip_dirs": {"type": "string", "description": "Comma-separated directory names to skip: 'docs,generated'"},
                "recent": {"type": "boolean", "default": False, "description": "Sort by last-modified time, newest first"},
                "no_tests": {"type": "boolean", "default": False,
                             "description": "Exclude test files: test_*, tests_*, *_test.*, *.test.*, *.spec.*, *Test.java, conftest.py and tests/, __tests__/, spec/, e2e/, cypress/ directories"},
                "top": {"type": "integer", "default": 50, "description": "Limit result count"},
            },
        },
    },
    {
        "name": "recent_files",
        "description": "The most recently modified files in the project (newest first), with last-modified timestamps. "
                       "Use for 'what was I working on / continue the last task' questions, or after the user says "
                       "'this file here' without naming it. Optional pattern/lang/only_ext/only_dirs/skip filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 15, "description": "How many files to return"},
                "pattern": {"type": "string", "description": "Optional glob filter: 'api_*', '*Dialog*'"},
                "lang": {"type": "string", "description": "Optional language filter: 'typescript'"},
                "only_dirs": {"type": "string", "description": "Comma-separated directory names files MUST live under (any depth): 'api,ui'"},
                "only_ext": {"type": "string", "description": "Comma-separated extensions files MUST have: '.py,.tsx'"},
                "skip_ext": {"type": "string", "description": "Comma-separated extensions to skip: '.md,.txt'"},
                "skip_dirs": {"type": "string", "description": "Comma-separated directory names to skip"},
                "no_tests": {"type": "boolean", "default": False,
                             "description": "Exclude test files (names and tests/ dirs heuristics)"},
            },
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
            ranked = rank_files(
                self.con, _arg(args, "task", "query"),
                top_k=int(args.get("top_k", 15) or 0),
                pattern=args.get("pattern"),
                only_lang=args.get("only_lang"),
                only_dirs=args.get("only_dirs"),
                only_exts=args.get("only_ext"),
                exclude_exts=args.get("skip_ext"),
                exclude_dirs=args.get("skip_dirs"),
                recent_bias=bool(args.get("recent", False)),
                no_tests=bool(args.get("no_tests", False)),
            )
            return json.dumps(ranked, indent=2)
        if name == "find":
            return json.dumps(find_matching(
                self.con,
                pattern=args.get("pattern"),
                lang=args.get("lang"),
                ext=args.get("ext"),
                symbol=args.get("symbol"),
                only_dirs=args.get("only_dirs"),
                only_exts=args.get("only_ext"),
                exclude_exts=args.get("skip_ext"),
                exclude_dirs=args.get("skip_dirs"),
                recent=bool(args.get("recent", False)),
                top=int(args.get("top", 50) or 0) or None,
                no_tests=bool(args.get("no_tests", False)),
            ), indent=2, default=str)
        if name == "recent_files":
            return json.dumps(find_matching(
                self.con,
                pattern=args.get("pattern"),
                lang=args.get("lang"),
                only_dirs=args.get("only_dirs"),
                only_exts=args.get("only_ext"),
                exclude_exts=args.get("skip_ext"),
                exclude_dirs=args.get("skip_dirs"),
                recent=True,
                top=int(args.get("count", 15) or 15),
                no_tests=bool(args.get("no_tests", False)),
            ), indent=2, default=str)
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