import os

_PY_CANDIDATE_EXTS = (".py",)
_JS_CANDIDATE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def _try_candidates(base_no_ext, exts, is_dir_pkg=False):
    for ext in exts:
        if os.path.isfile(base_no_ext + ext):
            return base_no_ext + ext
    if is_dir_pkg:
        for ext in exts:
            init = os.path.join(base_no_ext, "__init__" + ext)
            if os.path.isfile(init):
                return init
    return None


def resolve_python_import(root, from_file, module):
    """Resolve a python import module string to a project-relative file, or None."""
    parts = module.split(".")
    level = 0
    while parts and parts[0] == "":
        level += 1
        parts = parts[1:]
    if level:
        base_dir = os.path.dirname(os.path.join(root, from_file))
        for _ in range(level - 1):
            base_dir = os.path.dirname(base_dir)
        base = os.path.join(base_dir, *parts) if parts else base_dir
    else:
        base = os.path.join(root, *parts) if parts else None
    if base is None:
        return None
    rel = os.path.relpath(base, root).replace(os.sep, "/")
    hit = _try_candidates(base, _PY_CANDIDATE_EXTS, is_dir_pkg=True)
    return os.path.relpath(hit, root).replace(os.sep, "/") if hit else None


def resolve_js_import(root, from_file, spec):
    if not spec.startswith("."):
        return None
    base_dir = os.path.dirname(os.path.join(root, from_file))
    base = os.path.normpath(os.path.join(base_dir, spec))
    if not base.startswith(os.path.abspath(root)):
        return None
    if os.path.isfile(base):
        return os.path.relpath(base, root).replace(os.sep, "/")
    hit = _try_candidates(base, _JS_CANDIDATE_EXTS, is_dir_pkg=True)
    return os.path.relpath(hit, root).replace(os.sep, "/") if hit else None


def resolve_import(root, from_file, module, lang):
    if lang == "python":
        return resolve_python_import(root, from_file, module)
    if lang in ("javascript", "typescript"):
        return resolve_js_import(root, from_file, module)
    return None


def build_edges(con, root):
    """Persist import edges (file->file) and call edges (file->file, label=symbol)."""
    rows = con.execute("SELECT f.path, f.lang, i.module FROM imports i JOIN files f ON f.path = i.file").fetchall()
    for row in rows:
        target = resolve_import(root, row["path"], row["module"], row["lang"])
        if target and target != row["path"]:
            con.execute("INSERT INTO edges VALUES (?,?,?,?)", (row["path"], target, "import", row["module"]))
    func_files = {}
    for row in con.execute("SELECT file, name FROM symbols WHERE kind='function'").fetchall():
        func_files.setdefault(row["name"], set()).add(row["file"])
    for row in con.execute("SELECT file, name FROM symbols WHERE kind='call'").fetchall():
        for target in func_files.get(row["name"], ()):
            if target != row["file"]:
                con.execute("INSERT INTO edges VALUES (?,?,?,?)", (row["file"], target, "call", row["name"]))
    con.commit()


def neighbors(con, path):
    rows = con.execute(
        "SELECT to_file AS other, kind, label FROM edges WHERE from_file=? "
        "UNION SELECT from_file, kind, label FROM edges WHERE to_file=?",
        (path, path),
    ).fetchall()
    return [dict(r) for r in rows]


def call_graph(con, symbol):
    defs = [dict(r) for r in con.execute(
        "SELECT file, lineno FROM symbols WHERE name=? AND kind IN ('function','class') ORDER BY file", (symbol,)
    ).fetchall()]
    callers = sorted({r["from_file"] for r in con.execute(
        "SELECT from_file FROM edges WHERE kind='call' AND label=?", (symbol,)
    ).fetchall()})
    callees = sorted({r["to_file"] for r in con.execute(
        "SELECT to_file FROM edges WHERE kind='call' AND label=?", (symbol,)
    ).fetchall()})
    return {"symbol": symbol, "definitions": defs, "callers": callers, "callees": callees}
