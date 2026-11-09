import argparse
import json
import os
import sys

from .graph import call_graph, neighbors
from .index import connect, default_db_path, ensure_index
from .map import repo_map
from .packer import build_pack
from .rank import rank_files


def _open_db(args):
    root = os.path.abspath(args.root)
    db = ensure_index(root, args.db, force=args.reindex)
    return root, db, connect(db)


def cmd_index(args):
    root, db, con = _open_db(args)
    n = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    print("Indexed %d files -> %s" % (n, db))
    return 0


def cmd_map(args):
    root, db, con = _open_db(args)
    sys.stdout.write(repo_map(con))
    return 0


def cmd_ask(args):
    root, db, con = _open_db(args)
    for path, score, reasons in rank_files(con, args.query, top_k=args.top):
        print("%.2f  %s  (%s)" % (score, path, ", ".join(reasons[:3])))
    return 0


def cmd_pack(args):
    root, db, con = _open_db(args)
    text = build_pack(con, root, args.query, top_k=args.top)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("Wrote %s" % args.output)
    else:
        sys.stdout.write(text)
    return 0


def cmd_graph(args):
    root, db, con = _open_db(args)
    if args.symbol:
        print(json.dumps(call_graph(con, args.symbol), indent=2))
    else:
        for n in neighbors(con, args.path):
            print("%s %s %s" % (n["kind"], n["other"], n.get("label") or ""))
    return 0


def cmd_serve(args):
    from .mcp_server import serve
    serve(os.path.abspath(args.root), args.db)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="ctxp", description="Offline context packer for AI agents on local projects.")
    p.add_argument("--db", default=None, help="Index DB path (default: <root>/.xzagent/index.db)")
    p.add_argument("--reindex", action="store_true", help="Rebuild the index even if it exists")
    sub = p.add_subparsers(dest="command", required=True)

    def add_root(sp):
        sp.add_argument("root", help="Project root path")

    sp = sub.add_parser("index", help="Build the index for a project")
    add_root(sp)
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("map", help="Print the repo map")
    add_root(sp)
    sp.set_defaults(func=cmd_map)

    sp = sub.add_parser("ask", help="Rank files relevant to a task")
    add_root(sp)
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=10)
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("pack", help="Write a full context pack (context.md) for a task")
    add_root(sp)
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=8)
    sp.add_argument("-o", "--output", default=None)
    sp.set_defaults(func=cmd_pack)

    sp = sub.add_parser("graph", help="Show call graph for a symbol, or neighbors of a file")
    add_root(sp)
    sp.add_argument("symbol", nargs="?", default=None)
    sp.add_argument("--path", default=None, help="Show import/call neighbors of this file instead")
    sp.set_defaults(func=cmd_graph)

    sp = sub.add_parser("serve", help="Run the MCP stdio server for this project")
    add_root(sp)
    sp.set_defaults(func=cmd_serve)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)
