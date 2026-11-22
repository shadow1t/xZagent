import hashlib
import os
import sqlite3
import time

from .graph import build_edges
from .scanner import scan
from .symbols import extract


def _code_version():
    here = os.path.dirname(os.path.abspath(__file__))
    h = hashlib.sha256()
    for name in ("symbols.py", "scanner.py", "languages.py", "graph.py"):
        with open(os.path.join(here, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]

SCHEMA = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE files(path TEXT PRIMARY KEY, ext TEXT, size INT, mtime REAL, lang TEXT, summary TEXT);
CREATE TABLE symbols(file TEXT, kind TEXT, name TEXT, lineno INT);
CREATE TABLE imports(file TEXT, module TEXT, lineno INT);
CREATE TABLE edges(from_file TEXT, to_file TEXT, kind TEXT, label TEXT);
CREATE VIRTUAL TABLE content_fts USING fts5(path, symbols, content, tokenize='unicode61');
CREATE INDEX idx_symbols_name ON symbols(name);
CREATE INDEX idx_edges_from ON edges(from_file);
CREATE INDEX idx_edges_label ON edges(label);
CREATE TABLE IF NOT EXISTS db_tables(name TEXT, source TEXT, columns TEXT, fks TEXT, PRIMARY KEY(name, source));
"""

CONTENT_INDEX_LIMIT = 20_000


def _config_json(no_default_skips, extra_ignores):
    import json as _json

    return _json.dumps({
        "no_default_skips": bool(no_default_skips),
        "extra_ignores": sorted(set(extra_ignores or [])),
    })


def default_db_path(root):
    return os.path.join(os.path.abspath(root), ".xzagent", "index.db")


def connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def build_index(root, db_path=None, no_default_skips=False, extra_ignores=None,
                progress=None):
    """Full rebuild of the index for root. Returns the db path.

    no_default_skips=True disables the built-in noise skips (tmp_*, *.min.js,
    migrations/, ...). extra_ignores is a list of gitignore-syntax patterns
    excluded from indexing. The chosen configuration is stored in the index
    and reused by automatic rebuilds.

    progress, when given, is called as progress(done, total, note) during
    the run so TUIs (ctxp on a terminal) can render a live bar. It must be
    safe to ignore (non-TTY consumers pass nothing).
    """
    def report(done, total, note=None):
        if progress is not None:
            try:
                progress(done, total, note)
            except Exception:
                pass

    root = os.path.abspath(root)
    if db_path is None:
        db_path = default_db_path(root)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    report(0, 0, "scanning files")
    files = scan(root, no_default_skips=no_default_skips, extra_ignores=extra_ignores)
    total = len(files)
    report(0, total, "indexing")
    con = connect(db_path)
    con.executescript(
        "DROP TABLE IF EXISTS meta; DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS symbols;"
        "DROP TABLE IF EXISTS imports; DROP TABLE IF EXISTS edges; DROP TABLE IF EXISTS content_fts;"
        "DROP TABLE IF EXISTS db_tables;"
    )
    con.executescript(SCHEMA)
    for idx, f in enumerate(files, 1):
        try:
            with open(f.abspath, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        symbols, imports, calls, summary = extract(text, f.lang)
        con.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?)",
            (f.path, f.ext, f.size, f.mtime, f.lang, summary),
        )
        for kind, name, lineno in symbols:
            con.execute("INSERT INTO symbols VALUES (?,?,?,?)", (f.path, kind, name, lineno))
        for name, lineno in calls:
            con.execute("INSERT INTO symbols VALUES (?,?,?,?)", (f.path, "call", name, lineno))
        for module, lineno in imports:
            con.execute("INSERT INTO imports VALUES (?,?,?)", (f.path, module, lineno))
        symbols_text = " ".join(name for _, name, _ in symbols) + " " + " ".join(m for m, _ in imports)
        con.execute(
            "INSERT INTO content_fts VALUES (?,?,?)",
            (f.path, symbols_text, text[:CONTENT_INDEX_LIMIT]),
        )
        report(idx, total, "indexing")
    report(0, 0, "resolving import/call graph")
    build_edges(con, root)
    from .schema import collect_db_schema
    import json as _json
    for t in collect_db_schema(root, files):
        con.execute(
            "INSERT OR REPLACE INTO db_tables VALUES (?,?,?,?)",
            (t["name"], t["source"], _json.dumps(t["columns"]), _json.dumps(t["fks"])),
        )
    con.execute("INSERT OR REPLACE INTO meta VALUES ('root', ?)", (root,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (str(time.time()),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('code_version', ?)", (_code_version(),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('file_count', ?)", (str(len(files)),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('scan_config', ?)", (_config_json(no_default_skips, extra_ignores),))
    con.commit()
    con.close()
    return db_path


def _stored_config(db_path):
    """Read the scan configuration stored in an index, or None if absent."""
    import json as _json

    try:
        con = connect(db_path)
        row = con.execute("SELECT v FROM meta WHERE k='scan_config'").fetchone()
        con.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        cfg = _json.loads(row["v"])
        return (bool(cfg.get("no_default_skips", False)), list(cfg.get("extra_ignores") or []))
    except (ValueError, TypeError, KeyError):
        return None


def ensure_index(root, db_path=None, force=False, no_default_skips=None, extra_ignores=None,
                 progress=None):
    """Return db_path, rebuilding automatically if any file changed since the last build.

    no_default_skips / extra_ignores override the stored scan configuration
    when given (not None) and trigger a rebuild if they differ; when omitted,
    the configuration stored in the index is reused, so one-off CLI flags
    from the last explicit run keep applying.
    """
    if db_path is None:
        db_path = default_db_path(root)
    stored = _stored_config(db_path) if os.path.exists(db_path) else None
    base = stored if stored is not None else (False, [])
    eff_no_skip = base[0] if no_default_skips is None else bool(no_default_skips)
    eff_extra = base[1] if extra_ignores is None else sorted(set(extra_ignores))
    if force or not os.path.exists(db_path):
        return build_index(root, db_path, no_default_skips=eff_no_skip, extra_ignores=eff_extra,
                          progress=progress)
    try:
        con = connect(db_path)
        indexed = {r["path"]: (r["size"], r["mtime"]) for r in con.execute("SELECT path, size, mtime FROM files").fetchall()}
        code_version = con.execute("SELECT v FROM meta WHERE k='code_version'").fetchone()
        con.close()
    except Exception:
        return build_index(root, db_path, no_default_skips=eff_no_skip, extra_ignores=eff_extra,
                          progress=progress)
    if code_version is None or code_version["v"] != _code_version():
        return build_index(root, db_path, no_default_skips=eff_no_skip, extra_ignores=eff_extra,
                          progress=progress)
    if _config_json(eff_no_skip, eff_extra) != _config_json(*base):
        return build_index(root, db_path, no_default_skips=eff_no_skip, extra_ignores=eff_extra,
                          progress=progress)
    scanned = scan(root, no_default_skips=eff_no_skip, extra_ignores=eff_extra)
    if len(scanned) != len(indexed):
        return build_index(root, db_path, no_default_skips=eff_no_skip, extra_ignores=eff_extra,
                          progress=progress)
    for f in scanned:
        prev = indexed.get(f.path)
        if prev is None or prev[0] != f.size or abs(prev[1] - f.mtime) > 1e-6:
            return build_index(root, db_path, no_default_skips=eff_no_skip, extra_ignores=eff_extra,
                          progress=progress)
    return db_path