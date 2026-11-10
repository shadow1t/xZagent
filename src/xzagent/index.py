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
"""

CONTENT_INDEX_LIMIT = 20_000


def default_db_path(root):
    return os.path.join(os.path.abspath(root), ".xzagent", "index.db")


def connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def build_index(root, db_path=None):
    """Full rebuild of the index for root. Returns the db path."""
    root = os.path.abspath(root)
    if db_path is None:
        db_path = default_db_path(root)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    files = scan(root)
    con = connect(db_path)
    con.executescript(
        "DROP TABLE IF EXISTS meta; DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS symbols;"
        "DROP TABLE IF EXISTS imports; DROP TABLE IF EXISTS edges; DROP TABLE IF EXISTS content_fts;"
    )
    con.executescript(SCHEMA)
    for f in files:
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
    build_edges(con, root)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('root', ?)", (root,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('built_at', ?)", (str(time.time()),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('code_version', ?)", (_code_version(),))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('file_count', ?)", (str(len(files)),))
    con.commit()
    con.close()
    return db_path


def ensure_index(root, db_path=None, force=False):
    """Return db_path, rebuilding automatically if any file changed since the last build."""
    if db_path is None:
        db_path = default_db_path(root)
    if force or not os.path.exists(db_path):
        return build_index(root, db_path)
    try:
        con = connect(db_path)
        indexed = {r["path"]: (r["size"], r["mtime"]) for r in con.execute("SELECT path, size, mtime FROM files").fetchall()}
        code_version = con.execute("SELECT v FROM meta WHERE k='code_version'").fetchone()
        con.close()
    except Exception:
        return build_index(root, db_path)
    if code_version is None or code_version["v"] != _code_version():
        return build_index(root, db_path)
    scanned = scan(root)
    if len(scanned) != len(indexed):
        return build_index(root, db_path)
    for f in scanned:
        prev = indexed.get(f.path)
        if prev is None or prev[0] != f.size or abs(prev[1] - f.mtime) > 1e-6:
            return build_index(root, db_path)
    return db_path
