import os
from dataclasses import dataclass

from .gitignore import GitignoreMatcher
from .languages import MAX_FILE_BYTES, SPECIAL_FILES, TEXT_EXTS, lang_for

DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".next", ".nuxt", "vendor", ".idea", ".vscode",
    "coverage", ".mypy_cache", ".pytest_cache", ".tox", ".cache", "out", ".output",
}


@dataclass
class ScannedFile:
    path: str
    abspath: str
    ext: str
    size: int
    mtime: float
    lang: str


def scan(root):
    """Walk root and return indexable text files, honoring .gitignore and defaults."""
    root = os.path.abspath(root)
    matcher = GitignoreMatcher()
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        gi = os.path.join(dirpath, ".gitignore")
        if os.path.isfile(gi):
            with open(gi, encoding="utf-8", errors="ignore") as fh:
                matcher.add_gitignore(dirpath, fh.read())
        dirnames[:] = [
            d for d in dirnames
            if d not in DEFAULT_IGNORE_DIRS
            and not matcher.is_ignored(root, os.path.join(dirpath, d), True)
        ]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            if fn in DEFAULT_IGNORE_DIRS or matcher.is_ignored(root, full, False):
                continue
            if os.path.basename(full) in SPECIAL_FILES:
                ext = SPECIAL_FILES[os.path.basename(full)]
            else:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXTS:
                    continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not os.path.isfile(full) or st.st_size > MAX_FILE_BYTES:
                continue
            results.append(ScannedFile(
                path=os.path.relpath(full, root).replace(os.sep, "/"),
                abspath=full,
                ext=ext,
                size=st.st_size,
                mtime=st.st_mtime,
                lang=lang_for(full) or "text",
            ))
    results.sort(key=lambda f: f.path)
    return results
