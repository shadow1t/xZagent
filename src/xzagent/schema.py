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
  <title>DB Schema Explorer — High-Scale 3D Engine</title>
  <style>
    /* =====================================================================
       DESIGN TOKENS & RESET
       High-contrast dark navy glassmorphism optimized for large schemas
       ===================================================================== */
    :root {
      --bg-deep: #060b16;
      --bg-mid: #0c1528;
      --bg-surface: rgba(15, 23, 42, 0.9);
      --bg-elevated: #17233b;
      --border: #1e2e4d;
      --border-strong: #33486e;
      --text: #e2e8f0;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --accent: #3b82f6;
      --hot: #f97316;
      --hot-soft: #fdba74;
      --amber: #fbbf24;
      --emerald: #34d399;
      --cyan: #22d3ee;
      --violet: #a855f7;
      --shadow-lg: 0 14px 40px rgba(0, 0, 0, 0.58);
      --shadow-sm: 0 4px 14px rgba(0, 0, 0, 0.38);
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
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(1200px 680px at 65% -10%, #152546 0%, #091020 55%, #050912 100%);
      color: var(--text);
      font-size: 14px;
      line-height: 1.45;
    }

    body {
      display: flex;
      flex-direction: column;
    }

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
      gap: 7px;
      padding: 8px 14px;
      background: linear-gradient(180deg, rgba(19, 31, 56, 0.97), rgba(12, 21, 40, 0.96));
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
      font-size: 14.5px;
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
      flex: 0 1 235px;
      min-width: 160px;
    }

    .search-wrap input {
      width: 100%;
      background: rgba(6, 11, 22, 0.88);
      color: var(--text);
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 6.5px 32px 6.5px 11px;
      font-size: 12.5px;
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
      font-size: 10.5px;
      color: var(--text-dim);
      background: #17233b;
      border: 1px solid var(--border-strong);
      border-radius: 5px;
      padding: 1px 5px;
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
      background: rgba(30, 41, 59, 0.8);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 11.5px;
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
      gap: 5px;
      flex-wrap: wrap;
    }

    .btn {
      background: #1a2845;
      color: #cbd5e1;
      border: 1px solid var(--border-strong);
      border-radius: 9px;
      padding: 6px 10px;
      font-size: 12px;
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

    .btn.galaxy-active {
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      border-color: #c084fc;
      color: #ffffff;
      box-shadow: 0 2px 12px rgba(124, 58, 237, 0.38);
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

    /* =====================================================================
       MULTI-DATABASE LEGEND BAR
       ===================================================================== */
    #legend {
      flex: none;
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 6px 14px;
      background: rgba(10, 17, 32, 0.94);
      border-bottom: 1px solid var(--border);
      font-size: 11.5px;
      overflow-x: auto;
      white-space: nowrap;
      scrollbar-width: thin;
      z-index: 25;
    }

    .lg {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(18, 29, 52, 0.85);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3.5px 10px;
      cursor: pointer;
      transition: all 0.14s ease;
      color: #cbd5e1;
      user-select: none;
    }

    .lg:hover {
      border-color: #60a5fa;
      background: rgba(30, 48, 84, 0.92);
    }

    .lg.off {
      opacity: 0.35;
      text-decoration: line-through;
    }

    .lg.solo {
      border-color: #60a5fa;
      background: rgba(37, 99, 235, 0.25);
      box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.35);
      color: #ffffff;
    }

    .lg .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex: none;
      box-shadow: 0 0 7px currentColor;
    }

    .lg .n { color: var(--text-dim); font-size: 10.5px; }

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

    /* Collapsible Left Sidebar (Lazy-loaded DOM) */
    #side {
      width: 288px;
      min-width: 288px;
      background: rgba(11, 19, 36, 0.96);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      transition: margin-left 0.22s cubic-bezier(0.4, 0, 0.2, 1);
      z-index: 20;
    }

    #side.closed { margin-left: -288px; }

    #side-tools {
      flex: none;
      padding: 8px 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
      background: rgba(15, 25, 46, 0.96);
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
      padding: 3.5px 9px;
      font-size: 11px;
      cursor: pointer;
      transition: all 0.14s;
    }

    #side-tools button:hover {
      border-color: var(--accent);
      color: #fff;
    }

    #side-list {
      flex: 1 1 auto;
      overflow-y: auto;
      padding: 7px;
    }

    details.src { margin-bottom: 4px; }

    details.src summary {
      cursor: pointer;
      color: #bfdbfe;
      font-size: 12px;
      padding: 6.5px 8px;
      border-radius: 8px;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 7px;
      font-weight: 600;
      user-select: none;
      transition: background 0.14s;
    }

    details.src summary::-webkit-details-marker { display: none; }
    details.src summary:hover { background: rgba(30, 41, 59, 0.8); }
    details.src summary .caret {
      font-size: 9px;
      color: var(--text-dim);
      transition: transform 0.14s;
      width: 10px;
      text-align: center;
    }
    details.src[open] summary .caret { transform: rotate(90deg); }
    details.src summary .dot {
      width: 8.5px;
      height: 8.5px;
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
    details.src summary .solo-mini {
      background: rgba(59, 130, 246, 0.14);
      color: #93c5fd;
      border: 1px solid rgba(59, 130, 246, 0.3);
      border-radius: 5px;
      padding: 1px 5px;
      font-size: 10px;
      cursor: pointer;
      opacity: 0.7;
      transition: all 0.12s;
    }
    details.src summary .solo-mini:hover {
      opacity: 1;
      background: #2563eb;
      color: #fff;
      border-color: #60a5fa;
    }

    .titem {
      padding: 6.5px 9px;
      border-radius: 7px;
      cursor: pointer;
      font-size: 12px;
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
      font-size: 10px;
      background: rgba(15, 23, 42, 0.65);
      padding: 1px 6px;
      border-radius: 999px;
      flex: none;
    }

    .titem.sel .nfk {
      background: rgba(255, 255, 255, 0.2);
      color: #e0f2fe;
    }

    .side-more-btn {
      width: calc(100% - 12px);
      margin: 4px 0 4px 10px;
      padding: 6px;
      background: rgba(30, 41, 59, 0.6);
      border: 1px dashed var(--border-strong);
      border-radius: 7px;
      color: #93c5fd;
      font-size: 11.5px;
      cursor: pointer;
    }

    .side-more-btn:hover { background: rgba(37, 99, 235, 0.2); }

    /* =====================================================================
       CENTRAL STAGE — HIGH-SPEED 60FPS HTML5 CANVAS + INSPECTOR DRAWER
       ===================================================================== */
    #content {
      flex: 1 1 auto;
      min-width: 0;
      min-height: 0;
      display: flex;
      position: relative;
      overflow: hidden;
    }

    #graph-wrap {
      flex: 1 1 auto;
      min-width: 0;
      min-height: 0;
      position: relative;
      display: flex;
      flex-direction: column;
      background: radial-gradient(780px 480px at 50% 46%, #11203e 0%, #081020 65%, #050a14 100%);
      overflow: hidden;
    }

    #graphCanvas {
      width: 100%;
      height: 100%;
      display: block;
      user-select: none;
      touch-action: none;
      cursor: grab;
    }

    #graphCanvas.grabbing { cursor: grabbing; }
    #graphCanvas.pan-mode { cursor: move; }
    #graphCanvas.pointer { cursor: pointer; }

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

    /* Floating Top-Left Context HUD Pill */
    #stage-hud {
      position: absolute;
      top: 14px;
      left: 14px;
      display: flex;
      align-items: center;
      gap: 8px;
      background: rgba(15, 23, 42, 0.88);
      backdrop-filter: blur(10px);
      border: 1px solid var(--border-strong);
      border-radius: 999px;
      padding: 6px 12px 6px 13px;
      box-shadow: var(--shadow-sm);
      z-index: 12;
      font-size: 12px;
      max-width: calc(100% - 80px);
    }

    #stage-hud.focus-style { border-color: rgba(249, 115, 22, 0.65); }
    #stage-hud.galaxy-style { border-color: rgba(168, 85, 247, 0.65); }

    #stage-hud .fdot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--violet);
      box-shadow: 0 0 8px currentColor;
      flex: none;
    }

    #stage-hud .ftxt {
      color: #f8fafc;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-weight: 500;
    }

    #stage-hud button {
      background: rgba(255, 255, 255, 0.1);
      color: #e2e8f0;
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 11px;
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.15s;
    }

    #stage-hud button:hover {
      background: var(--accent);
      border-color: var(--accent);
    }

    .hud-seg {
      display: inline-flex;
      align-items: center;
      background: rgba(6, 11, 22, 0.72);
      border: 1px solid var(--border-strong);
      border-radius: 999px;
      padding: 2px;
      gap: 2px;
    }

    #stage-hud .hud-seg button {
      background: transparent;
      border: 0;
      color: var(--text-muted);
      padding: 3px 9px;
      font-size: 11px;
      font-weight: 600;
      border-radius: 999px;
    }

    #stage-hud .hud-seg button:hover {
      color: #ffffff;
      background: rgba(255, 255, 255, 0.08);
    }

    #stage-hud .hud-seg button.on {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
    }

    #stage-hud .hud-seg button.on-galaxy {
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      color: #ffffff;
    }

    .detail-mode-bar {
      display: flex;
      align-items: center;
      gap: 5px;
      margin-top: 10px;
      padding: 3px;
      background: rgba(6, 11, 22, 0.65);
      border: 1px solid var(--border);
      border-radius: 10px;
    }

    .detail-mode-bar button {
      flex: 1;
      background: transparent;
      border: 0;
      color: var(--text-muted);
      border-radius: 7px;
      padding: 5px 7px;
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.14s ease;
      white-space: nowrap;
    }

    .detail-mode-bar button:hover {
      color: #ffffff;
      background: rgba(255, 255, 255, 0.07);
    }

    .detail-mode-bar button.on {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(37, 99, 235, 0.35);
    }

    .detail-mode-bar button.on-galaxy {
      background: linear-gradient(135deg, #7c3aed, #2563eb);
      color: #ffffff;
      box-shadow: 0 2px 8px rgba(124, 58, 237, 0.35);
    }

    /* Hover Tooltip Pill */
    #hover-tip {
      position: absolute;
      pointer-events: none;
      display: none;
      background: rgba(9, 15, 28, 0.94);
      backdrop-filter: blur(8px);
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 7px 11px;
      font-size: 12px;
      color: #f8fafc;
      box-shadow: var(--shadow-sm);
      z-index: 16;
      max-width: 280px;
    }

    #hover-tip .ht-title {
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 2px;
    }

    #hover-tip .ht-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex: none;
    }

    #hover-tip .ht-meta {
      color: var(--text-muted);
      font-size: 11px;
    }

    /* Bottom-Left Hint + Performance Badge */
    #canvas-footer {
      position: absolute;
      bottom: 12px;
      left: 14px;
      right: 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      pointer-events: none;
      z-index: 10;
      gap: 10px;
    }

    .footer-pill {
      background: rgba(9, 15, 28, 0.76);
      backdrop-filter: blur(6px);
      border: 1px solid rgba(30, 46, 77, 0.85);
      border-radius: 999px;
      padding: 4.5px 12px;
      font-size: 11px;
      color: var(--text-dim);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .footer-pill b { color: #34d399; font-weight: 600; }

    /* =====================================================================
       SLEEK INSPECTOR DRAWER (#detail)
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
      transition: width 0.24s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.2s ease;
      z-index: 18;
    }

    #detail.open {
      width: 385px;
      min-width: 330px;
      opacity: 1;
      border-left-width: 1px;
      box-shadow: -8px 0 30px rgba(0, 0, 0, 0.48);
    }

    .detail-header {
      flex: none;
      padding: 14px 16px 12px;
      background: linear-gradient(180deg, rgba(23, 35, 59, 0.92), rgba(15, 23, 42, 0.88));
      border-bottom: 1px solid var(--border);
    }

    .detail-title-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
    }

    .detail-title-wrap { min-width: 0; }

    #tname {
      margin: 0;
      font-size: 17.5px;
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
      transition: all 0.14s;
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
      font-size: 11px;
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
      padding: 7.5px 10px;
      border-top: 1px solid rgba(30, 46, 77, 0.7);
      vertical-align: middle;
    }

    table.cols tr:hover td { background: rgba(30, 45, 76, 0.55); }

    .col-cell {
      display: flex;
      align-items: center;
      gap: 6px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
    }

    .badge {
      font-family: 'Inter', system-ui, sans-serif;
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
       PAGINATED ER CARDS VIEW (#cards) — LAZY BUILT ONLY WHEN ACTIVE
       ===================================================================== */
    #cards-wrap {
      display: none;
      flex: 1 1 auto;
      overflow-y: auto;
      padding: 18px;
      background: radial-gradient(800px 400px at 50% 0%, #111f3c 0%, #081020 75%);
    }

    #cards-wrap.visible { display: block; }

    #cards {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(255px, 1fr));
      gap: 14px;
      align-content: start;
    }

    #cards-pager {
      margin-top: 18px;
      text-align: center;
      padding-bottom: 24px;
    }

    .card {
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid var(--border);
      border-left: 4px solid var(--sc, #3b82f6);
      border-radius: 12px;
      cursor: pointer;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: transform 0.14s ease, border-color 0.14s ease, box-shadow 0.14s ease;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
    }

    .card:hover {
      border-color: #60a5fa;
      transform: translateY(-2px);
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
      font-family: 'Inter', system-ui, sans-serif;
      font-size: 11px;
      padding-top: 3px;
    }

    /* =====================================================================
       PASTE SQL & STRESS BENCHMARK MODAL
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
      max-width: 740px;
      max-height: 90vh;
      background: #0f172a;
      border: 1px solid var(--border-strong);
      border-radius: 16px;
      box-shadow: var(--shadow-lg);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      animation: modalPop 0.16s ease-out;
    }

    @keyframes modalPop {
      from { opacity: 0; transform: scale(0.96) translateY(8px); }
      to { opacity: 1; transform: scale(1) translateY(0); }
    }

    .modal-head {
      padding: 13px 18px;
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
      gap: 8px;
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
      padding: 6.5px 10px;
      font-size: 12.5px;
      outline: none;
      flex: 1;
      min-width: 160px;
    }

    .modal-input:focus, .modal-textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.2);
    }

    .modal-textarea {
      width: 100%;
      min-height: 210px;
      background: #070d1a;
      color: #e2e8f0;
      border: 1px solid var(--border-strong);
      border-radius: 10px;
      padding: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
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
       TOAST NOTIFICATION
       ===================================================================== */
    #toast {
      position: fixed;
      bottom: 18px;
      left: 50%;
      transform: translateX(-50%) translateY(8px);
      background: rgba(15, 23, 42, 0.95);
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
       RESPONSIVE ADAPTATIONS
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

      #canvas-hint-pill { display: none; }
      .legend-hint { display: none; }
    }

    @media (max-width: 600px) {
      #top { padding: 8px 10px; gap: 6px; }
      .search-wrap { flex: 1 1 100%; order: 10; }
      .btn { padding: 5.5px 8.5px; font-size: 11.5px; }
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
      <button id="vGalaxy" class="btn" title="Overview: 1 orb per database (click a DB to drill in)">🌌 DB Galaxy</button>
      <button id="vGraph" class="btn active" title="3D Table Network on HTML5 Canvas">🕸 3D Tables</button>
      <button id="vCards" class="btn" title="Paginated ER Cards">🗂 ER Cards</button>
    </div>

    <div class="btn-group">
      <button id="vEdges" class="btn active">🔗 Edges: On</button>
      <button id="vLabels" class="btn">🏷 Labels: Smart</button>
      <button id="vSpin" class="btn active" title="Toggle gentle auto-rotation">⟳ Spin</button>
      <button id="openSqlBtn" class="btn primary-sql" title="Paste SQL or test 8,000-table stress mode">＋ SQL / 8K Test</button>
    </div>
  </header>

  <!-- =====================================================================
       MULTI-DATABASE LEGEND BAR
       ===================================================================== -->
  <div id="legend"></div>

  <!-- =====================================================================
       MAIN WORKSPACE
       ===================================================================== -->
  <div id="main">
    <!-- Left Collapsible Sidebar (Lazy DOM) -->
    <aside id="side">
      <div id="side-tools">
        <span class="cnt" id="side-cnt">0 dbs</span>
        <button id="foldBtn">Open All</button>
      </div>
      <div id="side-list"></div>
    </aside>

    <!-- Central Stage -->
    <main id="content">
      <!-- High-Speed 60FPS HTML5 Canvas Stage -->
      <section id="graph-wrap">
        <canvas id="graphCanvas" aria-label="3D Database Schema Canvas"></canvas>

        <!-- Dynamic Stage HUD (Galaxy / Solo DB / Focus Mode) -->
        <div id="stage-hud" style="display:none">
          <span class="fdot" id="hud-dot"></span>
          <span class="ftxt" id="hud-txt"></span>
          <div class="hud-seg" id="hud-seg" style="display:none"></div>
          <button id="hud-btn"></button>
        </div>

        <!-- Lightweight Hover Tooltip -->
        <div id="hover-tip"></div>

        <!-- Floating Canvas Controls -->
        <div id="ctl">
          <button id="zIn" title="Zoom in (+)">+</button>
          <button id="zOut" title="Zoom out (−)">−</button>
          <button id="zFit" title="Fit view">⤢</button>
          <button id="hand" title="Toggle Rotate / Pan tool">🔄</button>
          <button id="zReset" title="Reset view (Esc)">⊙</button>
        </div>

        <!-- Subtle Footer Hint & Engine Telemetry -->
        <div id="canvas-footer">
          <div class="footer-pill" id="canvas-hint-pill">
            Drag = Rotate · Shift+Drag / ✋ = Pan · Scroll = Zoom · Click = Inspect / Drill-In · Esc = Reset
          </div>
          <div class="footer-pill" id="perf-pill">
            Canvas 2D · <b>60 fps</b>
          </div>
        </div>
      </section>

      <!-- Paginated ER Cards View (Zero DOM cost until opened) -->
      <section id="cards-wrap">
        <div id="cards"></div>
        <div id="cards-pager"></div>
      </section>

      <!-- Sleek Slide-In Table Inspector Drawer -->
      <aside id="detail" aria-label="Table Inspector">
        <div class="detail-header">
          <div class="detail-title-row">
            <div class="detail-title-wrap">
              <h2 id="tname">table_name</h2>
              <div id="tsrcc" class="srchip"><span class="sdot"></span><span id="tsrcc-txt"></span></div>
            </div>
            <button id="closeDetail" title="Close inspector &amp; exit focus (Esc)">×</button>
          </div>
          <div class="detail-stats" id="tstats"></div>
          <div class="detail-mode-bar" id="detail-mode-bar"></div>
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
       PASTE SQL & 8,000-TABLE STRESS BENCHMARK MODAL
       ===================================================================== -->
  <div id="sqlModal" role="dialog" aria-modal="true" aria-labelledby="sqlModalTitle">
    <div class="modal-box">
      <div class="modal-head">
        <h3 id="sqlModalTitle">⚡ Live SQL DDL Parser &amp; High-Scale Benchmark</h3>
        <button id="closeSqlBtn" class="btn">×</button>
      </div>
      <div class="modal-body">
        <div class="modal-row">
          <label for="sqlSourceName">Source Label:</label>
          <input id="sqlSourceName" class="modal-input" type="text" value="custom_schema.sql" placeholder="e.g. billing.sql">
          <button id="loadSampleSqlBtn" class="btn">Sample SQL</button>
          <button id="bench8kBtn" class="btn galaxy-active" title="Simulate 67 DBs, ~8,300 tables &amp; ~22,000 relations">🚀 Simulate 8,300 Tables (67 DBs)</button>
          <button id="restoreDefaultBtn" class="btn">4-DB Demo</button>
        </div>
        <textarea id="sqlInput" class="modal-textarea" placeholder="-- Paste CREATE TABLE statements here...
-- Supports -- comments, block comments, inline REFERENCES, and FOREIGN KEY (...) REFERENCES ...

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
          Engine automatically switches to <b>DB Galaxy</b> for huge multi-DB schemas (&gt;600 tables).
        </span>
        <div class="btn-group">
          <button id="sqlAddBtn" class="btn active">＋ Add to Current</button>
          <button id="sqlReplaceBtn" class="btn primary-sql">↻ Replace Schema</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast Notification -->
  <div id="toast"></div>

  <!-- =====================================================================
       HIGH-SCALE 3D CANVAS ENGINE (ZERO DEPENDENCIES)
       ===================================================================== -->
  <script>
  (function () {
    'use strict';

    /* =====================================================================
       1. DATA INJECTION HOOK & DEFAULT DEMO DATASET
       For Python integration: replace `null` below with `__DATA__`
       ===================================================================== */
    const INJECTED_DATA = null;

    const DEFAULT_DATA = [
      // --- 1. core_auth.sql ---
      { name: 'tenants', source: 'core_auth.sql', columns: ['id', 'slug', 'name', 'plan_tier', 'is_active', 'created_at'], fks: [] },
      { name: 'users', source: 'core_auth.sql', columns: ['id', 'tenant_id', 'manager_id', 'email', 'full_name', 'status', 'last_login_at'], fks: [{ column: 'tenant_id', ref_table: 'tenants' }, { column: 'manager_id', ref_table: 'users' }] },
      { name: 'roles', source: 'core_auth.sql', columns: ['id', 'code', 'title', 'permissions_json'], fks: [] },
      { name: 'user_roles', source: 'core_auth.sql', columns: ['id', 'user_id', 'role_id', 'assigned_at'], fks: [{ column: 'user_id', ref_table: 'users' }, { column: 'role_id', ref_table: 'roles' }] },
      { name: 'audit_logs', source: 'core_auth.sql', columns: ['id', 'tenant_id', 'actor_id', 'action', 'entity_type', 'entity_id', 'created_at'], fks: [{ column: 'actor_id', ref_table: 'users' }, { column: 'tenant_id', ref_table: 'tenants' }] },

      // --- 2. accounting_ledger.sql ---
      { name: 'accounts', source: 'accounting_ledger.sql', columns: ['id', 'tenant_id', 'parent_id', 'code', 'name', 'account_type', 'currency'], fks: [{ column: 'parent_id', ref_table: 'accounts' }, { column: 'tenant_id', ref_table: 'tenants' }] },
      { name: 'fiscal_years', source: 'accounting_ledger.sql', columns: ['id', 'tenant_id', 'label', 'start_date', 'end_date', 'is_closed'], fks: [{ column: 'tenant_id', ref_table: 'tenants' }] },
      { name: 'cost_centers', source: 'accounting_ledger.sql', columns: ['id', 'parent_id', 'code', 'name', 'department'], fks: [{ column: 'parent_id', ref_table: 'cost_centers' }] },
      { name: 'journal_entries', source: 'accounting_ledger.sql', columns: ['id', 'fiscal_year_id', 'entry_no', 'entry_date', 'memo', 'created_by', 'approved_by', 'posted_at'], fks: [{ column: 'fiscal_year_id', ref_table: 'fiscal_years' }, { column: 'created_by', ref_table: 'users' }, { column: 'approved_by', ref_table: 'users' }] },
      { name: 'journal_lines', source: 'accounting_ledger.sql', columns: ['id', 'entry_id', 'account_id', 'cost_center_id', 'debit', 'credit', 'description'], fks: [{ column: 'entry_id', ref_table: 'journal_entries' }, { column: 'account_id', ref_table: 'accounts' }, { column: 'cost_center_id', ref_table: 'cost_centers' }] },
      { name: 'budgets', source: 'accounting_ledger.sql', columns: ['id', 'fiscal_year_id', 'account_id', 'cost_center_id', 'allocated_amount', 'revised_amount'], fks: [{ column: 'cost_center_id', ref_table: 'cost_centers' }, { column: 'account_id', ref_table: 'accounts' }, { column: 'fiscal_year_id', ref_table: 'fiscal_years' }] },

      // --- 3. sales_pos.sql ---
      { name: 'customers', source: 'sales_pos.sql', columns: ['id', 'tenant_id', 'receivable_account_id', 'name', 'tax_id', 'phone', 'credit_limit'], fks: [{ column: 'tenant_id', ref_table: 'tenants' }, { column: 'receivable_account_id', ref_table: 'accounts' }] },
      { name: 'pos_terminals', source: 'sales_pos.sql', columns: ['id', 'branch_id', 'terminal_code', 'register_name', 'is_online'], fks: [{ column: 'branch_id', ref_table: 'warehouses' }] },
      { name: 'invoices', source: 'sales_pos.sql', columns: ['id', 'invoice_no', 'customer_id', 'terminal_id', 'journal_entry_id', 'cashier_id', 'total_amount', 'issued_at'], fks: [{ column: 'customer_id', ref_table: 'customers' }, { column: 'terminal_id', ref_table: 'pos_terminals' }, { column: 'journal_entry_id', ref_table: 'journal_entries' }, { column: 'cashier_id', ref_table: 'users' }] },
      { name: 'invoice_items', source: 'sales_pos.sql', columns: ['id', 'invoice_id', 'product_id', 'qty', 'unit_price', 'discount', 'line_total'], fks: [{ column: 'invoice_id', ref_table: 'invoices' }, { column: 'product_id', ref_table: 'products' }] },
      { name: 'payments', source: 'sales_pos.sql', columns: ['id', 'invoice_id', 'account_id', 'method', 'amount', 'paid_at'], fks: [{ column: 'invoice_id', ref_table: 'invoices' }, { column: 'account_id', ref_table: 'accounts' }] },

      // --- 4. inventory.sqlite3 ---
      { name: 'warehouses', source: 'inventory.sqlite3', columns: ['id', 'code', 'name', 'city', 'manager_id', 'capacity_sqm'], fks: [{ column: 'manager_id', ref_table: 'users' }] },
      { name: 'categories', source: 'inventory.sqlite3', columns: ['id', 'parent_id', 'name', 'slug'], fks: [{ column: 'parent_id', ref_table: 'categories' }] },
      { name: 'products', source: 'inventory.sqlite3', columns: ['id', 'sku', 'name', 'category_id', 'expense_account_id', 'unit_cost', 'sale_price'], fks: [{ column: 'category_id', ref_table: 'categories' }, { column: 'expense_account_id', ref_table: 'accounts' }] },
      { name: 'stock_levels', source: 'inventory.sqlite3', columns: ['id', 'warehouse_id', 'product_id', 'qty_on_hand', 'reorder_point', 'updated_at'], fks: [{ column: 'warehouse_id', ref_table: 'warehouses' }, { column: 'product_id', ref_table: 'products' }] },
      { name: 'stock_moves', source: 'inventory.sqlite3', columns: ['id', 'product_id', 'from_warehouse_id', 'to_warehouse_id', 'created_by', 'quantity', 'moved_at'], fks: [{ column: 'product_id', ref_table: 'products' }, { column: 'from_warehouse_id', ref_table: 'warehouses' }, { column: 'to_warehouse_id', ref_table: 'warehouses' }, { column: 'created_by', ref_table: 'users' }] },
      { name: 'suppliers', source: 'inventory.sqlite3', columns: ['id', 'name', 'contact_email', 'payable_account_id', 'lead_time_days'], fks: [{ column: 'payable_account_id', ref_table: 'accounts' }] }
    ];

    /* =====================================================================
       2. STATE, CONSTANTS & PALETTE
       ===================================================================== */
    const PALETTE = [
      '#3b82f6', '#22c55e', '#a855f7', '#06b6d4',
      '#f97316', '#ec4899', '#eab308', '#14b8a6',
      '#6366f1', '#10b981', '#f43f5e', '#0ea5e9'
    ];

    const SPHERE_RADIUS = 275;
    const HUGE_TABLE_THRESHOLD = 550;
    const CARDS_PAGE_SIZE = 60;
    const SIDE_CHUNK_SIZE = 80;

    let data = Array.isArray(INJECTED_DATA) && INJECTED_DATA.length
      ? INJECTED_DATA
      : cloneData(DEFAULT_DATA);

    let sources = [];
    let sColor = {};
    let sourceTableMap = {}; // source -> array of tables

    // Table-level graph structures
    let nodes = [];
    let byKey = {};
    let byKeyLower = {};
    let byNameLower = {};
    let links = [];
    let pairs = [];
    let incomingByKey = {};
    let hasCross = false;

    // Database Galaxy structures (1 orb per database source)
    let galaxyNodes = [];
    let galaxyLinks = [];
    let galaxyBySource = {};

    // View & Interaction State
    let mode = 'graph';          // 'galaxy' | 'graph' | 'cards'
    let selectedKey = null;      // Focused table key in 'graph' or highlighted in 'galaxy'
    let focusIsolate = true;     // In 'graph' mode with selectedKey: true = Sunflower Focus, false = Full 3D Sphere
    let hoveredItem = null;      // Hovered table node or galaxy node
    let soloSource = null;       // Soloed DB source
    let disabledSources = new Set();
    let labelMode = 'smart';     // 'smart' | 'all' | 'off'
    let showEdges = true;
    let autoSpin = true;
    let tool = 'rotate';         // 'rotate' | 'pan'
    let searchQuery = '';

    // Camera & Smooth Transition State
    let rot = { rx: -0.26, ry: 0.52 };
    let zoom = 1;
    let pan = { x: 0, y: 0 };
    let anim = null;
    let needsRender = true;
    let lastFrameTime = performance.now();

    // Canvas & Telemetry
    const canvas = document.getElementById('graphCanvas');
    const ctx = canvas.getContext('2d', { alpha: false });
    let cw = 800, ch = 520, dpr = 1;
    let stars = [];
    let lastProjectedItems = []; // For fast O(N) screen-space hit-testing
    let fpsFrames = 0, fpsLastTime = performance.now(), currentFps = 60;

    // DOM References
    const sideEl = document.getElementById('side');
    const sideListEl = document.getElementById('side-list');
    const sideCntEl = document.getElementById('side-cnt');
    const foldBtn = document.getElementById('foldBtn');
    const detailEl = document.getElementById('detail');
    const cardsWrapEl = document.getElementById('cards-wrap');
    const cardsEl = document.getElementById('cards');
    const cardsPagerEl = document.getElementById('cards-pager');
    const graphWrapEl = document.getElementById('graph-wrap');
    const stageHudEl = document.getElementById('stage-hud');
    const hudDot = document.getElementById('hud-dot');
    const hudTxt = document.getElementById('hud-txt');
    const hudSegEl = document.getElementById('hud-seg');
    const hudBtn = document.getElementById('hud-btn');
    const detailModeBarEl = document.getElementById('detail-mode-bar');
    const hoverTipEl = document.getElementById('hover-tip');
    const perfPillEl = document.getElementById('perf-pill');
    const searchInput = document.getElementById('q');
    const searchClear = document.getElementById('qClear');

    let cardsRenderedCount = 0;
    let cardsFilteredList = [];
    let toastTimer = null;
    let searchDebounceTimer = null;

    /* =====================================================================
       3. HELPERS
       ===================================================================== */
    function cloneData(arr) {
      return arr.map(t => ({
        name: t.name,
        source: t.source,
        columns: t.columns.slice(),
        fks: t.fks.map(f => ({ column: f.column, ref_table: f.ref_table }))
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

    function toast(msg) {
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.classList.add('show');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        t.classList.remove('show');
        toastTimer = null;
      }, 2300);
    }

    function easeOutCubic(t) {
      return 1 - Math.pow(1 - t, 3);
    }

    function matchTable(t, q) {
      if (!q) return true;
      if (t._nameLower.indexOf(q) >= 0) return true;
      if (t._srcLower.indexOf(q) >= 0) return true;
      return t._colsLower.indexOf(q) >= 0;
    }

    function isSourceVisible(src) {
      return !disabledSources.has(src);
    }

    function initStars() {
      stars = [];
      let seed = 1337;
      const rand = () => {
        seed = (seed * 16807) % 2147483647;
        return (seed - 1) / 2147483646;
      };
      for (let i = 0; i < 80; i++) {
        stars.push({
          nx: rand(),
          ny: rand(),
          r: 0.6 + rand() * 1.35,
          a: 0.08 + rand() * 0.28
        });
      }
    }

    /* =====================================================================
       4. DATASET INDEXING (TABLE GRAPH + DATABASE GALAXY)
       Runs in < 40ms even for 10,000+ tables and 25,000+ relations
       ===================================================================== */
    function rebuildDataset(autoSelectMode) {
      sources = [];
      sourceTableMap = {};
      data.forEach(t => {
        if (!sourceTableMap[t.source]) {
          sources.push(t.source);
          sourceTableMap[t.source] = [];
        }
        sourceTableMap[t.source].push(t);
      });

      // Sort sources by table count descending so major DBs appear first
      sources.sort((a, b) => sourceTableMap[b].length - sourceTableMap[a].length);

      sColor = {};
      sources.forEach((s, i) => {
        sColor[s] = PALETTE[i % PALETTE.length];
      });

      // Build Fibonacci Sphere coordinates for all tables
      const N = Math.max(1, data.length);
      nodes = new Array(data.length);
      byKey = {};
      byKeyLower = {};
      byNameLower = {};
      incomingByKey = {};

      for (let i = 0; i < data.length; i++) {
        const t = data[i];
        const phi = Math.acos(1 - 2 * (i + 0.5) / N);
        const th = Math.PI * (1 + Math.sqrt(5)) * i;
        const sx = Math.sin(phi) * Math.cos(th);
        const sy = Math.cos(phi);
        const sz = Math.sin(phi) * Math.sin(th);
        const k = keyOf(t);

        const node = {
          kind: 'table',
          name: t.name,
          source: t.source,
          columns: t.columns,
          fks: t.fks,
          key: k,
          color: sColor[t.source] || '#3b82f6',
          _nameLower: t.name.toLowerCase(),
          _srcLower: t.source.toLowerCase(),
          _colsLower: t.columns.join('\u0001').toLowerCase(),
          sph: { x: sx, y: sy, z: sz },
          cur: { x: sx, y: sy, z: sz },
          start: { x: sx, y: sy, z: sz },
          tgt: { x: sx, y: sy, z: sz },
          alpha: 1,
          startAlpha: 1,
          tgtAlpha: 1,
          deg: 0
        };
        nodes[i] = node;
        byKey[k] = node;
        byKeyLower[(t.source + '|' + t.name).toLowerCase()] = node;
        if (!(node._nameLower in byNameLower)) byNameLower[node._nameLower] = node;
        incomingByKey[k] = [];
      }

      // Resolve Foreign Keys (same-DB first, then cross-DB)
      links = [];
      const pairMap = {};
      pairs = [];
      const galaxyEdgeMap = {};
      const intraFkBySource = {};

      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        const fks = n.fks;
        for (let j = 0; j < fks.length; j++) {
          const fk = fks[j];
          const refLower = fk.ref_table.toLowerCase();
          const to = byKeyLower[(n.source + '|' + fk.ref_table).toLowerCase()] || byNameLower[refLower];
          if (!to) continue;

          const cross = n.source !== to.source;
          const self = n.key === to.key;
          const linkObj = { s: n, t: to, fk: fk, cross: cross, self: self };
          links.push(linkObj);
          incomingByKey[to.key].push(linkObj);
          n.deg++;
          to.deg++;

          // Aggregate pair edge
          const pk = n.key < to.key ? (n.key + '||' + to.key) : (to.key + '||' + n.key);
          let pr = pairMap[pk];
          if (!pr) {
            pr = {
              s: n.key <= to.key ? n : to,
              t: n.key <= to.key ? to : n,
              out: [],
              inn: [],
              cross: cross,
              self: self
            };
            pairMap[pk] = pr;
            pairs.push(pr);
          }
          if (cross) pr.cross = true;
          if (n.key === pr.s.key) {
            if (pr.out.indexOf(fk.column) < 0) pr.out.push(fk.column);
          } else {
            if (pr.inn.indexOf(fk.column) < 0) pr.inn.push(fk.column);
          }

          // Aggregate DB-to-DB links for Galaxy View
          if (cross) {
            const gk = n.source < to.source ? (n.source + '||' + to.source) : (to.source + '||' + n.source);
            if (!galaxyEdgeMap[gk]) {
              galaxyEdgeMap[gk] = { sSrc: n.source < to.source ? n.source : to.source, tSrc: n.source < to.source ? to.source : n.source, count: 0 };
            }
            galaxyEdgeMap[gk].count++;
          } else {
            intraFkBySource[n.source] = (intraFkBySource[n.source] || 0) + 1;
          }
        }
      }

      hasCross = links.some(l => l.cross);

      // Build Database Galaxy Nodes (Fibonacci Sphere of DB orbs)
      const S = Math.max(1, sources.length);
      galaxyNodes = [];
      galaxyBySource = {};
      for (let i = 0; i < sources.length; i++) {
        const s = sources[i];
        const phi = Math.acos(1 - 2 * (i + 0.5) / S);
        const th = Math.PI * (1 + Math.sqrt(5)) * i;
        const tCount = sourceTableMap[s].length;
        const gNode = {
          kind: 'db',
          source: s,
          label: srcLabel(s),
          tableCount: tCount,
          intraFks: intraFkBySource[s] || 0,
          crossFks: 0,
          color: sColor[s],
          radiusBase: Math.min(30, 10 + Math.log2(tCount + 1) * 3.1),
          cur: { x: Math.sin(phi) * Math.cos(th), y: Math.cos(phi), z: Math.sin(phi) * Math.sin(th) }
        };
        galaxyNodes.push(gNode);
        galaxyBySource[s] = gNode;
      }

      galaxyLinks = [];
      Object.keys(galaxyEdgeMap).forEach(k => {
        const e = galaxyEdgeMap[k];
        const sN = galaxyBySource[e.sSrc];
        const tN = galaxyBySource[e.tSrc];
        if (sN && tN) {
          sN.crossFks += e.count;
          tN.crossFks += e.count;
          galaxyLinks.push({ s: sN, t: tN, count: e.count });
        }
      });

      // Automatically choose the cleanest view for huge multi-DB corpora
      if (autoSelectMode) {
        if (data.length > HUGE_TABLE_THRESHOLD && sources.length > 1) {
          mode = 'galaxy';
          toast(`Large schema (${data.length.toLocaleString()} tables · ${sources.length} DBs) → opened in DB Galaxy mode`);
        } else {
          mode = 'graph';
        }
      }

      // Refresh UI
      buildLegend();
      buildSide(searchQuery);
      syncModeUI();
      updateStageHud();
      if (mode === 'cards') buildCards(searchQuery);
      recomputeVisibleSpherePositions();
      needsRender = true;
    }

    /* Redistribute currently visible tables cleanly on the sphere
       (critical when soloing 1 DB out of 67 so its tables fill the whole sphere!) */
    function recomputeVisibleSpherePositions() {
      const vis = nodes.filter(n => isSourceVisible(n.source));
      const N = Math.max(1, vis.length);
      for (let i = 0; i < vis.length; i++) {
        const phi = Math.acos(1 - 2 * (i + 0.5) / N);
        const th = Math.PI * (1 + Math.sqrt(5)) * i;
        const n = vis[i];
        n.sph.x = Math.sin(phi) * Math.cos(th);
        n.sph.y = Math.cos(phi);
        n.sph.z = Math.sin(phi) * Math.sin(th);
        if (!selectedKey || !focusIsolate) {
          n.cur.x = n.sph.x;
          n.cur.y = n.sph.y;
          n.cur.z = n.sph.z;
          n.alpha = 1;
        }
      }
      nodes.forEach(n => {
        if (!isSourceVisible(n.source)) {
          n.alpha = 0;
          n.tgtAlpha = 0;
        }
      });
    }

    /* Compute exact camera rotation (rx, ry) so unit point (x, y, z) faces straight toward the camera */
    function orientCameraToPoint(x, y, z) {
      const len = Math.hypot(x, y, z) || 1;
      const nx = x / len, ny = y / len, nz = z / len;
      const h = Math.hypot(nx, nz);
      const targetRy = Math.atan2(-nx, nz);
      const targetRx = Math.atan2(ny, h);
      // Choose shortest angular path from current rot.ry
      const twoPi = Math.PI * 2;
      const deltaY = ((targetRy - rot.ry + Math.PI) % twoPi + twoPi) % twoPi - Math.PI;
      return {
        rx: Math.max(-Math.PI / 2, Math.min(Math.PI / 2, targetRx)),
        ry: rot.ry + deltaY
      };
    }

    /* Return all databases connected to a specific table (via outgoing & incoming FKs) */
    function getTableGalaxyContext(key) {
      const t = byKey[key];
      if (!t) return null;
      const linkedSources = new Set([t.source]);
      const crossPairs = {}; // otherSource -> { outCols: [], inCols: [], tables: Set }

      for (let i = 0; i < t.fks.length; i++) {
        const fk = t.fks[i];
        const to = byKeyLower[(t.source + '|' + fk.ref_table).toLowerCase()] || byNameLower[fk.ref_table.toLowerCase()];
        if (!to) continue;
        linkedSources.add(to.source);
        if (to.source !== t.source) {
          const entry = crossPairs[to.source] || (crossPairs[to.source] = { out: [], inn: [] });
          entry.out.push(fk.column + '→' + to.name);
        }
      }
      const inc = incomingByKey[key] || [];
      for (let i = 0; i < inc.length; i++) {
        const l = inc[i];
        linkedSources.add(l.s.source);
        if (l.s.source !== t.source) {
          const entry = crossPairs[l.s.source] || (crossPairs[l.s.source] = { out: [], inn: [] });
          entry.inn.push(l.s.name + '.' + l.fk.column);
        }
      }
      return { table: t, homeSource: t.source, linkedSources: linkedSources, crossPairs: crossPairs };
    }

    /* =====================================================================
       5. 3D PROJECTION & CAMERA ANIMATIONS
       ===================================================================== */
    function project3D(x0, y0, z0) {
      const cosY = Math.cos(rot.ry), sinY = Math.sin(rot.ry);
      const cosX = Math.cos(rot.rx), sinX = Math.sin(rot.rx);

      const x1 = x0 * cosY + z0 * sinY;
      const z1 = -x0 * sinY + z0 * cosY;
      const y2 = y0 * cosX - z1 * sinX;
      const z2 = y0 * sinX + z1 * cosX;

      const f = 2.25 / (2.25 - z2);
      const wx = x1 * SPHERE_RADIUS * f;
      const wy = y2 * SPHERE_RADIUS * f;

      return {
        sx: cw * 0.5 + (wx - pan.x) * zoom,
        sy: ch * 0.5 + (wy - pan.y) * zoom,
        z: z2,
        f: f
      };
    }

    function fitBaseZoom() {
      return Math.max(0.5, Math.min(1.25, Math.min(cw, ch) / 700));
    }

    function startTransition(targetRot, targetZoom, targetPan, durationMs) {
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        n.start.x = n.cur.x;
        n.start.y = n.cur.y;
        n.start.z = n.cur.z;
        n.startAlpha = n.alpha;
      }
      anim = {
        startTime: performance.now(),
        duration: durationMs || 400,
        startRot: { rx: rot.rx, ry: rot.ry },
        targetRot: { rx: targetRot.rx, ry: targetRot.ry },
        startZoom: zoom,
        targetZoom: targetZoom,
        startPan: { x: pan.x, y: pan.y },
        targetPan: { x: targetPan.x, y: targetPan.y }
      };
      needsRender = true;
    }

    function computeFocusTargets(centerKey) {
      const c = byKey[centerKey];
      if (!c) return;

      const nearSet = new Set([centerKey]);
      // Outgoing FKs
      for (let i = 0; i < c.fks.length; i++) {
        const fk = c.fks[i];
        const to = byKeyLower[(c.source + '|' + fk.ref_table).toLowerCase()] || byNameLower[fk.ref_table.toLowerCase()];
        if (to && isSourceVisible(to.source)) nearSet.add(to.key);
      }
      // Incoming FKs
      const inc = incomingByKey[centerKey] || [];
      for (let i = 0; i < inc.length; i++) {
        if (isSourceVisible(inc[i].s.source)) nearSet.add(inc[i].s.key);
      }

      const neighbors = [];
      nearSet.forEach(k => {
        if (k !== centerKey && byKey[k]) neighbors.push(byKey[k]);
      });
      neighbors.sort((a, b) => a.name.localeCompare(b.name));

      const N = neighbors.length;
      const GA = Math.PI * (1 + Math.sqrt(5));
      const FZ = 0.35;

      c.tgt.x = 0;
      c.tgt.y = 0;
      c.tgt.z = FZ;
      c.tgtAlpha = 1;

      for (let i = 0; i < N; i++) {
        const n = neighbors[i];
        let r, angle;
        if (N <= 8) {
          r = N === 1 ? 0.62 : 0.72;
          angle = -Math.PI / 2 + (2 * Math.PI * i) / N;
        } else {
          r = 0.34 + 0.76 * Math.sqrt((i + 0.5) / N);
          angle = -Math.PI / 2 + GA * i;
        }
        n.tgt.x = r * Math.cos(angle);
        n.tgt.y = r * Math.sin(angle);
        n.tgt.z = FZ;
        n.tgtAlpha = 1;
      }

      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (!nearSet.has(n.key)) {
          n.tgt.x = n.sph.x * 1.15;
          n.tgt.y = n.sph.y * 1.15;
          n.tgt.z = n.sph.z * 1.15;
          n.tgtAlpha = 0;
        }
      }

      const maxExtent = SPHERE_RADIUS * (2.25 / (2.25 - FZ)) * (N <= 8 ? 1.05 : 1.3);
      const fitZoom = Math.max(0.45, Math.min(1.6, (0.86 * Math.min(cw, ch)) / (2 * maxExtent)));
      startTransition({ rx: 0, ry: 0 }, fitZoom, { x: 0, y: 0 }, 420);
    }

    function computeSphereTargets(focusNodeKey) {
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        n.tgt.x = n.sph.x;
        n.tgt.y = n.sph.y;
        n.tgt.z = n.sph.z;
        n.tgtAlpha = isSourceVisible(n.source) ? 1 : 0;
      }
      const focusNode = focusNodeKey ? byKey[focusNodeKey] : null;
      const targetRot = focusNode
        ? orientCameraToPoint(focusNode.sph.x, focusNode.sph.y, focusNode.sph.z)
        : { rx: -0.26, ry: 0.52 };
      startTransition(targetRot, fitBaseZoom(), { x: 0, y: 0 }, 400);
    }

    /* =====================================================================
       6. HIGH-SPEED CANVAS 2D RENDERER WITH COLLISION-FREE LABELS
       Handles 10,000+ tables & 25,000+ relations smoothly at 60fps
       ===================================================================== */
    function resizeCanvas() {
      const rect = graphWrapEl.getBoundingClientRect();
      cw = Math.max(100, Math.floor(rect.width));
      ch = Math.max(100, Math.floor(rect.height));
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(cw * dpr);
      canvas.height = Math.floor(ch * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      needsRender = true;
    }

    function drawBackground() {
      // Deep radial space gradient
      const grad = ctx.createRadialGradient(cw * 0.5, ch * 0.45, 20, cw * 0.5, ch * 0.48, Math.max(cw, ch) * 0.72);
      grad.addColorStop(0, '#112140');
      grad.addColorStop(0.6, '#081021');
      grad.addColorStop(1, '#040812');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, cw, ch);

      // Subtle starfield
      ctx.fillStyle = '#93c5fd';
      for (let i = 0; i < stars.length; i++) {
        const s = stars[i];
        ctx.globalAlpha = s.a;
        ctx.beginPath();
        ctx.arc(s.nx * cw, s.ny * ch, s.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      // Subtle orbital reference rings centered on planet
      const cx = cw * 0.5 - pan.x * zoom;
      const cy = ch * 0.5 - pan.y * zoom;
      ctx.save();
      ctx.setLineDash([4, 7]);
      ctx.lineWidth = 1;
      [205, 305, 420].forEach((r, idx) => {
        ctx.strokeStyle = idx === 1 ? 'rgba(96, 165, 250, 0.11)' : 'rgba(96, 165, 250, 0.055)';
        ctx.beginPath();
        ctx.arc(cx, cy, r * zoom, 0, Math.PI * 2);
        ctx.stroke();
      });
      ctx.restore();
    }

    /* Fast screen-space bounding-box collision filter so labels NEVER pile into unreadable soup */
    function createLabelPlacer() {
      const placed = [];
      return function tryPlace(x, y, w, h, force) {
        const x1 = x - w * 0.5 - 3;
        const y1 = y - h - 2;
        const x2 = x + w * 0.5 + 3;
        const y2 = y + 3;
        if (!force) {
          if (x2 < 0 || x1 > cw || y2 < 0 || y1 > ch) return false;
          for (let i = 0; i < placed.length; i++) {
            const b = placed[i];
            if (x1 < b[2] && x2 > b[0] && y1 < b[3] && y2 > b[1]) return false;
          }
        }
        placed.push([x1, y1, x2, y2]);
        return true;
      };
    }

    function drawArrowHead(x, y, angle, size, color) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x - size * Math.cos(angle - 0.42), y - size * Math.sin(angle - 0.42));
      ctx.lineTo(x - size * Math.cos(angle + 0.42), y - size * Math.sin(angle + 0.42));
      ctx.closePath();
      ctx.fill();
    }

    function drawLabelPill(text, x, y, color, fontSize, bold) {
      ctx.font = (bold ? '700 ' : '600 ') + fontSize + 'px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'alphabetic';
      ctx.lineWidth = 4;
      ctx.lineJoin = 'round';
      ctx.strokeStyle = 'rgba(5, 10, 20, 0.92)';
      ctx.strokeText(text, x, y);
      ctx.fillStyle = color;
      ctx.fillText(text, x, y);
    }

    /* --- RENDER MODE A: DATABASE GALAXY (1 Orb per DB, silky-smooth for 67+ DBs) ---
       When a single DB is drilled into (solo) or a table is focused, THAT DB's TABLES
       become the planets instead, so you never bounce back to the 67-DB overview. */
    function galaxyFocusDb() {
      const t = selectedKey ? byKey[selectedKey] : null;
      const src = soloSource || (t ? t.source : null);
      return (src && isSourceVisible(src)) ? src : null;
    }

    /* Layout knobs for a focused DB's table galaxy. A 200-table DB cannot
       reuse the 67-orb overview density: more planets => a wider orbit
       (capped so the perspective divide stays stable) plus a proportionally
       smaller fitted zoom and shrunken planet radii, so big databases read
       as a spread-out galaxy instead of one clumped ball of touching orbs. */
    function tableGalaxySpread(count) {
      return Math.min(1.42, Math.max(1, Math.sqrt(count / 70)));
    }
    function fitTableGalaxyZoom(source) {
      const src = source || galaxyFocusDb();
      if (!src || !sourceTableMap[src]) return fitBaseZoom();
      return Math.max(0.34, fitBaseZoom() / tableGalaxySpread(sourceTableMap[src].length));
    }

    /* Undo any 3D-focus isolation before drawing a table galaxy: every visible
       table goes back to its sphere spot at full alpha, ready to be a planet. */
    function restoreGalaxyTablePositions() {
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (isSourceVisible(n.source)) {
          n.cur.x = n.sph.x;
          n.cur.y = n.sph.y;
          n.cur.z = n.sph.z;
          n.alpha = 1;
          n.tgtAlpha = 1;
        }
      }
    }

    function renderGalaxy() {
      const focusSrc = galaxyFocusDb();
      if (focusSrc) {
        renderTableGalaxy(focusSrc);
        return;
      }
      const projList = [];
      const projBySource = {};
      const hasSearch = searchQuery.length > 0;

      // Active table or active solo DB context (preserved when switching into DB Galaxy!)
      const tableCtx = selectedKey ? getTableGalaxyContext(selectedKey) : null;
      const activeDb = tableCtx ? tableCtx.homeSource : soloSource;
      const hasActiveCtx = Boolean(activeDb);

      // Build set of DBs connected to activeDb when soloSource is set (without a table)
      let activeLinkedSources = null;
      if (tableCtx) {
        activeLinkedSources = tableCtx.linkedSources;
      } else if (activeDb) {
        activeLinkedSources = new Set([activeDb]);
        for (let i = 0; i < galaxyLinks.length; i++) {
          const gl = galaxyLinks[i];
          if (gl.s.source === activeDb) activeLinkedSources.add(gl.t.source);
          else if (gl.t.source === activeDb) activeLinkedSources.add(gl.s.source);
        }
      }

      for (let i = 0; i < galaxyNodes.length; i++) {
        const g = galaxyNodes[i];
        // Show all DBs in Galaxy when soloSource/table context is active so the full galaxy surrounds it
        if (!soloSource && !selectedKey && !isSourceVisible(g.source)) continue;
        const p = project3D(g.cur.x, g.cur.y, g.cur.z);
        const isPrimary = g.source === activeDb;
        const boost = isPrimary ? 1.24 : 1;
        const r = Math.max(5, g.radiusBase * boost * Math.max(0.55, p.f) * Math.min(1.6, Math.sqrt(zoom)));
        const matches = hasSearch && (
          g.source.toLowerCase().indexOf(searchQuery) >= 0 ||
          sourceTableMap[g.source].some(t => t.name.toLowerCase().indexOf(searchQuery) >= 0)
        );
        const isLinked = activeLinkedSources ? activeLinkedSources.has(g.source) : false;
        const item = { node: g, sx: p.sx, sy: p.sy, z: p.z, f: p.f, r: r, matches: matches, isPrimary: isPrimary, isLinked: isLinked };
        projList.push(item);
        projBySource[g.source] = item;
      }

      // 1. Draw cross-DB aggregated links
      if (showEdges && galaxyLinks.length) {
        ctx.save();
        for (let i = 0; i < galaxyLinks.length; i++) {
          const gl = galaxyLinks[i];
          const a = projBySource[gl.s.source];
          const b = projBySource[gl.t.source];
          if (!a || !b) continue;

          const isTableLink = tableCtx && (
            (gl.s.source === tableCtx.homeSource && Boolean(tableCtx.crossPairs[gl.t.source])) ||
            (gl.t.source === tableCtx.homeSource && Boolean(tableCtx.crossPairs[gl.s.source]))
          );
          const isDbLink = !tableCtx && activeDb && (gl.s.source === activeDb || gl.t.source === activeDb);
          const isHoverLink = hoveredItem && hoveredItem.kind === 'db' &&
            (hoveredItem.source === gl.s.source || hoveredItem.source === gl.t.source);
          const isHot = isTableLink || isDbLink || isHoverLink;
          const depth = ((a.z + b.z) * 0.5 + 1) * 0.5;

          ctx.beginPath();
          ctx.moveTo(a.sx, a.sy);
          ctx.lineTo(b.sx, b.sy);
          if (isHot) {
            ctx.strokeStyle = isTableLink ? 'rgba(249, 115, 22, 0.94)' : 'rgba(251, 191, 36, 0.88)';
            ctx.lineWidth = isTableLink ? 3.2 : Math.min(4.5, 1.6 + Math.log2(gl.count + 1) * 0.65);
            ctx.setLineDash([7, 4]);
          } else {
            const dimFactor = hasActiveCtx ? 0.28 : 1;
            ctx.strokeStyle = 'rgba(251, 191, 36, ' + ((0.06 + 0.24 * depth) * dimFactor).toFixed(3) + ')';
            ctx.lineWidth = Math.min(2.4, 0.7 + Math.log2(gl.count + 1) * 0.32);
            ctx.setLineDash([5, 5]);
          }
          ctx.stroke();

          // Draw arrowheads & edge summary when a specific table links across DBs
          if (isTableLink) {
            const otherSrc = gl.s.source === tableCtx.homeSource ? gl.t.source : gl.s.source;
            const homeP = projBySource[tableCtx.homeSource];
            const otherP = projBySource[otherSrc];
            const info = tableCtx.crossPairs[otherSrc];
            if (homeP && otherP && info) {
              const dx = otherP.sx - homeP.sx;
              const dy = otherP.sy - homeP.sy;
              const dist = Math.hypot(dx, dy) || 1;
              const ang = Math.atan2(dy, dx);
              ctx.setLineDash([]);
              if (info.out.length && dist > homeP.r + otherP.r + 12) {
                drawArrowHead(otherP.sx - (dx / dist) * (otherP.r + 5), otherP.sy - (dy / dist) * (otherP.r + 5), ang, 9, '#f97316');
              }
              if (info.inn.length && dist > homeP.r + otherP.r + 12) {
                drawArrowHead(homeP.sx + (dx / dist) * (homeP.r + 5), homeP.sy + (dy / dist) * (homeP.r + 5), ang + Math.PI, 9, '#fbbf24');
              }
            }
          }
        }
        ctx.restore();
      }

      // 2. Depth-sort DB orbs back-to-front
      projList.sort((a, b) => a.z - b.z);
      lastProjectedItems = projList;

      for (let i = 0; i < projList.length; i++) {
        const it = projList[i];
        const g = it.node;
        const isHover = hoveredItem === g;
        const depthAlpha = 0.36 + 0.64 * Math.max(0, (it.z + 1) * 0.5);
        let alpha = depthAlpha;
        if (hasSearch && !it.matches && !isHover && !it.isPrimary) alpha *= 0.2;
        else if (hasActiveCtx && !it.isLinked && !isHover) alpha *= 0.24;

        ctx.save();
        ctx.globalAlpha = alpha;

        // Outer soft aura for selected/linked/hovered DB
        if (it.isPrimary) {
          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + 11, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(249, 115, 22, 0.38)';
          ctx.lineWidth = 4;
          ctx.stroke();

          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + 6, 0, Math.PI * 2);
          ctx.strokeStyle = '#f97316';
          ctx.lineWidth = 2.4;
          ctx.stroke();
        } else if (isHover || it.matches || (hasActiveCtx && it.isLinked)) {
          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + 7, 0, Math.PI * 2);
          ctx.strokeStyle = isHover ? '#f97316' : (it.matches ? '#22d3ee' : '#fbbf24');
          ctx.lineWidth = 2.1;
          ctx.stroke();
        }

        // Planet body
        const grad = ctx.createRadialGradient(
          it.sx - it.r * 0.3, it.sy - it.r * 0.3, it.r * 0.1,
          it.sx, it.sy, it.r
        );
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(0.28, it.isPrimary ? '#f97316' : g.color);
        grad.addColorStop(1, '#0b1326');

        ctx.beginPath();
        ctx.arc(it.sx, it.sy, it.r, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.lineWidth = (isHover || it.isPrimary) ? 2.4 : 1.25;
        ctx.strokeStyle = it.isPrimary ? '#ffedd5' : (isHover ? '#ffffff' : 'rgba(255,255,255,0.55)');
        ctx.stroke();

        // Table count inside big orbs
        if (it.r >= 13) {
          ctx.font = '700 ' + Math.max(9.5, Math.min(12, it.r * 0.52)).toFixed(0) + 'px Inter, system-ui, sans-serif';
          ctx.fillStyle = '#ffffff';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(g.tableCount), it.sx, it.sy + 0.5);
        }

        ctx.restore();
      }

      // 3. Collision-free labels (front-to-back priority, always prioritizing active table's DB & linked DBs)
      if (labelMode !== 'off' || hasActiveCtx) {
        const placer = createLabelPlacer();
        const maxLabels = labelMode === 'all' ? 95 : 42;
        let drawn = 0;

        const labelOrder = projList.slice().sort((a, b) => {
          const pa = (a.isPrimary ? 1000 : 0) + (a.node === hoveredItem ? 500 : 0) + (a.isLinked ? 250 : 0) + (a.matches ? 50 : 0) + a.z;
          const pb = (b.isPrimary ? 1000 : 0) + (b.node === hoveredItem ? 500 : 0) + (b.isLinked ? 250 : 0) + (b.matches ? 50 : 0) + b.z;
          return pb - pa;
        });

        for (let i = 0; i < labelOrder.length; i++) {
          const it = labelOrder[i];
          const g = it.node;
          const force = it.isPrimary || g === hoveredItem || (tableCtx && it.isLinked);
          if (!force && (labelMode === 'off' || drawn >= maxLabels)) continue;
          if (!force && labelMode === 'smart' && it.z < -0.05 && !it.matches && !it.isLinked) continue;

          const baseName = g.label.length > 26 ? g.label.slice(0, 24) + '…' : g.label;
          const txt = (it.isPrimary && tableCtx) ? `${baseName} · ★ ${tableCtx.table.name}` : baseName;
          const estW = txt.length * 6.8 + 12;
          const ly = it.sy - it.r - 8;

          if (placer(it.sx, ly, estW, 15, force)) {
            ctx.globalAlpha = force ? 1 : (0.55 + 0.45 * Math.max(0, (it.z + 1) * 0.5));
            const clr = it.isPrimary ? '#ffedd5' : (it.isLinked ? '#fde68a' : (g === hoveredItem ? '#ffedd5' : '#f1f5f9'));
            drawLabelPill(txt, it.sx, ly, clr, it.isPrimary ? 13 : (force ? 12 : 11.5), force);
            drawn++;
          }
        }
        ctx.globalAlpha = 1;
      }
    }

    /* --- RENDER MODE A2: TABLE GALAXY (a drilled DB's tables rendered as planets, FK links between them) --- */
    function renderTableGalaxy(source) {
      const projList = [];
      const pMap = {};
      const hasSearch = searchQuery.length > 0;
      const selNode = selectedKey ? byKey[selectedKey] : null;
      const hoverNode = (hoveredItem && hoveredItem.kind === 'table') ? hoveredItem : null;

      // Gather this DB's visible tables first: spread & density adapt to count
      const tNodes = [];
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (n.source === source && n.alpha > 0.02) tNodes.push(n);
      }
      const tgSpread = tableGalaxySpread(tNodes.length);
      const tgDens = Math.min(1, Math.max(0.5, Math.sqrt(90 / Math.max(1, tNodes.length))));

      let nearSet = null;
      if (selNode) {
        nearSet = new Set([selNode.key]);
        for (let i = 0; i < selNode.fks.length; i++) {
          const fk = selNode.fks[i];
          const to = byKeyLower[(selNode.source + '|' + fk.ref_table).toLowerCase()] || byNameLower[fk.ref_table.toLowerCase()];
          if (to && to.source === source && to.key !== selNode.key) nearSet.add(to.key);
        }
        const inc = incomingByKey[selNode.key] || [];
        for (let i = 0; i < inc.length; i++) {
          if (inc[i].s.source === source && inc[i].s.key !== selNode.key) nearSet.add(inc[i].s.key);
        }
      }

      for (let i = 0; i < tNodes.length; i++) {
        const n = tNodes[i];
        const p = project3D(n.cur.x * tgSpread, n.cur.y * tgSpread, n.cur.z * tgSpread);
        const matches = hasSearch && matchTable(n, searchQuery);
        const item = { node: n, sx: p.sx, sy: p.sy, z: p.z, f: p.f, r: 6, matches: matches };
        projList.push(item);
        pMap[n.key] = item;
      }

      // Planet radius by FK connectivity (degree), same sizing family as DB orbs;
      // tgDens keeps a 200+ planet DB airy instead of a clump of touching balls
      const zoomScale = Math.min(1.75, Math.max(0.55, Math.sqrt(zoom)));
      for (let i = 0; i < projList.length; i++) {
        const it = projList[i];
        const n = it.node;
        const boost = (selectedKey === n.key) ? 1.26 : 1;
        const degR = Math.min(26, (7.5 + Math.log2((n.deg || 0) + 2) * 3.2) * tgDens);
        it.r = Math.max(4.5, degR * boost * Math.max(0.55, it.f) * zoomScale);
      }

      if (showEdges) {
        const totalPairs = pairs.length;
        const stride = Math.max(1, Math.ceil(totalPairs / 2600));
        ctx.save();
        ctx.lineCap = 'round';

        // Faint internal FK links between the tables of this database
        ctx.beginPath();
        let bgCount = 0;
        for (let i = 0; i < totalPairs; i += stride) {
          const pr = pairs[i];
          if (pr.cross) continue;
          const a = pMap[pr.s.key];
          const b = pMap[pr.t.key];
          if (!a || !b) continue;
          ctx.moveTo(a.sx, a.sy);
          ctx.lineTo(b.sx, b.sy);
          bgCount++;
        }
        if (bgCount) {
          ctx.strokeStyle = 'rgba(100, 116, 139, 0.30)';
          ctx.lineWidth = 1.0;
          ctx.stroke();
        }

        // Cross-DB FKs: amber dashed stubs pointing outward (toward their far-away DBs)
        ctx.setLineDash([6, 5]);
        ctx.beginPath();
        const stubs = [];
        for (let i = 0; i < totalPairs; i++) {
          const pr = pairs[i];
          if (!pr.cross) continue;
          const homeKey = pr.s.source === source ? pr.s.key : (pr.t.source === source ? pr.t.key : null);
          if (!homeKey) continue;
          const it = pMap[homeKey];
          if (!it) continue;
          let dx = it.sx - cw * 0.5, dy = it.sy - ch * 0.5;
          const d = Math.hypot(dx, dy);
          if (d < 1) { dx = 0; dy = -1; } else { dx /= d; dy /= d; }
          stubs.push({ it: it, dx: dx, dy: dy });
          ctx.moveTo(it.sx + dx * (it.r + 3), it.sy + dy * (it.r + 3));
          ctx.lineTo(it.sx + dx * (it.r + 26), it.sy + dy * (it.r + 26));
        }
        if (stubs.length) {
          ctx.strokeStyle = 'rgba(251, 191, 36, 0.5)';
          ctx.lineWidth = 1.3;
          ctx.stroke();
          ctx.setLineDash([]);
          for (let i = 0; i < stubs.length; i++) {
            const stt = stubs[i];
            drawArrowHead(stt.it.sx + stt.dx * (stt.it.r + 29), stt.it.sy + stt.dy * (stt.it.r + 29), Math.atan2(stt.dy, stt.dx), 7, '#fbbf24');
          }
        } else {
          ctx.setLineDash([]);
        }

        // Crisp hot edges for the selected/hovered table (same style as 3D Tables)
        const focusKey = selectedKey || (hoverNode ? hoverNode.key : null);
        if (focusKey) {
          for (let i = 0; i < totalPairs; i++) {
            const pr = pairs[i];
            if (pr.s.key !== focusKey && pr.t.key !== focusKey) continue;
            const a = pMap[pr.s.key];
            const b = pMap[pr.t.key];
            if (!a || !b) continue;
            if (pr.self) {
              const loopR = a.r * 1.9;
              const sx = a.sx + a.r * 0.68;
              const sy = a.sy - a.r * 0.68;
              const ex = a.sx + a.r * 1.02;
              const ey = a.sy + a.r * 0.32;
              ctx.strokeStyle = '#22d3ee';
              ctx.lineWidth = 2.2;
              ctx.beginPath();
              ctx.moveTo(sx, sy);
              ctx.bezierCurveTo(a.sx + loopR * 1.2, a.sy - loopR * 1.5, a.sx + loopR * 1.9, a.sy + loopR * 0.4, ex, ey);
              ctx.stroke();
              drawArrowHead(ex, ey, Math.atan2(ey - (a.sy + loopR * 0.4), ex - (a.sx + loopR * 1.9)), 8, '#22d3ee');
            } else {
              const dx = b.sx - a.sx;
              const dy = b.sy - a.sy;
              const dist = Math.hypot(dx, dy) || 1;
              const padA = a.r + 4;
              const padB = b.r + 4;
              let x1 = a.sx, y1 = a.sy, x2 = b.sx, y2 = b.sy;
              if (dist > padA + padB + 6) {
                x1 = a.sx + (dx / dist) * padA;
                y1 = a.sy + (dy / dist) * padA;
                x2 = b.sx - (dx / dist) * padB;
                y2 = b.sy - (dy / dist) * padB;
              }
              const col = pr.cross ? '#fbbf24' : '#f97316';
              const totalFks = pr.out.length + pr.inn.length;
              ctx.strokeStyle = col;
              ctx.lineWidth = totalFks > 1 ? 3.2 : 2.3;
              ctx.setLineDash(pr.cross ? [7, 5] : []);
              ctx.beginPath();
              ctx.moveTo(x1, y1);
              ctx.lineTo(x2, y2);
              ctx.stroke();
              ctx.setLineDash([]);
              const ang = Math.atan2(y2 - y1, x2 - x1);
              if (pr.out.length) drawArrowHead(x2, y2, ang, 8.5, col);
              if (pr.inn.length) drawArrowHead(x1, y1, ang + Math.PI, 8.5, col);
            }
          }
        }
        ctx.restore();
      }

      // 2. Depth-sort table planets back-to-front
      projList.sort((a, b) => a.z - b.z);
      lastProjectedItems = projList;

      for (let i = 0; i < projList.length; i++) {
        const it = projList[i];
        const n = it.node;
        const isSel = selectedKey === n.key;
        const isHover = hoveredItem === n;
        const isNear = nearSet ? nearSet.has(n.key) : false;

        let alpha = 0.36 + 0.64 * Math.max(0, (it.z + 1) * 0.5);
        if (hasSearch && !it.matches && !isSel && !isHover && !isNear) alpha *= 0.18;
        else if (nearSet && !isNear && !isHover && !isSel) alpha *= 0.3;

        ctx.save();
        ctx.globalAlpha = alpha;

        if (isSel) {
          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + 11, 0, Math.PI * 2);
          ctx.strokeStyle = 'rgba(249, 115, 22, 0.38)';
          ctx.lineWidth = 4;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + 6, 0, Math.PI * 2);
          ctx.strokeStyle = '#f97316';
          ctx.lineWidth = 2.4;
          ctx.stroke();
        } else if (isHover || it.matches || isNear) {
          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + 7, 0, Math.PI * 2);
          ctx.strokeStyle = isHover ? '#f97316' : (it.matches ? '#22d3ee' : '#fbbf24');
          ctx.lineWidth = 2.1;
          ctx.stroke();
        }

        const grad = ctx.createRadialGradient(it.sx - it.r * 0.3, it.sy - it.r * 0.3, it.r * 0.1, it.sx, it.sy, it.r);
        grad.addColorStop(0, '#ffffff');
        grad.addColorStop(0.28, isSel ? '#f97316' : n.color);
        grad.addColorStop(1, '#0b1326');
        ctx.beginPath();
        ctx.arc(it.sx, it.sy, it.r, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.lineWidth = (isHover || isSel) ? 2.4 : 1.25;
        ctx.strokeStyle = isSel ? '#ffedd5' : (isHover ? '#ffffff' : 'rgba(255,255,255,0.55)');
        ctx.stroke();

        if (it.r >= 13) {
          ctx.font = '700 ' + Math.max(9.5, Math.min(12, it.r * 0.52)).toFixed(0) + 'px Inter, system-ui, sans-serif';
          ctx.fillStyle = '#ffffff';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(n.deg || 0), it.sx, it.sy + 0.5);
        }
        ctx.restore();
      }

      // 3. Collision-free labels (selected & hovered table always on top)
      if (labelMode !== 'off' || selNode || hoverNode || hasSearch) {
        const placer = createLabelPlacer();
        const maxLabels = labelMode === 'all' ? 90 : 40;
        let drawn = 0;
        const labelOrder = projList.slice().sort((a, b) => {
          const pa = (a.node.key === selectedKey ? 1000 : 0) + (a.node === hoveredItem ? 500 : 0) + ((nearSet && nearSet.has(a.node.key)) ? 250 : 0) + (a.matches ? 50 : 0) + a.z;
          const pb = (b.node.key === selectedKey ? 1000 : 0) + (b.node === hoveredItem ? 500 : 0) + ((nearSet && nearSet.has(b.node.key)) ? 250 : 0) + (b.matches ? 50 : 0) + b.z;
          return pb - pa;
        });
        for (let i = 0; i < labelOrder.length; i++) {
          const it = labelOrder[i];
          const n = it.node;
          const isSel = n.key === selectedKey;
          const isHover = n === hoveredItem;
          const isNear = nearSet ? nearSet.has(n.key) : false;
          const force = isSel || isHover;
          if (!force && (labelMode === 'off' || drawn >= maxLabels)) continue;
          if (!force && labelMode === 'smart' && it.z < -0.05 && !it.matches && !isNear) continue;
          const txt = n.name.length > 26 ? n.name.slice(0, 24) + '…' : n.name;
          const estW = txt.length * 6.8 + 12;
          const ly = it.sy - it.r - 8;
          if (placer(it.sx, ly, estW, 15, force)) {
            ctx.globalAlpha = force ? 1 : (0.55 + 0.45 * Math.max(0, (it.z + 1) * 0.5));
            const clr = isSel ? '#ffedd5' : (isNear ? '#fde68a' : (it.matches ? '#67e8f9' : '#f1f5f9'));
            drawLabelPill(txt, it.sx, ly, clr, isSel ? 13 : (force ? 12 : 11.5), force);
            drawn++;
          }
        }
        ctx.globalAlpha = 1;
      }
    }

    /* --- RENDER MODE B: 3D TABLE NETWORK + SUNFLOWER FOCUS (60FPS CANVAS) --- */
    function renderTableNetwork() {
      const hasSearch = searchQuery.length > 0;
      const isFocused = Boolean(selectedKey) && focusIsolate;
      const hasSelectedInSphere = Boolean(selectedKey) && !focusIsolate;
      const isDraggingNow = Boolean(dragState);

      // Neighbor set when highlighting a selected table inside the full 3D sphere
      let sphereNearSet = null;
      if (hasSelectedInSphere && byKey[selectedKey]) {
        sphereNearSet = new Set([selectedKey]);
        const selNode = byKey[selectedKey];
        for (let i = 0; i < selNode.fks.length; i++) {
          const fk = selNode.fks[i];
          const to = byKeyLower[(selNode.source + '|' + fk.ref_table).toLowerCase()] || byNameLower[fk.ref_table.toLowerCase()];
          if (to) sphereNearSet.add(to.key);
        }
        const inc = incomingByKey[selectedKey] || [];
        for (let i = 0; i < inc.length; i++) sphereNearSet.add(inc[i].s.key);
      }

      // Collect visible nodes and project them
      const projList = [];
      const pMap = {};
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (!isSourceVisible(n.source) || n.alpha <= 0.02) continue;
        const p = project3D(n.cur.x, n.cur.y, n.cur.z);
        const matches = hasSearch && matchTable(n, searchQuery);
        const item = { node: n, sx: p.sx, sy: p.sy, z: p.z, f: p.f, r: 6, matches: matches };
        projList.push(item);
        pMap[n.key] = item;
      }

      // Dynamic adaptive node radius based on visible table count (prevents blob wall!)
      const visCount = projList.length;
      let baseR = 13.5;
      if (isFocused) baseR = 14.5;
      else if (visCount > 3000) baseR = 3.0;
      else if (visCount > 1000) baseR = 4.2;
      else if (visCount > 350) baseR = 5.8;
      else if (visCount > 120) baseR = 8.2;
      else if (visCount > 45) baseR = 10.5;

      const zoomScale = Math.min(1.85, Math.max(0.6, Math.sqrt(zoom)));
      for (let i = 0; i < projList.length; i++) {
        const it = projList[i];
        const isSel = selectedKey === it.node.key;
        const boost = isSel ? 1.28 : (it.matches ? 1.22 : 1);
        it.r = Math.max(2.0, baseR * boost * Math.max(0.55, it.f) * zoomScale);
      }

      // 1. Draw Edges (with smart LOD budget for 20,000+ relations!)
      const hotPairs = [];
      if (showEdges || isFocused) {
        // Determine edge sampling stride when rendering huge unfocused graphs
        const totalPairs = pairs.length;
        let bgStride = 1;
        if (!isFocused && totalPairs > 2500) {
          bgStride = isDraggingNow ? Math.ceil(totalPairs / 900) : Math.ceil(totalPairs / 2600);
        }

        ctx.save();
        ctx.lineCap = 'round';

        // Fast batched path for non-hot same-DB edges (HUGE performance win on 20k edges!)
        if (showEdges && !isFocused) {
          ctx.beginPath();
          let sameCount = 0;
          const maxBgAlpha = totalPairs > 3000 ? 0.14 : (totalPairs > 600 ? 0.22 : 0.34);
          ctx.strokeStyle = hasSearch
            ? 'rgba(100, 116, 139, 0.08)'
            : 'rgba(100, 116, 139, ' + maxBgAlpha + ')';
          ctx.lineWidth = totalPairs > 1500 ? 0.7 : 1.0;

          for (let i = 0; i < totalPairs; i += bgStride) {
            const pr = pairs[i];
            if (pr.self || pr.cross) continue;
            const a = pMap[pr.s.key];
            const b = pMap[pr.t.key];
            if (!a || !b) continue;
            // Cull offscreen edges
            if ((a.sx < -40 && b.sx < -40) || (a.sx > cw + 40 && b.sx > cw + 40) ||
                (a.sy < -40 && b.sy < -40) || (a.sy > ch + 40 && b.sy > ch + 40)) continue;
            ctx.moveTo(a.sx, a.sy);
            ctx.lineTo(b.sx, b.sy);
            sameCount++;
          }
          if (sameCount > 0) ctx.stroke();

          // Batched pass for cross-DB dashed amber edges
          ctx.beginPath();
          let crossCount = 0;
          ctx.strokeStyle = hasSearch
            ? 'rgba(251, 191, 36, 0.10)'
            : 'rgba(251, 191, 36, ' + (totalPairs > 2000 ? 0.18 : 0.32) + ')';
          ctx.lineWidth = totalPairs > 1500 ? 0.85 : 1.3;
          ctx.setLineDash([6, 5]);
          for (let i = 0; i < totalPairs; i += bgStride) {
            const pr = pairs[i];
            if (pr.self || !pr.cross) continue;
            const a = pMap[pr.s.key];
            const b = pMap[pr.t.key];
            if (!a || !b) continue;
            ctx.moveTo(a.sx, a.sy);
            ctx.lineTo(b.sx, b.sy);
            crossCount++;
          }
          if (crossCount > 0) ctx.stroke();
          ctx.setLineDash([]);
        }

        // Collect hot edges (for selected table or hovered table)
        const focusKey = selectedKey || (hoveredItem && hoveredItem.kind === 'table' ? hoveredItem.key : null);
        if (focusKey) {
          for (let i = 0; i < totalPairs; i++) {
            const pr = pairs[i];
            if (pr.s.key === focusKey || pr.t.key === focusKey) {
              const a = pMap[pr.s.key];
              const b = pMap[pr.t.key];
              if (a && b) hotPairs.push({ pr: pr, a: a, b: b });
            }
          }
        }

        // Draw hot edges with crisp arrows & glow
        for (let i = 0; i < hotPairs.length; i++) {
          const { pr, a, b } = hotPairs[i];
          const alpha = Math.min(pr.s.alpha, pr.t.alpha);
          ctx.globalAlpha = alpha * (isFocused ? 0.96 : 0.82);

          if (pr.self) {
            const r = a.r;
            const loopR = r * 1.9;
            const sx = a.sx + r * 0.68;
            const sy = a.sy - r * 0.68;
            const ex = a.sx + r * 1.02;
            const ey = a.sy + r * 0.32;
            ctx.strokeStyle = '#22d3ee';
            ctx.lineWidth = 2.2;
            ctx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.bezierCurveTo(a.sx + loopR * 1.2, a.sy - loopR * 1.5, a.sx + loopR * 1.9, a.sy + loopR * 0.4, ex, ey);
            ctx.stroke();
            drawArrowHead(ex, ey, Math.atan2(ey - (a.sy + loopR * 0.4), ex - (a.sx + loopR * 1.9)), 8, '#22d3ee');
          } else {
            const dx = b.sx - a.sx;
            const dy = b.sy - a.sy;
            const dist = Math.hypot(dx, dy) || 1;
            const padA = a.r + 4;
            const padB = b.r + 4;
            let x1 = a.sx, y1 = a.sy, x2 = b.sx, y2 = b.sy;
            if (dist > padA + padB + 6) {
              x1 = a.sx + (dx / dist) * padA;
              y1 = a.sy + (dy / dist) * padA;
              x2 = b.sx - (dx / dist) * padB;
              y2 = b.sy - (dy / dist) * padB;
            }
            const col = pr.cross ? '#fbbf24' : '#f97316';
            const totalFks = pr.out.length + pr.inn.length;
            ctx.strokeStyle = col;
            ctx.lineWidth = totalFks > 1 ? 3.2 : 2.3;
            ctx.setLineDash(pr.cross ? [7, 5] : []);
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.stroke();
            ctx.setLineDash([]);

            const ang = Math.atan2(y2 - y1, x2 - x1);
            if (pr.out.length) drawArrowHead(x2, y2, ang, 8.5, col);
            if (pr.inn.length) drawArrowHead(x1, y1, ang + Math.PI, 8.5, col);
          }
        }
        ctx.restore();
      }

      // 2. Depth-sort nodes back-to-front & render circles
      projList.sort((a, b) => a.z - b.z);
      lastProjectedItems = projList;

      const simpleDraw = visCount > 1200 && !isFocused;

      for (let i = 0; i < projList.length; i++) {
        const it = projList[i];
        const n = it.node;
        // Viewport culling
        if (it.sx < -40 || it.sx > cw + 40 || it.sy < -40 || it.sy > ch + 40) continue;

        const isSel = selectedKey === n.key;
        const isHover = hoveredItem === n;
        const isNearInSphere = sphereNearSet ? sphereNearSet.has(n.key) : false;
        let alpha = isFocused ? n.alpha : ((0.30 + 0.70 * Math.max(0, (it.z + 1) * 0.5)) * n.alpha);
        if (hasSearch && !it.matches && !isSel && !isHover) alpha *= 0.16;
        else if (hasSelectedInSphere && !isNearInSphere && !isHover) alpha *= 0.22;

        ctx.globalAlpha = alpha;

        if (isSel || isHover || it.matches || (hasSelectedInSphere && isNearInSphere)) {
          ctx.beginPath();
          ctx.arc(it.sx, it.sy, it.r + (isSel ? 7 : 5), 0, Math.PI * 2);
          ctx.strokeStyle = isSel ? '#f97316' : (it.matches ? '#22d3ee' : (isNearInSphere ? '#fbbf24' : '#ffffff'));
          ctx.lineWidth = isSel ? 2.5 : 1.8;
          ctx.stroke();
        }

        ctx.beginPath();
        ctx.arc(it.sx, it.sy, it.r, 0, Math.PI * 2);
        ctx.fillStyle = isSel ? '#f97316' : n.color;
        ctx.fill();

        if (!simpleDraw || isSel || isHover || it.matches) {
          ctx.lineWidth = isSel ? 2.4 : (it.r > 5 ? 1.15 : 0.65);
          ctx.strokeStyle = isSel ? '#ffedd5' : 'rgba(255, 255, 255, 0.58)';
          ctx.stroke();

          if (it.r >= 7) {
            ctx.beginPath();
            ctx.arc(it.sx - it.r * 0.3, it.sy - it.r * 0.32, it.r * 0.3, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(255, 255, 255, 0.28)';
            ctx.fill();
          }
        }
      }
      ctx.globalAlpha = 1;

      // 3. Draw Hot Edge Column Labels (only in Focus Mode so they're clean & readable)
      if (isFocused && hotPairs.length) {
        const edgePlacer = createLabelPlacer();
        for (let i = 0; i < hotPairs.length; i++) {
          const { pr, a, b } = hotPairs[i];
          const alpha = Math.min(pr.s.alpha, pr.t.alpha);
          ctx.globalAlpha = alpha;
          if (pr.self) {
            const loopR = a.r * 1.9;
            drawLabelPill('↻ ' + pr.out.join(' · '), a.sx + loopR * 1.45, a.sy - loopR * 0.7, '#67e8f9', 10.5, false);
          } else {
            const hubIsT = selectedKey === pr.t.key;
            const fromP = hubIsT ? b : a;
            const toP = hubIsT ? a : b;
            const cols = pr.out.length && pr.inn.length
              ? [pr.out.join(' · '), pr.inn.join(' · ')]
              : [pr.out.concat(pr.inn).join(' · ')];
            const fracs = cols.length === 2 ? [0.54, 0.72] : [0.62];
            const lblColor = pr.cross ? '#fde68a' : '#fdba74';

            for (let c = 0; c < cols.length; c++) {
              const txt = cols[c];
              const lx = fromP.sx + (toP.sx - fromP.sx) * fracs[c];
              const ly = fromP.sy + (toP.sy - fromP.sy) * fracs[c] - 6;
              if (edgePlacer(lx, ly, txt.length * 6 + 6, 12, cols.length === 1 && hotPairs.length <= 12)) {
                drawLabelPill(txt, lx, ly, lblColor, 10.5, false);
              }
            }
          }
        }
        ctx.globalAlpha = 1;
      }

      // 4. Collision-Free Node Labels (never creates unreadable white text soup!)
      if (labelMode !== 'off' || isFocused || hasSearch || hoveredItem) {
        const placer = createLabelPlacer();
        const maxLabels = isFocused ? 80 : (labelMode === 'all' ? 85 : 36);
        let drawn = 0;

        // Sort front-to-back with priority for selected, hovered, and search matches
        const candidates = [];
        for (let i = 0; i < projList.length; i++) {
          const it = projList[i];
          const n = it.node;
          const isSel = selectedKey === n.key;
          const isHover = hoveredItem === n;
          const isNearInSphere = sphereNearSet ? sphereNearSet.has(n.key) : false;
          if (!isSel && !isHover && !it.matches && !isFocused && !isNearInSphere) {
            if (labelMode === 'off') continue;
            if (labelMode === 'smart' && it.z < 0.2) continue;
          }
          candidates.push({
            it: it,
            prio: (isSel ? 1000 : 0) + (isHover ? 500 : 0) + (isNearInSphere ? 300 : 0) + (it.matches ? 200 : 0) + it.z * 10 + Math.min(5, n.deg * 0.2)
          });
        }
        candidates.sort((a, b) => b.prio - a.prio);

        for (let i = 0; i < candidates.length; i++) {
          const it = candidates[i].it;
          const n = it.node;
          const isSel = selectedKey === n.key;
          const isHover = hoveredItem === n;
          const isNearInSphere = sphereNearSet ? sphereNearSet.has(n.key) : false;
          const force = isSel || isHover;
          if (!force && drawn >= maxLabels) break;

          const txt = n.name.length > 28 ? n.name.slice(0, 26) + '…' : n.name;
          const fSize = isSel ? 13 : (isHover ? 12.5 : 11);
          const estW = txt.length * (fSize * 0.58) + 8;
          const ly = it.sy - it.r - 6;

          if (placer(it.sx, ly, estW, fSize + 3, force)) {
            ctx.globalAlpha = force ? 1 : (isFocused ? n.alpha : (isNearInSphere ? 0.96 : Math.max(0.45, (it.z + 1) * 0.55)));
            drawLabelPill(txt, it.sx, ly, isSel ? '#ffedd5' : (isNearInSphere ? '#fde68a' : (it.matches ? '#67e8f9' : '#f1f5f9')), fSize, isSel || isHover);
            drawn++;
          }
        }
        ctx.globalAlpha = 1;
      }
    }

    function renderFrame() {
      if (mode === 'cards') return;
      drawBackground();
      if (mode === 'galaxy') renderGalaxy();
      else renderTableNetwork();
    }

    /* Master Animation Loop — only redraws when needed (0% idle CPU when spin is off!) */
    function tick(now) {
      const dt = Math.min(50, now - lastFrameTime);
      lastFrameTime = now;

      if (anim) {
        const t = Math.min(1, (now - anim.startTime) / anim.duration);
        const e = easeOutCubic(t);

        if (mode === 'graph') {
          for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            n.cur.x = n.start.x + (n.tgt.x - n.start.x) * e;
            n.cur.y = n.start.y + (n.tgt.y - n.start.y) * e;
            n.cur.z = n.start.z + (n.tgt.z - n.start.z) * e;
            n.alpha = n.startAlpha + (n.tgtAlpha - n.startAlpha) * e;
          }
        }

        rot.rx = anim.startRot.rx + (anim.targetRot.rx - anim.startRot.rx) * e;
        rot.ry = anim.startRot.ry + (anim.targetRot.ry - anim.startRot.ry) * e;
        zoom = anim.startZoom + (anim.targetZoom - anim.startZoom) * e;
        pan.x = anim.startPan.x + (anim.targetPan.x - anim.startPan.x) * e;
        pan.y = anim.startPan.y + (anim.targetPan.y - anim.startPan.y) * e;

        needsRender = true;
        if (t >= 1) anim = null;
      } else if (autoSpin && !(selectedKey && mode === 'graph' && focusIsolate) && !dragState && mode !== 'cards') {
        rot.ry += dt * 0.00028;
        needsRender = true;
      }

      if (needsRender && mode !== 'cards') {
        renderFrame();
        needsRender = false;
        fpsFrames++;
      }

      if (now - fpsLastTime >= 750) {
        currentFps = Math.round((fpsFrames * 1000) / (now - fpsLastTime));
        fpsFrames = 0;
        fpsLastTime = now;
        const visLabel = mode === 'galaxy'
          ? (galaxyFocusDb() ? `${lastProjectedItems.length} table orbs` : `${lastProjectedItems.length} DB orbs`)
          : `${lastProjectedItems.length.toLocaleString()} tables`;
        perfPillEl.innerHTML = `Canvas 2D · ${visLabel} · <b>${Math.max(1, currentFps || 60)} fps</b>`;
      }

      requestAnimationFrame(tick);
    }

    /* =====================================================================
       7. SELECTION, DB DRILL-DOWN & TABLE INSPECTOR
       ===================================================================== */
    function drillIntoDatabase(source) {
      soloSource = source;
      disabledSources = new Set(sources.filter(s => s !== source));
      selectedKey = null;
      hoveredItem = null;
      hideHoverTip();
      detailEl.classList.remove('open');

      mode = 'graph';
      syncModeUI();
      buildLegend();
      buildSide(searchQuery);
      recomputeVisibleSpherePositions();
      updateStageHud();

      startTransition({ rx: -0.26, ry: 0.52 }, fitBaseZoom(), { x: 0, y: 0 }, 380);
      toast(`Drilled into ${srcLabel(source)} (${sourceTableMap[source].length} tables)`);
    }

    function setTable3DView(isolate) {
      focusIsolate = isolate;
      if (mode !== 'graph') {
        mode = 'graph';
        syncModeUI();
      }
      hoveredItem = null;
      hideHoverTip();
      updateStageHud();
      renderDetailModeBar();
      requestAnimationFrame(() => {
        resizeCanvas();
        if (selectedKey) {
          if (focusIsolate) computeFocusTargets(selectedKey);
          else {
            recomputeVisibleSpherePositions();
            computeSphereTargets(selectedKey);
          }
        }
      });
    }

    function selectTable(key, opts) {
      const t = byKey[key];
      if (!t) return;
      opts = opts || {};

      if (disabledSources.has(t.source)) {
        disabledSources.delete(t.source);
        soloSource = null;
        buildLegend();
        buildSide(searchQuery);
        recomputeVisibleSpherePositions();
      }

      // Keep DB Galaxy mode if jumping while already exploring in Galaxy, otherwise switch to 3D
      if (!(opts.keepMode && mode === 'galaxy')) {
        if (mode !== 'graph') {
          mode = 'graph';
          syncModeUI();
        }
      }

      selectedKey = key;
      hoveredItem = null;
      hideHoverTip();
      toast((mode === 'galaxy' ? 'DB Galaxy · ' : 'Focused: ') + t.name + ' (' + srcLabel(t.source) + ')');

      document.querySelectorAll('.titem.sel, .card.sel').forEach(el => el.classList.remove('sel'));
      const activeItem = sideListEl.querySelector(`.titem[data-key="${CSS.escape(key)}"]`);
      if (activeItem) {
        activeItem.classList.add('sel');
        if (activeItem.scrollIntoView) activeItem.scrollIntoView({ block: 'nearest' });
      }

      renderInspector(t);
      detailEl.classList.add('open');
      updateStageHud();

      requestAnimationFrame(() => {
        resizeCanvas();
        if (mode === 'galaxy') {
          const focSrc = galaxyFocusDb();
          if (focSrc) {
            restoreGalaxyTablePositions();
            startTransition({ rx: -0.26, ry: 0.52 }, fitTableGalaxyZoom(focSrc), { x: 0, y: 0 }, 360);
          } else needsRender = true;
        } else if (focusIsolate) {
          computeFocusTargets(key);
        } else {
          computeSphereTargets(key);
        }
      });
    }

    function renderDetailModeBar() {
      if (!selectedKey) {
        detailModeBarEl.innerHTML = '';
        return;
      }
      detailModeBarEl.innerHTML =
        `<button id="dmGalaxy" class="${mode === 'galaxy' ? 'on-galaxy' : ''}" title="This table as a highlighted planet among its database's table planets">🌌 DB Galaxy</button>` +
        `<button id="dmFocus" class="${mode === 'graph' && focusIsolate ? 'on' : ''}" title="Isolate this table & its direct relations">🌻 3D Focus</button>` +
        `<button id="dmSphere" class="${mode === 'graph' && !focusIsolate ? 'on' : ''}" title="Highlight this table inside the full 3D Sphere">🌐 3D Sphere</button>`;

      document.getElementById('dmGalaxy').onclick = () => setMode('galaxy');
      document.getElementById('dmFocus').onclick = () => setTable3DView(true);
      document.getElementById('dmSphere').onclick = () => setTable3DView(false);
    }

    function renderInspector(t) {
      const color = sColor[t.source] || '#3b82f6';
      document.getElementById('tname').textContent = t.name;

      const srcc = document.getElementById('tsrcc');
      srcc.style.borderColor = color;
      srcc.style.color = color;
      srcc.querySelector('.sdot').style.background = color;
      document.getElementById('tsrcc-txt').textContent = t.source;

      const incoming = incomingByKey[t.key] || [];
      document.getElementById('tstats').innerHTML =
        `<span class="chip"><b>${t.columns.length}</b> columns</span>` +
        `<span class="chip"><b>${t.fks.length}</b> outgoing FKs</span>` +
        `<span class="chip"><b>${incoming.length}</b> referenced by</span>`;

      renderDetailModeBar();

      document.getElementById('col-sec-cnt').textContent = t.columns.length + ' cols';
      document.getElementById('inc-sec-cnt').textContent = incoming.length + ' refs';

      const cb = document.getElementById('cbody');
      cb.innerHTML = '';
      t.columns.forEach((col, idx) => {
        const fk = t.fks.find(f => f.column === col);
        const isPk = (idx === 0 && (col === 'id' || (col.endsWith('_id') && !fk)));
        const tr = document.createElement('tr');

        let badgeHtml = '';
        if (isPk) badgeHtml += '<span class="badge pk">PK</span>';
        if (fk) badgeHtml += '<span class="badge fk">FK</span>';

        let relHtml = '<span style="color:var(--text-dim)">—</span>';
        if (fk) {
          const tgt = byKeyLower[(t.source + '|' + fk.ref_table).toLowerCase()] || byNameLower[fk.ref_table.toLowerCase()];
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
        a.addEventListener('click', () => selectTable(a.dataset.k, { keepMode: mode === 'galaxy' }));
      });

      const refBody = document.getElementById('refbody');
      refBody.innerHTML = '';
      if (!incoming.length) {
        refBody.innerHTML = '<div class="empty-refs">No other tables reference this table.</div>';
      } else {
        const maxRefs = 60;
        incoming.slice(0, maxRefs).forEach(l => {
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
          div.addEventListener('click', () => selectTable(l.s.key, { keepMode: mode === 'galaxy' }));
          refBody.appendChild(div);
        });
        if (incoming.length > maxRefs) {
          const more = document.createElement('div');
          more.className = 'empty-refs';
          more.textContent = `+ ${incoming.length - maxRefs} more referencing tables`;
          refBody.appendChild(more);
        }
      }
    }

    function updateStageHud() {
      hudSegEl.style.display = 'none';
      hudSegEl.innerHTML = '';

      if (selectedKey && byKey[selectedKey]) {
        const t = byKey[selectedKey];
        stageHudEl.style.display = 'flex';
        stageHudEl.className = mode === 'galaxy' ? 'galaxy-style' : 'focus-style';
        hudDot.style.background = mode === 'galaxy' ? '#a855f7' : '#f97316';
        hudTxt.textContent = `${t.name} · ${srcLabel(t.source)}`;

        // Quick 1-click toggle right on the canvas between DB Galaxy, 3D Focus & 3D Sphere
        hudSegEl.style.display = 'inline-flex';
        hudSegEl.innerHTML =
          `<button id="hudGalaxy" class="${mode === 'galaxy' ? 'on-galaxy' : ''}">🌌 Galaxy</button>` +
          `<button id="hudFocus" class="${mode === 'graph' && focusIsolate ? 'on' : ''}">🌻 Focus</button>` +
          `<button id="hudSphere" class="${mode === 'graph' && !focusIsolate ? 'on' : ''}">🌐 3D</button>`;
        document.getElementById('hudGalaxy').onclick = () => setMode('galaxy');
        document.getElementById('hudFocus').onclick = () => setTable3DView(true);
        document.getElementById('hudSphere').onclick = () => setTable3DView(false);

        hudBtn.textContent = '× Exit';
        hudBtn.onclick = () => exitFocus();
        renderDetailModeBar();
      } else if (soloSource) {
        stageHudEl.style.display = 'flex';
        stageHudEl.className = mode === 'galaxy' ? 'galaxy-style' : '';
        hudDot.style.background = sColor[soloSource] || '#3b82f6';
        hudTxt.textContent = `DB: ${srcLabel(soloSource)} (${sourceTableMap[soloSource].length} tables)`;

        hudSegEl.style.display = 'inline-flex';
        hudSegEl.innerHTML =
          `<button id="hudGalaxy" class="${mode === 'galaxy' ? 'on-galaxy' : ''}">🌌 DB Galaxy</button>` +
          `<button id="hud3D" class="${mode === 'graph' ? 'on' : ''}">🕸 3D Tables</button>`;
        document.getElementById('hudGalaxy').onclick = () => setMode('galaxy');
        document.getElementById('hud3D').onclick = () => setMode('graph');

        hudBtn.textContent = 'Show All DBs';
        hudBtn.onclick = () => {
          soloSource = null;
          disabledSources = new Set();
          buildLegend();
          buildSide(searchQuery);
          recomputeVisibleSpherePositions();
          updateStageHud();
          if (mode === 'galaxy') needsRender = true;
          else computeSphereTargets();
        };
      } else if (mode === 'galaxy') {
        stageHudEl.style.display = 'flex';
        stageHudEl.className = 'galaxy-style';
        hudDot.style.background = '#a855f7';
        hudTxt.textContent = `DB Galaxy · ${sources.length} databases — click any planet to inspect its tables`;
        hudBtn.textContent = 'Show All 3D Tables';
        hudBtn.onclick = () => setMode('graph');
      } else {
        stageHudEl.style.display = 'none';
      }
    }

    function exitFocus() {
      selectedKey = null;
      detailEl.classList.remove('open');
      document.querySelectorAll('.titem.sel, .card.sel').forEach(el => el.classList.remove('sel'));
      updateStageHud();
      requestAnimationFrame(() => {
        resizeCanvas();
        if (mode === 'galaxy') {
          needsRender = true;
        } else {
          recomputeVisibleSpherePositions();
          computeSphereTargets();
        }
      });
    }

    function resetAll() {
      selectedKey = null;
      soloSource = null;
      disabledSources = new Set();
      searchQuery = '';
      searchInput.value = '';
      searchClear.classList.remove('visible');
      detailEl.classList.remove('open');
      hideHoverTip();

      if (data.length > HUGE_TABLE_THRESHOLD && sources.length > 1) {
        mode = 'galaxy';
      } else if (mode === 'cards') {
        mode = 'graph';
      }

      syncModeUI();
      buildLegend();
      buildSide('');
      recomputeVisibleSpherePositions();
      updateStageHud();

      requestAnimationFrame(() => {
        resizeCanvas();
        computeSphereTargets();
      });
    }

    /* =====================================================================
       8. LAZY SIDEBAR, LEGEND & PAGINATED ER CARDS
       Zero startup DOM bloat even with 10,000 tables!
       ===================================================================== */
    function buildLegend() {
      const lg = document.getElementById('legend');
      lg.innerHTML = '';

      if (soloSource || disabledSources.size > 0) {
        const allChip = document.createElement('div');
        allChip.className = 'lg';
        allChip.style.borderColor = '#38bdf8';
        allChip.style.color = '#e0f2fe';
        allChip.textContent = '↺ Show All DBs';
        allChip.onclick = () => {
          soloSource = null;
          disabledSources = new Set();
          applySourceFilterUpdate();
          toast('Showing all databases');
        };
        lg.appendChild(allChip);
      }

      sources.forEach(s => {
        const count = sourceTableMap[s].length;
        const d = document.createElement('div');
        d.className = 'lg';
        if (disabledSources.has(s)) d.classList.add('off');
        if (soloSource === s) d.classList.add('solo');

        d.innerHTML =
          `<span class="dot" style="background:${sColor[s]};color:${sColor[s]}"></span>` +
          `<span>${esc(srcLabel(s))}</span>` +
          `<span class="n">${count}</span>`;
        d.title = `Source: ${s} · Click: toggle · Double-click: SOLO & drill in`;

        let clickTimer = null;
        d.addEventListener('click', () => {
          if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; return; }
          clickTimer = setTimeout(() => {
            clickTimer = null;
            if (soloSource && soloSource !== s) {
              drillIntoDatabase(s);
              return;
            }
            if (disabledSources.has(s)) disabledSources.delete(s);
            else disabledSources.add(s);
            toast((disabledSources.has(s) ? 'Hidden: ' : 'Visible: ') + srcLabel(s));
            applySourceFilterUpdate();
          }, 185);
        });

        d.addEventListener('dblclick', (e) => {
          e.preventDefault();
          if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
          if (soloSource === s) {
            soloSource = null;
            disabledSources = new Set();
            toast('Showing all databases');
            applySourceFilterUpdate();
          } else {
            drillIntoDatabase(s);
          }
        });

        lg.appendChild(d);
      });

      if (hasCross) {
        const c = document.createElement('div');
        c.className = 'chip';
        c.innerHTML =
          `<svg width="22" height="8" style="vertical-align:middle"><line x1="1" y1="4" x2="21" y2="4" stroke="#fbbf24" stroke-width="2" stroke-dasharray="5 4"/></svg>` +
          `<span>cross-db FK</span>`;
        lg.appendChild(c);
      }

      const hint = document.createElement('span');
      hint.className = 'legend-hint';
      hint.textContent = 'Click = toggle · 2×Click = SOLO DB';
      lg.appendChild(hint);

      document.getElementById('cnt').innerHTML =
        `<b>${data.length.toLocaleString()}</b> tables · <b>${links.length.toLocaleString()}</b> relations · <b>${sources.length}</b> db${sources.length === 1 ? '' : 's'}`;
    }

    function applySourceFilterUpdate() {
      buildLegend();
      buildSide(searchQuery);
      recomputeVisibleSpherePositions();
      updateStageHud();
      if (mode === 'cards') buildCards(searchQuery);

      if (selectedKey && disabledSources.has(byKey[selectedKey].source)) {
        exitFocus();
      } else if (selectedKey) {
        computeFocusTargets(selectedKey);
      } else {
        needsRender = true;
      }
    }

    /* LAZY SIDEBAR: Only builds table items inside a <details> when expanded! */
    function populateSideGroup(container, list, offset) {
      const slice = list.slice(offset, offset + SIDE_CHUNK_SIZE);
      const frag = document.createDocumentFragment();
      for (let i = 0; i < slice.length; i++) {
        const t = slice[i];
        const k = t.key;
        const item = document.createElement('div');
        item.className = 'titem' + (selectedKey === k ? ' sel' : '');
        item.dataset.key = k;
        item.innerHTML =
          `<span class="tname-txt">${esc(t.name)}</span>` +
          `<span class="nfk">${t.fks.length} fk</span>`;
        item.addEventListener('click', () => {
          selectTable(k);
          if (window.innerWidth <= 960) sideEl.classList.add('closed');
        });
        frag.appendChild(item);
      }
      container.appendChild(frag);

      const nextOffset = offset + slice.length;
      if (nextOffset < list.length) {
        const moreBtn = document.createElement('button');
        moreBtn.className = 'side-more-btn';
        moreBtn.textContent = `Show more (${list.length - nextOffset} remaining)`;
        moreBtn.onclick = () => {
          moreBtn.remove();
          populateSideGroup(container, list, nextOffset);
        };
        container.appendChild(moreBtn);
      }
    }

    function buildSide(q) {
      q = (q || '').trim().toLowerCase();
      sideListEl.innerHTML = '';

      const visSources = sources.filter(s => isSourceVisible(s));
      sideCntEl.textContent = `${visSources.length} / ${sources.length} dbs`;

      // Start collapsed whenever there are > 4 DBs (keeps startup DOM under 100 nodes!)
      const startOpen = (visSources.length <= 4 && data.length <= 300) || q.length > 0;
      const dets = [];
      const frag = document.createDocumentFragment();

      for (let i = 0; i < visSources.length; i++) {
        const s = visSources[i];
        const allNodesInSrc = nodes.filter(n => n.source === s);
        const filtered = q ? allNodesInSrc.filter(n => matchTable(n, q)) : allNodesInSrc;
        if (q && !filtered.length) continue;

        const det = document.createElement('details');
        det.className = 'src';
        det.open = startOpen && dets.length < 15; // Cap auto-open groups during broad searches

        const sum = document.createElement('summary');
        sum.title = 'Source: ' + s;
        sum.innerHTML =
          `<span class="caret">▶</span>` +
          `<span class="dot" style="background:${sColor[s]}"></span>` +
          `<span class="sname">${esc(srcLabel(s))}</span>` +
          `<span class="cnt">${filtered.length}${filtered.length !== allNodesInSrc.length ? '/' + allNodesInSrc.length : ''}</span>`;

        const soloBtn = document.createElement('button');
        soloBtn.className = 'solo-mini';
        soloBtn.textContent = '3D';
        soloBtn.title = 'Drill into this database in 3D';
        soloBtn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          drillIntoDatabase(s);
          if (window.innerWidth <= 960) sideEl.classList.add('closed');
        });
        sum.appendChild(soloBtn);
        det.appendChild(sum);

        const bodyDiv = document.createElement('div');
        det.appendChild(bodyDiv);

        let rendered = false;
        const ensureRendered = () => {
          if (rendered) return;
          rendered = true;
          populateSideGroup(bodyDiv, filtered, 0);
        };

        if (det.open) ensureRendered();
        det.addEventListener('toggle', () => {
          if (det.open) ensureRendered();
        });

        dets.push(det);
        frag.appendChild(det);
      }

      if (!dets.length) {
        sideListEl.innerHTML = '<div style="padding:18px;text-align:center;color:var(--text-dim);font-size:12.5px">No matching tables found.</div>';
      } else {
        sideListEl.appendChild(frag);
      }

      foldBtn.textContent = startOpen ? 'Close All' : 'Open All';
      foldBtn.onclick = () => {
        const anyOpen = dets.some(d => d.open);
        dets.slice(0, anyOpen ? dets.length : 15).forEach(d => { d.open = !anyOpen; });
        if (!anyOpen && dets.length > 15) {
          toast('Opened first 15 database groups for fast performance');
        }
        foldBtn.textContent = anyOpen ? 'Open All' : 'Close All';
      };
    }

    /* LAZY & PAGINATED ER CARDS: Builds 60 cards per page only when Cards tab is open */
    function buildCards(q) {
      q = (q || '').trim().toLowerCase();
      cardsEl.innerHTML = '';
      cardsPagerEl.innerHTML = '';
      cardsFilteredList = nodes.filter(n => isSourceVisible(n.source) && matchTable(n, q));
      cardsRenderedCount = 0;

      if (!cardsFilteredList.length) {
        cardsEl.innerHTML = '<div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-dim)">No tables match the current filter.</div>';
        return;
      }
      appendNextCardPage();
    }

    function appendNextCardPage() {
      const slice = cardsFilteredList.slice(cardsRenderedCount, cardsRenderedCount + CARDS_PAGE_SIZE);
      const frag = document.createDocumentFragment();

      for (let i = 0; i < slice.length; i++) {
        const t = slice[i];
        const k = t.key;
        const card = document.createElement('div');
        card.className = 'card' + (selectedKey === k ? ' sel' : '');
        card.dataset.key = k;
        card.style.setProperty('--sc', t.color);

        const maxCols = 8;
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
          `<div class="card-src"><span class="dot" style="background:${t.color}"></span>${esc(srcLabel(t.source))}</div>` +
          `<div class="card-cols">${colsHtml}</div>`;

        card.addEventListener('click', () => selectTable(k));
        frag.appendChild(card);
      }

      cardsEl.appendChild(frag);
      cardsRenderedCount += slice.length;

      cardsPagerEl.innerHTML = '';
      if (cardsRenderedCount < cardsFilteredList.length) {
        const rem = cardsFilteredList.length - cardsRenderedCount;
        const btn = document.createElement('button');
        btn.className = 'btn active';
        btn.textContent = `Load ${Math.min(CARDS_PAGE_SIZE, rem)} more cards (showing ${cardsRenderedCount.toLocaleString()} of ${cardsFilteredList.length.toLocaleString()})`;
        btn.onclick = appendNextCardPage;
        cardsPagerEl.appendChild(btn);
      }
    }

    /* =====================================================================
       9. FAST SCREEN-SPACE HIT TESTING, MOUSE, TOUCH & WHEEL CONTROLS
       ===================================================================== */
    let dragState = null;
    let dragMoved = 0;
    let pinchState = null;

    function hitTestCanvas(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const mx = clientX - rect.left;
      const my = clientY - rect.top;

      // Traverse front-to-back (lastProjectedItems is sorted back-to-front)
      for (let i = lastProjectedItems.length - 1; i >= 0; i--) {
        const it = lastProjectedItems[i];
        const hitR = Math.max(8, it.r + 4);
        const dx = mx - it.sx;
        const dy = my - it.sy;
        if (dx * dx + dy * dy <= hitR * hitR) {
          return { item: it, mx: mx, my: my };
        }
      }
      return null;
    }

    function showHoverTip(hit) {
      const it = hit.item;
      const obj = it.node;
      hoverTipEl.style.display = 'block';

      if (obj.kind === 'db') {
        const activeTbl = selectedKey ? byKey[selectedKey] : null;
        const isHomeOfSelected = activeTbl && activeTbl.source === obj.source;
        const actionHint = isHomeOfSelected
          ? `Click to switch to 3D view for "${esc(activeTbl.name)}"`
          : 'Click to drill into this database in 3D';
        hoverTipEl.innerHTML =
          `<div class="ht-title"><span class="ht-dot" style="background:${obj.color}"></span>${esc(obj.label)}</div>` +
          `<div class="ht-meta">${obj.tableCount.toLocaleString()} tables · ${obj.intraFks.toLocaleString()} internal FKs · ${obj.crossFks.toLocaleString()} cross-DB</div>` +
          `<div class="ht-meta" style="color:#93c5fd;margin-top:2px">${actionHint}</div>`;
      } else {
        const incCnt = (incomingByKey[obj.key] || []).length;
        hoverTipEl.innerHTML =
          `<div class="ht-title"><span class="ht-dot" style="background:${obj.color}"></span>${esc(obj.name)}</div>` +
          `<div class="ht-meta">${esc(srcLabel(obj.source))} · ${obj.columns.length} cols · ${obj.fks.length} out · ${incCnt} in</div>`;
      }

      const tipW = hoverTipEl.offsetWidth || 200;
      const tipH = hoverTipEl.offsetHeight || 54;
      let tx = hit.mx + 16;
      let ty = hit.my - tipH * 0.5;
      if (tx + tipW > cw - 12) tx = hit.mx - tipW - 16;
      if (ty < 10) ty = 10;
      if (ty + tipH > ch - 10) ty = ch - tipH - 10;

      hoverTipEl.style.left = tx + 'px';
      hoverTipEl.style.top = ty + 'px';
    }

    function hideHoverTip() {
      hoverTipEl.style.display = 'none';
    }

    canvas.addEventListener('mousedown', (e) => {
      if (e.button !== 0 && e.button !== 1) return;
      anim = null;
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
      canvas.classList.add('grabbing');
      hideHoverTip();
    });

    window.addEventListener('mousemove', (e) => {
      if (dragState) {
        const dx = e.clientX - dragState.x;
        const dy = e.clientY - dragState.y;
        dragMoved = Math.max(dragMoved, Math.hypot(dx, dy));

        if (dragState.pan) {
          pan.x = dragState.px - dx / zoom;
          pan.y = dragState.py - dy / zoom;
        } else {
          rot.ry = dragState.ry - dx * 0.0072;
          rot.rx = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, dragState.rx + dy * 0.0072));
        }
        needsRender = true;
        return;
      }

      if (e.target !== canvas || mode === 'cards') {
        if (hoveredItem) {
          hoveredItem = null;
          hideHoverTip();
          canvas.classList.remove('pointer');
          needsRender = true;
        }
        return;
      }

      const hit = hitTestCanvas(e.clientX, e.clientY);
      const nextHover = hit ? hit.item.node : null;
      if (nextHover !== hoveredItem) {
        hoveredItem = nextHover;
        canvas.classList.toggle('pointer', Boolean(nextHover));
        needsRender = true;
      }
      if (hit) showHoverTip(hit);
      else hideHoverTip();
    });

    window.addEventListener('mouseup', () => {
      if (!dragState) return;
      dragState = null;
      canvas.classList.remove('grabbing');
      needsRender = true; // Restore full edge LOD after drag settles
    });

    canvas.addEventListener('click', (e) => {
      if (dragMoved > 5) return;
      const hit = hitTestCanvas(e.clientX, e.clientY);
      if (!hit) return;
      const obj = hit.item.node;
      if (obj.kind === 'db') {
        if (selectedKey && byKey[selectedKey] && byKey[selectedKey].source === obj.source) {
          setMode('graph');
        } else {
          drillIntoDatabase(obj.source);
        }
      } else {
        selectTable(obj.key);
      }
    });

    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      anim = null;
      const factor = e.deltaY < 0 ? 1.15 : (1 / 1.15);
      zoom = Math.max(0.32, Math.min(14, zoom * factor));
      needsRender = true;
    }, { passive: false });

    // Touch support (1-finger orbit/pan + 2-finger pinch zoom + tap)
    canvas.addEventListener('touchstart', (e) => {
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

    canvas.addEventListener('touchmove', (e) => {
      if (e.touches.length === 1 && dragState) {
        const t = e.touches[0];
        const dx = t.clientX - dragState.x;
        const dy = t.clientY - dragState.y;
        dragMoved = Math.max(dragMoved, Math.hypot(dx, dy));
        if (dragState.pan) {
          pan.x = dragState.px - dx / zoom;
          pan.y = dragState.py - dy / zoom;
        } else {
          rot.ry = dragState.ry - dx * 0.0085;
          rot.rx = Math.max(-Math.PI / 2, Math.min(Math.PI / 2, dragState.rx + dy * 0.0085));
        }
        needsRender = true;
      } else if (e.touches.length === 2 && pinchState) {
        const d = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        zoom = Math.max(0.32, Math.min(14, pinchState.zoom * (d / (pinchState.dist || 1))));
        needsRender = true;
      }
    }, { passive: true });

    canvas.addEventListener('touchend', (e) => {
      if (dragState && dragMoved < 8 && e.changedTouches.length === 1) {
        const t = e.changedTouches[0];
        const hit = hitTestCanvas(t.clientX, t.clientY);
        if (hit) {
          if (hit.item.node.kind === 'db') {
            if (selectedKey && byKey[selectedKey] && byKey[selectedKey].source === hit.item.node.source) {
              setMode('graph');
            } else {
              drillIntoDatabase(hit.item.node.source);
            }
          } else {
            selectTable(hit.item.node.key);
          }
        }
      }
      dragState = null;
      pinchState = null;
      needsRender = true;
    });

    /* =====================================================================
       10. TOOLBAR & KEYBOARD CONTROLS
       ===================================================================== */
    function syncModeUI() {
      document.getElementById('vGalaxy').className = 'btn' + (mode === 'galaxy' ? ' galaxy-active' : '');
      document.getElementById('vGraph').className = 'btn' + (mode === 'graph' ? ' active' : '');
      document.getElementById('vCards').className = 'btn' + (mode === 'cards' ? ' active' : '');

      graphWrapEl.style.display = (mode === 'cards') ? 'none' : 'flex';
      cardsWrapEl.classList.toggle('visible', mode === 'cards');
    }

    function setMode(m) {
      // Preserve selectedKey, Inspector drawer, and soloSource when switching between Galaxy and 3D!
      mode = m;
      hoveredItem = null;
      hideHoverTip();
      syncModeUI();
      updateStageHud();
      renderDetailModeBar();

      if (m === 'cards') {
        buildCards(searchQuery);
      } else {
        requestAnimationFrame(() => {
          resizeCanvas();
          if (m === 'galaxy') {
            const focSrc = galaxyFocusDb();
            if (focSrc) restoreGalaxyTablePositions();
            startTransition({ rx: -0.26, ry: 0.52 }, focSrc ? fitTableGalaxyZoom(focSrc) : fitBaseZoom(), { x: 0, y: 0 }, 360);
            if (galaxyFocusDb()) {
              const tt = selectedKey ? byKey[selectedKey] : null;
              if (tt) {
                toast(`DB Galaxy · "${tt.name}" among its ${sourceTableMap[tt.source].length} table planets in ${srcLabel(tt.source)}`);
              } else if (soloSource) {
                toast(`DB Galaxy · ${sourceTableMap[soloSource].length} table planets of ${srcLabel(soloSource)}`);
              }
            }
          } else if (selectedKey) {
            if (focusIsolate) {
              computeFocusTargets(selectedKey);
            } else {
              recomputeVisibleSpherePositions();
              computeSphereTargets(selectedKey);
            }
          } else {
            recomputeVisibleSpherePositions();
            computeSphereTargets();
          }
        });
      }
    }

    document.getElementById('vGalaxy').addEventListener('click', () => setMode('galaxy'));
    document.getElementById('vGraph').addEventListener('click', () => setMode('graph'));
    document.getElementById('vCards').addEventListener('click', () => setMode('cards'));

    document.getElementById('vEdges').addEventListener('click', (e) => {
      showEdges = !showEdges;
      e.currentTarget.classList.toggle('active', showEdges);
      e.currentTarget.classList.toggle('off', !showEdges);
      e.currentTarget.textContent = showEdges ? '🔗 Edges: On' : '🔗 Edges: Off';
      needsRender = true;
    });

    document.getElementById('vLabels').addEventListener('click', (e) => {
      const order = ['smart', 'all', 'off'];
      labelMode = order[(order.indexOf(labelMode) + 1) % order.length];
      const names = { smart: '🏷 Labels: Smart', all: '🏷 Labels: Dense', off: '🏷 Labels: Off' };
      e.currentTarget.textContent = names[labelMode];
      e.currentTarget.classList.toggle('active', labelMode === 'all');
      e.currentTarget.classList.toggle('off', labelMode === 'off');
      needsRender = true;
    });

    document.getElementById('vSpin').addEventListener('click', (e) => {
      autoSpin = !autoSpin;
      e.currentTarget.classList.toggle('active', autoSpin);
      e.currentTarget.classList.toggle('off', !autoSpin);
      toast(autoSpin ? 'Auto-rotation enabled' : 'Auto-rotation paused');
    });

    document.getElementById('sideToggle').addEventListener('click', () => {
      sideEl.classList.toggle('closed');
      setTimeout(resizeCanvas, 230);
    });

    document.getElementById('closeDetail').addEventListener('click', () => exitFocus());

    document.getElementById('zIn').addEventListener('click', () => {
      anim = null;
      zoom = Math.min(14, zoom * 1.35);
      needsRender = true;
    });

    document.getElementById('zOut').addEventListener('click', () => {
      anim = null;
      zoom = Math.max(0.32, zoom / 1.35);
      needsRender = true;
    });

    document.getElementById('zFit').addEventListener('click', () => {
      if (selectedKey && mode === 'graph' && focusIsolate) computeFocusTargets(selectedKey);
      else startTransition({ rx: rot.rx, ry: rot.ry }, fitBaseZoom(), { x: 0, y: 0 }, 280);
    });

    document.getElementById('hand').addEventListener('click', (e) => {
      tool = (tool === 'rotate') ? 'pan' : 'rotate';
      const isPan = tool === 'pan';
      canvas.classList.toggle('pan-mode', isPan);
      e.currentTarget.classList.toggle('active-tool', isPan);
      e.currentTarget.textContent = isPan ? '✋' : '🔄';
      toast(isPan ? 'Pan tool active' : 'Rotate tool active');
    });

    document.getElementById('zReset').addEventListener('click', () => {
      resetAll();
      toast('Reset view & filters');
    });

    // Debounced search so typing stays instant even across 10,000+ tables
    searchInput.addEventListener('input', (e) => {
      const val = e.target.value.trim().toLowerCase();
      searchClear.classList.toggle('visible', val.length > 0);
      if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        searchQuery = val;
        buildSide(searchQuery);
        if (mode === 'cards') buildCards(searchQuery);
        needsRender = true;
      }, 110);
    });

    searchClear.addEventListener('click', () => {
      searchInput.value = '';
      searchQuery = '';
      searchClear.classList.remove('visible');
      buildSide('');
      if (mode === 'cards') buildCards('');
      needsRender = true;
      searchInput.focus();
    });

    window.addEventListener('keydown', (e) => {
      const modalOpen = document.getElementById('sqlModal').classList.contains('open');
      if (e.key === 'Escape') {
        if (modalOpen) closeSqlModal();
        else if (selectedKey) exitFocus();
        else resetAll();
        return;
      }
      if (modalOpen) return;
      if (e.key === '/' && document.activeElement !== searchInput && document.activeElement.tagName !== 'TEXTAREA' && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        searchInput.focus();
      }
    });

    /* =====================================================================
       11. LIVE SQL PARSER & 8,300-TABLE / 67-DB STRESS GENERATOR
       ===================================================================== */
    const sqlModal = document.getElementById('sqlModal');
    const sqlInput = document.getElementById('sqlInput');
    const sqlSourceInput = document.getElementById('sqlSourceName');
    const sqlMsg = document.getElementById('sqlMsg');

    function stripSqlComments(sql) {
      let out = '', i = 0, len = sql.length, inS = false, inD = false;
      while (i < len) {
        const ch = sql[i], next = sql[i + 1];
        if (!inS && !inD) {
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
        if (ch === "'" && !inD) inS = !inS;
        else if (ch === '"' && !inS) inD = !inD;
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
          if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
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
            else if (ch === ')') { depth--; if (depth === 0) { endIdx = i; break; } }
          }
        }
        if (endIdx === -1) continue;

        const body = text.slice(openIdx + 1, endIdx);
        const columns = [], fks = [];
        splitTopLevelCommas(body).forEach(rawPart => {
          const part = rawPart.trim();
          if (!part) return;
          const fkMatch = FK_RE.exec(part);
          if (fkMatch) { fks.push({ column: fkMatch[1], ref_table: fkMatch[2] }); return; }
          if (CONSTRAINT_STARTS.test(part)) return;
          const inlineFk = INLINE_FK_RE.exec(part);
          if (inlineFk) fks.push({ column: inlineFk[1], ref_table: inlineFk[2] });
          const colToken = part.split(/\s+/)[0].replace(/^["'`\[]|["'`\]]$/g, '');
          if (colToken && columns.indexOf(colToken) < 0) columns.push(colToken);
        });

        parsed.push({ name: tableName, source: sourceName || 'custom_schema.sql', columns: columns, fks: fks });
        CREATE_RE.lastIndex = endIdx + 1;
      }
      return parsed;
    }

    /* Generate a realistic 67-DB / ~8,300-table / ~22,000-FK corpus to verify 60fps Canvas performance */
    function generate8kCorpus() {
      const prefixes = ['auth', 'pos', 'accounting', 'inventory', 'hr', 'crm', 'billing', 'saas', 'audit', 'warehouse', 'tax', 'payroll'];
      const nouns = ['orders', 'lines', 'entries', 'items', 'accounts', 'invoices', 'payments', 'users', 'sessions', 'logs', 'rules', 'batches', 'moves', 'tickets', 'quotes'];
      const out = [];
      const dbCount = 67;
      for (let d = 0; d < dbCount; d++) {
        const isSmall = d >= 52;
        const dbName = isSmall
          ? `00${d - 40}_migration_${prefixes[d % prefixes.length]}.py`
          : (d % 4 === 0 ? `company_${d + 1}.sqlite3` : (d % 4 === 1 ? `default_${d + 1}.sqlite3` : `service_${d + 1}.sql`));
        const tCount = isSmall ? (4 + (d % 8)) : (145 + (d % 35));
        const namesInDb = [];
        for (let t = 0; t < tCount; t++) {
          const tName = `${prefixes[t % prefixes.length]}_${nouns[Math.floor(t / prefixes.length) % nouns.length]}_${t}`;
          namesInDb.push(tName);
        }
        for (let t = 0; t < tCount; t++) {
          const cols = ['id', 'tenant_id', 'code', 'status', 'created_at'];
          const fks = [];
          if (t > 0) {
            const ref1 = namesInDb[(t - 1) % t];
            cols.push('parent_ref_id');
            fks.push({ column: 'parent_ref_id', ref_table: ref1 });
          }
          if (t > 2) {
            const ref2 = namesInDb[(t * 7) % t];
            cols.push('owner_ref_id');
            fks.push({ column: 'owner_ref_id', ref_table: ref2 });
          }
          if (t % 3 === 0 && t > 4) {
            const ref3 = namesInDb[(t * 13) % t];
            cols.push('ledger_ref_id');
            fks.push({ column: 'ledger_ref_id', ref_table: ref3 });
          }
          if (d > 0 && t === 0) {
            cols.push('core_tenant_id');
            fks.push({ column: 'core_tenant_id', ref_table: 'auth_orders_0' });
          }
          out.push({ name: namesInDb[t], source: dbName, columns: cols, fks: fks });
        }
      }
      return out;
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
      const srcName = sqlSourceInput.value.trim() || 'custom_schema.sql';
      if (!raw) {
        sqlMsg.className = 'modal-msg err';
        sqlMsg.textContent = 'Please paste at least one CREATE TABLE statement.';
        return;
      }
      const extracted = parseSqlDDL(raw, srcName);
      if (!extracted.length) {
        sqlMsg.className = 'modal-msg err';
        sqlMsg.textContent = 'No valid CREATE TABLE definitions found.';
        return;
      }
      if (replaceAll) {
        data = extracted;
      } else {
        const map = new Map();
        data.forEach(t => map.set(keyOf(t), t));
        extracted.forEach(t => map.set(keyOf(t), t));
        data = Array.from(map.values());
      }
      selectedKey = null;
      soloSource = null;
      disabledSources = new Set();
      detailEl.classList.remove('open');
      rebuildDataset(true);
      closeSqlModal();
      toast(`Parsed ${extracted.length} tables from ${srcName}`);
    }

    document.getElementById('openSqlBtn').addEventListener('click', openSqlModal);
    document.getElementById('closeSqlBtn').addEventListener('click', closeSqlModal);
    sqlModal.addEventListener('click', (e) => { if (e.target === sqlModal) closeSqlModal(); });
    document.getElementById('sqlAddBtn').addEventListener('click', () => applySqlImport(false));
    document.getElementById('sqlReplaceBtn').addEventListener('click', () => applySqlImport(true));

    document.getElementById('bench8kBtn').addEventListener('click', () => {
      selectedKey = null;
      soloSource = null;
      disabledSources = new Set();
      detailEl.classList.remove('open');
      data = generate8kCorpus();
      rebuildDataset(true);
      closeSqlModal();
      startTransition({ rx: -0.26, ry: 0.52 }, fitBaseZoom(), { x: 0, y: 0 }, 380);
    });

    document.getElementById('loadSampleSqlBtn').addEventListener('click', () => {
      sqlSourceInput.value = 'hr_payroll.sql';
      sqlInput.value = `-- HR & Payroll Extension
CREATE TABLE departments (
  id SERIAL PRIMARY KEY,
  parent_id INT REFERENCES departments(id),
  cost_center_id INT REFERENCES cost_centers(id),
  name VARCHAR(120) NOT NULL
);

CREATE TABLE employees (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  department_id INT NOT NULL REFERENCES departments(id),
  manager_id INT REFERENCES employees(id),
  hire_date DATE,
  base_salary NUMERIC(12,2)
);

CREATE TABLE payslips (
  id SERIAL PRIMARY KEY,
  employee_id INT NOT NULL REFERENCES employees(id),
  journal_entry_id INT REFERENCES journal_entries(id),
  net_pay NUMERIC(12,2)
);`;
      sqlMsg.className = 'modal-msg ok';
      sqlMsg.textContent = 'Sample SQL loaded — click "Add to Current".';
    });

    document.getElementById('restoreDefaultBtn').addEventListener('click', () => {
      selectedKey = null;
      soloSource = null;
      disabledSources = new Set();
      detailEl.classList.remove('open');
      data = cloneData(DEFAULT_DATA);
      rebuildDataset(true);
      closeSqlModal();
      startTransition({ rx: -0.26, ry: 0.52 }, fitBaseZoom(), { x: 0, y: 0 }, 360);
      toast('Restored 4-DB (22 tables) demo');
    });

    /* =====================================================================
       12. BOOTSTRAP ON WINDOW LOAD
       ===================================================================== */
    window.addEventListener('load', () => {
      initStars();
      if (window.innerWidth <= 960) sideEl.classList.add('closed');

      const ro = new ResizeObserver(() => resizeCanvas());
      ro.observe(graphWrapEl);
      resizeCanvas();

      rebuildDataset(true);
      zoom = fitBaseZoom();
      needsRender = true;
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
    (Canvas 2D 3D network + DB Galaxy + sunflower focus + ER cards; zero dependencies)."""
    import json
    # "</" is escaped as "<\/" so a table/column name can never close the
    # inline <script> tag early; JSON allows the escaped-solidus form.
    # The template ships byte-exactly as designed with
    # `const INJECTED_DATA = null;`; we substitute that one line at
    # generation time so the shipped template stays pristine.
    payload = json.dumps(rows).replace("</", r"<\/")
    return HTML_TEMPLATE.replace(
        "const INJECTED_DATA = null;",
        "const INJECTED_DATA = " + payload + ";")


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