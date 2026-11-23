import argparse
import json
import os
import shutil
import sys
import time

from .find import find_matching, find_on_disk, recently_modified
from .graph import call_graph, neighbors
from .index import connect, default_db_path, ensure_index
from .interrupt import ABORT_EXIT_CODE, abort_line, install_guard, restore_guard
from .map import repo_map
from .packer import build_pack
from .rank import rank_files
from .selfupdate import child_finish, selfupdate
from .uninstall import REINSTALL_HINT, uninstall
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


def _justify_root_and_main(args, main_attr):
    """Forgiving positional order: `ctxp find "tmp_*"` inside a project.

    ROOT is a positional, so a lone pattern/query/symbol naturally lands
    in it. If the main argument is missing and ROOT is not a directory,
    they obviously swapped: run against the current directory. If both
    are given and only the main one is a directory, they were typed the
    other way round; swap them. Anything else reaches _open_db, which
    rejects it with a one-line error instead of a traceback.
    """
    main = getattr(args, main_attr, None)
    root = getattr(args, "root", None)
    if main is None and root and root != "." and not os.path.isdir(root):
        setattr(args, main_attr, root)
        args.root = "."
    elif (main is not None and root and root != "."
          and not os.path.isdir(root) and os.path.isdir(main)):
        setattr(args, main_attr, root)
        args.root = main


def _open_db(args):
    """Open the index, showing live progress whenever a (re)build is needed.

    Every command goes through here (index, map, ask, find, pack, graph,
    schema), so an automatic rebuild behind `ctxp ask` or `ctxp map
    --reindex` is just as visible as an explicit `ctxp index` -- never a
    silent multi-second stall. Piped/Non-TTY sessions stay byte-identical.
    """
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        raise SystemExit(
            "error: %s is not a directory\n"
            "  tip: run inside the project, or pass its folder first"
            % args.root)
    index_all = getattr(args, "index_all", None)
    default_skips = getattr(args, "default_skips", None)
    if index_all and default_skips:
        raise SystemExit("error: use either --index-all or --default-skips, not both")
    no_default_skips = True if index_all else (False if default_skips else None)
    prog = Progress()
    prog.start()
    try:
        db = ensure_index(
            root, args.db, force=args.reindex,
            no_default_skips=no_default_skips,
            extra_ignores=_split_skips(getattr(args, "skip", None)),
            progress=prog.update if prog.enabled else None,
        )
    finally:
        prog.finish()
    return root, db, connect(db)


def cmd_update(args):
    prog = Progress()
    prog.start()
    try:
        return selfupdate(
            path_arg=getattr(args, "src", None),
            no_pull=bool(getattr(args, "no_pull", False)),
            progress=prog.update if prog.enabled else None,
        )
    finally:
        prog.finish()


def cmd_index(args):
    t0 = time.perf_counter()
    root, db, con = _open_db(args)
    dt = time.perf_counter() - t0
    n = con.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]
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
    _justify_root_and_main(args, "query")
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
    _justify_root_and_main(args, "pattern")
    root, db, con = _open_db(args)
    recent = bool(getattr(args, "recent", False))
    common = {
        "exclude_exts": getattr(args, "skip_ext", None),
        "exclude_dirs": getattr(args, "skip_dirs", None),
        "only_dirs": getattr(args, "only_dirs", None),
        "only_exts": getattr(args, "only_ext", None),
    }
    if recent and not args.pattern:
        rows = recently_modified(con, count=args.top or 15, **common)
    else:
        # Pattern search also scans the disk for noise-skipped files
        # (tmp_*, *.min.js, lockfiles, ...) the indexer never ingested:
        # find is explicit targeting, so named matches must show up.
        rows = find_matching(
            con,
            pattern=args.pattern,
            lang=getattr(args, "lang", None),
            ext=getattr(args, "ext", None),
            symbol=getattr(args, "symbol", None),
            recent=recent,
            top=None,
            no_tests=getattr(args, "no_tests", False),
            **common,
        )
        if args.pattern and not getattr(args, "symbol", None):
            # the disk pass can walk a whole big tree: keep the console
            # alive with a live counter, exactly like the index build does
            prog = Progress()
            prog.start()
            try:
                disk_rows = find_on_disk(
                    root,
                    pattern=args.pattern,
                    lang=getattr(args, "lang", None),
                    ext=getattr(args, "ext", None),
                    no_tests=getattr(args, "no_tests", False),
                    extra_ignores=_split_skips(getattr(args, "skip", None)),
                    no_default_skips=(True if getattr(args, "index_all", None)
                                      else (False if getattr(args, "default_skips", None) else None)),
                    progress=prog.update if prog.enabled else None,
                    **common,
                )
            finally:
                prog.finish()
            have = {r["path"] for r in rows}
            rows += [r for r in disk_rows if r["path"] not in have]
            if recent:
                rows.sort(key=lambda x: -x["mtime"])
        if args.top:
            rows = rows[:args.top]
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
            if r.get("on_disk"):
                line += "  " + paint("\u00b7 not indexed", GRAY)
            print(line)
        return 0
    for r in rows:
        line = "%-5s %s  %s" % (r["lang"] or "text", r["modified"], r["path"])
        if r.get("symbols"):
            line += "  [" + ", ".join(r["symbols"][:4]) + "]"
        if r.get("on_disk"):
            line += "  [not indexed]"
        print(line)
    return 0


def cmd_pack(args):
    _justify_root_and_main(args, "query")
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
    _justify_root_and_main(args, "symbol")
    root, db, con = _open_db(args)
    if args.symbol:
        print(json.dumps(call_graph(con, args.symbol), indent=2))
    else:
        for n in neighbors(con, args.path):
            print("%s %s %s" % (n["kind"], n["other"], n.get("label") or ""))
    return 0


def cmd_schema(args):
    import json as _json

    from .schema import fetch_postgres_schema, filter_schema_rows, format_schema
    rows_filter = lambda rs: filter_schema_rows(
        rs, getattr(args, "only", None), getattr(args, "exclude", None))
    if getattr(args, "pg", None):
        # live schema fetch can take seconds against a real server
        prog = Progress()
        prog.start()
        try:
            tables = fetch_postgres_schema(args.pg)
        finally:
            prog.finish()
        rows = [{"name": n, "source": "postgresql", "columns": t["columns"], "fks": t["fks"]}
                for n, t in sorted(tables.items())]
        rows = rows_filter(rows)
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
    rows = rows_filter(rows)
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


def cmd_uninstall(args):
    return uninstall(yes=bool(getattr(args, "yes", False)),
                     purge=bool(getattr(args, "purge", False)))


def _get_subparser(topic):
    """The argparse subparser object for one command name, or None."""
    p = build_parser()
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get(topic)
    return None


def cmd_help(args):
    """`ctxp help [COMMAND]`: the curated overview, or one command in depth."""
    topic = getattr(args, "topic", None)
    if not topic:
        build_parser().print_help()
        return 0
    sub = _get_subparser(topic)
    if sub is None:
        raise SystemExit("error: unknown command '%s' \u00b7 run ctxp help" % topic)
    sub.print_help()
    return 0


MCP_CLIENT_FILES = {
    "claude": ".mcp.json",           # Claude Code, project scope
    "cursor": ".cursor/mcp.json",    # Cursor, project scope
}

MCP_GLOBAL_FILES = {
    # Whole-machine config files OUTSIDE any project, so they never
    # land in a commit. These clients do not launch the server with a
    # guaranteed project cwd, so their entries are always absolute,
    # and the server key is per-project (xzagent, xzagent_<folder>, ...)
    # so several projects can coexist in one file.
    # Candidates are ordered CURRENT-FIRST: Antigravity moved its global
    # config, so present builds read ~/.gemini/config/mcp_config.json
    # while pre-migration installs still read ~/.gemini/antigravity/.
    "antigravity": (".gemini/config/mcp_config.json",
                    ".gemini/antigravity/mcp_config.json"),
    "windsurf": (".codeium/windsurf/mcp_config.json",),
}


def _global_candidates(client):
    """All possible global-config paths for this client, current-first."""
    home = os.path.expanduser("~")
    return [os.path.join(home, *rel.split("/"))
            for rel in MCP_GLOBAL_FILES[client]]


def _resolve_global_path(client):
    """The global config file this machine's app ACTUALLY reads.

    The directory that already exists is the one the installed app
    created (a migrated Antigravity makes ~/.gemini/config/), so trust
    it first. A lone file matches too (written on a clean machine).
    Nothing anywhere? Default to the CURRENT path so a first run lands
    where new builds look, never in the legacy location.
    """
    paths = _global_candidates(client)
    for p in paths:
        if os.path.isdir(os.path.dirname(p)):
            return p
    for p in paths:
        if os.path.isfile(p):
            return p
    return paths[0]


def _mcp_entry(root, absolute=False):
    """A stdio server entry for MCP client configs.

    Portable (the default): `ctxp serve .` -- zero machine-specific
    paths, so the committed config works for anyone who has ctxp
    installed. Project-scoped MCP clients (Claude Code .mcp.json,
    Cursor .cursor/mcp.json) launch servers with cwd = project root,
    which is exactly what "." means.

    absolute=True pins THIS machine's ctxp executable and project
    path, for clients that launch from an unexpected cwd or have a
    thin PATH. Never commit that flavor: it leaks your username and
    breaks on every other machine.
    """
    if absolute:
        exe = shutil.which("ctxp")
        return {"command": exe if exe else "ctxp", "args": ["serve", root]}
    return {"command": "ctxp", "args": ["serve", "."]}


def _read_servers(path):
    """Load cfg + the mcpServers dict from one config file, tolerantly."""
    cfg = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError):
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    return cfg, servers


def _apply_mcp_config(path, entry, remove, key="xzagent"):
    """Add (or remove) one server in an mcpServers-format file."""
    cfg, servers = _read_servers(path)
    if remove:
        servers.pop(key, None)
    else:
        servers[key] = entry
    if not servers:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    cfg["mcpServers"] = servers
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    return path


def _global_server_key(servers, root):
    """Pick a stable, unique server key inside a GLOBAL config file.

    Reuses the key already pointing at this project; otherwise takes
    'xzagent', then 'xzagent_<folder>' so a second project in the same
    global file cannot overwrite the first one.
    """
    for key, entry in servers.items():
        if (key.startswith("xzagent") and isinstance(entry, dict)
                and entry.get("args", [None, None])[1:2] == [root]):
            return key
    if "xzagent" not in servers:
        return "xzagent"
    base = "xzagent_" + (os.path.basename(root.rstrip("/\\")) or "proj")
    key, n = base, 2
    while key in servers:
        key = "%s_%d" % (base, n)
        n += 1
    return key


def cmd_setup(args):
    """`ctxp setup`: write MCP client configs for this project.

    Project-scoped clients (claude, cursor) get portable configs
    INSIDE the project, safe to commit. Whole-machine clients
    (antigravity, windsurf) get an absolute entry in their global
    config file, so the app discovers xzagent on this machine without
    any manual JSON editing.
    """
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        raise SystemExit(
            "error: %s is not a directory\n"
            "  tip: run ctxp setup inside the project, or pass its folder"
            % args.root)
    remove = bool(getattr(args, "remove", False))
    absolute = bool(getattr(args, "absolute", False))
    clients = args.client or ["claude", "cursor"]
    if "generic" in clients:
        # any stdio MCP client: print a ready-to-paste snippet, nothing written
        cfg = {"mcpServers": {"xzagent": _mcp_entry(root, absolute)}}
        print(json.dumps(cfg, indent=2))
        return 0
    verb = "removed from" if remove else "written to"
    touched_project_cfg = False
    for client in clients:
        if client in MCP_CLIENT_FILES:      # project-scoped, portable
            rel = MCP_CLIENT_FILES[client]
            path = _apply_mcp_config(os.path.join(root, *rel.split("/")),
                                     _mcp_entry(root, absolute), remove)
            key = "xzagent"
            show = os.path.relpath(path, root) if path else None
            where = "project config"
            touched_project_cfg = True
        else:                              # global, machine-level client
            cands = _global_candidates(client)
            if remove:
                # clean the key from every place older ctxp may have
                # written it (current AND legacy paths)
                path = None
                key = None
                for p in cands:
                    if not os.path.isfile(p):
                        continue
                    k = _global_server_key(_read_servers(p)[1], root)
                    touched = _apply_mcp_config(p, None, True, key=k)
                    if touched is not None and path is None:
                        path, key = touched, k
            else:
                path = _resolve_global_path(client)
                key = _global_server_key(_read_servers(path)[1], root)
                path = _apply_mcp_config(path, _mcp_entry(root, absolute=True),
                                         remove, key=key)
            show = path if path else None
            where = "global machine config \u00b7 never in a commit"
        if color_enabled():
            head = paint("\u2714", GREEN)
            if path:
                print("%s%s%s%s%s%s%s%s" % (
                    head, paint(" MCP config " + verb, WHITE),
                    paint(" \u00b7 ", GRAY), paint(client, CYAN),
                    paint(" \u2192 ", GRAY),
                    paint(show, WHITE, bold=True),
                    paint("  \u00b7 server: %s" % key, GRAY),
                    paint("  \u00b7 %s" % where, GRAY)))
            else:
                print("%s%s%s%s%s" % (
                    head, paint(" MCP config emptied", WHITE),
                    paint(" \u00b7 ", GRAY), paint(client, CYAN),
                    paint(" \u00b7 no servers left, file removed", GRAY)))
        else:
            if path:
                print("MCP config %s: %s -> %s (server %s, %s)"
                      % (verb, client, show, key, where))
            else:
                print("MCP config %s: %s (no servers left, file removed)"
                      % (verb, client))
    if not remove:
        flavor = ("" if touched_project_cfg else "global config \u00b7 machine-wide, do NOT commit")
        if absolute and touched_project_cfg:
            flavor = "absolute paths \u00b7 this machine only, do NOT commit"
        elif touched_project_cfg:
            flavor = "portable \u00b7 no machine paths, safe to commit"
        if color_enabled():
            notes = []
            if touched_project_cfg:
                notes.append("Claude Code: open this project and approve the"
                             " tools once (/mcp to check). Cursor: one restart.")
                notes.append("Commit the config: any agent opening the"
                             " project gets the tools with zero setup.")
            else:
                notes.append("Antigravity / Windsurf: restart the app, the"
                             " tools appear in its tool list.")
            notes.append(flavor)
            print(paint("\u21b3 ", GRAY) +
                  paint(("\n\u21b3 ").join(n for n in notes if n), GRAY))
        else:
            if touched_project_cfg:
                print("Claude Code: open this project and approve the tools "
                      "once (/mcp to check). Cursor: one restart.")
                print("Commit the config: any agent opening the project "
                      "gets the tools with zero setup.")
            else:
                print("Antigravity / Windsurf: restart the app, the tools "
                      "appear in its tool list.")
            if flavor:
                print("Config flavor: " + flavor)
    return 0


def _add_parser(sub, name, **kw):
    """Every subcommand gets multiline descriptions in its own help."""
    kw.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
    return sub.add_parser(name, **kw)


def build_parser():
    p = argparse.ArgumentParser(
        prog="ctxp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "ctxp %s \u00b7 offline context packer for AI agents on local projects.\n"
            "\n"
            "One index, every command reads it. ctxp indexes the codebase once\n"
            "into a portable SQLite DB (<root>/.xzagent/index.db), then answers\n"
            "instantly, fully offline: which files matter for this task, how the\n"
            "repo is laid out, how files and symbols relate, what the database\n"
            "schema looks like.\n"
            "\n"
            "HOW THE COMMANDS FIT TOGETHER\n"
            "  index        build the index (or it builds itself on first use)\n"
            "  map          overview for humans: every directory, top files per dir\n"
            "  ask          pick the few files worth reading for a task (ranked)\n"
            "  find         target files precisely: name/symbol/language/extension\n"
            "  pack         bundle a ready-to-paste context.md for an AI agent\n"
            "  graph        how files import/call each other\n"
            "  schema       tables, columns, FKs (SQL files, SQLite, live PostgreSQL)\n"
            "  setup        connect MCP clients (Claude, Cursor, Antigravity, ...)\n"
            "  serve        run the MCP stdio server those clients launch\n"
            "  update       update the CLI itself in one command\n"
            "  uninstall    remove the CLI from this machine (project data stays)\n"
            "  help         this overview, or per-command help: ctxp help find\n"
            "\n"
            "THE FASTEST WAY TO LEARN A COMMAND\n"
            "  ctxp help <command>      every flag explained, with examples\n"
            "  ctxp <command> -h        the same help\n"
            "  docs/USAGE.md            complete docs for humans and AI agents"
            % __version__
        ),
        epilog=(
            "GETTING HELP\n"
            "  ctxp -h / ctxp help          this overview: commands, flags, workflow\n"
            "  ctxp help <command>           full help for one command, examples included\n"
            "  docs/USAGE.md                 complete docs for humans and AI agents\n"
            "\n"
            "GLOBAL FLAGS (work before or after the subcommand)\n"
            "  --db PATH                     custom index DB path\n"
            "  --reindex                     rebuild the index first\n"
            "  --index-all                   index everything, even noisy files\n"
            "  --default-skips               restore the built-in noise skips\n"
            "  --skip PATTERN                extra exclude, repeatable, gitignore syntax\n"
            "\n"
            "SEARCH FLAGS narrow existing results, never rebuild anything\n"
            "  --pattern, --only-dirs, --skip-dirs, --only-ext, --skip-ext,\n"
            "  --no-tests, --recent, --lang\n"
            "\n"
            "TYPICAL WORKFLOW\n"
            "  1. cd into a project and run: ctxp map         (index builds itself)\n"
            "  2. describe the task:   ctxp ask \"billing retry\"\n"
            "  3. hand over context:   ctxp pack \"billing retry\" -o context.md\n"
            "  4. wire agents once:    ctxp setup             (MCP clients connect)\n"
            "\n"
            "EXAMPLES\n"
            "  ctxp map . -o map.md                         repo map written to a file\n"
            "  ctxp map . --full                            every file, no per-dir cap\n"
            "  ctxp find 'api_v2*' --only-dirs api,ui       name glob, scoped to dirs\n"
            "  ctxp find --symbol 'Dialog*' --recent --top 5  symbol search, freshest first\n"
            "  ctxp ask 'billing flow' --only-ext .py       rank only real code files\n"
            "  ctxp ask 'billing flow' --no-tests           score code, not its tests\n"
            "  ctxp pack 'refactor login flow' -o context.md  full context pack\n"
            "  ctxp graph --path api_v2/auth.py             import/call neighbors\n"
            "  ctxp schema myproj --html schema.html        interactive 3D schema map\n"
            "  ctxp index . --skip docs/ --skip '*.g.dart'  sticky extra excludes\n"
            "  ctxp serve .                                  run the MCP stdio server\n"
            "  ctxp update                                   one-command self-update\n"
            "  ctxp uninstall --purge                       remove the CLI cleanly\n"
            "\n"
            "NICE TO KNOW\n"
            "  Ctrl+C asks before it stops: y stops (exit 130), Enter keeps going;\n"
            "  press Ctrl+C twice to stop without waiting for an answer.\n"
            "  NO_COLOR=1 forces plain output; piped output is plain anyway.\n"
            "  '.xzagentignore' in the project root adds permanent per-project\n"
            "  excludes ('!' re-includes); index-time excludes use gitignore syntax."
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

    sp = _add_parser(sub, "index",
                     help="Build the index for a project",
                     description=(
                         "Build the index: one portable SQLite DB at <root>/.xzagent/index.db.\n"
                         "\n"
                         "Everything else reads this index. Any command that needs it\n"
                         "builds it automatically when missing or stale; ctxp index makes\n"
                         "that build explicit (or forced).\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp index .                    build (or refresh) once\n"
                         "  ctxp index . --reindex           rebuild from scratch\n"
                         "  ctxp index . --index-all         also keep noise files (tmp_*, *.min.js, ...)\n"
                         "  ctxp index . --skip build/       extra exclude, sticky (stored in the index)"))
    add_root(sp)
    sp.set_defaults(func=cmd_index)

    sp = _add_parser(sub, "map",
                     help="Print the repo map (every directory is always shown)",
                     description=(
                         "Print the repo map: every directory always, top files per dir.\n"
                         "\n"
                         "The map is what a human (or an agent) reads first to understand\n"
                         "the layout. Directories are never skipped, only the files shown\n"
                         "inside each one are capped.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp map                         from inside the project\n"
                         "  ctxp map . -o map.md             write to a file\n"
                         "  ctxp map . --per-dir 40          more files per directory\n"
                         "  ctxp map . --full                every file, no cap"))
    add_root(sp)
    sp.add_argument("-o", "--output", help="Write the map to a file instead of stdout")
    sp.add_argument("--per-dir", type=int, default=25, metavar="N",
                    help="Max files shown per directory in the tree (default: 25, 0 = unlimited)")
    sp.add_argument("--full", action="store_true", help="Show every file with no per-directory cap")
    sp.set_defaults(func=cmd_map)

    sp = _add_parser(sub, "ask",
                     help="Rank files relevant to a task (root optional: ctxp ask \"task\" inside the project)",
                     description=(
                         "Rank files by relevance to a task: the shortlist worth reading\n"
                         "first. Scores combine name match, symbol match, path depth,\n"
                         "recency and language match.\n"
                         "\n"
                         "The root is optional inside a project: ctxp ask \"task\".\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp ask 'billing retry flow'\n"
                         "  ctxp ask 'auth check' --top 20\n"
                         "  ctxp ask 'billing flow' --no-tests          score code, not tests\n"
                         "  ctxp ask 'api_v2' --only-dirs api --skip-ext .md,.txt\n"
                         "  ctxp ask 'ui cleanup' --pattern '*Dialog*' --recent"))
    sp.add_argument("query")
    add_root(sp)
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

    sp = _add_parser(sub, "find",
                     help="Find files by name/path glob, language, extension, or defining symbol",
                     description=(
                         "Target files precisely: by name glob, defining symbol, language\n"
                         "or extension. Unlike ask there is no task text: you already\n"
                         "know what you want.\n"
                         "\n"
                         "Also scans the disk for noise-skipped files (tmp_*, *.min.js,\n"
                         "lockfiles, ...) the index never ingested: a named file always\n"
                         "shows up.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp find 'api_v2*'\n"
                         "  ctxp find 'api_v2*' --only-dirs api,ui\n"
                         "  ctxp find --symbol 'Dialog*'\n"
                         "  ctxp find --symbol 'Dialog*' --recent --top 5\n"
                         "  ctxp find --lang python --recent"))
    add_root(sp)
    sp.add_argument("pattern", nargs="?", default=None,
                    help="Glob pattern(s), comma-separated: 'api_v2*', '*Dialog*.tsx', 'docs/**'")
    sp.add_argument("--lang", help="Filter by language, comma-separated (e.g. tsx,python)")
    sp.add_argument("--ext", metavar="EXTS", help="Filter by extension, comma-separated (e.g. .tsx,.ts)")
    sp.add_argument("--only-ext", metavar="EXTS",
                    help="Same as --ext, kept for consistency with ask (e.g. .py,.tsx)")
    sp.add_argument("--symbol", metavar="GLOB", help="Only files defining a class/function matching glob (e.g. Dialog*, *Handler)")
    sp.add_argument("--skip-ext", metavar="EXTS", help="Skip extensions, comma-separated (e.g. .md,.txt)")
    sp.add_argument("--skip-dirs", metavar="DIRS", help="Skip these directory names, comma-separated (e.g. docs,generated)")
    sp.add_argument("--only-dirs", metavar="DIRS",
                    help="Only files under these directory names, comma-separated (matches any depth, e.g. api,ui)")
    sp.add_argument("--no-tests", action="store_true",
                    help="Exclude test files (test_*, *_test.*, *.test.*, *.spec.*, tests/ dirs, ...)")
    sp.add_argument("--recent", action="store_true", help="Sort by last-modified (newest first)")
    sp.add_argument("--top", type=int, default=None, help="Limit result count")
    sp.set_defaults(func=cmd_find)

    sp = _add_parser(sub, "pack",
                     help="Write a full context pack (context.md) for a task",
                     description=(
                         "Write a complete, ready-to-paste context pack for an AI agent:\n"
                         "the ranked files' contents with headers, to context.md. One\n"
                         "command replaces copy-pasting files into a chat by hand.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp pack 'refactor login flow' -o context.md\n"
                         "  ctxp pack 'fix v2 auth check' --top 12"))
    sp.add_argument("query")
    add_root(sp)
    sp.add_argument("--top", type=int, default=8)
    sp.add_argument("-o", "--output", default=None)
    sp.set_defaults(func=cmd_pack)

    sp = _add_parser(sub, "graph",
                     help="Show call graph for a symbol, or neighbors of a file",
                     description=(
                         "Relations between modules: who imports/calls whom.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp graph login                     upstream + downstream callers of a symbol (JSON)\n"
                         "  ctxp graph --path api_v2/auth.py     import/call neighbors of one file\n"
                         "\n"
                         "The root is optional inside a project: ctxp graph login."))
    add_root(sp)
    sp.add_argument("symbol", nargs="?", default=None)
    sp.add_argument("--path", default=None, help="Show import/call neighbors of this file instead")
    sp.set_defaults(func=cmd_graph)

    sp = _add_parser(sub, "schema",
                     help="Show DB schema (tables, columns, foreign keys) from .sql files and SQLite DBs",
                     description=(
                         "Tables, columns and foreign keys, from .sql files and SQLite\n"
                         "DBs found in the project, or live from a PostgreSQL server.\n"
                         "\n"
                         "Plain listing on stdout, or one standalone interactive HTML\n"
                         "map: 3D + ER views, one color per database, a live inspector.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp schema myproj\n"
                         "  ctxp schema myproj --html schema.html\n"
                         "  ctxp schema myproj --only tenants/ --exclude migrations\n"
                         "  ctxp schema --pg postgresql://user:pass@localhost:5432/db --html schema.html"))
    add_root(sp)
    sp.add_argument("-o", "--output", help="Write the schema to a file instead of stdout")
    sp.add_argument("--html", metavar="FILE",
                    help="Write an interactive HTML map: 3D network + ER cards, one color per database, legend chips show/hide a whole DB")
    sp.add_argument("--pg", metavar="DSN", help="Fetch schema from a live PostgreSQL server, e.g. postgresql://user:pass@localhost:5432/dbname")
    sp.add_argument("--only", metavar="SRC", action="append",
                    help="Keep only tables whose source path contains this substring (repeatable): --only db.sqlite3 --only tenants/")
    sp.add_argument("--exclude", metavar="SRC", action="append",
                    help="Drop tables whose source path contains this substring (repeatable): --exclude migrations")
    sp.set_defaults(func=cmd_schema)

    sp = _add_parser(sub, "serve",
                     help="Run the MCP stdio server for this project",
                     description=(
                         "Run the MCP stdio server for this project (query, find, map,\n"
                         "pack, graph and schema as agent tools). Not meant for humans\n"
                         "to read; launch it through an MCP client:\n"
                         "\n"
                         "  ctxp setup          write the client configs once\n"
                         "  ctxp setup --remove undo them just as cleanly"))
    add_root(sp)
    sp.set_defaults(func=cmd_serve)

    sp = _add_parser(sub, "setup",
                     help="Write MCP client configs into the project (Claude Code, Cursor): agents find the tools with zero setup",
                     description=(
                         "Write MCP client configs so agents inside this project get\n"
                         "the ctxp tools with zero setup. Project-scoped clients\n"
                         "(Claude Code, Cursor) get portable configs INSIDE the\n"
                         "project, safe to commit. Machine-level clients (Antigravity,\n"
                         "Windsurf) get an absolute entry in their global config.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp setup                            Claude Code + Cursor (default)\n"
                         "  ctxp setup --client cursor            one client only\n"
                         "  ctxp setup --client windsurf --absolute\n"
                         "  ctxp setup --remove                   undo\n"
                         "  ctxp setup --client generic           print a paste-ready snippet"))
    add_root(sp)
    sp.add_argument("--client", action="append",
                    choices=(sorted(set(list(MCP_CLIENT_FILES)
                                        + list(MCP_GLOBAL_FILES)))
                             + ["generic"]),
                    help="Which client to configure (default: claude + cursor; "
                         "antigravity/windsurf write this machine's global config; repeatable)")
    sp.add_argument("--remove", action="store_true",
                    help="Remove the xzagent server from the configs instead")
    sp.add_argument("--absolute", action="store_true",
                    help="Write THIS machine's absolute ctxp path and project path "
                         "instead of the portable ctxp/\".\" (only for clients that "
                         "launch from an odd cwd; never commit that flavor)")
    sp.set_defaults(func=cmd_setup)

    sp = _add_parser(sub, "update",
                     help="Update the xzagent CLI itself: git pull + reinstall, one command (no more pip uninstall/install)",
                     description=(
                         "Update the xzagent CLI itself: one command, no pip dance.\n"
                         "\n"
                         "Source resolution: the folder argument, the source remembered\n"
                         "from your last update (~/.xzagent/config.json), an xZagent\n"
                         "checkout containing your current directory, or GitHub itself\n"
                         "(no local clone needed).\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp update                       from the remembered/GitHub source\n"
                         "  ctxp update ~/code/xZagent          from your clone (git pull included)\n"
                         "  ctxp update ~/code/xZagent --no-pull"))
    sp.add_argument("src", nargs="?", default=None,
                    help="Source folder (your xZagent clone); default: remembered source, an enclosing checkout, or GitHub")
    sp.add_argument("--no-pull", action="store_true",
                    help="Skip git pull and reinstall the clone's current files as-is")
    sp.set_defaults(func=cmd_update)

    sp = _add_parser(sub, "uninstall",
                     help="Remove the xzagent CLI from this machine (project data is never touched)",
                     description=(
                         "Remove the xzagent CLI from this machine: pip uninstalls the\n"
                         "package and its ctxp commands. A y/N confirmation runs first\n"
                         "(--yes skips it).\n"
                         "\n"
                         "Project data is never touched: each project's .xzagent/ index\n"
                         "is plain project data, and MCP client configs belong to\n"
                         "their projects. To clean one project's MCP config run\n"
                         "'ctxp setup --remove' inside it first.\n"
                         "\n"
                         "Examples:\n"
                         "  ctxp uninstall                    ask, then remove\n"
                         "  ctxp uninstall --yes              no confirmation\n"
                         "  ctxp uninstall --purge            also remove ~/.xzagent (global config)\n"
                         "\n"
                         "Reinstall any time: " + REINSTALL_HINT))
    sp.add_argument("--yes", action="store_true",
                    help="Skip the y/N confirmation")
    sp.add_argument("--purge", action="store_true",
                    help="Also remove the global ~/.xzagent config dir (saved update source)")
    sp.set_defaults(func=cmd_uninstall)

    sp = _add_parser(sub, "help",
                     help="Show the full overview, or detailed help for one command",
                     description=(
                         "ctxp help            the overview: commands, global flags, workflow\n"
                         "ctxp help find       detailed help for ONE command: every flag parsed\n"
                         "\n"
                         "Same as ctxp <command> -h. Examples:\n"
                         "  ctxp help schema\n"
                         "  ctxp help find"))
    sp.add_argument("topic", nargs="?", default=None, metavar="COMMAND",
                    help="Command to describe (ctxp help find); omit for the overview")
    sp.set_defaults(func=cmd_help)
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
    # Hidden receiver of the Windows self-update handoff
    # (python -m xzagent __update-child SPEC PPID [LABEL]): intentionally
    # NOT an argparse subcommand, so it never appears in `ctxp -h`.
    if len(argv) >= 3 and argv[0] == "__update-child":
        return child_finish(argv[1], argv[2],
                            argv[3] if len(argv) > 3 else argv[1])
    args = build_parser().parse_args(_hoist_global_opts(argv))
    if args.func is cmd_serve:
        # the MCP stdio server must die instantly on Ctrl+C with no
        # question ever written into its protocol stream
        return args.func(args)
    install_guard(getattr(args, "command", None) or "command")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        abort_line(getattr(args, "command", None))
        return ABORT_EXIT_CODE
    finally:
        restore_guard()


if __name__ == "__main__":
    raise SystemExit(main())