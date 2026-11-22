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


def dir_allowed(path, only_dirs):
    """True if path lives under one of the required directory names (any depth).

    only_dirs='api,ui' keeps every file whose path has 'api' or 'ui' as ANY
    parent segment (root-level and nested both).
    """
    if not only_dirs:
        return True
    return bool(set(parse_csv(only_dirs)) & _dir_parts(path))


_TEST_DIRS = {"test", "tests", "__tests__", "__snapshots__",
              "spec", "specs", "e2e", "cypress", "testing"}
_TEST_NAME_PATTERNS = (
    "test_*", "tests_*", "test-*",          # pytest / their tests_*.py style
    "*_test", "*_test.*", "*_tests.*",      # foo_test.py, *_test.go, foo_tests.rs
    "*.test.*", "*.spec.*",                 # jest / vitest / jasmine
    "*Test.java", "*Tests.java",            # JUnit
    "conftest.py",
)


def is_test_file(path):
    """Heuristic: does this path look like a test file? (used by --no-tests)

    Matches both directory placement (tests/, __tests__/, spec/, e2e/, cypress/)
    and file naming (test_*.py, tests_*.py, *_test.*, *.test.*, *.spec.*,
    *_Test.java, conftest.py, ...). Deliberately errs on the side of matching:
    a false positive only hides a file the agent can still find by pattern.
    """
    base = posixpath.basename(path)
    if _dir_parts(path) & _TEST_DIRS:
        return True
    lowered = base.lower()
    return any(fnmatch.fnmatchcase(base, p) or fnmatch.fnmatchcase(lowered, p)
               for p in _TEST_NAME_PATTERNS)


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
                  exclude_exts=None, exclude_dirs=None, only_dirs=None,
                  only_exts=None, recent=False, top=None, no_tests=False):
    """Return matching files as dicts: path, lang, ext, mtime, modified, symbols.

    pattern:  glob on basename or full path ('api_v2*', '*Dialog*', 'docs/*.md')
    lang:     language name or CSV list ('python', 'typescript,tsx')
    ext:      extension or CSV list ('.md', 'md,.txt') — alias: only_exts
    symbol:   only files defining a class/function matching glob ('Dialog*', '*Handler')
    exclude_exts: CSV of extensions to skip ('.md,.txt,.log')
    exclude_dirs: CSV of directory names to skip ('docs,tests,generated')
    only_dirs: CSV of directory names the file MUST live under ('api,ui');
               matches any parent segment at any depth
    only_exts: CSV of extensions the file MUST have ('.py,.tsx'); alias of ext,
               kept so ask/find/MCP expose one consistent flag name
    recent:   sort by last-modified (newest first) instead of by path
    top:      limit result count
    no_tests: drop files that look like tests (is_test_file heuristic)
    """
    exclude_exts = normalize_exts(exclude_exts)
    exclude_dirs = parse_csv(exclude_dirs)
    only_dir_set = set(parse_csv(only_dirs))
    patterns = parse_csv(pattern)
    langs = {l.lower() for l in parse_csv(lang)}
    exts = set(normalize_exts(ext)) | set(normalize_exts(only_exts))
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
        if only_dir_set and not (only_dir_set & _dir_parts(path)):
            continue
        if no_tests and is_test_file(path):
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


def recently_modified(con, count=15, exclude_exts=None, exclude_dirs=None,
                      only_dirs=None, only_exts=None, no_tests=False):
    """The N most recently modified files in the index (newest first).

    Great for 'where was I working' tasks; honors exclusions and only_* filters.
    """
    rows = find_matching(con, exclude_exts=exclude_exts, exclude_dirs=exclude_dirs,
                         only_dirs=only_dirs, only_exts=only_exts, no_tests=no_tests)
    rows.sort(key=lambda x: -x["mtime"])
    return rows[:int(count)]


# Directory names that are commonly noise for search results.
NOISE_DIR_HINTS = set(DEFAULT_IGNORE_DIRS)

def find_on_disk(root, pattern=None, lang=None, ext=None, only_exts=None,
                 exclude_exts=None, exclude_dirs=None, only_dirs=None,
                 no_tests=False, extra_ignores=None, no_default_skips=False):
    """Direct-disk glob fallback for ctxp find.

    find is an explicit targeting command: when the user asks for files BY
    NAME, the matches must show even if the indexer skipped them as noise
    (tmp_*, *.min.js, lockfiles, *.log, oversized files, ...). This walks
    the tree exactly like scanner.scan() — honoring .gitignore,
    .xzagentignore, extra ignores, and built-in noise DIRECTORIES — but
    lets noise-named FILES through, then applies the same find filters.

    Returns rows in find_matching() shape with an extra on_disk=True flag
    (cmd_find merges them with index rows and marks them 'not indexed').
    Symbol filtering is impossible here (no index), so callers should skip
    the disk pass when --symbol is given.
    """
    from .gitignore import GitignoreMatcher
    from .languages import lang_for
    from .scanner import _ignored_name as _ignored_dir

    root = os.path.abspath(root)
    patterns = parse_csv(pattern)
    langs = {l.lower() for l in parse_csv(lang)}
    exts = set(normalize_exts(ext)) | set(normalize_exts(only_exts))
    exclude_exts_n = normalize_exts(exclude_exts)
    exclude_dirs_n = parse_csv(exclude_dirs)
    only_dir_set = set(parse_csv(only_dirs))

    matcher = GitignoreMatcher()
    extra_ignores = list(extra_ignores or [])
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        gi = os.path.join(dirpath, ".gitignore")
        if os.path.isfile(gi):
            with open(gi, encoding="utf-8", errors="ignore") as fh:
                matcher.add_gitignore(dirpath, fh.read())
        if dirpath == root:
            xzignore = os.path.join(root, ".xzagentignore")
            if os.path.isfile(xzignore):
                with open(xzignore, encoding="utf-8", errors="ignore") as fh:
                    matcher.add_gitignore(root, fh.read())
            if extra_ignores:
                matcher.add_gitignore(root, "\n".join(extra_ignores))

        keep = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            ignored = ((no_default_skips is False and _ignored_dir(d))
                       or matcher.is_ignored(root, full, True))
            if ignored and not matcher.is_negation_override(root, full, True):
                continue
            keep.append(d)
        dirnames[:] = keep

        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if matcher.is_ignored(root, full, False) \
                    and not matcher.is_negation_override(root, full, False):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if patterns and not glob_match(rel, patterns):
                continue
            f_lang = (lang_for(rel) or "text").lower()
            if langs and f_lang not in langs:
                continue
            f_ext = os.path.splitext(rel)[1].lower()
            if exts and f_ext not in exts:
                continue
            if only_dir_set and not (only_dir_set & _dir_parts(rel)):
                continue
            if no_tests and is_test_file(rel):
                continue
            if path_excluded(rel, exclude_exts_n, exclude_dirs_n):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not os.path.isfile(full):
                continue
            results.append({
                "path": rel,
                "lang": lang_for(rel) or "text",
                "ext": f_ext,
                "mtime": st.st_mtime,
                "modified": _modified_iso(st.st_mtime),
                "symbols": None,
                "on_disk": True,
            })
    results.sort(key=lambda r: r["path"])
    return results