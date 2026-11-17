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
body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;overflow:hidden}
#bar{display:flex;gap:8px;align-items:center;padding:10px 14px;background:#1e293b;font-size:14px}
#bar input{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:5px 9px;width:260px}
#bar span{color:#94a3b8}
#wrap{position:relative;width:100vw;height:calc(100vh - 45px)}
svg{display:block;width:100%%;height:100%%;cursor:grab}
.node circle{fill:#2563eb;stroke:#93c5fd;stroke-width:1.5;cursor:pointer}
.node text{fill:#fff;font-size:11px;text-anchor:middle;pointer-events:none}
.node.dim{opacity:.15}
.edge{stroke:#475569;stroke-width:1}
#info{position:absolute;top:12px;right:12px;background:#1e293b;border:1px solid #334155;padding:10px 14px;border-radius:8px;max-width:340px;font-size:13px;display:none;pointer-events:none}
</style></head><body>
<div id="bar"><b>DB schema map</b><input id="q" placeholder="filter tables..."><span id="cnt"></span><span>scroll=zoom, drag=pan, click node=details</span></div>
<div id="wrap"><div id="info"></div><svg id="g"></svg></div>
<script>
const data = %s;
const svg = document.getElementById('g');
const wrap = document.getElementById('wrap');
let view = {x:0,y:0,k:1};
const nodes = data.map((t,i)=>({...t, x:(Math.random()-.5)*800, y:(Math.random()-.5)*800, vx:0, vy:0}));
const byName = {}; nodes.forEach(n=>byName[n.name]=n);
const links = [];
data.forEach(t=>t.fks.forEach(fk=>{const to=byName[fk.ref_table]; if(to) links.push({s:t, t:to, fk});}));
// simple force-directed layout
for(let iter=0; iter<400; iter++){
  for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j]; let dx=a.x-b.x, dy=a.y-b.y; let d=Math.hypot(dx,dy)||1;
    const f=6000/(d*d); a.vx+=dx/d*f; a.vy+=dy/d*f; b.vx-=dx/d*f; b.vy-=dy/d*f;
  }
  links.forEach(l=>{let dx=l.t.x-l.s.x, dy=l.t.y-l.s.y; let d=Math.hypot(dx,dy)||1;
    const f=(d-160)*0.02; l.s.vx+=dx/d*f; l.s.vy+=dy/d*f; l.t.vx-=dx/d*f; l.t.vy-=dy/d*f;});
  nodes.forEach(n=>{n.vx-=n.x*0.01; n.vy-=n.y*0.01; n.x+=n.vx*0.5; n.y+=n.vy*0.5; n.vx*=0.8; n.vy*=0.8;});
}
const ns='http://www.w3.org/2000/svg';
function el(t,a){const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);return e}
let edgeEls=[], nodeEls=[];
function render(){
  svg.innerHTML=''; edgeEls=[]; nodeEls=[];
  const W=wrap.clientWidth, H=wrap.clientHeight;
  links.forEach(l=>{const e=el('line',{x1:l.s.x,y1:l.s.y,x2:l.t.x,y2:l.t.y,'class':'edge'});svg.appendChild(e);edgeEls.push({e,l});});
  nodes.forEach(n=>{
    const g=el('g',{'class':'node'});
    g.appendChild(el('circle',{cx:n.x,cy:n.y,r:16}));
    const tx=el('text',{x:n.x,y:n.y+4}); tx.textContent=n.name; g.appendChild(tx);
    g.onclick=ev=>{ev.stopPropagation(); showInfo(n);};
    svg.appendChild(g); nodeEls.push({g,n});
  });
  apply();
}
function apply(){
  svg.setAttribute('viewBox', (view.x)+' '+(view.y)+' '+(wrap.clientWidth/view.k)+' '+(wrap.clientHeight/view.k));
}
function showInfo(n){const i=document.getElementById('info');i.style.display='block';
  i.innerHTML='<b>'+n.name+'</b><br>source: '+n.source+'<br>columns: '+n.columns.join(', ')+
    '<br>fks: '+(n.fks.map(f=>f.column+' → '+f.ref_table).join('; ')||'—');}
render();
document.getElementById('cnt').textContent = nodes.length+' tables, '+links.length+' relations';
let drag=null;
svg.onmousedown=e=>{drag={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};};
window.onmousemove=e=>{if(drag){view.x=drag.vx-(e.clientX-drag.x)/view.k; view.y=drag.vy-(e.clientY-drag.y)/view.k; apply();}};
window.onmouseup=()=>drag=null;
svg.onwheel=e=>{e.preventDefault(); const f=e.deltaY<0?1.15:1/1.15; view.k=Math.min(8,Math.max(.1,view.k*f)); apply();};
document.getElementById('q').oninput=e=>{
  const q=e.target.value.toLowerCase();
  nodeEls.forEach(({g,n})=>g.classList.toggle('dim', q && !n.name.toLowerCase().includes(q)));
  edgeEls.forEach(({e,l})=>{const d=q && !(l.s.name.toLowerCase().includes(q)||l.t.name.toLowerCase().includes(q)); e.style.opacity=d?.1:1;});
};
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
