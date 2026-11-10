import re


def _tokens(query):
    return [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1]


def _fts_query(query):
    toks = _tokens(query)
    if not toks:
        return None
    return " OR ".join('"%s"' % t for t in toks)


def rank_files(con, query, top_k=10, graph_boost=True):
    """Rank files for a natural-language task. Lower score = better (bm25 based)."""
    scores, reasons = {}, {}

    def add(path, delta, why):
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

    def _word_hit(text, t):
        return re.search(r"(?<![\w])" + re.escape(t) + r"(?![\w])", text, re.IGNORECASE) is not None

    for t in toks:
        for row in con.execute("SELECT path FROM files WHERE path LIKE ?", ("%" + t + "%",)).fetchall():
            if _word_hit(row["path"], t):
                add(row["path"], -2.0, "path contains '%s'" % t)
        for row in con.execute("SELECT DISTINCT file, name FROM symbols WHERE name LIKE ?", ("%" + t + "%",)).fetchall():
            if _word_hit(row["name"], t):
                add(row["file"], -5.0, "symbol contains '%s'" % t)
        for row in con.execute("SELECT path, summary FROM files WHERE summary LIKE ?", ("%" + t + "%",)).fetchall():
            if _word_hit(row["summary"] or "", t):
                add(row["path"], -3.0, "summary contains '%s'" % t)
    if graph_boost and scores:
        strong = sorted(scores, key=scores.get)[: max(3, top_k // 2)]
        for path in strong:
            for row in con.execute(
                "SELECT to_file FROM edges WHERE from_file=? UNION SELECT from_file FROM edges WHERE to_file=?",
                (path, path),
            ).fetchall():
                add(row[0], -1.0, "linked to '%s'" % path)
    ranked = sorted(scores.items(), key=lambda kv: kv[1])[:top_k]
    return [(path, round(score, 3), reasons.get(path, [])) for path, score in ranked]
