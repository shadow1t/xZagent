import os

from .map import repo_map
from .rank import rank_files


def _outline(con, path):
    rows = con.execute(
        "SELECT kind, name, lineno FROM symbols WHERE file=? AND kind IN ('class','function') ORDER BY lineno",
        (path,),
    ).fetchall()
    return ["%s %s (line %d)" % (r["kind"], r["name"], r["lineno"]) for r in rows]


def build_pack(con, root, query, top_k=8, max_chars_per_file=4000):
    ranked = rank_files(con, query, top_k=top_k)
    lines = [
        "# Context Pack",
        "",
        "## Root",
        "`%s`" % root,
        "",
        "## Task",
        query,
        "",
        "## Relevant files",
    ]
    for path, score, reasons in ranked:
        lines.append("- `%s` (score %.2f) — %s" % (path, score, ", ".join(reasons[:3])))
    lines.append("")
    lines.append("## Repo map")
    lines.append("```")
    lines.append(repo_map(con, max_per_dir=8, include_files_list=False).rstrip())
    lines.append("```")
    lines.append("")
    lines.append("## File excerpts")
    for path, score, _reasons in ranked:
        lines.append("### `%s` (score %.2f)" % (path, score))
        outline = _outline(con, path)
        if outline:
            lines.append("Symbols: " + "; ".join(outline[:20]))
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            content = ""
        lines.append("```")
        lines.append(content[:max_chars_per_file])
        lines.append("```")
        lines.append("")
    return "\n".join(lines)