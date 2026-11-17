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


_DJANGO_MODEL_RE = re.compile(
    r"migrations\.CreateModel\(\s*name=[\"'](\w+)[\"']\s*,\s*fields=\[(.*?)\]\s*,?\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_FIELD_RE = re.compile(r"\(\s*[\"'](\w+)[\"']\s*,\s*models\.(\w+)\((.*?)\)", re.DOTALL)
_FK_TARGET_RE = re.compile(r"[\"'](?:[\w.]+\.)?(\w+)[\"']")


def parse_django_migrations(text):
    """Extract tables from Django migration files (CreateModel + ForeignKey fields)."""
    tables = {}
    for m in _DJANGO_MODEL_RE.finditer(text):
        name = m.group(1)
        cols, fks = [], []
        for f in _FIELD_RE.finditer(m.group(2)):
            col, field_type, args = f.group(1), f.group(2), f.group(3)
            if field_type in ("ForeignKey", "OneToOneField"):
                tgt = _FK_TARGET_RE.search(args)
                if tgt:
                    fks.append({"column": col, "ref_table": tgt.group(1)})
                    cols.append(col)
                    continue
            cols.append(col)
        tables[name] = {"columns": cols, "fks": fks}
    return tables


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>DB schema map</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0}
h1{font-size:18px;padding:12px 16px;margin:0;background:#1e293b}
svg{display:block}
.node circle{fill:#2563eb;stroke:#93c5fd;stroke-width:2;cursor:pointer}
.node text{fill:#fff;font-size:12px;text-anchor:middle;pointer-events:none}
.edge{stroke:#64748b;stroke-width:1.5}
.elabel{fill:#94a3b8;font-size:10px;text-anchor:middle}
#info{position:fixed;top:60px;right:12px;background:#1e293b;padding:10px 14px;border-radius:8px;max-width:320px;font-size:13px;display:none}
</style></head><body>
<h1>DB schema map</h1>
<div id="info"></div>
<svg id="g" width="100%%" height="92%%"></svg>
<script>
const data = %s;
const svg = document.getElementById('g');
const W = svg.clientWidth || innerWidth, H = svg.clientHeight || innerHeight;
const nodes = data.map((t,i)=>({...t, x: W/2 + Math.cos(2*Math.PI*i/data.length)*Math.min(W,H)*0.35, y: H/2 + Math.sin(2*Math.PI*i/data.length)*Math.min(W,H)*0.35}));
const byName = {}; nodes.forEach(n=>byName[n.name]=n);
const ns = 'http://www.w3.org/2000/svg';
function el(t,a){const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);return e}
data.forEach(t=>t.fks.forEach(fk=>{
  const to=byName[fk.ref_table]; if(!to) return;
  svg.appendChild(el('line',{x1:t.x,y1:t.y,x2:to.x,y2:to.y,'class':'edge'}));
}));
nodes.forEach(n=>{
  const g=el('g',{'class':'node'});
  g.appendChild(el('circle',{cx:n.x,cy:n.y,r:26}));
  const tx=el('text',{x:n.x,y:n.y+4}); tx.textContent=n.name; g.appendChild(tx);
  g.onclick=()=>{const i=document.getElementById('info');i.style.display='block';
    i.innerHTML='<b>'+n.name+'</b><br>source: '+n.source+'<br>columns: '+n.columns.join(', ')+
      '<br>fks: '+(n.fks.map(f=>f.column+' → '+f.ref_table).join('; ')||'—');};
  svg.appendChild(g);
});
</script></body></html>
"""


def schema_html(rows):
    import json
    return HTML_TEMPLATE % json.dumps(rows)


_PG_TABLES_SQL = """
SELECT table_name, column_name, data_type FROM information_schema.columns
WHERE table_schema = 'public' ORDER BY table_name, ordinal_position;
"""
_PG_FKS_SQL = """
SELECT tc.table_name, kcu.column_name, ccu.table_name AS ref_table
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public';
"""


def rows_to_tables(col_rows, fk_rows):
    tables = {}
    for t, c, _dt in col_rows:
        tables.setdefault(t, {"columns": [], "fks": []})["columns"].append(c)
    for t, c, ref in fk_rows:
        tables.setdefault(t, {"columns": [], "fks": []})["fks"].append({"column": c, "ref_table": ref})
    return tables


def fetch_postgres_schema(dsn):
    """Fetch schema from a live PostgreSQL server. Tries psycopg, then psql CLI."""
    try:
        import psycopg  # type: ignore
        with psycopg.connect(dsn) as con:
            cols = con.execute(_PG_TABLES_SQL).fetchall()
            fks = con.execute(_PG_FKS_SQL).fetchall()
        return rows_to_tables(cols, fks)
    except ImportError:
        pass
    import shutil
    import subprocess
    if shutil.which("psql"):
        env_dsn = dsn
        out_c = subprocess.run(["psql", env_dsn, "-At", "-F", "\t", "-c", _PG_TABLES_SQL],
                               capture_output=True, text=True)
        out_f = subprocess.run(["psql", env_dsn, "-At", "-F", "\t", "-c", _PG_FKS_SQL],
                               capture_output=True, text=True)
        if out_c.returncode == 0 and out_f.returncode == 0:
            cols = [tuple(l.split("\t")) for l in out_c.stdout.strip().splitlines() if l]
            fks = [tuple(l.split("\t")) for l in out_f.stdout.strip().splitlines() if l]
            return rows_to_tables(cols, fks)
    raise RuntimeError("Need psycopg (pip install psycopg) or psql on PATH, or use: pg_dump --schema-only")


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
        if f.ext == ".py" and "migrations" in f.path:
            try:
                with open(f.abspath, encoding="utf-8", errors="ignore") as fh:
                    for tname, t in parse_django_migrations(fh.read()).items():
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
