import argparse
import json
import os
import sys
import time

from .find import find_matching, recently_modified
from .graph import call_graph, neighbors
from .index import connect, default_db_path, ensure_index
from .map import repo_map
from .packer import build_pack
from .rank import rank_files
from . import __version__
from .tui import BLUE, CYAN, GRAY, GREEN, VIOLET, WHITE, Progress, bar, color_enabled, paint


def _split_skips(value):
    """--skip may be passed multiple times and/or comma-separated."""
    if value is None:
        return None
    out = []
    for item in value:
        out.extend(p.strip() for p in item.split(",") if p.strip())
    return out


def _open_db(args, progress=None):
    root = os.path.abspath(args.root)
    index_all = getattr(args, "index_all", None)
    default_skips = getattr(args, "default_skips", None)
    if index_all and default_skips:
        raise SystemExit("error: use either --index-all or --default-skips, not both")
    no_default_skips = True if index_all else (False if default_skips else None)
    db = ensure_index(
        root, args.db, force=args.reindex,
        no_default_skips=no_default_skips,
        extra_ignores=_split_skips(getattr(args, "skip", None)),
        progress=progress,
    )
    return root, db, connect(db)


def cmd_index(args):
    prog = Progress()
    t0 = time.perf_counter()
    root, db, con = _open_db(args, progress=prog.update if prog.enabled else None)
    dt = time.perf_counter() - t0
    n = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
    prog.finish()
    if color_enabled():
        print("%s%s%s%s%s%s%s" % (
            paint("\u2714", GREEN), paint(" Indexed ", WHITE),
            paint("%d files" % n, GREEN, bold=True), paint(" in ", WHITE),
            paint("%.2fs" % dt, GREEN, bold=True), paint("  \u2192  ", GRAY),
            paint(db, GRAY)))
    else:
        print("Indexed %d files -> %s" % (n, db))
    return 0


def cmd_map(args):
    root, db, con = _open_db(args)
    per_dir = getattr(args, "per_dir", 25)
    if getattr(args, "full", False) or not per_dir:
        per_dir = None
    out = repo_map(con, max_per_dir=per_dir)
    if getattr(args, "output", None):
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        print("Wrote %s" % args.output)
    else:
        sys.stdout.write(out)
    return 0


def cmd_ask(args):
    root, db, con = _open_db(args)
    top = args.top or 0
    ranked = rank_files(
        con, args.query,
        top_k=top,
        pattern=getattr(args, "pattern", None),
        only_lang=getattr(args, "lang", None),
        only_exts=getattr(args, "only_ext", None),
        exclude_exts=getattr(args, "skip_ext", None),
        exclude_dirs=getattr(args, "skip_dirs", None),
        only_dirs=getattr(args, "only_dirs", None),
        recent_bias=getattr(args, "recent", False),
        no_tests=getattr(args, "no_tests", False),
    )
    if not ranked:
        print("No matching files.")
        return 0
    if color_enabled():
        total_files = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
        print("%s%s" % (
            paint("\u2714 %d most relevant files" % len(ranked), GREEN, bold=True),
            paint(" (out of %d)" % total_files, GRAY)))
        import shutil

        from .tui import clip_left, clip_right, display_width, pad_to
        cols = max((shutil.get_terminal_size().columns or 80) - 1, 40)
        bar_w = 12
        path_w = min(max(display_width(p) for p, _s, _r in ranked),
                     max(20, cols - bar_w - 24))
        hi, lo = ranked[0][1], ranked[-1][1]
        span = (hi - lo) or 1.0
        for i, (path, score, reasons) in enumerate(ranked, 1):
            frac = (score - lo) / span if len(ranked) > 1 else 1.0
            frac = max(frac, 0.08)
            fixed = 4 + path_w + 2 + bar_w
            tail = ""
            avail = cols - fixed - 2
            if avail >= 8:
                tail = paint("  " + clip_right(", ".join(reasons[:3]), avail), GRAY)
            print("%s%s%s%s%s%s" % (
                paint("%2d" % i, CYAN, bold=True), paint("  ", WHITE),
                pad_to(paint(clip_left(path, path_w), WHITE, bold=True), path_w),
                paint("  ", WHITE), bar(frac, bar_w), tail))
    else:
        for path, score, reasons in ranked:
            print("%.2f  %s  (%s)" % (score, path, ", ".join(reasons[:3])))
    return 0


def cmd_find(args):
    root, db, con = _open_db(args)
    recent = bool(getattr(args, "recent", False))
    if recent and not args.pattern:
        rows = recently_modified(
            con,
            count=args.top or 15,
            exclude_exts=getattr(args, "skip_ext", None),
            exclude_dirs=getattr(args, "skip_dirs", None),
            only_dirs=getattr(args, "only_dirs", None),
            only_exts=getattr(args, "only_ext", None),
        )
    else:
        rows = find_matching(
            con,
            pattern=args.pattern,
            lang=getattr(args, "lang", None),
            ext=getattr(args, "ext", None),
            symbol=getattr(args, "symbol", None),
            exclude_exts=getattr(args, "skip_ext", None),
            exclude_dirs=getattr(args, "skip_dirs", None),
            only_dirs=getattr(args, "only_dirs", None),
            only_exts=getattr(args, "only_ext", None),
            recent=recent,
            top=args.top or None,
            no_tests=getattr(args, "no_tests", False),
        )
    if not rows:
        print("No matching files.")
        return 0
    if color_enabled():
        for r in rows:
            line = "%s %s  %s" % (
                paint((r["lang"] or "text").ljust(5), BLUE),
                paint(r["modified"], GRAY), paint(r["path"], WHITE, bold=True))
            if r.get("symbols"):
                line += "  " + paint("[" + ", ".join(r["symbols"][:4]) + "]", VIOLET)
            print(line)
        return 0
    for r in rows:
        line = "%-5s %s  %s" % (r["lang"] or "text", r["modified"], r["path"])
        if r.get("symbols"):
            line += "  [" + ", ".join(r["symbols"][:4]) + "]"
        print(line)
    return 0


def cmd_pack(args):
    root, db, con = _open_db(args)
    text = build_pack(con, root, args.query, top_k=args.top)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        if color_enabled():
            print("%s%s%s%s%s" % (
                paint("\u2714 ", GREEN), paint(args.output, WHITE, bold=True),
                paint(" ready", WHITE), paint(" \u00b7 ", GRAY),
                paint("%.1f KB" % (len(text.encode("utf-8")) / 1024.0), CYAN, bold=True),
            ))
        else:
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


def cmd_schema(args):
    import json as _json

    from .schema import fetch_postgres_schema, format_schema
    if getattr(args, "pg", None):
        tables = fetch_postgres_schema(args.pg)
        rows = [{"name": n, "source": "postgresql", "columns": t["columns"], "fks": t["fks"]}
                for n, t in sorted(tables.items())]
        if getattr(args, "html", None):
            from .schema import schema_html
            with open(args.html, "w", encoding="utf-8") as fh:
                fh.write(schema_html(rows))
            print("Wrote %s (open in browser)" % args.html)
            return 0
        sys.stdout.write(format_schema(rows))
        return 0
    root, db, con = _open_db(args)
    rows = [
        {"name": r["name"], "source": r["source"],
         "columns": _json.loads(r["columns"]), "fks": _json.loads(r["fks"])}
        for r in con.execute("SELECT name, source, columns, fks FROM db_tables").fetchall()
    ]
    if getattr(args, "html", None):
        from .schema import schema_html
        with open(args.html, "w", encoding="utf-8") as fh:
            fh.write(schema_html(rows))
        print("Wrote %s (open in browser)" % args.html)
        return 0
    out = format_schema(rows)
    if getattr(args, "output", None):
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
        print("Wrote %s" % args.output)
    else:
        sys.stdout.write(out)
    return 0


def cmd_serve(args):
    from .mcp_server import serve
    serve(os.path.abspath(args.root), args.db)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="ctxp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Offline context packer for AI agents on local projects.\n"
            "Indexes a codebase once, then answers: which files matter for a task,\n"
            "what does the repo look like, and how do files/symbols relate.\n\n"
            "Full docs for humans and AI agents: docs/USAGE.md (every key, tool\n"
            "and workflow below is documented there in detail)."
        ),
        epilog=(
            "examples:\n"
            "  ctxp map . -o map.md                repo map (every dir shown; --full for all files)\n"
            "  ctxp map . --reindex               rebuild the index first\n"
            "  ctxp find 'api_v2*' --only-dirs api,ui    filename glob, scoped to dirs\n"
            "  ctxp find --symbol 'Dialog*' --recent --top 5    symbol search, freshest first\n"
            "  ctxp ask 'fix v2 auth check' --only-dirs api --skip-ext .md,.txt\n"
            "  ctxp ask 'billing flow' --only-ext .py,.tsx    rank only real code files\n"
            "  ctxp ask 'billing flow' --no-tests              score code, not its tests\n"
            "  ctxp pack 'refactor login flow' -o context.md\n"
            "  ctxp graph --path api_v2/auth.py   import/call neighbors\n"
            "  ctxp index . --index-all            disable built-in noise skips once\n"
            "  ctxp index . --skip docs/ --skip '*.g.dart'   sticky extra excludes\n"
            "  ctxp serve .                       run the MCP stdio server\n\n"
            "index-time excludes use gitignore syntax. '.xzagentignore' in the project\n"
            "root adds permanent per-project excludes ('!' re-includes).\n"
            "global flags (--db, --reindex, --index-all, --default-skips, --skip) work\n"
            "BEFORE or AFTER the subcommand: ctxp --reindex map . OR ctxp map . --reindex.\n"
            "search-time filters (--pattern/--only-dirs/--only-ext/--skip-ext/--skip-dirs/--no-tests) narrow\n"
            "existing indexes and never rebuild them."
        ),
    )
    p.add_argument("--version", action="version", version="xzagent " + __version__)
    p.add_argument("--db", default=None, help="Index DB path (default: <root>/.xzagent/index.db)")
    p.add_argument("--reindex", action="store_true", help="Rebuild the index even if it exists")
    p.add_argument("--index-all", action="store_true", default=None,
                   help="Index EVERYTHING: disable built-in noise skips (tmp_*, *.min.js, migrations/, ...)")
    p.add_argument("--default-skips", action="store_true", default=None,
                   help="Restore the built-in noise skips (after a previous --index-all)")
    p.add_argument("--skip", metavar="PATTERN", action="append", default=None,
                   help="Extra excludes for indexing, gitignore syntax, repeatable: "
                        "--skip docs/ --skip '*.g.dart' (stored in the index)")
    sub = p.add_subparsers(dest="command", required=True)

    def add_root(sp):
        sp.add_argument("root", nargs="?", default=".", help="Project root path (default: current directory)")

    sp = sub.add_parser("index", help="Build the index for a project")
    add_root(sp)
    sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("map", help="Print the repo map (every directory is always shown)")
    add_root(sp)
    sp.add_argument("-o", "--output", help="Write the map to a file instead of stdout")
    sp.add_argument("--per-dir", type=int, default=25, metavar="N",
                    help="Max files shown per directory in the tree (default: 25, 0 = unlimited)")
    sp.add_argument("--full", action="store_true", help="Show every file with no per-directory cap")
    sp.set_defaults(func=cmd_map)

    sp = sub.add_parser("ask", help="Rank files relevant to a task")
    add_root(sp)
    sp.add_argument("query")
    sp.add_argument("--top", type=int, default=10, help="Max results (0 = all matches)")
    sp.add_argument("--pattern", metavar="GLOB", help="Only rank files matching this glob (e.g. api_v2* or *Dialog*.tsx)")
    sp.add_argument("--lang", help="Only these languages, comma-separated (e.g. python,typescript)")
    sp.add_argument("--only-ext", metavar="EXTS",
                    help="Only rank files with these extensions, comma-separated (e.g. .py,.tsx)")
    sp.add_argument("--skip-ext", metavar="EXTS", help="Skip extensions, comma-separated (e.g. .md,.txt)")
    sp.add_argument("--skip-dirs", metavar="DIRS", help="Skip these directory names, comma-separated (e.g. docs,tests)")
    sp.add_argument("--only-dirs", metavar="DIRS",
                    help="Only search files under these directory names, comma-separated (matches any depth, e.g. api,ui)")
    sp.add_argument("--no-tests", action="store_true",
                    help="Exclude test files (test_*, *_test.*, *.test.*, *.spec.*, tests/ dirs, ...)")
    sp.add_argument("--recent", action="store_true", help="Prefer recently modified files among the matches")
    sp.set_defaults(func=cmd_ask)

    sp = sub.add_parser("find", help="Find files by name/path glob, language, extension, or defining symbol")
    add_root(sp)
    sp.add_argument("pattern", nargs="?", default=None,
                    help="Glob pattern(s), comma-separated: 'api_v2*', '*Dialog*.tsx', 'docs/**'")
    sp.add_argument("--lang", help="Filter by language, comma-separated (e.g. tsx,python)")
    sp.add_argument("--ext", metavar="EXTS", help="Filter by extension, comma-separated (e.g. .tsx,.ts)")
    sp.add_argument("--only-ext", metavar="EXTS",
                    help="Same as --ext, kept for consistency with ask (e.g. .py,.tsx)")
    sp.add_argument("--symbol", metavar="GLOB", help="Only files defining a class/function matching glob (e.g. Dialog*, *Handler)")
    sp.add_argument("--skip-ext", metavar="EXTS", help="Skip extensions, comma-separated (e.g. .md,.txt)")
    sp.add_argument("--skip-dirs", metavar="DIRS", help="Skip directory names, comma-separated (e.g. docs,generated)")
    sp.add_argument("--only-dirs", metavar="DIRS",
                    help="Only files under these directory names, comma-separated (matches any depth, e.g. api,ui)")
    sp.add_argument("--no-tests", action="store_true",
                    help="Exclude test files (test_*, *_test.*, *.test.*, *.spec.*, tests/ dirs, ...)")
    sp.add_argument("--recent", action="store_true", help="Sort by last-modified (newest first)")
    sp.add_argument("--top", type=int, default=None, help="Limit result count")
    sp.set_defaults(func=cmd_find)

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

    sp = sub.add_parser("schema", help="Show DB schema (tables, columns, foreign keys) from .sql files and SQLite DBs")
    add_root(sp)
    sp.add_argument("-o", "--output", help="Write the schema to a file instead of stdout")
    sp.add_argument("--html", help="Write an interactive HTML network map of tables/relations")
    sp.add_argument("--pg", metavar="DSN", help="Fetch schema from a live PostgreSQL server, e.g. postgresql://user:pass@localhost:5432/dbname")
    sp.set_defaults(func=cmd_schema)

    sp = sub.add_parser("serve", help="Run the MCP stdio server for this project")
    add_root(sp)
    sp.set_defaults(func=cmd_serve)
    return p


_GLOBAL_OPTS = {"--db", "--reindex", "--index-all", "--default-skips", "--skip"}
_GLOBAL_OPTS_WITH_VALUE = {"--db", "--skip"}


def _hoist_global_opts(argv):
    """Accept global flags AFTER the subcommand too (ctxp map . --reindex).

    argparse only parses them before the subcommand, so relocate them there
    up front. --skip/--db values travel with their flag, which also keeps
    repeatable --skip values from both sides merging in command-line order.
    """
    argv = list(argv)
    # find the subcommand: skip global flags AND their values before it
    sub_at, i = None, 0
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("-"):
            name = tok.split("=", 1)[0]
            i += 2 if ("=" not in tok and name in _GLOBAL_OPTS_WITH_VALUE) else 1
            continue
        sub_at = i
        break
    if sub_at is None:
        return argv
    hoisted, kept = [], []
    tail = argv[sub_at:]
    i = 0
    while i < len(tail):
        tok = tail[i]
        name = tok.split("=", 1)[0]
        if name in _GLOBAL_OPTS:
            hoisted.append(tok)
            if "=" not in tok and name in _GLOBAL_OPTS_WITH_VALUE and i + 1 < len(tail):
                hoisted.append(tail[i + 1])
                i += 1
        else:
            kept.append(tok)
        i += 1
    if not hoisted:
        return argv
    return argv[:sub_at] + hoisted + kept


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = build_parser().parse_args(_hoist_global_opts(argv))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())