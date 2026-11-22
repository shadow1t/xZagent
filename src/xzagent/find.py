"""Pattern-based file targeting: glob filters, language/ext/symbol filters,
exclusion rules, and recency sorting.

This is what an agent should use when the task names specific files or
categories of files (e.g. "only the api_v2 files", "skip .md docs",
"the last 5 dialogs I edited") instead of ranking the whole repo.
"""
import fnmatch
import os
import posixpath
from datetime import datetime

from .scanner import DEFAULT_IGNORE_DIRS


def parse_csv(value):
    """'a, b' -> ['a', 'b']; None/'' -> []. Accepts a list or a CSV string."""
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in value.split(",") if v.strip()]


def normalize_exts(exts):
    """['md', '.txt'] -> ['.md', '.txt']"""
    return [("." + e if not e.startswith(".") else e).lower() for e in parse_csv(exts)]


def _dir_parts(path):
    return {p.lower() for p in path.split("/")[:-1]}


def path_excluded(path, exclude_exts, exclude_dirs):
    """True if path matches any exclusion (extension list or directory name list)."""
    if exclude_exts and os.path.splitext(path)[1].lower() in set(exclude_exts):
        return True
    if exclude_dirs and (set(parse_csv(exclude_dirs)) & _dir_parts(path)):
        return True
    return False


def glob_match(path, patterns):
    """Case-insensitive glob match against the basename AND the full relative path.

    Supports comma-separated multiple patterns: 'api_v2*, *Dialog*.tsx'.
    '**/' prefix matches at any depth (handled by fnmatch against the path).
    """
    base = posixpath.basename(path)
    for pat in patterns:
        if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(path, pat):
            return True
    return False


def _modified_iso(mtime):
    try:
        return datetime.fromtimestamp(mtime).isoformat(sep=" ", timespec="minutes")
    except (OSError, ValueError, OverflowError):
        return ""


def find_matching(con, pattern=None, lang=None, ext=None, symbol=None,
                  exclude_exts=None, exclude_dirs=None, recent=False, top=None):
    """Return matching files as dicts: path, lang, ext, mtime, modified, symbols.

    pattern:  glob on basename or full path ('api_v2*', '*Dialog*', 'docs/*.md')
    lang:     language name or CSV list ('python', 'typescript,tsx')
    ext:      extension or CSV list ('.md', 'md,.txt')
    symbol:   only files defining a class/function matching glob ('Dialog*', '*Handler')
    exclude_exts: CSV of extensions to skip ('.md,.txt,.log')
    exclude_dirs: CSV of directory names to skip ('docs,tests,generated')
    recent:   sort by last-modified (newest first) instead of by path
    top:      limit result count
    """
    exclude_exts = normalize_exts(exclude_exts)
    exclude_dirs = parse_csv(exclude_dirs)
    patterns = parse_csv(pattern)
    langs = {l.lower() for l in parse_csv(lang)}
    exts = set(normalize_exts(ext))
    sym_pats = parse_csv(symbol)

    sym_files = {}
    if sym_pats:
        for r in con.execute(
            "SELECT file, name FROM symbols WHERE kind IN ('class','function')"
        ).fetchall():
            for sp in sym_pats:
                if fnmatch.fnmatch(r["name"], sp):
                    sym_files.setdefault(r["file"], set()).add(r["name"])

    results = []
    for r in con.execute("SELECT path, lang, ext, mtime FROM files ORDER BY path").fetchall():
        path = r["path"]
        if patterns and not glob_match(path, patterns):
            continue
        if langs and (r["lang"] or "").lower() not in langs:
            continue
        if exts and (r["ext"] or "").lower() not in exts:
            continue
        if sym_pats and path not in sym_files:
            continue
        if path_excluded(path, exclude_exts, exclude_dirs):
            continue
        results.append({
            "path": path,
            "lang": r["lang"],
            "ext": r["ext"],
            "mtime": r["mtime"],
            "modified": _modified_iso(r["mtime"]),
            "symbols": sorted(sym_files.get(path, [])) if sym_pats else None,
        })
    if recent:
        results.sort(key=lambda x: -x["mtime"])
    if top:
        results = results[:int(top)]
    return results


def recently_modified(con, count=15, exclude_exts=None, exclude_dirs=None):
    """The N most recently modified files in the index (newest first).

    Great for 'where was I working' tasks; honors exclusions.
    """
    rows = find_matching(con, exclude_exts=exclude_exts, exclude_dirs=exclude_dirs)
    rows.sort(key=lambda x: -x["mtime"])
    return rows[:int(count)]


# Directory names that are commonly noise for search results.
NOISE_DIR_HINTS = set(DEFAULT_IGNORE_DIRS)