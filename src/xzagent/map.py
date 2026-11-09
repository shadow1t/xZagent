def _tree_lines(paths):
    tree = {}
    for p in paths:
        node = tree
        for part in p.split("/"):
            node = node.setdefault(part, {})
    lines = []

    def walk(node, prefix):
        items = sorted(node.items(), key=lambda kv: (bool(kv[1]), kv[0]))
        for i, (name, children) in enumerate(items):
            last = i == len(items) - 1
            lines.append(prefix + ("└── " if last else "├── ") + name + ("/" if children else ""))
            if children:
                walk(children, prefix + ("    " if last else "│   "))

    walk(tree, "")
    return lines


def repo_map(con, max_files=400):
    rows = con.execute("SELECT path, COALESCE(summary,'') AS summary FROM files ORDER BY path").fetchall()
    sym_rows = con.execute(
        "SELECT file, name FROM symbols WHERE kind IN ('class','function') ORDER BY file, lineno"
    ).fetchall()
    syms = {}
    for r in sym_rows:
        syms.setdefault(r["file"], [])
        if len(syms[r["file"]]) < 6:
            syms[r["file"]].append(r["name"])
    files = [r["path"] for r in rows]
    truncated = len(files) > max_files
    files = files[:max_files]
    out = ["# Repo map", ""]
    out.extend(_tree_lines(files))
    if truncated:
        out.append("└── ... (%d more files)" % (len(rows) - max_files))
    out.append("")
    out.append("## Files")
    for r in rows[:max_files]:
        names = syms.get(r["path"], [])
        line = "- `%s`" % r["path"]
        if r["summary"]:
            line += " — " + r["summary"]
        if names:
            line += "  `" + "`, `".join(names) + "`"
        out.append(line)
    return "\n".join(out) + "\n"
