import re

from .find import glob_match, normalize_exts, parse_csv, path_excluded


_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "when", "in",
    "on", "at", "to", "for", "of", "with", "by", "from", "as", "is", "are",
    "was", "were", "be", "been", "it", "its", "this", "that", "these", "those",
    "i", "you", "he", "she", "we", "they", "them", "his", "her", "their",
    "our", "my", "your", "not", "no", "do", "does", "did", "can", "could",
    "should", "would", "will", "shall", "may", "might", "must", "so", "than",
    "too", "very", "just", "about", "into", "over", "after", "before", "how",
    "what", "which", "who", "whom", "why", "all", "any", "some", "such",
}


def _tokens(query):
    return [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1 and t.lower() not in _STOPWORDS]


def _fts_query(query):
    toks = _tokens(query)
    if not toks:
        return None
    return " OR ".join('"%s"' % t for t in toks)


def rank_files(con, query, top_k=10, graph_boost=True, pattern=None, only_lang=None,
               exclude_exts=None, exclude_dirs=None, recent_bias=False):
    """Rank files for a natural-language task. Lower score = better (bm25 based).

    pattern:       only rank files whose basename or path matches this glob
                   ('api_v2*', '*Dialog*.tsx', 'web/**')
    only_lang:     CSV/list of languages to keep ('python', 'typescript,tsx')
    exclude_exts:  CSV/list of extensions to skip ('.md,.txt')
    exclude_dirs:  CSV/list of directory names to skip ('docs,tests')
    recent_bias:   prefer recently modified files among the matches (adds a
                   small penalty proportional to age, up to +5)
    """
    scores, reasons = {}, {}
    exclude_exts = normalize_exts(exclude_exts)
    exclude_dirs = {d.lower() for d in parse_csv(exclude_dirs)}
    patterns = parse_csv(pattern)
    langs = {l.lower() for l in parse_csv(only_lang)}
    meta = {}
    for r in con.execute("SELECT path, ext, lang, mtime FROM files").fetchall():
        meta[r["path"]] = (r["ext"] or "", (r["lang"] or "").lower(), r["mtime"])

    def allowed(path):
        m = meta.get(path)
        if m is None:
            return False
        if patterns and not glob_match(path, patterns):
            return False
        if langs and m[1] not in langs:
            return False
        if path_excluded(path, exclude_exts, exclude_dirs):
            return False
        return True

    def add(path, delta, why):
        if not allowed(path):
            return
        scores[path] = scores.get(path, 0.0) + delta
        reasons.setdefault(path, [])
        if why not in reasons[path]:
            reasons[path].append(why)

    fts = _fts_query(query)
    if fts:
        for row in con.execute(
            "SELECT path, bm25(content_fts) AS s FROM content_fts WHERE content_fts MATCH ? ORDER BY s LIMIT 300",
            (fts,),
        ).fetchall():
            add(row["path"], row["s"], "content match")
    toks = _tokens(query)

    def _words(text):
        parts = re.split(r"[^A-Za-z0-9]+", text or "")
        words = []
        for p in parts:
            words.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", p))
        return [w.lower() for w in words]

    def _word_hit(text, t):
        return t.lower() in _words(text)

    for t in toks:
        for row in con.execute("SELECT path FROM files WHERE path LIKE ?", ("%" + t + "%",)).fetchall():
            if _word_hit(row["path"], t):
                add(row["path"], -10.0, "path contains '%s'" % t)
        for row in con.execute("SELECT DISTINCT file, name FROM symbols WHERE name LIKE ?", ("%" + t + "%",)).fetchall():
            if _word_hit(row["name"], t):
                add(row["file"], -15.0, "symbol contains '%s'" % t)
        for row in con.execute("SELECT path, summary FROM files WHERE summary LIKE ?", ("%" + t + "%",)).fetchall():
            if _word_hit(row["summary"] or "", t):
                add(row["path"], -12.0, "summary contains '%s'" % t)
    if graph_boost and scores:
        strong = sorted(scores, key=scores.get)[: max(3, top_k // 2)]
        for path in strong:
            for row in con.execute(
                "SELECT to_file FROM edges WHERE from_file=? UNION SELECT from_file FROM edges WHERE to_file=?",
                (path, path),
            ).fetchall():
                add(row[0], -4.0, "linked to '%s'" % path)
    if recent_bias and scores:
        newest = max(m[2] for m in meta.values()) if meta else 0.0
        for path in list(scores):
            mtime = meta.get(path, (None, None, 0.0))[2]
            age_days = max(0.0, (newest - mtime) / 86400.0)
            scores[path] += min(age_days / 30.0, 1.0) * 5.0
    ranked = sorted(scores.items(), key=lambda kv: kv[1])
    if top_k:
        ranked = ranked[:top_k]
    return [(path, round(score, 3), reasons.get(path, [])) for path, score in ranked]