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
# Postgres-dump style inline foreign key: "col INT REFERENCES other(id)"
INLINE_FK_RE = re.compile(
    r'''^["'`\[]?(\w+)["'`\]]?[\s\S]*?\bREFERENCES\s+'''
    r'''(?:["'`\[]?\w+["'`\]]?\.)?["'`\[]?(\w+)''',
    re.IGNORECASE,
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


def strip_sql_comments(text):
    """Remove -- line comments and /* */ block comments while keeping
    quoted strings safe (mirrors the HTML viewer's own parser)."""
    out = []
    i, n = 0, len(text)
    in_single = in_double = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if not in_single and not in_double:
            if ch == "-" and nxt == "-":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                i += 2
                while i < n - 1 and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        out.append(ch)
        i += 1
    return "".join(out)


def parse_sql_schema(text):
    text = strip_sql_comments(text)
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
            inline_fk = INLINE_FK_RE.match(part)
            if inline_fk:
                # column-level REFERENCES: the column itself stays in cols
                fks.append({"column": inline_fk.group(1),
                            "ref_table": inline_fk.group(2)})
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


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DB Schema Explorer — 3D Interactive Visualizer</title>
  <style>
    /* =====================================================================
       DESIGN TOKENS & RESET
       Modern dark slate/navy glassmorphism with high legibility & depth
       ===================================================================== */
    :root {
      --bg-deep: #070d1a;
      --bg-mid: #0d1629;
      --bg-surface: rgba(15, 23, 42, 0.86);
      --bg-surface-solid: #0f172a;
      --bg-elevated: #17233b;
      --border: #1e2e4d;
      --border-strong: #33486e;
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --accent: #3b82f6;
      --accent-glow: rgba(59, 130, 246, 0.35);
      --hot: #f97316;
      --hot-soft: #fdba74;
      --amber: #fbbf24;
      --emerald: #34d399;
      --cyan: #22d3ee;
      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 16px;
      --shadow-lg: 0 14px 40px rgba(0, 0, 0, 0.55);
      --shadow-sm: 0 4px 14px rgba(0, 0, 0, 0.35);
    }

    * {
      box-sizing: border-box;
      -webkit-tap-highlight-color: transparent;
    }

    html, body {
      margin: 0;
      padding: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      /* Segoe UI first: crisp native rendering on Windows */
      font-family: 'Segoe UI', 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(1200px 680px at 65% -10%, #152546 0%, #091020 55%, #060a14 100%);
      color: var(--text);
      font-size: 14px;
      line-height: 1.45;
    }

    body {
      display: flex;
      flex-direction: column;
    }

    /* Custom subtle scrollbars */
    ::-webkit-scrollbar { width: 7px; height: 7px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #263656; border-radius: 999px; }
    ::-webkit-scrollbar-thumb:hover { background: #3b5380; }

    /* =====================================================================
       TOP NAVIGATION BAR
       ===================================================================== */
    #top {
      flex: none;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 9px 14px;
      background: linear-gradient(180deg, rgba(19, 31, 56, 0.96), rgba(13, 22, 41, 0.95));
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      box-shadow: 0 4px 18px rgba(0, 0, 0, 0.45);
      flex-wrap: wrap;
      z-index: 30;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 700;
      font-size: 15px;
      letter-spacing: 0.2px;
      color: #e0f2fe;
      white-space: nowrap;
      margin-right: 4px;
    }

    .brand-icon {
      width: 26px;
      height: 26px;
      border-radius: 8px;
      background: linear-gradient(135deg, #2563eb, #06b6d4);
      display: grid;
      place-items: center;
      box-shadow: 0 0 14px rgba(59, 130, 246, 0.45);
      font-size: 14px;
    }

    .search-wrap {
      position: relative;
      display: flex;
      align-items: center;
      flex: 0 1 250px;
      min-width: 170px;
    }

    .search-wrap input {
      width: 100%;
      background: rgba(7, 13, 26, 0.85);
      color: var(--text);
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 7px 34px 7px 12px;
      font-size: 13px;
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }

    .search-wrap input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.22);
    }

    .search-kbd {
      position: absolute;
      right: 8px;
      font-size: 11px;
      color: var(--text-dim);
      background: #17233b;
      border: 1px solid var(--border-strong);
      border-radius: 5px;
      padding: 1px 6px;
      pointer-events: none;
    }

    .search-clear {
      position: absolute;
      right: 6px;
      background: transparent;
      border: 0;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 14px;
      padding: 2px 6px;
      display: none;
    }

    .search-clear.visible { display: block; }
    .search-clear.visible + .search-kbd { display: none; }

    .chip {
      background: rgba(30, 41, 59, 0.78);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 4px 11px;
      font-size: 12px;
      color: var(--text-muted);
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 5px;
    }

    .chip b { color: #f8fafc; font-weight: 600; }

    .btn-group {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }

    .btn {
      background: #1a2845;
      color: #cbd5e1;
      border: 1px solid var(--border-strong);
      border-radius: 9px;
      padding: 6px 11px;
      font-size: 12.5px;
      font-weight: 500;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      white-space: nowrap;
      transition: all 0.15s ease;
      user-select: none;
    }

    .btn:hover {
      background: #24375e;
      border-color: #4f6ca1;
      color: #ffffff;
    }

    .btn.active {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      border-color: #60a5fa;
      color: #ffffff;
      box-shadow: 0 2px 12px rgba(37, 99, 235, 0.35);
    }

    .btn.off {
      background: rgba(15, 23, 42, 0.65);
      color: var(--text-dim);
      border-color: var(--border);
    }

    .btn.primary-sql {
      background: linear-gradient(135deg, #0d9488, #0284c7);
      border-color: #38bdf8;
      color: #ffffff;
    }

    .btn.primary-sql:hover {
      filter: brightness(1.1);
      box-shadow: 0 2px 12px rgba(14, 165, 233, 0.35);
    }

    .top-spacer { flex: 1; }

    /* =====================================================================
       MULTI-DATABASE LEGEND BAR
       ===================================================================== */
    #legend {
      flex: none;
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 7px 14px;
      background: rgba(10, 17, 32, 0.92);
      border-bottom: 1px solid var(--border);
      font-size: 12px;
      overflow-x: auto;
      white-space: nowrap;
      scrollbar-width: thin;
      z-index: 25;
    }

    .lg {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      background: rgba(18, 29, 52, 0.85);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 4px 11px;
      cursor: pointer;
      transition: all 0.15s ease;
      color: #cbd5e1;
      user-select: none;
    }

    .lg:hover {
      border-color: #60a5fa;
      background: rgba(30, 48, 84, 0.9);
    }

    .lg.off {
      opacity: 0.35;
      text-decoration: line-through;
    }

    .lg.solo {
      border-color: #60a5fa;
      background: rgba(37, 99, 235, 0.22);
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.35);
      color: #fff;
    }

    .lg .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      flex: none;
      box-shadow: 0 0 8px currentColor;
    }

    .lg .n { color: var(--text-dim); font-size: 11px; }

    .legend-hint {
      margin-left: auto;
      color: var(--text-dim);
      font-size: 11px;
      padding-left: 10px;
    }

    /* =====================================================================
       MAIN WORKSPACE LAYOUT
       ===================================================================== */
    #main {
      display: flex;
      flex: 1 1 auto;
      min-height: 0;
      position: relative;
      overflow: hidden;
    }

    /* Collapsible Left Sidebar */
    #side {
      width: 286px;
      min-width: 286px;
      background: rgba(12, 20, 38, 0.95);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      transition: margin-left 0.22s cubic-bezier(0.4, 0, 0.2, 1), transform 0.22s ease;
      z-index: 20;
    }

    #side.closed {
      margin-left: -286px;
    }

    #side-tools {
      flex: none;
      padding: 8px 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      background: rgba(15, 25, 46, 0.95);
      border-bottom: 1px solid var(--border);
    }

    #side-tools .cnt {
      color: var(--text-muted);
      font-size: 11.5px;
      font-weight: 500;
    }

    #side-tools button {
      background: #1a2845;
      color: #cbd5e1;
      border: 1px solid var(--border-strong);
      border-radius: 7px;
      padding: 4px 10px;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.15s;
    }

    #side-tools button:hover {
      border-color: var(--accent);
      color: #fff;
    }

    #side-list {
      flex: 1 1 auto;
      overflow-y: auto;
      padding: 8px;
    }

    details.src {
      margin-bottom: 6px;
    }

    details.src summary {
      cursor: pointer;
      color: #bfdbfe;
      font-size: 12px;
      padding: 7px 9px;
      border-radius: 9px;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 8px;
      font-weight: 600;
      user-select: none;
      transition: background 0.15s;
    }

    details.src summary::-webkit-details-marker { display: none; }
    details.src summary:hover { background: rgba(30, 41, 59, 0.75); }
    details.src summary .caret {
      font-size: 9px;
      color: var(--text-dim);
      transition: transform 0.15s;
      width: 10px;
      text-align: center;
    }
    details.src[open] summary .caret { transform: rotate(90deg); }
    details.src summary .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      flex: none;
    }
    details.src summary .sname {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }
    details.src summary .cnt {
      color: var(--text-dim);
      font-weight: 400;
      font-size: 11px;
    }

    .titem {
      padding: 7px 10px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 12.5px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin: 2px 0 2px 10px;
      color: #cbd5e1;
      transition: background 0.12s, color 0.12s;
    }

    .titem:hover {
      background: rgba(30, 45, 76, 0.85);
      color: #fff;
    }

    .titem.sel {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      box-shadow: 0 2px 10px rgba(37, 99, 235, 0.35);
    }

    .titem .tname-txt {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .titem .nfk {
      color: var(--text-dim);
      font-size: 10.5px;
      background: rgba(15, 23, 42, 0.65);
      padding: 1px 6px;
      border-radius: 999px;
      flex: none;
    }

    .titem.sel .nfk {
      background: rgba(255, 255, 255, 0.2);
      color: #e0f2fe;
    }

    /* =====================================================================
       CENTRAL STAGE (#content) — PERMANENT 3D CANVAS + INSPECTOR DRAWER
       ===================================================================== */
    #content {
      flex: 1 1 auto;
      min-width: 0;
      min-height: 0;
      display: flex;
      position: relative;
      overflow: hidden;
    }

    /* Permanent 3D Graph Container */
    #graph-wrap {
      flex: 1 1 auto;
      min-width: 0;
      min-height: 0;
      position: relative;
      display: flex;
      flex-direction: column;
      background: radial-gradient(750px 460px at 50% 45%, #122140 0%, #091122 65%, #060b16 100%);
      overflow: hidden;
    }

    #graph {
      width: 100%;
      height: 100%;
      display: block;
      user-select: none;
      touch-action: none;
      cursor: grab;
    }

    #graph.grabbing { cursor: grabbing; }
    #graph.pan-mode { cursor: move; }
    #graph.no-edges .edge:not(.hot):not(.dimrel) { display: none; }

    /* SVG Elements Styling */
    .bg-ring {
      fill: none;
      stroke: rgba(96, 165, 250, 0.08);
      stroke-width: 1;
      stroke-dasharray: 4 6;
      pointer-events: none;
    }

    .bg-star {
      fill: #93c5fd;
      pointer-events: none;
    }

    .edge {
      stroke: #475569;
      fill: none;
      pointer-events: none;
      transition: stroke 0.2s;
    }

    .edge.cross {
      stroke: var(--amber);
      stroke-dasharray: 7 5;
    }

    /* neighbor-to-neighbor relations inside the focus view: visible but hushed */
    .edge.dimrel {
      stroke: #64748b;
      stroke-dasharray: 2 4;
    }

    .edge.hot {
      stroke: var(--hot);
      stroke-dasharray: none;
      filter: drop-shadow(0 0 5px rgba(249, 115, 22, 0.65));
    }

    .edge.hot.cross {
      stroke: var(--amber);
      stroke-dasharray: 7 5;
      filter: drop-shadow(0 0 5px rgba(251, 191, 36, 0.65));
    }

    .node {
      cursor: pointer;
    }

    .node .halo {
      fill: none;
      stroke: var(--sc, #3b82f6);
      stroke-width: 1.5;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.18s;
    }

    .node:hover .halo,
    .node.sel .halo,
    .node.match .halo {
      opacity: 0.65;
    }

    .node.sel .halo {
      stroke: var(--hot);
      stroke-width: 2.5;
    }

    .node.match .halo {
      stroke: var(--cyan);
      stroke-width: 2.5;
    }

    .node .core {
      fill: var(--sc, #3b82f6);
      stroke: rgba(255, 255, 255, 0.65);
      stroke-width: 1.4;
      transition: fill 0.15s, stroke 0.15s;
    }

    .node:hover .core {
      stroke: #ffffff;
      stroke-width: 2.2;
    }

    .node.sel .core {
      fill: var(--hot);
      stroke: #ffedd5;
      stroke-width: 2.8;
      filter: drop-shadow(0 0 12px rgba(249, 115, 22, 0.8));
    }

    .node .shine {
      fill: rgba(255, 255, 255, 0.28);
      pointer-events: none;
    }

    .node .nlabel {
      fill: #f1f5f9;
      font-size: 12px;
      font-weight: 600;
      text-anchor: middle;
      pointer-events: none;
      paint-order: stroke;
      stroke: #070d1a;
      stroke-width: 4.5px;
      stroke-linejoin: round;
      transition: opacity 0.15s;
    }

    .node.sel .nlabel {
      fill: #ffedd5;
      font-size: 13.5px;
      font-weight: 700;
    }

    .elabel {
      fill: var(--hot-soft);
      font-size: 10.5px;
      font-weight: 600;
      text-anchor: middle;
      pointer-events: none;
      paint-order: stroke;
      stroke: #070d1a;
      stroke-width: 4.5px;
      stroke-linejoin: round;
    }

    .elabel.cross {
      fill: #fde68a;
    }

    /* Floating Canvas Controls (#ctl) */
    #ctl {
      position: absolute;
      top: 14px;
      right: 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      z-index: 12;
    }

    #ctl button {
      width: 38px;
      height: 38px;
      border-radius: 11px;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(8px);
      color: #cbd5e1;
      border: 1px solid var(--border-strong);
      font-size: 16px;
      cursor: pointer;
      display: grid;
      place-items: center;
      box-shadow: var(--shadow-sm);
      transition: all 0.15s;
    }

    #ctl button:hover {
      background: #2563eb;
      border-color: #60a5fa;
      color: #ffffff;
      transform: translateY(-1px);
    }

    #ctl button.active-tool {
      background: #0284c7;
      border-color: #38bdf8;
      color: #ffffff;
    }

    /* Floating Focus Banner (top-left of graph) */
    #focus-hud {
      position: absolute;
      top: 14px;
      left: 14px;
      display: none;
      align-items: center;
      gap: 8px;
      background: rgba(15, 23, 42, 0.88);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(249, 115, 22, 0.5);
      border-radius: 999px;
      padding: 6px 12px 6px 14px;
      box-shadow: var(--shadow-sm);
      z-index: 12;
      font-size: 12px;
      max-width: calc(100% - 80px);
    }

    #focus-hud.visible { display: flex; }

    #focus-hud .fdot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--hot);
      box-shadow: 0 0 8px var(--hot);
      flex: none;
    }

    #focus-hud .ftxt {
      color: #f8fafc;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    #focus-hud button {
      background: rgba(255, 255, 255, 0.1);
      color: #e2e8f0;
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 11px;
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.15s;
    }

    #focus-hud button:hover {
      background: var(--accent);
      border-color: var(--accent);
    }

    /* Subtle Bottom Canvas Hint Bar */
    #canvas-hint {
      position: absolute;
      bottom: 12px;
      left: 14px;
      background: rgba(10, 17, 32, 0.72);
      backdrop-filter: blur(6px);
      border: 1px solid rgba(30, 46, 77, 0.8);
      border-radius: 999px;
      padding: 5px 12px;
      font-size: 11px;
      color: var(--text-dim);
      pointer-events: none;
      z-index: 10;
    }

    /* =====================================================================
       SLEEK INSPECTOR DRAWER (#detail) — NEVER HIDES THE GRAPH
       ===================================================================== */
    #detail {
      width: 0;
      min-width: 0;
      opacity: 0;
      overflow: hidden;
      background: rgba(13, 21, 39, 0.97);
      backdrop-filter: blur(14px);
      border-left: 0 solid var(--border);
      display: flex;
      flex-direction: column;
      transition: width 0.25s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
      z-index: 18;
    }

    #detail.open {
      width: 390px;
      min-width: 340px;
      opacity: 1;
      border-left-width: 1px;
      box-shadow: -8px 0 30px rgba(0, 0, 0, 0.45);
    }

    .detail-header {
      flex: none;
      padding: 14px 16px 12px;
      background: linear-gradient(180deg, rgba(23, 35, 59, 0.9), rgba(15, 23, 42, 0.85));
      border-bottom: 1px solid var(--border);
    }

    .detail-title-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }

    .detail-title-wrap {
      min-width: 0;
    }

    #tname {
      margin: 0;
      font-size: 18px;
      font-weight: 700;
      color: #f8fafc;
      word-break: break-word;
    }

    .srchip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid;
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 11px;
      font-weight: 600;
      margin-top: 6px;
      background: rgba(7, 13, 26, 0.65);
    }

    .srchip .sdot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
    }

    #closeDetail {
      background: rgba(30, 41, 59, 0.85);
      color: var(--text-muted);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      width: 30px;
      height: 30px;
      cursor: pointer;
      font-size: 16px;
      flex: none;
      display: grid;
      place-items: center;
      transition: all 0.15s;
    }

    #closeDetail:hover {
      background: #ef4444;
      border-color: #f87171;
      color: #fff;
    }

    .detail-stats {
      display: flex;
      gap: 6px;
      margin-top: 10px;
      flex-wrap: wrap;
    }

    .detail-body {
      flex: 1 1 auto;
      overflow-y: auto;
      padding: 14px 16px 24px;
    }

    .sec-title {
      font-size: 11.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.7px;
      color: var(--text-dim);
      margin: 0 0 8px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    table.cols {
      width: 100%;
      border-collapse: collapse;
      font-size: 12.5px;
      background: rgba(15, 23, 42, 0.75);
      border-radius: 10px;
      overflow: hidden;
      border: 1px solid var(--border);
      margin-bottom: 18px;
    }

    table.cols th {
      background: rgba(30, 41, 59, 0.8);
      text-align: left;
      padding: 8px 10px;
      color: #93c5fd;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.4px;
    }

    table.cols td {
      padding: 8px 10px;
      border-top: 1px solid rgba(30, 46, 77, 0.7);
      vertical-align: middle;
    }

    table.cols tr:hover td {
      background: rgba(30, 45, 76, 0.55);
    }

    .col-cell {
      display: flex;
      align-items: center;
      gap: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
    }

    .badge {
      font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
      font-size: 9.5px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: 4px;
      letter-spacing: 0.3px;
      flex: none;
    }

    .badge.pk {
      background: rgba(16, 185, 129, 0.16);
      color: var(--emerald);
      border: 1px solid rgba(52, 211, 153, 0.35);
    }

    .badge.fk {
      background: rgba(245, 158, 11, 0.16);
      color: var(--amber);
      border: 1px solid rgba(251, 191, 36, 0.35);
    }

    .badge.cross {
      background: rgba(251, 191, 36, 0.12);
      color: #fde68a;
      border: 1px dashed rgba(251, 191, 36, 0.5);
      margin-left: 4px;
    }

    .badge.self {
      background: rgba(34, 211, 238, 0.14);
      color: var(--cyan);
      border: 1px solid rgba(34, 211, 238, 0.35);
      margin-left: 4px;
    }

    a.jump {
      color: #60a5fa;
      cursor: pointer;
      text-decoration: none;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 6px;
      border-radius: 6px;
      background: rgba(59, 130, 246, 0.12);
      transition: all 0.14s;
    }

    a.jump:hover {
      background: rgba(59, 130, 246, 0.28);
      color: #bfdbfe;
    }

    .ref-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .ref-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 10px;
      background: rgba(15, 23, 42, 0.78);
      border: 1px solid var(--border);
      border-radius: 9px;
      cursor: pointer;
      transition: all 0.15s;
      font-size: 12.5px;
    }

    .ref-item:hover {
      border-color: var(--accent);
      background: rgba(30, 45, 76, 0.75);
      transform: translateX(2px);
    }

    .ref-left {
      display: flex;
      align-items: center;
      gap: 7px;
      min-width: 0;
    }

    .ref-left .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex: none;
    }

    .ref-col {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px;
      color: var(--amber);
      background: rgba(251, 191, 36, 0.1);
      padding: 2px 6px;
      border-radius: 5px;
      white-space: nowrap;
    }

    .empty-refs {
      font-size: 12px;
      color: var(--text-dim);
      padding: 10px 12px;
      background: rgba(15, 23, 42, 0.45);
      border: 1px dashed var(--border);
      border-radius: 9px;
    }

    /* =====================================================================
       ER CARDS VIEW (#cards)
       ===================================================================== */
    #cards {
      display: none;
      flex: 1 1 auto;
      overflow-y: auto;
      padding: 18px;
      grid-template-columns: repeat(auto-fill, minmax(255px, 1fr));
      gap: 14px;
      align-content: start;
      background: radial-gradient(800px 400px at 50% 0%, #111f3c 0%, #081020 75%);
    }

    #cards.visible { display: grid; }

    .card {
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid var(--border);
      border-left: 4px solid var(--sc, #3b82f6);
      border-radius: 12px;
      cursor: pointer;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
    }

    .card:hover {
      border-color: #60a5fa;
      transform: translateY(-3px);
      box-shadow: 0 10px 24px rgba(0, 0, 0, 0.45);
    }

    .card.sel {
      border-color: var(--hot);
      box-shadow: 0 0 0 2px rgba(249, 115, 22, 0.45);
    }

    .card-head {
      padding: 10px 12px;
      background: rgba(25, 38, 66, 0.85);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .card-head h3 {
      margin: 0;
      font-size: 13.5px;
      color: #f1f5f9;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .card-src {
      padding: 5px 12px 2px;
      font-size: 10.5px;
      color: var(--text-dim);
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .card-src .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      flex: none;
    }

    .card-cols {
      padding: 6px 12px 10px;
      font-size: 11.5px;
      color: var(--text-muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      display: flex;
      flex-direction: column;
      gap: 3px;
    }

    .card-col-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      padding: 2px 0;
    }

    .card-col-row.is-fk { color: var(--amber); }
    .card-col-row.is-pk { color: var(--emerald); }

    .card-more {
      color: var(--text-dim);
      font-family: 'Segoe UI', 'Inter', system-ui, sans-serif;
      font-size: 11px;
      padding-top: 3px;
    }

    /* =====================================================================
       PASTE SQL MODAL
       ===================================================================== */
    #sqlModal {
      position: fixed;
      inset: 0;
      background: rgba(4, 8, 18, 0.78);
      backdrop-filter: blur(8px);
      display: none;
      align-items: center;
      justify-content: center;
      padding: 16px;
      z-index: 100;
    }

    #sqlModal.open { display: flex; }

    .modal-box {
      width: 100%;
      max-width: 720px;
      max-height: 90vh;
      background: #0f172a;
      border: 1px solid var(--border-strong);
      border-radius: 16px;
      box-shadow: var(--shadow-lg);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      animation: modalPop 0.18s ease-out;
    }

    @keyframes modalPop {
      from { opacity: 0; transform: scale(0.96) translateY(8px); }
      to { opacity: 1; transform: scale(1) translateY(0); }
    }

    .modal-head {
      padding: 14px 18px;
      background: #17233b;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .modal-head h3 {
      margin: 0;
      font-size: 15px;
      color: #f8fafc;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .modal-body {
      padding: 16px 18px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .modal-row {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    .modal-row label {
      font-size: 12px;
      color: var(--text-muted);
      font-weight: 600;
    }

    .modal-input {
      background: #070d1a;
      color: var(--text);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      padding: 7px 10px;
      font-size: 13px;
      outline: none;
      flex: 1;
      min-width: 180px;
    }

    .modal-input:focus, .modal-textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
    }

    .modal-textarea {
      width: 100%;
      min-height: 230px;
      background: #070d1a;
      color: #e2e8f0;
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12.5px;
      line-height: 1.5;
      resize: vertical;
      outline: none;
    }

    .modal-msg {
      font-size: 12px;
      padding: 8px 12px;
      border-radius: 8px;
      display: none;
    }

    .modal-msg.err {
      display: block;
      background: rgba(239, 68, 68, 0.14);
      border: 1px solid rgba(248, 113, 113, 0.4);
      color: #fca5a5;
    }

    .modal-msg.ok {
      display: block;
      background: rgba(16, 185, 129, 0.14);
      border: 1px solid rgba(52, 211, 153, 0.4);
      color: #6ee7b7;
    }

    .modal-foot {
      padding: 12px 18px;
      background: rgba(15, 23, 42, 0.95);
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }

    /* =====================================================================
       DEBOUNCED TOAST NOTIFICATION
       ===================================================================== */
    #toast {
      position: fixed;
      bottom: 18px;
      left: 50%;
      transform: translateX(-50%) translateY(8px);
      background: rgba(15, 23, 42, 0.94);
      backdrop-filter: blur(10px);
      border: 1px solid var(--border-strong);
      padding: 8px 16px;
      border-radius: 999px;
      font-size: 12.5px;
      color: #f1f5f9;
      box-shadow: var(--shadow-sm);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease, transform 0.2s ease;
      z-index: 200;
      white-space: nowrap;
      max-width: 90vw;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    #toast.show {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }

    /* =====================================================================
       RESPONSIVE ADAPTATIONS (TABLET & MOBILE)
       ===================================================================== */
    @media (max-width: 960px) {
      #side {
        position: absolute;
        top: 0;
        bottom: 0;
        left: 0;
        box-shadow: 10px 0 30px rgba(0, 0, 0, 0.55);
      }

      #detail.open {
        position: absolute;
        left: 0;
        right: 0;
        bottom: 0;
        width: 100%;
        min-width: 0;
        height: 48%;
        border-left: 0;
        border-top: 1px solid var(--border-strong);
        border-radius: 16px 16px 0 0;
        box-shadow: 0 -10px 30px rgba(0, 0, 0, 0.6);
      }

      #canvas-hint { display: none; }
      .legend-hint { display: none; }
    }

    @media (max-width: 600px) {
      #top { padding: 8px 10px; gap: 6px; }
      .search-wrap { flex: 1 1 100%; order: 10; }
      .btn { padding: 6px 9px; font-size: 12px; }
      #ctl button { width: 34px; height: 34px; font-size: 15px; }
    }
  </style>
</head>
<body>
  <!-- =====================================================================
       TOP BAR
       ===================================================================== -->
  <header id="top">
    <button id="sideToggle" class="btn" title="Toggle sidebar">☰</button>
    <div class="brand">
      <div class="brand-icon">🧭</div>
      <span>DB Schema Explorer</span>
    </div>

    <div class="search-wrap">
      <input id="q" type="text" placeholder="Search tables or columns..." autocomplete="off">
      <button id="qClear" class="search-clear" title="Clear search">×</button>
      <span class="search-kbd">/</span>
    </div>

    <span class="chip" id="cnt">0 tables</span>

    <div class="btn-group">
      <button id="vGraph" class="btn active">🕸 3D Network</button>
      <button id="vCards" class="btn">🗂 ER Cards</button>
    </div>

    <div class="btn-group">
      <button id="vEdges" class="btn active">🔗 Edges: On</button>
      <button id="vLabels" class="btn">🏷 Labels: Front</button>
      <button id="vSpin" class="btn active" title="Toggle gentle auto-rotation">⟳ Spin</button>
      <button id="openSqlBtn" class="btn primary-sql" title="Parse custom SQL DDL live">＋ Paste SQL</button>
    </div>
  </header>

  <!-- =====================================================================
       MULTI-DATABASE LEGEND BAR
       ===================================================================== -->
  <div id="legend"></div>

  <!-- =====================================================================
       MAIN WORKSPACE: SIDEBAR + PERMANENT 3D STAGE + INSPECTOR DRAWER
       ===================================================================== -->
  <div id="main">
    <!-- Left Collapsible Sidebar -->
    <aside id="side">
      <div id="side-tools">
        <span class="cnt" id="side-cnt">0 dbs</span>
        <button id="foldBtn">Close All</button>
      </div>
      <div id="side-list"></div>
    </aside>

    <!-- Main Content Stage -->
    <main id="content">
      <!-- Permanent 3D SVG Canvas Wrapper (Always visible in 3D Network mode!) -->
      <section id="graph-wrap">
        <svg id="graph" aria-label="3D Database Schema Network">
          <defs>
            <!-- Directional Arrowheads -->
            <marker id="fkA" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0.8 L7.5,4 L0,7.2 z" fill="#fdba74"></path>
            </marker>
            <marker id="fkR" markerWidth="8" markerHeight="8" refX="1" refY="4" orient="auto">
              <path d="M7.5,0.8 L0,4 L7.5,7.2 z" fill="#fdba74"></path>
            </marker>
            <marker id="fkACross" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0.8 L7.5,4 L0,7.2 z" fill="#fbbf24"></path>
            </marker>
            <marker id="fkRCross" markerWidth="8" markerHeight="8" refX="1" refY="4" orient="auto">
              <path d="M7.5,0.8 L0,4 L7.5,7.2 z" fill="#fbbf24"></path>
            </marker>
            <marker id="fkSelf" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0.6 L6.8,3.5 L0,6.4 z" fill="#22d3ee"></path>
            </marker>
          </defs>
          <g id="layer-bg"></g>
          <g id="layer-edges"></g>
          <g id="layer-nodes"></g>
          <g id="layer-elabels"></g>
        </svg>

        <!-- Focus Mode HUD Pill -->
        <div id="focus-hud">
          <span class="fdot"></span>
          <span class="ftxt" id="focus-hud-txt">Focused</span>
          <button id="exitFocusBtn">← Full 3D Planet (Esc)</button>
        </div>

        <!-- Floating Canvas Controls -->
        <div id="ctl">
          <button id="zIn" title="Zoom in (+)">+</button>
          <button id="zOut" title="Zoom out (−)">−</button>
          <button id="zFit" title="Center &amp; fit view">⤢</button>
          <button id="hand" title="Toggle Rotate / Pan tool">🔄</button>
          <button id="zReset" title="Reset to 3D Sphere (Esc)">⊙</button>
        </div>

        <!-- Subtle Controls Hint -->
        <div id="canvas-hint">
          Drag = Rotate · Shift+Drag / ✋ = Pan · Scroll = Zoom · Click Node = Sunflower Focus · Dbl-Click / Esc = Reset
        </div>
      </section>

      <!-- ER Cards Grid View -->
      <section id="cards"></section>

      <!-- Sleek Slide-In Table Inspector Drawer (Alongside Graph) -->
      <aside id="detail" aria-label="Table Inspector">
        <div class="detail-header">
          <div class="detail-title-row">
            <div class="detail-title-wrap">
              <h2 id="tname">table_name</h2>
              <div id="tsrcc" class="srchip"><span class="sdot"></span><span id="tsrcc-txt"></span></div>
            </div>
            <button id="closeDetail" title="Close inspector &amp; reset (Esc)">×</button>
          </div>
          <div class="detail-stats" id="tstats"></div>
        </div>
        <div class="detail-body">
          <div class="sec-title">
            <span>Columns &amp; Foreign Keys</span>
            <span id="col-sec-cnt"></span>
          </div>
          <table class="cols">
            <thead>
              <tr>
                <th>Column</th>
                <th>Relation</th>
              </tr>
            </thead>
            <tbody id="cbody"></tbody>
          </table>

          <div class="sec-title">
            <span>Referenced By (Incoming)</span>
            <span id="inc-sec-cnt"></span>
          </div>
          <div id="refbody" class="ref-list"></div>
        </div>
      </aside>
    </main>
  </div>

  <!-- =====================================================================
       PASTE SQL DDL MODAL
       ===================================================================== -->
  <div id="sqlModal" role="dialog" aria-modal="true" aria-labelledby="sqlModalTitle">
    <div class="modal-box">
      <div class="modal-head">
        <h3 id="sqlModalTitle">⚡ Live SQL DDL Parser</h3>
        <button id="closeSqlBtn" class="btn">×</button>
      </div>
      <div class="modal-body">
        <div class="modal-row">
          <label for="sqlSourceName">Source Label:</label>
          <input id="sqlSourceName" class="modal-input" type="text" value="custom_schema.sql" placeholder="e.g. billing.sql">
          <button id="loadSampleSqlBtn" class="btn">Insert Sample SQL</button>
          <button id="restoreDefaultBtn" class="btn">Restore 4-DB Demo</button>
        </div>
        <textarea id="sqlInput" class="modal-textarea" placeholder="-- Paste CREATE TABLE statements here...
-- Supports -- comments, /* block comments */, inline REFERENCES, and FOREIGN KEY (...) REFERENCES ...

CREATE TABLE projects (
  id SERIAL PRIMARY KEY,
  tenant_id INT REFERENCES tenants(id),
  owner_id INT REFERENCES users(id),
  name TEXT NOT NULL
);"></textarea>
        <div id="sqlMsg" class="modal-msg"></div>
      </div>
      <div class="modal-foot">
        <span style="font-size: 11.5px; color: var(--text-dim);">
          Parses <code>CREATE TABLE</code>, inline <code>REFERENCES</code> &amp; <code>FOREIGN KEY</code>
        </span>
        <div class="btn-group">
          <button id="sqlAddBtn" class="btn active">＋ Add to Current Schema</button>
          <button id="sqlReplaceBtn" class="btn primary-sql">↻ Replace Schema</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast Notification -->
  <div id="toast"></div>

  <!-- =====================================================================
       APPLICATION SCRIPT (ZERO DEPENDENCIES)
       ===================================================================== -->
  <script>
  (function () {
    'use strict';

    /* =====================================================================
       1. REALISTIC PRELOADED 22-TABLE MULTI-DATABASE DATASET
       Covers intra-DB FKs, cross-DB FKs, multi-FK pairs, and self-refs
       ===================================================================== */
    /* Injected by `ctxp schema --html`: the project's real extracted schema
       (tables, columns, foreign keys). Falls back to the built-in demo
       dataset when the project had no schema to show. */
    const INITIAL_DATA = __DATA__;

    const DEFAULT_DATA = [
      // --- 1. core_auth.sql (5 tables) ---
      {
        name: 'tenants',
        source: 'core_auth.sql',
        columns: ['id', 'slug', 'name', 'plan_tier', 'is_active', 'created_at'],
        fks: []
      },
      {
        name: 'users',
        source: 'core_auth.sql',
        columns: ['id', 'tenant_id', 'manager_id', 'email', 'full_name', 'status', 'last_login_at'],
        fks: [
          { column: 'tenant_id', ref_table: 'tenants' },
          { column: 'manager_id', ref_table: 'users' }
        ]
      },
      {
        name: 'roles',
        source: 'core_auth.sql',
        columns: ['id', 'code', 'title', 'permissions_json'],
        fks: []
      },
      {
        name: 'user_roles',
        source: 'core_auth.sql',
        columns: ['id', 'user_id', 'role_id', 'assigned_at'],
        fks: [
          { column: 'user_id', ref_table: 'users' },
          { column: 'role_id', ref_table: 'roles' }
        ]
      },
      {
        name: 'audit_logs',
        source: 'core_auth.sql',
        columns: ['id', 'tenant_id', 'actor_id', 'action', 'entity_type', 'entity_id', 'created_at'],
        fks: [
          { column: 'actor_id', ref_table: 'users' },
          { column: 'tenant_id', ref_table: 'tenants' }
        ]
      },

      // --- 2. accounting_ledger.sql (6 tables) ---
      {
        name: 'accounts',
        source: 'accounting_ledger.sql',
        columns: ['id', 'tenant_id', 'parent_id', 'code', 'name', 'account_type', 'currency'],
        fks: [
          { column: 'parent_id', ref_table: 'accounts' },
          { column: 'tenant_id', ref_table: 'tenants' }
        ]
      },
      {
        name: 'fiscal_years',
        source: 'accounting_ledger.sql',
        columns: ['id', 'tenant_id', 'label', 'start_date', 'end_date', 'is_closed'],
        fks: [
          { column: 'tenant_id', ref_table: 'tenants' }
        ]
      },
      {
        name: 'cost_centers',
        source: 'accounting_ledger.sql',
        columns: ['id', 'parent_id', 'code', 'name', 'department'],
        fks: [
          { column: 'parent_id', ref_table: 'cost_centers' }
        ]
      },
      {
        name: 'journal_entries',
        source: 'accounting_ledger.sql',
        columns: ['id', 'fiscal_year_id', 'entry_no', 'entry_date', 'memo', 'created_by', 'approved_by', 'posted_at'],
        fks: [
          { column: 'fiscal_year_id', ref_table: 'fiscal_years' },
          { column: 'created_by', ref_table: 'users' },
          { column: 'approved_by', ref_table: 'users' }
        ]
      },
      {
        name: 'journal_lines',
        source: 'accounting_ledger.sql',
        columns: ['id', 'entry_id', 'account_id', 'cost_center_id', 'debit', 'credit', 'description'],
        fks: [
          { column: 'entry_id', ref_table: 'journal_entries' },
          { column: 'account_id', ref_table: 'accounts' },
          { column: 'cost_center_id', ref_table: 'cost_centers' }
        ]
      },
      {
        name: 'budgets',
        source: 'accounting_ledger.sql',
        columns: ['id', 'fiscal_year_id', 'account_id', 'cost_center_id', 'allocated_amount', 'revised_amount'],
        fks: [
          { column: 'cost_center_id', ref_table: 'cost_centers' },
          { column: 'account_id', ref_table: 'accounts' },
          { column: 'fiscal_year_id', ref_table: 'fiscal_years' }
        ]
      },

      // --- 3. sales_pos.sql (5 tables) ---
      {
        name: 'customers',
        source: 'sales_pos.sql',
        columns: ['id', 'tenant_id', 'receivable_account_id', 'name', 'tax_id', 'phone', 'credit_limit'],
        fks: [
          { column: 'tenant_id', ref_table: 'tenants' },
          { column: 'receivable_account_id', ref_table: 'accounts' }
        ]
      },
      {
        name: 'pos_terminals',
        source: 'sales_pos.sql',
        columns: ['id', 'branch_id', 'terminal_code', 'register_name', 'is_online'],
        fks: [
          { column: 'branch_id', ref_table: 'warehouses' }
        ]
      },
      {
        name: 'invoices',
        source: 'sales_pos.sql',
        columns: ['id', 'invoice_no', 'customer_id', 'terminal_id', 'journal_entry_id', 'cashier_id', 'total_amount', 'issued_at'],
        fks: [
          { column: 'customer_id', ref_table: 'customers' },
          { column: 'terminal_id', ref_table: 'pos_terminals' },
          { column: 'journal_entry_id', ref_table: 'journal_entries' },
          { column: 'cashier_id', ref_table: 'users' }
        ]
      },
      {
        name: 'invoice_items',
        source: 'sales_pos.sql',
        columns: ['id', 'invoice_id', 'product_id', 'qty', 'unit_price', 'discount', 'line_total'],
        fks: [
          { column: 'invoice_id', ref_table: 'invoices' },
          { column: 'product_id', ref_table: 'products' }
        ]
      },
      {
        name: 'payments',
        source: 'sales_pos.sql',
        columns: ['id', 'invoice_id', 'account_id', 'method', 'amount', 'paid_at'],
        fks: [
          { column: 'invoice_id', ref_table: 'invoices' },
          { column: 'account_id', ref_table: 'accounts' }
        ]
      },

      // --- 4. inventory.sqlite3 (6 tables) ---
      {
        name: 'warehouses',
        source: 'inventory.sqlite3',
        columns: ['id', 'code', 'name', 'city', 'manager_id', 'capacity_sqm'],
        fks: [
          { column: 'manager_id', ref_table: 'users' }
        ]
      },
      {
        name: 'categories',
        source: 'inventory.sqlite3',
        columns: ['id', 'parent_id', 'name', 'slug'],
        fks: [
          { column: 'parent_id', ref_table: 'categories' }
        ]
      },
      {
        name: 'products',
        source: 'inventory.sqlite3',
        columns: ['id', 'sku', 'name', 'category_id', 'expense_account_id', 'unit_cost', 'sale_price'],
        fks: [
          { column: 'category_id', ref_table: 'categories' },
          { column: 'expense_account_id', ref_table: 'accounts' }
        ]
      },
      {
        name: 'stock_levels',
        source: 'inventory.sqlite3',
        columns: ['id', 'warehouse_id', 'product_id', 'qty_on_hand', 'reorder_point', 'updated_at'],
        fks: [
          { column: 'warehouse_id', ref_table: 'warehouses' },
          { column: 'product_id', ref_table: 'products' }
        ]
      },
      {
        name: 'stock_moves',
        source: 'inventory.sqlite3',
        columns: ['id', 'product_id', 'from_warehouse_id', 'to_warehouse_id', 'created_by', 'quantity', 'moved_at'],
        fks: [
          { column: 'product_id', ref_table: 'products' },
          { column: 'from_warehouse_id', ref_table: 'warehouses' },
          { column: 'to_warehouse_id', ref_table: 'warehouses' },
          { column: 'created_by', ref_table: 'users' }
        ]
      },
      {
        name: 'suppliers',
        source: 'inventory.sqlite3',
        columns: ['id', 'name', 'contact_email', 'payable_account_id', 'lead_time_days'],
        fks: [
          { column: 'payable_account_id', ref_table: 'accounts' }
        ]
      }
    ];

    /* =====================================================================
       2. GLOBAL STATE & PALETTE
       ===================================================================== */
    const PALETTE = [
      '#3b82f6', // Royal Blue
      '#22c55e', // Emerald
      '#a855f7', // Violet
      '#06b6d4', // Cyan
      '#f97316', // Orange
      '#ec4899', // Pink
      '#eab308', // Yellow
      '#14b8a6'  // Teal
    ];

    const FRONT_Z = 0.22;
    const SPHERE_RADIUS = 285;
    const NS = 'http://www.w3.org/2000/svg';

    let data = (INITIAL_DATA && INITIAL_DATA.length) ? cloneData(INITIAL_DATA) : cloneData(DEFAULT_DATA);
    let sources = [];
    let sColor = {};
    let nodes = [];
    let byKey = {};
    let byKeyLower = {};
    let byNameLower = {};
    let links = [];
    let pairs = [];
    let hasCross = false;

    // Interactive View State
    let mode = 'graph'; // 'graph' | 'cards'
    let selectedKey = null;
    let soloSource = null;
    let disabledSources = new Set();
    let showAllLabels = false;
    let showEdges = true;
    let autoSpin = true;
    let tool = 'rotate'; // 'rotate' | 'pan'
    let searchQuery = '';

    // keys that participate in the current focus view (hub + neighbors)
    let focusSet = new Set();

    // Camera & Smooth Animation State
    let rot = { rx: -0.28, ry: 0.55 };
    let zoom = 1;
    let pan = { x: 0, y: 0 };

    // Transition animation envelope (~420ms cubic ease-out)
    let anim = null;
    let lastFrameTime = performance.now();

    // DOM References
    const svg = document.getElementById('graph');
    const layerBg = document.getElementById('layer-bg');
    const layerEdges = document.getElementById('layer-edges');
    const layerNodes = document.getElementById('layer-nodes');
    const layerElabels = document.getElementById('layer-elabels');

    const sideEl = document.getElementById('side');
    const sideListEl = document.getElementById('side-list');
    const sideCntEl = document.getElementById('side-cnt');
    const foldBtn = document.getElementById('foldBtn');
    const detailEl = document.getElementById('detail');
    const cardsEl = document.getElementById('cards');
    const graphWrapEl = document.getElementById('graph-wrap');
    const focusHudEl = document.getElementById('focus-hud');
    const focusHudTxt = document.getElementById('focus-hud-txt');
    const searchInput = document.getElementById('q');
    const searchClear = document.getElementById('qClear');

    let sideItem = {};
    let bgStars = [];
    let toastTimer = null;
    let lastZOrderSig = '';

    /* =====================================================================
       3. UTILITY HELPERS
       ===================================================================== */
    function cloneData(arr) {
      return arr.map(t => ({
        name: t.name,
        source: t.source,
        columns: (t.columns || []).slice(),
        fks: (t.fks || []).map(f => ({ column: f.column, ref_table: f.ref_table }))
      }));
    }

    function esc(s) {
      return String(s).replace(/[&<>"']/g, m => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[m]));
    }

    function srcLabel(s) {
      const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
      return i >= 0 ? s.slice(i + 1) : s;
    }

    function keyOf(t) {
      return t.source + '|' + t.name;
    }

    function svgEl(tag, attrs) {
      const e = document.createElementNS(NS, tag);
      if (attrs) {
        for (const k in attrs) e.setAttribute(k, attrs[k]);
      }
      return e;
    }

    function toast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        t.classList.remove('show');
        toastTimer = null;
      }, 2100);
    }

    function easeOutCubic(t) {
      return 1 - Math.pow(1 - t, 3);
    }

    function matchTable(t, q) {
      if (!q) return true;
      if (t.name.toLowerCase().indexOf(q) >= 0) return true;
      if (t.source.toLowerCase().indexOf(q) >= 0) return true;
      return t.columns.some(c => c.toLowerCase().indexOf(q) >= 0);
    }

    function isSourceVisible(src) {
      return !disabledSources.has(src);
    }

    /* =====================================================================
       4. GRAPH DATA MODEL & REUSABLE SVG SCENE CONSTRUCTION
       Builds DOM elements once per dataset load for silky-smooth 60fps
       ===================================================================== */
    function rebuildDataset() {
      sources = [];
      data.forEach(t => {
        if (sources.indexOf(t.source) < 0) sources.push(t.source);
      });

      sColor = {};
      sources.forEach((s, i) => {
        sColor[s] = PALETTE[i % PALETTE.length];
      });

      // Huge corpora (thousands of tables): continuous auto-spin is
      // expensive per frame, so start paused; the Spin button re-enables it.
      autoSpin = data.length <= 1500;
      const spinBtn = document.getElementById('vSpin');
      spinBtn.classList.toggle('active', autoSpin);
      spinBtn.classList.toggle('off', !autoSpin);
      spinBtn.title = autoSpin ? 'Toggle gentle auto-rotation'
        : 'Auto-rotation paused for a large schema (' + data.length + ' tables); click to enable';

      // Distribute tables cleanly across a Fibonacci Sphere
      const N = Math.max(1, data.length);
      nodes = data.map((t, i) => {
        const phi = Math.acos(1 - 2 * (i + 0.5) / N);
        const th = Math.PI * (1 + Math.sqrt(5)) * i;
        const sx = Math.sin(phi) * Math.cos(th);
        const sy = Math.cos(phi);
        const sz = Math.sin(phi) * Math.sin(th);
        return {
          name: t.name,
          source: t.source,
          columns: t.columns,
          fks: t.fks,
          key: keyOf(t),
          sph: { x: sx, y: sy, z: sz },
          cur: { x: sx, y: sy, z: sz },
          start: { x: sx, y: sy, z: sz },
          tgt: { x: sx, y: sy, z: sz },
          alpha: 1,
          startAlpha: 1,
          tgtAlpha: 1,
          hovered: false,
          el: null,
          haloEl: null,
          coreEl: null,
          shineEl: null,
          labelEl: null
        };
      });

      // Case-insensitive lookup indices
      byKey = {};
      byKeyLower = {};
      byNameLower = {};
      nodes.forEach(n => {
        byKey[n.key] = n;
        byKeyLower[(n.source + '|' + n.name).toLowerCase()] = n;
        const ln = n.name.toLowerCase();
        if (!(ln in byNameLower)) byNameLower[ln] = n;
      });

      // Resolve all Foreign Keys (same-DB first, then cross-DB)
      links = [];
      nodes.forEach(n => {
        n.fks.forEach(fk => {
          const to = byKeyLower[(n.source + '|' + fk.ref_table).toLowerCase()]
            || byNameLower[fk.ref_table.toLowerCase()];
          if (to) {
            links.push({
              s: n,
              t: to,
              fk: fk,
              cross: n.source !== to.source,
              self: n.key === to.key
            });
          }
        });
      });

      hasCross = links.some(l => l.cross);

      // Deduplicate edges per pair of tables (combining multi-FK labels)
      const pairMap = {};
      pairs = [];
      links.forEach(l => {
        const k = l.s.key < l.t.key ? (l.s.key + '||' + l.t.key) : (l.t.key + '||' + l.s.key);
        let pr = pairMap[k];
        if (!pr) {
          pr = {
            id: k,
            s: l.s.key <= l.t.key ? l.s : l.t,
            t: l.s.key <= l.t.key ? l.t : l.s,
            out: [], // FKs from pr.s -> pr.t
            inn: [], // FKs from pr.t -> pr.s
            cross: false,
            self: l.self,
            el: null,
            labelOutEl: null,
            labelInnEl: null
          };
          pairMap[k] = pr;
          pairs.push(pr);
        }
        if (l.cross) pr.cross = true;
        if (l.s.key === pr.s.key) {
          if (pr.out.indexOf(l.fk.column) < 0) pr.out.push(l.fk.column);
        } else {
          if (pr.inn.indexOf(l.fk.column) < 0) pr.inn.push(l.fk.column);
        }
      });

      buildSvgScene();
      buildLegend();
      buildSide(searchQuery);
      buildCards(searchQuery);
      updateViewBox();
      renderFrame();
    }

    function buildSvgScene() {
      // Clear layers once when schema changes
      layerBg.innerHTML = '';
      layerEdges.innerHTML = '';
      layerNodes.innerHTML = '';
      layerElabels.innerHTML = '';
      lastZOrderSig = '';

      // 1. Subtle cosmic backdrop: orbital rings + gentle starfield
      [210, 315, 430].forEach((r, idx) => {
        const ring = svgEl('circle', {
          cx: 0, cy: 0, r: r,
          class: 'bg-ring',
          'stroke-opacity': idx === 1 ? '0.13' : '0.06'
        });
        layerBg.appendChild(ring);
      });

      bgStars = [];
      // Deterministic pseudo-random starfield for crisp ambiance
      let seed = 42;
      const rand = () => {
        seed = (seed * 16807) % 2147483647;
        return (seed - 1) / 2147483646;
      };
      for (let i = 0; i < 65; i++) {
        const x = (rand() - 0.5) * 1400;
        const y = (rand() - 0.5) * 900;
        const r = 0.7 + rand() * 1.3;
        const op = 0.08 + rand() * 0.25;
        const st = svgEl('circle', { cx: x, cy: y, r: r, class: 'bg-star', opacity: op.toFixed(2) });
        layerBg.appendChild(st);
        bgStars.push(st);
      }

      // 2. Pre-create reusable edge elements (self-ref uses <path>, normal uses <line>)
      // Huge corpora: edge label texts only ever appear around a focused
      // table, so skip pre-creating one (or two) per pair and make them
      // lazily in ensurePairLabels(); keeps ~10k+ unused <text> nodes away.
      const bigSchema = pairs.length > 2500;
      pairs.forEach(pr => {
        const totalFks = pr.out.length + pr.inn.length;
        if (pr.self) {
          const path = svgEl('path', {
            class: 'edge self-edge',
            'stroke-width': 1.8
          });
          layerEdges.appendChild(path);
          pr.el = path;
        } else {
          const line = svgEl('line', {
            class: pr.cross ? 'edge cross' : 'edge',
            'stroke-width': totalFks > 1 ? 2.4 : 1.25
          });
          layerEdges.appendChild(line);
          pr.el = line;
        }

        if (!bigSchema) ensurePairLabels(pr);
      });

      // 3. Pre-create reusable node groups
      nodes.forEach(n => {
        const g = svgEl('g', { class: 'node' });
        g.style.setProperty('--sc', sColor[n.source] || '#3b82f6');

        const halo = svgEl('circle', { class: 'halo', cx: 0, cy: 0, r: 22 });
        const core = svgEl('circle', { class: 'core', cx: 0, cy: 0, r: 14 });
        const shine = svgEl('circle', { class: 'shine', cx: -4, cy: -4, r: 4.5 });
        const lbl = svgEl('text', { class: 'nlabel', x: 0, y: -22 });
        lbl.textContent = n.name;

        g.appendChild(halo);
        g.appendChild(core);
        g.appendChild(shine);
        g.appendChild(lbl);

        // Node hover & click handlers
        g.addEventListener('mouseenter', () => {
          n.hovered = true;
          renderFrame();
        });
        g.addEventListener('mouseleave', () => {
          n.hovered = false;
          renderFrame();
        });
        g.addEventListener('click', (e) => {
          e.stopPropagation();
          if (dragMoved > 5) return;
          selectTable(n.key);
        });

        layerNodes.appendChild(g);
        n.el = g;
        n.haloEl = halo;
        n.coreEl = core;
        n.shineEl = shine;
        n.labelEl = lbl;
      });
    }

    /* =====================================================================
       5. 3D PROJECTION & SUNFLOWER FOCUS TRANSITION ENGINE
       ===================================================================== */
    function project3D(x0, y0, z0) {
      const cosY = Math.cos(rot.ry), sinY = Math.sin(rot.ry);
      const cosX = Math.cos(rot.rx), sinX = Math.sin(rot.rx);

      const x1 = x0 * cosY + z0 * sinY;
      const z1 = -x0 * sinY + z0 * cosY;
      const y2 = y0 * cosX - z1 * sinX;
      const z2 = y0 * sinX + z1 * cosX;

      const f = 2.2 / (2.2 - z2);
      return {
        x: x1 * SPHERE_RADIUS * f,
        y: y2 * SPHERE_RADIUS * f,
        z: z2,
        f: f
      };
    }

    function updateViewBox() {
      const W = svg.clientWidth || 800;
      const H = svg.clientHeight || 520;
      svg.setAttribute(
        'viewBox',
        (-W / 2 / zoom + pan.x).toFixed(2) + ' ' +
        (-H / 2 / zoom + pan.y).toFixed(2) + ' ' +
        (W / zoom).toFixed(2) + ' ' +
        (H / zoom).toFixed(2)
      );
    }

    function startTransition(targetRot, targetZoom, targetPan, durationMs) {
      nodes.forEach(n => {
        n.start.x = n.cur.x;
        n.start.y = n.cur.y;
        n.start.z = n.cur.z;
        n.startAlpha = n.alpha;
      });
      anim = {
        startTime: performance.now(),
        duration: durationMs || 420,
        startRot: { rx: rot.rx, ry: rot.ry },
        targetRot: { rx: targetRot.rx, ry: targetRot.ry },
        startZoom: zoom,
        targetZoom: targetZoom,
        startPan: { x: pan.x, y: pan.y },
        targetPan: { x: targetPan.x, y: targetPan.y }
      };
    }

    function computeFocusTargets(centerKey) {
      const c = byKey[centerKey];
      if (!c) return;

      // Gather directly connected neighbors (both outgoing FKs and incoming refs)
      const nearSet = new Set([centerKey]);
      links.forEach(l => {
        if (!isSourceVisible(l.s.source) || !isSourceVisible(l.t.source)) return;
        if (l.s.key === centerKey) nearSet.add(l.t.key);
        if (l.t.key === centerKey) nearSet.add(l.s.key);
      });
      focusSet = new Set(nearSet);

      const neighbors = nodes
        .filter(n => n.key !== centerKey && nearSet.has(n.key) && isSourceVisible(n.source))
        .sort((a, b) => a.name.localeCompare(b.name));

      const N = neighbors.length;
      const GA = Math.PI * (1 + Math.sqrt(5));
      const FZ = 0.35;

      // Hub sits cleanly at the center facing the camera
      c.tgt.x = 0;
      c.tgt.y = 0;
      c.tgt.z = FZ;
      c.tgtAlpha = 1;

      // Neighbors arrange on a clean 2D Sunflower Focus Disc
      neighbors.forEach((n, i) => {
        let r, angle;
        if (N <= 7) {
          // Evenly spaced ring for small clusters — super clean and symmetrical
          r = N === 1 ? 0.65 : 0.72;
          angle = -Math.PI / 2 + (2 * Math.PI * i) / N;
        } else {
          // Golden-angle sunflower disc for larger clusters
          r = 0.36 + 0.72 * Math.sqrt((i + 0.5) / N);
          angle = -Math.PI / 2 + GA * i;
        }
        n.tgt.x = r * Math.cos(angle);
        n.tgt.y = r * Math.sin(angle);
        n.tgt.z = FZ;
        n.tgtAlpha = 1;
      });

      // Unrelated nodes smoothly fade out
      nodes.forEach(n => {
        if (!nearSet.has(n.key) || !isSourceVisible(n.source)) {
          n.tgt.x = n.sph.x * 1.15;
          n.tgt.y = n.sph.y * 1.15;
          n.tgt.z = n.sph.z * 1.15;
          n.tgtAlpha = 0;
        }
      });

      // Compute ideal zoom to fit the sunflower cluster comfortably
      const W = svg.clientWidth || 800;
      const H = svg.clientHeight || 520;
      const maxExtent = SPHERE_RADIUS * (2.2 / (2.2 - FZ)) * (N <= 7 ? 1.05 : 1.32);
      const fitZoom = Math.max(0.45, Math.min(1.65, (0.86 * Math.min(W, H)) / (2 * maxExtent)));

      // Smoothly unwind rot.ry toward nearest multiple of 2*PI so it doesn't spin wildly
      const nearestZeroY = Math.round(rot.ry / (Math.PI * 2)) * Math.PI * 2;
      startTransition({ rx: 0, ry: nearestZeroY }, fitZoom, { x: 0, y: 0 }, 420);
    }

    function computeSphereTargets() {
      nodes.forEach(n => {
        n.tgt.x = n.sph.x;
        n.tgt.y = n.sph.y;
        n.tgt.z = n.sph.z;
        n.tgtAlpha = isSourceVisible(n.source) ? 1 : 0;
      });
      const W = svg.clientWidth || 800;
      const H = svg.clientHeight || 520;
      const baseZoom = Math.max(0.55, Math.min(1.15, Math.min(W, H) / 760));
      startTransition({ rx: -0.28, ry: 0.55 }, baseZoom, { x: 0, y: 0 }, 440);
    }

    /* =====================================================================
       6. HIGH-PERFORMANCE RENDER LOOP (60FPS IN-PLACE DOM UPDATES)
       ===================================================================== */
    // Lazy label creation: the (at most 2) label texts for one edge pair.
    // Pre-created up-front for small schemas; created on first hot use on
    // huge corpora to keep the initial DOM small and the boot instant.
    function ensurePairLabels(pr) {
      if (pr.labelOutEl) return;
      const lbOut = svgEl('text', {
        class: pr.cross ? 'elabel cross' : 'elabel',
        display: 'none'
      });
      layerElabels.appendChild(lbOut);
      pr.labelOutEl = lbOut;

      if (!pr.self && pr.out.length && pr.inn.length) {
        const lbInn = svgEl('text', {
          class: pr.cross ? 'elabel cross' : 'elabel',
          display: 'none'
        });
        layerElabels.appendChild(lbInn);
        pr.labelInnEl = lbInn;
      }
    }

    function renderFrame() {
      const projected = new Array(nodes.length);
      const pMap = {};
      const hasSearch = searchQuery.length > 0;

      // Project all nodes
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const p = project3D(n.cur.x, n.cur.y, n.cur.z);
        projected[i] = { n: n, p: p };
        pMap[n.key] = p;
      }

      // 1. Update Edges in place
      for (let i = 0; i < pairs.length; i++) {
        const pr = pairs[i];
        const visS = isSourceVisible(pr.s.source) && pr.s.alpha > 0.04;
        const visT = isSourceVisible(pr.t.source) && pr.t.alpha > 0.04;
        const edgeAlpha = Math.min(pr.s.alpha, pr.t.alpha);

        const isHot = Boolean(selectedKey && (pr.s.key === selectedKey || pr.t.key === selectedKey));
        // Focus view: hub edges stay hot, edges BETWEEN two neighbors show
        // dimmed (the local web of relations around the hub), rest hide
        const dimNeighbor = Boolean(selectedKey && !isHot && !pr.self
          && focusSet.has(pr.s.key) && focusSet.has(pr.t.key));

        if (!visS || !visT || (selectedKey && !isHot && !dimNeighbor)) {
          pr.el.setAttribute('display', 'none');
          if (pr.labelOutEl) pr.labelOutEl.setAttribute('display', 'none');
          if (pr.labelInnEl) pr.labelInnEl.setAttribute('display', 'none');
          continue;
        }

        pr.el.removeAttribute('display');
        pr.el.classList.toggle('hot', isHot);
        pr.el.classList.toggle('dimrel', dimNeighbor);

        const a = pMap[pr.s.key];
        const b = pMap[pr.t.key];

        if (pr.self) {
          // Graceful self-referencing loop arcing above-right of the node
          const r = 14.5 * Math.max(0.62, a.f);
          const loopR = r * 1.9;
          const sx = a.x + r * 0.68;
          const sy = a.y - r * 0.68;
          const ex = a.x + r * 0.95;
          const ey = a.y + r * 0.35;
          const cx1 = a.x + loopR * 1.2;
          const cy1 = a.y - loopR * 1.5;
          const cx2 = a.x + loopR * 1.9;
          const cy2 = a.y + loopR * 0.4;
          pr.el.setAttribute('d', `M ${sx.toFixed(1)} ${sy.toFixed(1)} C ${cx1.toFixed(1)} ${cy1.toFixed(1)}, ${cx2.toFixed(1)} ${cy2.toFixed(1)}, ${ex.toFixed(1)} ${ey.toFixed(1)}`);
          pr.el.style.stroke = isHot ? '#22d3ee' : '#38bdf8';
          pr.el.style.opacity = ((isHot ? 0.95 : 0.25 + 0.35 * ((a.z + 1) / 2)) * edgeAlpha).toFixed(3);
          if (isHot) {
            pr.el.setAttribute('marker-end', 'url(#fkSelf)');
            ensurePairLabels(pr);
            pr.labelOutEl.removeAttribute('display');
            pr.labelOutEl.setAttribute('x', (a.x + loopR * 1.45).toFixed(1));
            pr.labelOutEl.setAttribute('y', (a.y - loopR * 0.65).toFixed(1));
            pr.labelOutEl.style.fill = '#67e8f9';
            pr.labelOutEl.textContent = '↻ ' + pr.out.join(' · ');
            pr.labelOutEl.style.opacity = edgeAlpha.toFixed(3);
          } else {
            pr.el.removeAttribute('marker-end');
            if (pr.labelOutEl) pr.labelOutEl.setAttribute('display', 'none');
          }
        } else {
          // Shorten line endpoints slightly when hot so arrowheads touch circle edge cleanly
          let x1 = a.x, y1 = a.y, x2 = b.x, y2 = b.y;
          if (isHot) {
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const dist = Math.hypot(dx, dy) || 1;
            const rA = 16.5 * Math.max(0.65, a.f) + 4;
            const rB = 16.5 * Math.max(0.65, b.f) + 4;
            if (dist > rA + rB + 8) {
              x1 = a.x + (dx / dist) * rA;
              y1 = a.y + (dy / dist) * rA;
              x2 = b.x - (dx / dist) * rB;
              y2 = b.y - (dy / dist) * rB;
            }
          }

          pr.el.setAttribute('x1', x1.toFixed(1));
          pr.el.setAttribute('y1', y1.toFixed(1));
          pr.el.setAttribute('x2', x2.toFixed(1));
          pr.el.setAttribute('y2', y2.toFixed(1));

          const depthFactor = ((a.z + b.z) / 2 + 1) / 2;
          let op = isHot ? 0.95 : (0.07 + 0.42 * Math.max(0, depthFactor));
          // neighbor-to-neighbor relations read at a whisper: visible, not loud
          if (dimNeighbor) op = 0.12 + 0.14 * depthFactor;
          if (pr.cross && !isHot && !dimNeighbor) op *= 0.85;
          if (hasSearch && !isHot) op *= 0.25;
          pr.el.style.opacity = (op * edgeAlpha).toFixed(3);

          const totalFks = pr.out.length + pr.inn.length;
          pr.el.setAttribute('stroke-width', isHot ? (totalFks > 1 ? 3.6 : 2.8) : (totalFks > 1 ? 2.2 : 1.15));

          if (isHot) {
            const mEnd = pr.cross ? 'url(#fkACross)' : 'url(#fkA)';
            const mStart = pr.cross ? 'url(#fkRCross)' : 'url(#fkR)';
            if (pr.out.length) pr.el.setAttribute('marker-end', mEnd);
            else pr.el.removeAttribute('marker-end');
            if (pr.inn.length) pr.el.setAttribute('marker-start', mStart);
            else pr.el.removeAttribute('marker-start');

            // Position edge labels cleanly toward the outer node (avoiding hub clutter)
            const hubIsT = (selectedKey === pr.t.key);
            const fromP = hubIsT ? b : a;
            const toP = hubIsT ? a : b;

            ensurePairLabels(pr);
            if (pr.out.length && pr.inn.length && pr.labelInnEl) {
              placeEdgeLabel(pr.labelOutEl, fromP, toP, 0.56, pr.out.join(' · '), edgeAlpha);
              placeEdgeLabel(pr.labelInnEl, fromP, toP, 0.74, pr.inn.join(' · '), edgeAlpha);
            } else {
              const cols = pr.out.length ? pr.out : pr.inn;
              placeEdgeLabel(pr.labelOutEl, fromP, toP, 0.64, cols.join(' · '), edgeAlpha);
              if (pr.labelInnEl) pr.labelInnEl.setAttribute('display', 'none');
            }
          } else {
            pr.el.removeAttribute('marker-end');
            pr.el.removeAttribute('marker-start');
            if (pr.labelOutEl) pr.labelOutEl.setAttribute('display', 'none');
            if (pr.labelInnEl) pr.labelInnEl.setAttribute('display', 'none');
          }
        }
      }

      // 2. Depth-sort nodes (back to front) & update transforms in place
      projected.sort((u, v) => u.p.z - v.p.z);

      let zSig = '';
      for (let i = 0; i < projected.length; i++) {
        const { n, p } = projected[i];
        zSig += n.key + ';';

        const visibleNode = isSourceVisible(n.source) && n.alpha > 0.02;
        if (!visibleNode) {
          n.el.setAttribute('display', 'none');
          continue;
        }
        n.el.removeAttribute('display');

        const isSel = (selectedKey === n.key);
        const isMatch = hasSearch && matchTable(n, searchQuery);
        const radius = (isSel ? 17.5 : 14.5) * Math.max(0.58, p.f);

        n.el.classList.toggle('sel', isSel);
        n.el.classList.toggle('match', isMatch);

        n.coreEl.setAttribute('cx', p.x.toFixed(1));
        n.coreEl.setAttribute('cy', p.y.toFixed(1));
        n.coreEl.setAttribute('r', radius.toFixed(1));

        n.haloEl.setAttribute('cx', p.x.toFixed(1));
        n.haloEl.setAttribute('cy', p.y.toFixed(1));
        n.haloEl.setAttribute('r', (radius + (isSel ? 9 : 6)).toFixed(1));

        n.shineEl.setAttribute('cx', (p.x - radius * 0.3).toFixed(1));
        n.shineEl.setAttribute('cy', (p.y - radius * 0.32).toFixed(1));
        n.shineEl.setAttribute('r', (radius * 0.32).toFixed(1));

        n.labelEl.setAttribute('x', p.x.toFixed(1));
        n.labelEl.setAttribute('y', (p.y - radius - 8).toFixed(1));

        // Opacity reflects 3D depth, search filter, and focus transition
        let baseOp = selectedKey ? 1 : (0.34 + 0.66 * Math.max(0, (p.z + 1) / 2));
        if (hasSearch && !isMatch && !isSel) baseOp *= 0.18;
        n.el.style.opacity = (baseOp * n.alpha).toFixed(3);

        // Label visibility: front hemisphere, or All, or hovered, or selected, or search match
        const showLbl = showAllLabels || Boolean(selectedKey) || n.hovered || isSel || isMatch || p.z > FRONT_Z;
        n.labelEl.style.opacity = showLbl ? '1' : '0';
      }

      // Only touch DOM child ordering when Z-order actually changed
      if (zSig !== lastZOrderSig) {
        lastZOrderSig = zSig;
        for (let i = 0; i < projected.length; i++) {
          layerNodes.appendChild(projected[i].n.el);
        }
      }
    }

    function placeEdgeLabel(el, fromP, toP, frac, text, alpha) {
      el.removeAttribute('display');
      el.setAttribute('x', (fromP.x + (toP.x - fromP.x) * frac).toFixed(1));
      el.setAttribute('y', (fromP.y + (toP.y - fromP.y) * frac - 7).toFixed(1));
      el.textContent = text;
      el.style.opacity = alpha.toFixed(3);
    }

    // Master 60fps animation loop (handles lerp transitions & gentle auto-spin)
    function tick(now) {
      const dt = Math.min(50, now - lastFrameTime);
      lastFrameTime = now;
      let dirty = false;

      if (anim) {
        const t = Math.min(1, (now - anim.startTime) / anim.duration);
        const e = easeOutCubic(t);

        for (let i = 0; i < nodes.length; i++) {
          const n = nodes[i];
          n.cur.x = n.start.x + (n.tgt.x - n.start.x) * e;
          n.cur.y = n.start.y + (n.tgt.y - n.start.y) * e;
          n.cur.z = n.start.z + (n.tgt.z - n.start.z) * e;
          n.alpha = n.startAlpha + (n.tgtAlpha - n.startAlpha) * e;
        }

        rot.rx = anim.startRot.rx + (anim.targetRot.rx - anim.startRot.rx) * e;
        rot.ry = anim.startRot.ry + (anim.targetRot.ry - anim.startRot.ry) * e;
        zoom = anim.startZoom + (anim.targetZoom - anim.startZoom) * e;
        pan.x = anim.startPan.x + (anim.targetPan.x - anim.startPan.x) * e;
        pan.y = anim.startPan.y + (anim.targetPan.y - anim.startPan.y) * e;

        updateViewBox();
        dirty = true;
        if (t >= 1) anim = null;
      } else if (autoSpin && !selectedKey && !dragState && mode === 'graph') {
        rot.ry += dt * 0.00028;
        dirty = true;
      }

      if (dirty) renderFrame();
      requestAnimationFrame(tick);
    }

    /* =====================================================================
       7. SELECTION, SUNFLOWER FOCUS & INSPECTOR DRAWER
       ===================================================================== */
    function selectTable(key) {
      const t = byKey[key];
      if (!t) return;

      // Ensure source is visible if user jumped to a hidden DB
      if (disabledSources.has(t.source)) {
        disabledSources.delete(t.source);
        soloSource = null;
        buildLegend();
        buildSide(searchQuery);
        buildCards(searchQuery);
      }

      // If user clicked an ER card, switch to 3D Network Focus view smoothly
      if (mode === 'cards') {
        setMode('graph', true);
      }

      selectedKey = key;
      toast('Focused: ' + t.name + ' (' + srcLabel(t.source) + ')');

      // Highlight in sidebar & cards
      document.querySelectorAll('.titem').forEach(el => el.classList.toggle('sel', el.dataset.key === key));
      document.querySelectorAll('.card').forEach(el => el.classList.toggle('sel', el.dataset.key === key));

      const sit = sideItem[key];
      if (sit) {
        sit.det.open = true;
        if (sit.el.scrollIntoView) sit.el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }

      // Populate Inspector Drawer
      renderInspector(t);
      detailEl.classList.add('open');

      // Update Focus HUD pill
      focusHudTxt.textContent = 'Focus: ' + t.name;
      focusHudEl.classList.add('visible');

      // Wait one frame for drawer resize width if just opened, then animate focus layout
      requestAnimationFrame(() => {
        updateViewBox();
        computeFocusTargets(key);
      });
    }

    function renderInspector(t) {
      const color = sColor[t.source] || '#3b82f6';
      document.getElementById('tname').textContent = t.name;

      const srcc = document.getElementById('tsrcc');
      srcc.style.borderColor = color;
      srcc.style.color = color;
      srcc.querySelector('.sdot').style.background = color;
      document.getElementById('tsrcc-txt').textContent = t.source;

      // Gather incoming references
      const incoming = links.filter(l => l.t.key === t.key);

      // Stats chips
      const statsEl = document.getElementById('tstats');
      statsEl.innerHTML =
        `<span class="chip"><b>${t.columns.length}</b> columns</span>` +
        `<span class="chip"><b>${t.fks.length}</b> outgoing FKs</span>` +
        `<span class="chip"><b>${incoming.length}</b> referenced by</span>`;

      document.getElementById('col-sec-cnt').textContent = t.columns.length + ' cols';
      document.getElementById('inc-sec-cnt').textContent = incoming.length + ' refs';

      // Build Columns Table
      const cb = document.getElementById('cbody');
      cb.innerHTML = '';
      t.columns.forEach((col, idx) => {
        const fk = t.fks.find(f => f.column === col);
        // Heuristic PK badge: first column that looks like a plain id.
        // A name ending in _id that IS a foreign key (parent_id etc.) is no PK.
        const isPk = (idx === 0 && !fk && /^(\w+_)?id$/i.test(col));
        const tr = document.createElement('tr');

        let badgeHtml = '';
        if (isPk) badgeHtml += '<span class="badge pk">PK</span>';
        if (fk) badgeHtml += '<span class="badge fk">FK</span>';

        let relHtml = '<span style="color:var(--text-dim)">—</span>';
        if (fk) {
          const tgt = byKeyLower[(t.source + '|' + fk.ref_table).toLowerCase()]
            || byNameLower[fk.ref_table.toLowerCase()];
          if (tgt) {
            const isSelf = tgt.key === t.key;
            const isCross = tgt.source !== t.source;
            relHtml = `<a class="jump" data-k="${esc(tgt.key)}">→ ${esc(tgt.name)}</a>`;
            if (isSelf) relHtml += '<span class="badge self">self</span>';
            else if (isCross) relHtml += `<span class="badge cross" title="Cross-DB: ${esc(tgt.source)}">cross-db</span>`;
          } else {
            relHtml = `<span style="color:var(--amber)">→ ${esc(fk.ref_table)}</span>`;
          }
        }

        tr.innerHTML =
          `<td><div class="col-cell">${badgeHtml}<span>${esc(col)}</span></div></td>` +
          `<td>${relHtml}</td>`;
        cb.appendChild(tr);
      });

      cb.querySelectorAll('.jump').forEach(a => {
        a.addEventListener('click', () => selectTable(a.dataset.k));
      });

      // Build Referenced By (Incoming) Section
      const refBody = document.getElementById('refbody');
      refBody.innerHTML = '';
      if (!incoming.length) {
        refBody.innerHTML = '<div class="empty-refs">No other tables reference this table.</div>';
      } else {
        incoming.forEach(l => {
          const div = document.createElement('div');
          div.className = 'ref-item';
          const srcClr = sColor[l.s.source] || '#3b82f6';
          const crossTag = l.cross ? '<span class="badge cross">cross</span>' : (l.self ? '<span class="badge self">self</span>' : '');
          div.innerHTML =
            `<div class="ref-left">` +
              `<span class="dot" style="background:${srcClr}"></span>` +
              `<strong style="color:#e2e8f0">${esc(l.s.name)}</strong>` +
              crossTag +
            `</div>` +
            `<span class="ref-col">.${esc(l.fk.column)}</span>`;
          div.addEventListener('click', () => selectTable(l.s.key));
          refBody.appendChild(div);
        });
      }
    }

    function resetToSphere(resetFilters) {
      selectedKey = null;
      focusSet = new Set();
      detailEl.classList.remove('open');
      focusHudEl.classList.remove('visible');
      document.querySelectorAll('.titem.sel, .card.sel').forEach(el => el.classList.remove('sel'));

      if (resetFilters) {
        disabledSources = new Set();
        soloSource = null;
        searchQuery = '';
        searchInput.value = '';
        searchClear.classList.remove('visible');
        buildLegend();
        buildSide('');
        buildCards('');
      }

      requestAnimationFrame(() => {
        updateViewBox();
        computeSphereTargets();
      });
    }

    /* =====================================================================
       8. SIDEBAR, LEGEND & ER CARDS BUILDERS
       ===================================================================== */
    function buildLegend() {
      const lg = document.getElementById('legend');
      lg.innerHTML = '';

      sources.forEach(s => {
        const count = data.filter(t => t.source === s).length;
        const d = document.createElement('div');
        d.className = 'lg';
        if (disabledSources.has(s)) d.classList.add('off');
        if (soloSource === s) d.classList.add('solo');

        d.innerHTML =
          `<span class="dot" style="background:${sColor[s]};color:${sColor[s]}"></span>` +
          `<span>${esc(srcLabel(s))}</span>` +
          `<span class="n">${count}</span>`;
        d.title = `Source: ${s} · Click: toggle on/off · Double-click: SOLO mode`;

        // Distinguish click vs double-click cleanly
        let clickTimer = null;
        d.addEventListener('click', () => {
          if (clickTimer) {
            clearTimeout(clickTimer);
            clickTimer = null;
            return;
          }
          clickTimer = setTimeout(() => {
            clickTimer = null;
            if (soloSource && soloSource !== s) {
              soloSource = null;
              disabledSources = new Set();
              toast('Showing all databases');
            } else {
              if (disabledSources.has(s)) disabledSources.delete(s);
              else disabledSources.add(s);
              toast((disabledSources.has(s) ? 'Hidden: ' : 'Visible: ') + srcLabel(s));
            }
            applySourceFilterUpdate();
          }, 190);
        });

        d.addEventListener('dblclick', (e) => {
          e.preventDefault();
          if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
          if (soloSource === s) {
            soloSource = null;
            disabledSources = new Set();
            toast('Showing all databases');
          } else {
            soloSource = s;
            disabledSources = new Set(sources.filter(x => x !== s));
            toast('SOLO database: ' + srcLabel(s));
          }
          applySourceFilterUpdate();
        });

        lg.appendChild(d);
      });

      if (hasCross) {
        const c = document.createElement('div');
        c.className = 'chip';
        c.innerHTML =
          `<svg width="24" height="8" style="vertical-align:middle"><line x1="1" y1="4" x2="23" y2="4" stroke="#fbbf24" stroke-width="2" stroke-dasharray="5 4"/></svg>` +
          `<span>dashed amber = fk across databases</span>`;
        lg.appendChild(c);
      }

      const hint = document.createElement('span');
      hint.className = 'legend-hint';
      hint.textContent = 'Click = toggle DB · 2×Click = SOLO';
      lg.appendChild(hint);

      // Update top bar stats chip
      document.getElementById('cnt').innerHTML =
        `<b>${data.length}</b> tables · <b>${links.length}</b> relations · <b>${sources.length}</b> db${sources.length === 1 ? '' : 's'}`;
    }

    function applySourceFilterUpdate() {
      buildLegend();
      buildSide(searchQuery);
      buildCards(searchQuery);
      if (selectedKey && disabledSources.has(byKey[selectedKey].source)) {
        resetToSphere(false);
      } else if (selectedKey) {
        computeFocusTargets(selectedKey);
      } else {
        nodes.forEach(n => {
          n.tgtAlpha = isSourceVisible(n.source) ? 1 : 0;
          n.alpha = n.tgtAlpha;
        });
        renderFrame();
      }
    }

    function buildSide(q) {
      q = (q || '').trim().toLowerCase();
      sideListEl.innerHTML = '';
      sideItem = {};

      const visibleSources = sources.filter(s => isSourceVisible(s));
      sideCntEl.textContent = `${visibleSources.length} / ${sources.length} dbs`;

      const startOpen = sources.length <= 5 || q.length > 0;
      const dets = [];

      sources.forEach(s => {
        if (!isSourceVisible(s)) return;
        const allInSrc = data.filter(t => t.source === s);
        const filtered = allInSrc.filter(t => matchTable(t, q));
        if (q && !filtered.length) return;

        const det = document.createElement('details');
        det.className = 'src';
        det.open = startOpen;

        const sum = document.createElement('summary');
        sum.title = 'Source: ' + s;
        sum.innerHTML =
          `<span class="caret">▶</span>` +
          `<span class="dot" style="background:${sColor[s]}"></span>` +
          `<span class="sname">${esc(srcLabel(s))}</span>` +
          `<span class="cnt">${filtered.length}${filtered.length !== allInSrc.length ? '/' + allInSrc.length : ''}</span>`;
        det.appendChild(sum);

        filtered.forEach(t => {
          const k = keyOf(t);
          const item = document.createElement('div');
          item.className = 'titem' + (selectedKey === k ? ' sel' : '');
          item.dataset.key = k;
          item.innerHTML =
            `<span class="tname-txt">${esc(t.name)}</span>` +
            `<span class="nfk">${t.fks.length} fk</span>`;
          item.addEventListener('click', () => {
            selectTable(k);
            // Auto-close sidebar drawer on mobile after picking a table
            if (window.innerWidth <= 960) sideEl.classList.add('closed');
          });
          det.appendChild(item);
          sideItem[k] = { el: item, det: det };
        });

        dets.push(det);
        sideListEl.appendChild(det);
      });

      if (!dets.length) {
        sideListEl.innerHTML = '<div style="padding:18px;text-align:center;color:var(--text-dim);font-size:12.5px">No matching tables found.</div>';
      }

      foldBtn.textContent = startOpen ? 'Close All' : 'Open All';
      foldBtn.onclick = () => {
        const anyOpen = dets.some(d => d.open);
        dets.forEach(d => { d.open = !anyOpen; });
        foldBtn.textContent = anyOpen ? 'Open All' : 'Close All';
      };
    }

    function buildCards(q) {
      q = (q || '').trim().toLowerCase();
      cardsEl.innerHTML = '';

      const list = data.filter(t => isSourceVisible(t.source) && matchTable(t, q));
      if (!list.length) {
        cardsEl.innerHTML = '<div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-dim)">No tables match the current filter.</div>';
        return;
      }

      list.forEach(t => {
        const k = keyOf(t);
        const card = document.createElement('div');
        card.className = 'card' + (selectedKey === k ? ' sel' : '');
        card.dataset.key = k;
        card.style.setProperty('--sc', sColor[t.source] || '#3b82f6');

        const maxCols = 9;
        let colsHtml = t.columns.slice(0, maxCols).map((col, idx) => {
          const fk = t.fks.find(f => f.column === col);
          const isPk = (idx === 0 && col === 'id');
          const cls = fk ? 'is-fk' : (isPk ? 'is-pk' : '');
          const tag = fk ? `<span style="opacity:.85">→ ${esc(fk.ref_table)}</span>` : (isPk ? '<span class="badge pk">PK</span>' : '');
          return `<div class="card-col-row ${cls}"><span>${esc(col)}</span>${tag}</div>`;
        }).join('');

        if (t.columns.length > maxCols) {
          colsHtml += `<div class="card-more">… +${t.columns.length - maxCols} more columns</div>`;
        }

        card.innerHTML =
          `<div class="card-head">` +
            `<h3>${esc(t.name)}</h3>` +
            `<span class="chip" style="padding:2px 7px;font-size:10.5px">${t.fks.length} FK</span>` +
          `</div>` +
          `<div class="card-src"><span class="dot" style="background:${sColor[t.source]}"></span>${esc(srcLabel(t.source))}</div>` +
          `<div class="card-cols">${colsHtml}</div>`;

        card.addEventListener('click', () => selectTable(k));
        cardsEl.appendChild(card);
      });
    }

    /* =====================================================================
       9. MOUSE, TOUCH, WHEEL & KEYBOARD INTERACTION
       ===================================================================== */
    let dragState = null;
    let dragMoved = 0;
    let pinchState = null;

    svg.addEventListener('mousedown', (e) => {
      if (e.button !== 0 && e.button !== 1) return;
      anim = null; // Immediately hand control to user
      const doPan = tool === 'pan' || e.shiftKey || e.button === 1;
      dragMoved = 0;
      dragState = {
        pan: doPan,
        x: e.clientX,
        y: e.clientY,
        rx: rot.rx,
        ry: rot.ry,
        px: pan.x,
        py: pan.y
      };
      svg.classList.add('grabbing');
    });

    window.addEventListener('mousemove', (e) => {
      if (!dragState) return;
      const dx = e.clientX - dragState.x;
      const dy = e.clientY - dragState.y;
      dragMoved = Math.max(dragMoved, Math.hypot(dx, dy));

      if (dragState.pan) {
        pan.x = dragState.px - dx / zoom;
        pan.y = dragState.py - dy / zoom;
        updateViewBox();
      } else {
        rot.ry = dragState.ry - dx * 0.0075;
        rot.rx = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, dragState.rx + dy * 0.0075));
        renderFrame();
      }
    });

    window.addEventListener('mouseup', () => {
      dragState = null;
      svg.classList.remove('grabbing');
    });

    // Smooth scroll-wheel zoom
    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      anim = null;
      const factor = e.deltaY < 0 ? 1.16 : (1 / 1.16);
      zoom = Math.max(0.35, Math.min(12, zoom * factor));
      updateViewBox();
    }, { passive: false });

    // Double-click empty SVG space resets to full 3D sphere
    svg.addEventListener('dblclick', (e) => {
      if (e.target.closest && e.target.closest('.node')) return;
      resetToSphere(false);
      toast('View reset to 3D Planet');
    });

    // Touch support (1-finger rotate/pan, 2-finger pinch-zoom)
    svg.addEventListener('touchstart', (e) => {
      anim = null;
      if (e.touches.length === 1) {
        const t = e.touches[0];
        dragMoved = 0;
        dragState = {
          pan: tool === 'pan',
          x: t.clientX,
          y: t.clientY,
          rx: rot.rx,
          ry: rot.ry,
          px: pan.x,
          py: pan.y
        };
        pinchState = null;
      } else if (e.touches.length === 2) {
        dragState = null;
        const d = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        pinchState = { dist: d, zoom: zoom };
      }
    }, { passive: true });

    svg.addEventListener('touchmove', (e) => {
      if (e.touches.length === 1 && dragState) {
        const t = e.touches[0];
        const dx = t.clientX - dragState.x;
        const dy = t.clientY - dragState.y;
        dragMoved = Math.max(dragMoved, Math.hypot(dx, dy));
        if (dragState.pan) {
          pan.x = dragState.px - dx / zoom;
          pan.y = dragState.py - dy / zoom;
          updateViewBox();
        } else {
          rot.ry = dragState.ry - dx * 0.0085;
          rot.rx = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, dragState.rx + dy * 0.0085));
          renderFrame();
        }
      } else if (e.touches.length === 2 && pinchState) {
        const d = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        zoom = Math.max(0.35, Math.min(12, pinchState.zoom * (d / (pinchState.dist || 1))));
        updateViewBox();
      }
    }, { passive: true });

    svg.addEventListener('touchend', () => {
      dragState = null;
      pinchState = null;
    });

    /* =====================================================================
       10. TOOLBAR & FLOATING CONTROL HANDLERS
       ===================================================================== */
    function setMode(m, keepSelection) {
      mode = m;
      document.getElementById('vGraph').classList.toggle('active', m === 'graph');
      document.getElementById('vCards').classList.toggle('active', m === 'cards');
      graphWrapEl.style.display = (m === 'graph') ? 'flex' : 'none';
      cardsEl.classList.toggle('visible', m === 'cards');
      if (m === 'graph') {
        requestAnimationFrame(() => {
          updateViewBox();
          if (!keepSelection && !selectedKey) renderFrame();
        });
      }
    }

    document.getElementById('vGraph').addEventListener('click', () => setMode('graph', true));
    document.getElementById('vCards').addEventListener('click', () => setMode('cards', true));

    document.getElementById('vEdges').addEventListener('click', (e) => {
      showEdges = !showEdges;
      svg.classList.toggle('no-edges', !showEdges);
      e.currentTarget.classList.toggle('active', showEdges);
      e.currentTarget.classList.toggle('off', !showEdges);
      e.currentTarget.textContent = showEdges ? '🔗 Edges: On' : '🔗 Edges: Off';
    });

    document.getElementById('vLabels').addEventListener('click', (e) => {
      showAllLabels = !showAllLabels;
      e.currentTarget.classList.toggle('active', showAllLabels);
      e.currentTarget.textContent = showAllLabels ? '🏷 Labels: All' : '🏷 Labels: Front';
      renderFrame();
    });

    document.getElementById('vSpin').addEventListener('click', (e) => {
      autoSpin = !autoSpin;
      e.currentTarget.classList.toggle('active', autoSpin);
      e.currentTarget.classList.toggle('off', !autoSpin);
      toast(autoSpin ? 'Auto-rotation enabled' : 'Auto-rotation paused');
    });

    document.getElementById('sideToggle').addEventListener('click', () => {
      sideEl.classList.toggle('closed');
      setTimeout(() => { updateViewBox(); renderFrame(); }, 230);
    });

    // Keep the SVG viewBox in sync while panels animate (drawer / sidebar),
    // otherwise the picture stretches during the width transition
    detailEl.addEventListener('transitionend', (e) => {
      if (e.propertyName === 'width') { updateViewBox(); renderFrame(); }
    });
    sideEl.addEventListener('transitionend', (e) => {
      if (e.propertyName === 'margin-left') { updateViewBox(); renderFrame(); }
    });

    document.getElementById('closeDetail').addEventListener('click', () => resetToSphere(false));
    document.getElementById('exitFocusBtn').addEventListener('click', () => resetToSphere(false));

    // Floating Canvas Controls
    document.getElementById('zIn').addEventListener('click', () => {
      anim = null;
      zoom = Math.min(12, zoom * 1.35);
      updateViewBox();
    });

    document.getElementById('zOut').addEventListener('click', () => {
      anim = null;
      zoom = Math.max(0.35, zoom / 1.35);
      updateViewBox();
    });

    document.getElementById('zFit').addEventListener('click', () => {
      if (selectedKey) {
        computeFocusTargets(selectedKey);
      } else {
        const W = svg.clientWidth || 800;
        const H = svg.clientHeight || 520;
        const fitZoom = Math.max(0.55, Math.min(1.15, Math.min(W, H) / 760));
        startTransition({ rx: rot.rx, ry: rot.ry }, fitZoom, { x: 0, y: 0 }, 300);
      }
    });

    document.getElementById('hand').addEventListener('click', (e) => {
      tool = (tool === 'rotate') ? 'pan' : 'rotate';
      const isPan = tool === 'pan';
      svg.classList.toggle('pan-mode', isPan);
      e.currentTarget.classList.toggle('active-tool', isPan);
      e.currentTarget.textContent = isPan ? '✋' : '🔄';
      e.currentTarget.title = isPan ? 'Pan mode active (click to rotate)' : 'Rotate mode active (click to pan)';
      toast(isPan ? 'Pan tool active (drag to move)' : 'Rotate tool active (drag to orbit)');
    });

    document.getElementById('zReset').addEventListener('click', () => {
      resetToSphere(true);
      toast('Reset everything to 3D Planet');
    });

    // Live Search Input
    searchInput.addEventListener('input', (e) => {
      searchQuery = e.target.value.trim().toLowerCase();
      searchClear.classList.toggle('visible', searchQuery.length > 0);
      buildSide(searchQuery);
      buildCards(searchQuery);
      renderFrame();
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchQuery = '';
      searchClear.classList.remove('visible');
      buildSide('');
      buildCards('');
      renderFrame();
      searchInput.focus();
    });

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      const modalOpen = document.getElementById('sqlModal').classList.contains('open');
      if (e.key === 'Escape') {
        if (modalOpen) {
          closeSqlModal();
        } else {
          resetToSphere(true);
          toast('Reset view & filters');
        }
        return;
      }
      if (modalOpen) return;
      if (e.key === '/' && document.activeElement !== searchInput && document.activeElement.tagName !== 'TEXTAREA' && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        searchInput.focus();
      }
    });

    window.addEventListener('resize', () => {
      updateViewBox();
      renderFrame();
    });

    /* =====================================================================
       11. BUILT-IN CLIENT-SIDE SQL DDL PARSER & MODAL
       Handles -- line comments, block comments, CREATE TABLE, inline REFERENCES,
       and table-level FOREIGN KEY (...) REFERENCES ...
       ===================================================================== */
    const sqlModal = document.getElementById('sqlModal');
    const sqlInput = document.getElementById('sqlInput');
    const sqlSourceInput = document.getElementById('sqlSourceName');
    const sqlMsg = document.getElementById('sqlMsg');

    function stripSqlComments(sql) {
      // Remove /* ... */ block comments and -- line comments while keeping quoted strings safe
      let out = '';
      let i = 0;
      const len = sql.length;
      let inSingle = false, inDouble = false;
      while (i < len) {
        const ch = sql[i];
        const next = sql[i + 1];
        if (!inSingle && !inDouble) {
          if (ch === '-' && next === '-') {
            while (i < len && sql[i] !== '\n') i++;
            continue;
          }
          if (ch === '/' && next === '*') {
            i += 2;
            while (i < len - 1 && !(sql[i] === '*' && sql[i + 1] === '/')) i++;
            i += 2;
            continue;
          }
        }
        if (ch === "'" && !inDouble) inSingle = !inSingle;
        else if (ch === '"' && !inSingle) inDouble = !inDouble;
        out += ch;
        i++;
      }
      return out;
    }

    function splitTopLevelCommas(body) {
      const parts = [];
      let depth = 0, cur = '', inS = false, inD = false;
      for (let i = 0; i < body.length; i++) {
        const ch = body[i];
        if (ch === "'" && !inD) inS = !inS;
        else if (ch === '"' && !inS) inD = !inD;
        if (!inS && !inD) {
          if (ch === '(') depth++;
          else if (ch === ')') depth = Math.max(0, depth - 1);
          if (ch === ',' && depth === 0) {
            parts.push(cur);
            cur = '';
            continue;
          }
        }
        cur += ch;
      }
      if (cur.trim()) parts.push(cur);
      return parts;
    }

    function parseSqlDDL(rawText, sourceName) {
      const text = stripSqlComments(rawText);
      const CREATE_RE = /CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:["'`\[]?\w+["'`\]]?\.)?["'`\[]?(\w+)["'`\]]?/gi;
      const FK_RE = /FOREIGN\s+KEY\s*\(\s*["'`\[]?(\w+)["'`\]]?\s*\)\s*REFERENCES\s+(?:["'`\[]?\w+["'`\]]?\.)?["'`\[]?(\w+)["'`\]]?/i;
      const INLINE_FK_RE = /^["'`\[]?(\w+)["'`\]]?\s+[\s\S]*?\bREFERENCES\s+(?:["'`\[]?\w+["'`\]]?\.)?["'`\[]?(\w+)["'`\]]?/i;
      const CONSTRAINT_STARTS = /^(PRIMARY|UNIQUE|CHECK|CONSTRAINT|FOREIGN|KEY|INDEX|FULLTEXT)\b/i;

      const parsed = [];
      let m;
      while ((m = CREATE_RE.exec(text)) !== null) {
        const tableName = m[1];
        const openIdx = text.indexOf('(', CREATE_RE.lastIndex);
        if (openIdx === -1) continue;

        let depth = 0, endIdx = -1, inS = false, inD = false;
        for (let i = openIdx; i < text.length; i++) {
          const ch = text[i];
          if (ch === "'" && !inD) inS = !inS;
          else if (ch === '"' && !inS) inD = !inD;
          if (!inS && !inD) {
            if (ch === '(') depth++;
            else if (ch === ')') {
              depth--;
              if (depth === 0) { endIdx = i; break; }
            }
          }
        }
        if (endIdx === -1) continue;

        const body = text.slice(openIdx + 1, endIdx);
        const columns = [];
        const fks = [];

        splitTopLevelCommas(body).forEach(rawPart => {
          const part = rawPart.trim();
          if (!part) return;

          const fkMatch = FK_RE.exec(part);
          if (fkMatch) {
            fks.push({ column: fkMatch[1], ref_table: fkMatch[2] });
            return;
          }
          if (CONSTRAINT_STARTS.test(part)) return;

          const inlineFk = INLINE_FK_RE.exec(part);
          if (inlineFk) {
            fks.push({ column: inlineFk[1], ref_table: inlineFk[2] });
          }

          const colToken = part.split(/\s+/)[0].replace(/^["'`\[]|["'`\]]$/g, '');
          if (colToken && columns.indexOf(colToken) < 0) {
            columns.push(colToken);
          }
        });

        parsed.push({
          name: tableName,
          source: sourceName || 'custom_schema.sql',
          columns: columns,
          fks: fks
        });
        CREATE_RE.lastIndex = endIdx + 1;
      }
      return parsed;
    }

    function openSqlModal() {
      sqlMsg.className = 'modal-msg';
      sqlMsg.textContent = '';
      sqlModal.classList.add('open');
      sqlInput.focus();
    }

    function closeSqlModal() {
      sqlModal.classList.remove('open');
    }

    function applySqlImport(replaceAll) {
      const raw = sqlInput.value.trim();
      const srcName = (sqlSourceInput.value.trim() || 'custom_schema.sql');
      if (!raw) {
        sqlMsg.className = 'modal-msg err';
        sqlMsg.textContent = 'Please paste at least one CREATE TABLE SQL statement.';
        return;
      }

      const extracted = parseSqlDDL(raw, srcName);
      if (!extracted.length) {
        sqlMsg.className = 'modal-msg err';
        sqlMsg.textContent = 'No valid CREATE TABLE definitions found. Check your SQL syntax.';
        return;
      }

      if (replaceAll) {
        data = extracted;
      } else {
        // Merge by (source, name) replacing duplicates if same source+name
        const map = new Map();
        data.forEach(t => map.set(keyOf(t), t));
        extracted.forEach(t => map.set(keyOf(t), t));
        data = Array.from(map.values());
      }

      selectedKey = null;
      focusSet = new Set();
      disabledSources = new Set();
      soloSource = null;
      detailEl.classList.remove('open');
      focusHudEl.classList.remove('visible');

      rebuildDataset();
      computeSphereTargets();
      closeSqlModal();
      toast(`Parsed ${extracted.length} table${extracted.length === 1 ? '' : 's'} from ${srcName}`);
    }

    document.getElementById('openSqlBtn').addEventListener('click', openSqlModal);
    document.getElementById('closeSqlBtn').addEventListener('click', closeSqlModal);
    sqlModal.addEventListener('click', (e) => { if (e.target === sqlModal) closeSqlModal(); });

    document.getElementById('sqlAddBtn').addEventListener('click', () => applySqlImport(false));
    document.getElementById('sqlReplaceBtn').addEventListener('click', () => applySqlImport(true));

    document.getElementById('loadSampleSqlBtn').addEventListener('click', () => {
      sqlSourceInput.value = 'hr_payroll.sql';
      sqlInput.value = `-- HR & Payroll Extension (connects to core_auth & accounting_ledger)
CREATE TABLE departments (
  id SERIAL PRIMARY KEY,
  parent_id INT REFERENCES departments(id), -- self-referencing hierarchy
  cost_center_id INT REFERENCES cost_centers(id),
  name VARCHAR(120) NOT NULL
);

/* Employee records linked to users and departments */
CREATE TABLE employees (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  department_id INT NOT NULL REFERENCES departments(id),
  manager_id INT REFERENCES employees(id),
  hire_date DATE,
  base_salary NUMERIC(12,2)
);

CREATE TABLE payroll_runs (
  id SERIAL PRIMARY KEY,
  fiscal_year_id INT,
  approved_by INT,
  journal_entry_id INT,
  period_label VARCHAR(20),
  FOREIGN KEY (fiscal_year_id) REFERENCES fiscal_years(id),
  FOREIGN KEY (approved_by) REFERENCES users(id),
  FOREIGN KEY (journal_entry_id) REFERENCES journal_entries(id)
);

CREATE TABLE payslips (
  id SERIAL PRIMARY KEY,
  payroll_run_id INT NOT NULL REFERENCES payroll_runs(id),
  employee_id INT NOT NULL REFERENCES employees(id),
  gross_pay NUMERIC(12,2),
  net_pay NUMERIC(12,2)
);`;
      sqlMsg.className = 'modal-msg ok';
      sqlMsg.textContent = 'Sample HR & Payroll SQL loaded — click "Add to Current Schema" to see cross-DB links!';
    });

    document.getElementById('restoreDefaultBtn').addEventListener('click', () => {
      const restoringProject = Boolean(INITIAL_DATA && INITIAL_DATA.length);
      data = restoringProject ? cloneData(INITIAL_DATA) : cloneData(DEFAULT_DATA);
      selectedKey = null;
      focusSet = new Set();
      disabledSources = new Set();
      soloSource = null;
      detailEl.classList.remove('open');
      focusHudEl.classList.remove('visible');
      rebuildDataset();
      computeSphereTargets();
      closeSqlModal();
      toast(restoringProject
        ? ('Restored project schema (' + data.length + ' tables)')
        : 'Restored built-in demo dataset');
    });

    /* =====================================================================
       12. PUBLIC HOOK (automation / tests) & BOOTSTRAP
       ===================================================================== */
    window.SchemaExplorer = {
      select: selectTable,
      reset: resetToSphere,
      parseSql: parseSqlDDL,
      state: () => ({
        selectedKey: selectedKey,
        focusSet: Array.from(focusSet),
        nodes: nodes.map(n => ({ key: n.key, name: n.name, x: n.cur.x, y: n.cur.y, z: n.cur.z, alpha: n.alpha })),
        pairs: pairs.map(p => ({
          id: p.id, s: p.s.key, t: p.t.key, self: p.self,
          out: p.out, inn: p.inn,
          display: p.el.getAttribute('display'),
          opacity: p.el.style.opacity
        }))
      })
    };

    window.addEventListener('load', () => {
      // Collapse sidebar by default on narrow mobile screens so 3D planet shines immediately
      if (window.innerWidth <= 960) {
        sideEl.classList.add('closed');
      }
      rebuildDataset();

      // Fit initial sphere nicely to the viewport
      const W = svg.clientWidth || 800;
      const H = svg.clientHeight || 520;
      zoom = Math.max(0.55, Math.min(1.15, Math.min(W, H) / 760));
      updateViewBox();
      renderFrame();

      if (INITIAL_DATA && INITIAL_DATA.length) {
        document.getElementById('restoreDefaultBtn').textContent = 'Restore Project Schema';
      } else {
        toast('No schema found in this project · showing the built-in demo dataset');
      }

      lastFrameTime = performance.now();
      requestAnimationFrame(tick);
    });
  })();
  </script>
</body>
</html>
"""



def filter_schema_rows(rows, only, exclude):
    """Filter table rows by SOURCE substring, so one run can target a
    single database. --only keeps sources containing any given substring,
    --exclude drops them. Both take lists (repeatable flags); both must
    be None/empty to keep everything."""
    def keep(r):
        src = r["source"]
        if only and not any(p in src for p in only):
            return False
        if exclude and any(p in src for p in exclude):
            return False
        return True
    return [r for r in rows if keep(r)]


def schema_html(rows):
    """Render schema rows as one standalone interactive HTML file
    (3D network + sunflower focus + ER cards; zero dependencies)."""
    import json
    # "</" is escaped as "<\/" so a table/column name can never close the
    # inline <script> tag early; JSON allows the escaped-solidus form.
    return HTML_TEMPLATE.replace(
        "__DATA__", json.dumps(rows).replace("</", r"<\/"))


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
    """Return list of {name, source, columns, fks} from .sql files and SQLite DBs.

    Django migration files are parsed even though migrations/ folders are
    (by default) excluded from general indexing as framework noise: they are
    skipped for code search, but still feed schema intelligence here.
    """
    out = []
    seen = set()

    def push(tname, source, t):
        key = (tname, source)
        if key in seen:
            return
        seen.add(key)
        out.append({"name": tname, "source": source,
                    "columns": t["columns"], "fks": t["fks"]})

    for f in scanned_files:
        if f.ext == ".sql":
            try:
                with open(f.abspath, encoding="utf-8", errors="ignore") as fh:
                    for tname, t in parse_sql_schema(fh.read()).items():
                        push(tname, f.path, t)
            except OSError:
                pass
        if f.ext == ".py" and "migrations" in f.path:
            try:
                with open(f.abspath, encoding="utf-8", errors="ignore") as fh:
                    for tname, t in parse_django_migrations(fh.read()).items():
                        push(tname, f.path, t)
            except OSError:
                pass
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", ".xzagent"}]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if fn.lower().endswith(DB_EXTS):
                for tname, t in read_sqlite_schema(full).items():
                    push(tname, rel, t)
            elif fn.endswith(".py") and rel != "__init__.py" and "migrations" in rel.split("/"):
                try:
                    with open(full, encoding="utf-8", errors="ignore") as fh:
                        for tname, t in parse_django_migrations(fh.read()).items():
                            push(tname, rel, t)
                except OSError:
                    pass
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