# xZagent — offline context packer for AI agents

xzagent (`ctxp`) helps an AI agent working on a **local project** know *where the story is*: it builds a repo map, an import/call graph, and ranks the files that matter for a given task — **fully offline, no API keys, no internet**.

## Why

When an agent faces a project with many files it tends to open files randomly, get lost, waste tokens and time. xzagent gives it a compressed map plus a ranked shortlist, so it opens 5–10 relevant files instead of 200.

## Install

```bash
pip install .
```

Requires Python ≥ 3.9. Pure standard library — nothing to download at runtime.

## CLI usage

```bash
ctxp index ./my-project                 # build the index (.xzagent/index.db)
ctxp map ./my-project                   # print the repo map
ctxp ask ./my-project "fix login bug"  # rank relevant files
ctxp pack ./my-project "add dark mode" -o context.md   # full context pack
ctxp graph ./my-project login           # call graph for a symbol
ctxp graph ./my-project --path app/auth.py           # neighbors of a file
```

## MCP server (for Claude Code / Cursor / Windsurf)

Add to your MCP config (stdio, newline-delimited JSON-RPC):

```json
{
  "mcpServers": {
    "xzagent": {
      "command": "ctxp",
      "args": ["serve", "/absolute/path/to/project"]
    }
  }
}
```

Tools exposed: `repo_map`, `find_files`, `file_outline`, `call_graph`, `neighbors`, `context_pack`.

## How it works

1. **Scanner** walks the project honoring `.gitignore` and default ignores.
2. **Symbol extraction** via Python `ast` + regex for JS/TS, Go, Rust, Java, C/C++ and more.
3. **SQLite FTS5 index** for full-text (BM25) search over paths, symbols and content.
4. **Graph**: import edges resolved to real files + call edges between symbols.
5. **Ranker**: BM25 + path/symbol boosts + graph proximity boost.

## Tests

```bash
python -m unittest discover -s tests -v
```

## العربية

أداة تعمل محلياً 100% بدون إنترنت أو مفاتيح API. όταν agent يعمل على مشروع كبير، الأداة تبني خريطة للمشروع + graph للعلاقات + قائمة بالملفات المهمة للمهمة، في sparen وقت و tokens.
