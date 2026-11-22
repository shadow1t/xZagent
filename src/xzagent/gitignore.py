import os
import re


def _translate(pat):
    res = ""
    i, n = 0, len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            if pat.startswith("**/", i):
                res += "(?:[^/]+/)*"
                i += 3
            elif pat.startswith("**", i):
                res += ".*"
                i += 2
            else:
                res += "[^/]*"
                i += 1
        elif c == "?":
            res += "[^/]"
            i += 1
        elif c == "[":
            j = pat.find("]", i + 1)
            if j == -1:
                res += re.escape("[")
                i += 1
            else:
                res += pat[i : j + 1]
                i = j + 1
        else:
            res += re.escape(c)
            i += 1
    return res


class GitignoreMatcher:
    """Minimal .gitignore matcher: *, **, ?, dir-only patterns, anchoring, negation.

    Each rule keeps a self regex (matches the entry itself) and, for dir-only
    rules, a child regex (matches anything under that directory).
    """

    def __init__(self):
        self.rules = []

    def add_gitignore(self, base, text):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:]
            dir_only = line.endswith("/")
            if dir_only:
                line = line[:-1]
            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            if not line:
                continue
            if not anchored and "/" not in line:
                body = r"(?:^|/)" + _translate(line)
            else:
                body = r"^" + _translate(line)
            self_re = re.compile(body + r"$")
            child_re = re.compile(body + r"(?:/.*)?$") if dir_only else None
            self.rules.append((base, self_re, child_re, negated, dir_only))

    def _match_rules(self, root, path, is_dir):
        """Return (ignored, negated_override) using gitignore precedence:
        the last matching rule wins; a negation (!) clears the ignore and
        marks a negated_override that defeats built-in default ignores."""
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if rel == ".":
            return (False, False)
        ignored = False
        negated_override = False
        for base, self_re, child_re, negated, dir_only in self.rules:
            base_rel = os.path.relpath(base, root).replace(os.sep, "/")
            if base_rel == ".":
                target = rel
            elif rel == base_rel:
                target = ""
            elif rel.startswith(base_rel + "/"):
                target = rel[len(base_rel) + 1 :]
            else:
                continue
            hit = False
            if is_dir:
                hit = bool(self_re.search(target)) or (child_re is not None and bool(child_re.search(target)))
            else:
                hit = (not dir_only and bool(self_re.search(target))) or (
                    child_re is not None and bool(child_re.search(target))
                )
            if hit:
                ignored = not negated
                negated_override = negated
        return (ignored, negated_override)

    def is_ignored(self, root, path, is_dir):
        return self._match_rules(root, path, is_dir)[0]

    def is_negation_override(self, root, path, is_dir):
        """True if the last matching rule is a '!pattern' negation.

        Use this to let users re-include files that built-in defaults
        would otherwise skip (e.g. '!migrations/' in .xzagentignore).
        """
        return self._match_rules(root, path, is_dir)[1]