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
body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:radial-gradient(1200px 600px at 70% -10%,#16233f 0%,#0b1220 55%);color:#e2e8f0;margin:0;overflow:hidden}
#top{display:flex;gap:10px;align-items:center;padding:10px 16px;background:linear-gradient(180deg,#131f38,#111c33);border-bottom:1px solid #1f2d4d;font-size:14px;box-shadow:0 2px 12px #0008}
#top b{color:#93c5fd;font-size:15px;letter-spacing:.3px}
#top input{background:#0b1220;color:#e2e8f0;border:1px solid #334155;border-radius:8px;padding:7px 12px;width:280px;outline:none;transition:border .15s}
#top input:focus{border-color:#3b82f6;box-shadow:0 0 0 3px #3b82f622}
.chip{background:#1e293b;border:1px solid #1f2d4d;border-radius:999px;padding:4px 12px;font-size:12px;color:#94a3b8}
.chip b{color:#e2e8f0}
#top .hint{color:#64748b;font-size:12px;margin-left:auto}
#top button{background:#2563eb;color:#fff;border:0;border-radius:8px;padding:7px 13px;cursor:pointer;font-size:13px;transition:all .15s}
#top button:hover{background:#3b82f6}
#top button.off{background:#1e293b;color:#94a3b8}
#cards{display:none;flex-wrap:wrap;gap:12px;align-content:flex-start}
.card{background:#0f172a;border:1px solid #1f2d4d;border-radius:10px;width:250px;cursor:pointer;overflow:hidden;transition:all .15s}
.card:hover{border-color:#3b82f6;transform:translateY(-2px)}
.card.sel{border-color:#f97316;box-shadow:0 0 0 2px #f9731655}
.card h3{margin:0;padding:9px 12px;background:#1e293b;font-size:13px;color:#93c5fd;font-weight:600}
.card .cols{padding:6px 12px;font-size:11px;color:#94a3b8;max-height:150px;overflow-y:auto}
.card .cols div{padding:2px 0}
.card .cols .fk{color:#fbbf24}
#main{display:flex;height:calc(100vh - 49px)}
#side{width:300px;min-width:300px;overflow-y:auto;background:#101a30;border-right:1px solid #1f2d4d;padding:8px;transition:margin .2s}
#side.closed{margin-left:-300px}
#sideToggle{background:#1e293b;color:#94a3b8}
#ctl{position:absolute;top:60px;right:16px;display:flex;flex-direction:column;gap:6px;z-index:5}
#ctl button{width:38px;height:38px;font-size:17px;padding:0;border-radius:10px;box-shadow:0 2px 8px #0007}
#graph.grab{cursor:grab}
#graph.grabbing{cursor:grabbing}
.titem{padding:7px 10px;border-radius:8px;cursor:pointer;font-size:13px;display:flex;justify-content:space-between;transition:background .1s}
.titem:hover{background:#1e293b}
.titem.sel{background:#2563eb;color:#fff}
.titem .nfk{color:#64748b;font-size:11px}
.titem.sel .nfk{color:#bfdbfe}
#content{flex:1;overflow:auto;padding:18px;position:relative}
#graph{width:100%;height:440px;background:radial-gradient(600px 300px at 50% 40%,#12203a 0%,#0d1526 70%);border:1px solid #1f2d4d;border-radius:12px;box-shadow:inset 0 0 40px #0006}
.node circle{fill:#2563eb;stroke:#93c5fd;stroke-width:1.5;cursor:pointer;transition:r .1s}
.node.sel circle{fill:#f97316;stroke:#fdba74;stroke-width:2.5}
.node text{fill:#fff;font-size:11px;text-anchor:middle;pointer-events:none;opacity:0;transition:opacity .15s;text-shadow:0 1px 3px #000}
.node:hover text, .node.near text, .node.sel text{opacity:1}
.node.dim{opacity:.12}
.edge{stroke:#475569;stroke-width:1}
.edge.hot{stroke:#f97316;stroke-width:3.5;filter:drop-shadow(0 0 4px #f97316aa)}
.elabel{fill:#fdba74;font-size:10px;text-anchor:middle;pointer-events:none}
#graph.no-edges .edge:not(.hot){display:none}
h2{font-size:18px;margin:14px 0 4px;color:#93c5fd}
.src{color:#64748b;font-size:12px;margin-bottom:10px}
table.cols{border-collapse:collapse;width:100%;font-size:13px;background:#0f172a;border-radius:10px;overflow:hidden;border:1px solid #1f2d4d}
table.cols th{background:#1e293b;text-align:left;padding:9px 12px;color:#93c5fd}
table.cols td{padding:9px 12px;border-top:1px solid #1f2d4d}
table.cols tr:hover td{background:#131f38}
td .fk{color:#fbbf24}
td .pk{color:#34d399}
a.jump{color:#60a5fa;cursor:pointer;text-decoration:underline}
#toast{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);background:#1e293b;border:1px solid #334155;padding:8px 16px;border-radius:999px;font-size:12px;color:#94a3b8;opacity:0;transition:opacity .2s;pointer-events:none}
</style></head><body>
<div id="top"><b>🧭 DB schema explorer</b><input id="q" placeholder="search tables...  ( / )"><span class="chip" id="cnt"></span>
<button id="vGraph">🕸 Network</button><button id="vCards" class="off">🗂 ER cards</button><button id="vEdges">🔗 edges: on</button>
<button id="sideToggle" title="toggle sidebar">☰</button>
<span class="hint">scroll=zoom · drag=rotate · ✋/shift+drag=pan · click=select · dbl-click=reset · esc=reset</span></div>
<div id="ctl"><button id="zIn" title="zoom in">+</button><button id="zOut" title="zoom out">−</button><button id="zReset" title="reset view">⊙</button><button id="hand" title="hand tool: drag to pan planet">✋</button></div>
<div id="toast"></div>
<div id="main">
<div id="side"></div>
<div id="content">
  <div id="cards"></div>
  <div id="empty" style="color:#64748b;padding:30px">Select a table from the left, or click a node/card.</div>
  <div id="detail" style="display:none">
    <h2 id="tname"></h2><div class="src" id="tsrc"></div>
    <svg id="graph" width="800" height="420"></svg>
    <h2 style="margin-top:18px">Columns</h2>
    <table class="cols"><thead><tr><th>column</th><th>relation</th></tr></thead><tbody id="cbody"></tbody></table>
  </div>
</div>
</div>
<script>
const data = __DATA__;
const byName = {}; data.forEach(t=>byName[t.name]=t);
// fibonacci sphere positions (pseudo-3D planet)
const nodes = data.map((t,i)=>{
  const N=data.length, phi=Math.acos(1-2*(i+0.5)/N), th=Math.PI*(1+Math.sqrt(5))*i;
  return {...t, sx:Math.sin(phi)*Math.cos(th), sy:Math.cos(phi), sz:Math.sin(phi)*Math.sin(th)};
});
const nbyName = {}; nodes.forEach(n=>nbyName[n.name]=n);
const links=[]; data.forEach(t=>t.fks.forEach(fk=>{const to=nbyName[fk.ref_table]; if(to) links.push({s:nbyName[t.name],t:to,fk});}));
const ns='http://www.w3.org/2000/svg';
function el(t,a){const e=document.createElementNS(ns,t);for(const k in a)e.setAttribute(k,a[k]);return e}
const svg=document.getElementById('graph');
let rot={rx:-0.4, ry:0.6}, zoom=1, pan={x:0,y:0};
let nodeEls=[];
let edgeEls=[];
function proj(n){
  let x=n.sx*Math.cos(rot.ry)+n.sz*Math.sin(rot.ry);
  let z=-n.sx*Math.sin(rot.ry)+n.sz*Math.cos(rot.ry);
  let y=n.sy*Math.cos(rot.rx)-z*Math.sin(rot.rx);
  z=n.sy*Math.sin(rot.rx)+z*Math.cos(rot.rx);
  const R=300, f=2.2/(2.2-z);
  return {x:x*R*f, y:y*R*f, z, f};
}
function drawGraph(){
  svg.innerHTML=''; nodeEls=[]; edgeEls=[];
  const W=svg.clientWidth||800, H=svg.clientHeight||420;
  svg.setAttribute('viewBox',(-W/2/zoom+pan.x)+' '+(-H/2/zoom+pan.y)+' '+(W/zoom)+' '+(H/zoom));
  const ps=nodes.map(n=>({n,p:proj(n)}));
  links.forEach(l=>{
    const a=ps.find(o=>o.n===l.s).p, b=ps.find(o=>o.n===l.t).p;
    const e=el('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,'class':'edge'});
    e.style.opacity=0.08+0.5*((a.z+b.z)/2+1)/2;
    if(selectedName && (l.s.name===selectedName||l.t.name===selectedName)){
      e.classList.add('hot');
      if(zoom>=2.5){
        const mx=(a.x+b.x)/2, my=(a.y+b.y)/2;
        const dx=b.x-a.x, dy=b.y-a.y, len=Math.hypot(dx,dy)||1;
        const off=(edgeEls.length%2? 10 : -10);
        const lb=el('text',{x:mx-dy/len*off,y:my+dx/len*off,'class':'elabel'});
        lb.textContent=l.fk.column; svg.appendChild(lb);
      }
    }
    svg.appendChild(e); edgeEls.push({e,l});
  });
  ps.sort((o1,o2)=>o1.p.z-o2.p.z).forEach(({n,p})=>{
    const g=el('g',{'class':'node'});
    g.appendChild(el('circle',{cx:p.x,cy:p.y,r:15*Math.max(.6,p.f)}));
    g.style.opacity=0.35+0.65*Math.max(0,(p.z+1)/2);
    const tx=el('text',{x:p.x,y:p.y+4}); tx.textContent=n.name; tx.style.opacity=p.z>-0.2?1:0; g.appendChild(tx);
    g.onclick=()=>select(n.name);
    g.onmouseenter=()=>{tx.style.opacity=1;}; g.onmouseleave=()=>{if(p.z<=-0.2)tx.style.opacity=0;};
    if(hiddenSet.has(n.name)) g.style.display='none';
    if(selectedName===n.name) g.classList.add('sel');
    svg.appendChild(g); nodeEls.push({g,n});
  });
}
let drag=null, tool='rotate';
svg.onmousedown=e=>{
  const doPan = tool==='pan' || e.shiftKey || e.button===1;
  svg.classList.add('grabbing');
  drag={pan:doPan,x:e.clientX,y:e.clientY,rx:rot.rx,ry:rot.ry,px:pan.x,py:pan.y};
};
window.onmousemove=e=>{if(drag){
  if(drag.pan){ pan.x=drag.px+(e.clientX-drag.x)/zoom; pan.y=drag.py+(e.clientY-drag.y)/zoom; }
  else { rot.ry=drag.ry+(e.clientX-drag.x)*0.008; rot.rx=drag.rx+(e.clientY-drag.y)*0.008; }
  drawGraph();
}};
window.onmouseup=()=>{drag=null; svg.classList.remove('grabbing');};
document.getElementById('hand').onclick=e=>{
  tool = tool==='pan' ? 'rotate' : 'pan';
  svg.classList.toggle('grab', tool==='pan');
  e.target.classList.toggle('off', tool!=='pan');
  e.target.textContent = tool==='pan' ? '✋' : '🔄';
  e.target.title = tool==='pan' ? 'pan mode (click to rotate)' : 'hand tool: drag to pan planet';
};
window.onmouseup=()=>drag=null;
svg.onwheel=e=>{e.preventDefault();zoom=Math.min(20,Math.max(.4,zoom*(e.deltaY<0?1.2:1/1.2)));drawGraph();};
document.getElementById('zIn').onclick=()=>{zoom=Math.min(20,zoom*1.5);drawGraph();};
document.getElementById('zOut').onclick=()=>{zoom=Math.max(.4,zoom/1.5);drawGraph();};
document.getElementById('zReset').onclick=()=>{zoom=1;pan={x:0,y:0};rot={rx:-0.4,ry:0.6};resetGraph();drawGraph();};
document.getElementById('sideToggle').onclick=()=>document.getElementById('side').classList.toggle('closed');
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
  toast('selected: '+name);
  document.getElementById('empty').style.display='none';
  document.getElementById('detail').style.display='block';
  document.getElementById('tname').textContent=t.name;
  document.getElementById('tsrc').textContent='source: '+t.source+' · '+t.columns.length+' columns · '+t.fks.length+' foreign keys';
  document.querySelectorAll('.titem').forEach(e=>e.classList.toggle('sel',e.dataset.name===name));
  document.querySelectorAll('.card').forEach(e=>e.classList.toggle('sel',e.dataset.name===name));
  document.getElementById('detail').dataset.open='1';
  const cb=document.getElementById('cbody'); cb.innerHTML='';
  t.columns.forEach(c=>{
    const fk=t.fks.find(f=>f.column===c);
    const tr=document.createElement('tr');
    tr.innerHTML='<td><span class="'+(fk?'fk':'pk')+'">'+c+'</span></td><td>'+(fk?'→ <a class="jump" data-t="'+fk.ref_table+'">'+fk.ref_table+'</a>':'')+'</td>';
    cb.appendChild(tr);
  });
  cb.querySelectorAll('.jump').forEach(a=>a.onclick=()=>select(a.dataset.t));
  selectedName = name;
  const near = new Set([name]);
  links.forEach(l=>{ if(l.s.name===name) near.add(l.t.name); if(l.t.name===name) near.add(l.s.name); });
  hiddenSet = new Set(nodes.map(n=>n.name).filter(x=>!near.has(x)));
  drawGraph();
  edgeEls.forEach(({e,l})=>{ if(!(l.s.name===name||l.t.name===name)) e.style.display='none'; });
}
document.getElementById('q').oninput=e=>{buildSide(e.target.value.toLowerCase()); buildCards(e.target.value.toLowerCase());};
window.onkeydown=e=>{
  if(e.key==='/' && document.activeElement!==document.getElementById('q')){ e.preventDefault(); document.getElementById('q').focus(); }
  if(e.key==='Escape'){ resetGraph(); document.getElementById('q').value=''; buildSide(); buildCards(); }
};
function toast(msg){const t=document.getElementById('toast'); t.textContent=msg; t.style.opacity=1; setTimeout(()=>t.style.opacity=0,1600);} 
document.getElementById('cnt').textContent=data.length+' tables · '+links.length+' relations';
function buildCards(q=''){
  const c=document.getElementById('cards'); c.innerHTML='';
  data.filter(t=>!q||t.name.toLowerCase().includes(q)).forEach(t=>{
    const d=document.createElement('div'); d.className='card'; d.dataset.name=t.name;
    let colsHtml=t.columns.slice(0,12).map(col=>{
      const fk=t.fks.find(f=>f.column===col);
      return '<div class="'+(fk?'fk':'')+'">'+col+(fk?' → '+fk.ref_table:'')+'</div>';
    }).join('');
    if(t.columns.length>12) colsHtml+='<div>… +'+(t.columns.length-12)+' more</div>';
    d.innerHTML='<h3>'+t.name+'</h3><div class="cols">'+colsHtml+'</div>';
    d.onclick=()=>select(t.name); c.appendChild(d);
  });
}
let mode='graph';
function setMode(m){
  mode=m;
  document.getElementById('vGraph').classList.toggle('off',m!=='graph');
  document.getElementById('vCards').classList.toggle('off',m!=='cards');
  document.getElementById('cards').style.display=m==='cards'?'flex':'none';
  document.getElementById('detail').style.display=(m==='cards'&&!document.getElementById('detail').dataset.open)?'none':'block';
  document.getElementById('empty').style.display=(m==='cards')?'none':'block';
}
document.getElementById('vGraph').onclick=()=>{setMode('graph'); resetGraph();};
document.getElementById('vCards').onclick=()=>setMode('cards');
document.getElementById('vEdges').onclick=e=>{
  const g=document.getElementById('graph');
  g.classList.toggle('no-edges');
  e.target.textContent = g.classList.contains('no-edges') ? '🔗 edges: off' : '🔗 edges: on';
};
let hiddenSet = new Set();
let selectedName = null;
function resetGraph(){ hiddenSet = new Set(); selectedName = null; drawGraph(); }
svg.ondblclick=()=>resetGraph();
buildSide(); buildCards();
requestAnimationFrame(()=>drawGraph());
window.onresize=()=>drawGraph();
</script></body></html>
"""


def schema_html(rows):
    import json
    return HTML_TEMPLATE.replace("__DATA__", json.dumps(rows))


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
