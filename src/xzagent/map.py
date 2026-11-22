def _split_children(items, max_per_dir):
    """Split sorted tree children into (dirs, shown_files, hidden_files_count).

    Directories are always kept; only the files inside a single directory can
    be capped, so the structural map shows every directory no matter how many
    files the project holds.
    """
    dirs = [kv for kv in items if kv[1]]
    files = [kv for kv in items if not kv[1]]
    if max_per_dir is None or len(files) <= max_per_dir:
        return dirs, files, 0
    return dirs, files[:max_per_dir], len(files) - max_per_dir


def _tree_lines(paths, max_per_dir=None):
    tree = {}
    for p in paths:
        node = tree
        for part in p.split("/"):
            node = node.setdefault(part, {})
    lines = []

    def walk(node, prefix):
        items = sorted(node.items(), key=lambda kv: (bool(kv[1]), kv[0]))
        dirs, files, hidden = _split_children(items, max_per_dir)
        entries = [(name, children) for name, children in dirs + files]
        if hidden:
            entries.append(("… (+%d more in this dir)" % hidden, None))
        for i, (name, children) in enumerate(entries):
            last = i == len(entries) - 1
            branch = "└── " if last else "├── "
            if children is None:
                lines.append(prefix + branch + name)
            else:
                lines.append(prefix + branch + name + ("/" if children else ""))
                if children:
                    walk(children, prefix + ("    " if last else "│   "))

    walk(tree, "")
    return lines


def _lang_stats(rows):
    counts = {}
    for r in rows:
        lang = r["lang"]
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    return ", ".join("%s %d" % (lang, n) for lang, n in top) if top else "no languages detected"


def repo_map(con, max_per_dir=25, include_files_list=True, max_list_files=None):
    """Structure-first repo map.

    The file tree always shows *every* directory; only the number of files
    rendered per directory is capped (``max_per_dir``), so arbitrarily large
    projects still produce a complete structural map instead of a globally
    truncated tree. Pass ``max_per_dir=None`` to render every file.
    """
    rows = con.execute(
        "SELECT path, lang, COALESCE(summary,'') AS summary FROM files ORDER BY path"
    ).fetchall()
    sym_rows = con.execute(
        "SELECT file, name FROM symbols WHERE kind IN ('class','function') ORDER BY file, lineno"
    ).fetchall()
    syms = {}
    for r in sym_rows:
        syms.setdefault(r["file"], [])
        if len(syms[r["file"]]) < 6:
            syms[r["file"]].append(r["name"])
    files = [r["path"] for r in rows]
    out = ["# Repo map", "", "%d files — %s" % (len(files), _lang_stats(rows)), ""]
    out.extend(_tree_lines(files, max_per_dir))
    out.append("")
    if include_files_list:
        out.append("## Files")
        listed = rows if max_list_files is None else rows[:max_list_files]
        for r in listed:
            names = syms.get(r["path"], [])
            line = "- `%s`" % r["path"]
            if r["summary"]:
                line += " — " + r["summary"]
            if names:
                line += "  `" + "`, `".join(names) + "`"
            out.append(line)
    return "\n".join(out).rstrip() + "\n"