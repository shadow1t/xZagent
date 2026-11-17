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
<title>DB schema explorer</title>
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0b1220;color:#e2e8f0;margin:0;overflow:hidden}
#top{display:flex;gap:10px;align-items:center;padding:10px 14px;background:#111c33;border-bottom:1px solid #1f2d4d;font-size:14px}
#top b{color:#93c5fd}
#top input{background:#0b1220;color:#e2e8f0;border:1px solid #334155;border-radius:6px;padding:6px 10px;width:280px}
#top span{color:#64748b;font-size:12px}
#main{display:flex;height:calc(100vh - 46px)}
#side{width:300px;min-width:300px;overflow-y:auto;background:#111c33;border-right:1px solid #1f2d4d;padding:8px}
.titem{padding:7px 10px;border-radius:6px;cursor:pointer;font-size:13px;display:flex;justify-content:space-between}
.titem:hover{background:#1e293b}
.titem.sel{background:#2563eb;color:#fff}
.titem .nfk{color:#64748b;font-size:11px}
.titem.sel .nfk{color:#bfdbfe}
#content{flex:1;overflow:auto;padding:18px;position:relative}
#graph{width:100%%;height:420px;background:#0f172a;border:1px solid #1f2d4d;border-radius:10px}
.node circle{fill:#2563eb;stroke:#93c5fd;stroke-width:1.5;cursor:pointer}
.node text{fill:#fff;font-size:11px;text-anchor:middle;pointer-events:none;opacity:0;transition:opacity .15s}
.node:hover text, .node.near text{opacity:1}
.node.dim{opacity:.12}
.edge{stroke:#475569;stroke-width:1}
h2{font-size:18px;margin:14px 0 4px;color:#93c5fd}
.src{color:#64748b;font-size:12px;margin-bottom:10px}
table.cols{border-collapse:collapse;width:100%%;font-size:13px;background:#0f172a;border-radius:8px;overflow:hidden}
table.cols th{background:#1e293b;text-align:left;padding:8px 12px;color:#93c5fd}
table.cols td{padding:8px 12px;border-top:1px solid #1f2d4d}
td .fk{color:#fbbf24}
td .pk{color:#34d399}
a.jump{color:#60a5fa;cursor:pointer;text-decoration:underline}
</style></head><body>
<div id="top"><b>DB schema explorer</b><input id="q" placeholder="search tables..."><span id="cnt"></span><span>graph: scroll=zoom drag=pan · click node=select table</span></div>
<div id="main">
<div id="side"></div>
<div id="content">
  <div id="empty" style="color:#64748b;padding:30px">Select a table from the left, or click a node in the graph.</div>
  <div id="detail" style="display:none">
    <h2 id="tname"></h2><div class="src" id="tsrc"></div>
    <svg id="graph"></svg>
    <h2 style="margin-top:18px">Columns</h2>
    <table class="cols"><thead><tr><th>column</th><th>relation</th></tr></thead><tbody id="cbody"></tbody></table>
  </div>
</div>
</div>
<script>
const data = %s;
const byName = {}; data.forEach(t=>byName[t.name]=t);
const nodes = data.map(t=>({...t, x:(Math.random()-.5)*600, y:(Math.random()-.5)*400, vx:0, vy:0}));
const nbyName = {}; nodes.forEach(n=>nbyName[n.name]=n);
const links=[]; data.forEach(t=>t.fks.forEach(fk=>{const to=nbyName[fk.ref_table]; if(to) links.push({s:nbyName[t.name],t:to,fk});}));
for(let it=0;it<600;it++){
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i],b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y;let d=Math.hypot(dx,dy)||1;const f=30000/(d*d);
    a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f;}
  links.forEach(l=>{let dx=l.t.x-l.s.x,dy=l.t.y-l.s.y;let d=Math.hypot(dx,dy)||1;const f=(d-220)*.03;
    l.s.vx+=dx/d*f;l.s.vy+=dy/d*f;l.t.vx-=dx/d*f;l.t.vy-=dy/d*f;});
  nodes.forEach(n=>{n.vx-=n.x*.008;n.vy-=n.y*.008;n.x+=n.vx*.5;n.y+=n.vy*.5;n.vx*=.75;n.vy*=.75;});
}
const ns='http://www.w3.org/2000/svg';
function el(t,a){const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);return e}
const svg=document.getElementById('graph');
let view={x:-40,y:-40,k:1};
let nodeEls=[];
function drawGraph(){
  svg.innerHTML=''; nodeEls=[];
  links.forEach(l=>svg.appendChild(el('line',{x1:l.s.x,y1:l.s.y,x2:l.t.x,y2:l.t.y,'class':'edge'})));
  nodes.forEach(n=>{const g=el('g',{'class':'node'});
    g.appendChild(el('circle',{cx:n.x,cy:n.y,r:15}));
    const tx=el('text',{x:n.x,y:n.y+4});tx.textContent=n.name;g.appendChild(tx);
    g.onclick=()=>select(n.name);
    g.onmouseenter=()=>g.classList.add('near'); g.onmouseleave=()=>g.classList.remove('near');
    svg.appendChild(g); nodeEls.push({g,n});});
  applyV();
}
function applyV(){const w=svg.clientWidth||800,h=svg.clientHeight||420;
  svg.setAttribute('viewBox',view.x+' '+view.y+' '+(w/view.k)+' '+(h/view.k));
  nodeEls.forEach(({g})=>g.classList.toggle('near', view.k>=2.2));}
let drag=null;
svg.onmousedown=e=>{drag={x:e.clientX,y:e.clientY,vx:view.x,vy:view.y};};
window.onmousemove=e=>{if(drag){view.x=drag.vx-(e.clientX-drag.x)/view.k;view.y=drag.vy-(e.clientY-drag.y)/view.k;applyV();}};
window.onmouseup=()=>drag=null;
svg.onwheel=e=>{e.preventDefault();view.k=Math.min(8,Math.max(.1,view.k*(e.deltaY<0?1.15:1/1.15)));applyV();};
const side=document.getElementById('side');
function buildSide(q=''){
  side.innerHTML='';
  data.filter(t=>!q||t.name.toLowerCase().includes(q)).forEach(t=>{
    const d=document.createElement('div'); d.className='titem'; d.dataset.name=t.name;
    d.innerHTML='<span>'+t.name+'</span><span class="nfk">'+t.fks.length+' fk</span>';
    d.onclick=()=>select(t.name); side.appendChild(d);
  });
}
function select(name){
  const t=byName[name]; if(!t) return;
  document.getElementById('empty').style.display='none';
  document.getElementById('detail').style.display='block';
  document.getElementById('tname').textContent=t.name;
  document.getElementById('tsrc').textContent='source: '+t.source+' · '+t.columns.length+' columns · '+t.fks.length+' foreign keys';
  document.querySelectorAll('.titem').forEach(e=>e.classList.toggle('sel',e.dataset.name===name));
  const cb=document.getElementById('cbody'); cb.innerHTML='';
  t.columns.forEach(c=>{
    const fk=t.fks.find(f=>f.column===c);
    const tr=document.createElement('tr');
    tr.innerHTML='<td><span class="'+(fk?'fk':'pk')+'">'+c+'</span></td><td>'+(fk?'→ <a class="jump" data-t="'+fk.ref_table+'">'+fk.ref_table+'</a>':'')+'</td>';
    cb.appendChild(tr);
  });
  cb.querySelectorAll('.jump').forEach(a=>a.onclick=()=>select(a.dataset.t));
  nodeEls.forEach(({g,n})=>g.classList.toggle('dim', n.name!==name && !links.some(l=>(l.s.name===name&&l.t.name===n.name)||(l.t.name===name&&l.s.name===n.name))));
}
document.getElementById('q').oninput=e=>buildSide(e.target.value.toLowerCase());
document.getElementById('cnt').textContent=data.length+' tables · '+links.length+' relations';
buildSide(); drawGraph();
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
