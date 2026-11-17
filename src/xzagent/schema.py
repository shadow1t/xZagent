"""Database schema extraction: SQL DDL files and SQLite database files."""
import os
import re
import sqlite3

CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)[\"'`\]]?", re.IGNORECASE
)
FK_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(\s*[\"'`\[]?(\w+)[\"'`\]]?\s*\)\s*REFERENCES\s+[\"'`\[]?(\w+)", re.IGNORECASE
)
CONSTRAINT_STARTS = ("PRIMARY", "UNIQUE", "CHECK", "CONSTRAINT", "FOREIGN")
DB_EXTS = (".db", ".sqlite", ".sqlite3")


def _split_top_level(body):
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def parse_sql_schema(text):
    tables = {}
    for m in CREATE_RE.finditer(text):
        name = m.group(1)
        start = text.find("(", m.end())
        if start == -1:
            continue
        depth, end = 0, start
        for i in range(start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        body = text[start + 1:end]
        cols, fks = [], []
        for part in _split_top_level(body):
            part = part.strip()
            if not part:
                continue
            fk = FK_RE.search(part)
            if fk:
                fks.append({"column": fk.group(1), "ref_table": fk.group(2)})
                continue
            if part.upper().startswith(CONSTRAINT_STARTS):
                continue
            cols.append(part.split()[0].strip('"`[]'))
        tables[name] = {"columns": cols, "fks": fks}
    return tables


def read_sqlite_schema(path):
    tables = {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.replace("?", "%3f"), uri=True)
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for n in names:
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % n)]
            fks = [{"column": r[3], "ref_table": r[2]}
                   for r in con.execute('PRAGMA foreign_key_list("%s")' % n)]
            tables[n] = {"columns": cols, "fks": fks}
        con.close()
    except Exception:
        pass
    return tables


def collect_db_schema(root, scanned_files):
    """Return list of {name, source, columns, fks} from .sql files and SQLite DBs."""
    out = []
    for f in scanned_files:
        if f.ext == ".sql":
            try:
                with open(f.abspath, encoding="utf-8", errors="ignore") as fh:
                    for tname, t in parse_sql_schema(fh.read()).items():
                        out.append({"name": tname, "source": f.path,
                                    "columns": t["columns"], "fks": t["fks"]})
            except OSError:
                pass
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".xzagent"}]
        for fn in filenames:
            if fn.lower().endswith(DB_EXTS):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                for tname, t in read_sqlite_schema(full).items():
                    out.append({"name": tname, "source": rel,
                                "columns": t["columns"], "fks": t["fks"]})
    return out


def format_schema(rows):
    lines = ["# DB schema", ""]
    for r in sorted(rows, key=lambda x: x["name"]):
        lines.append("## table `%s`  (source: %s)" % (r["name"], r["source"]))
        lines.append("columns: " + ", ".join(r["columns"]))
        for fk in r["fks"]:
            lines.append("fk: %s -> %s" % (fk["column"], fk["ref_table"]))
        lines.append("")
    return "\n".join(lines)
