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
ctxp map ./my-project                   # print the repo map (every directory shown)
ctxp map ./my-project --per-dir 40     # up to 40 files per directory in the tree
ctxp map ./my-project --full           # every single file, no capping
ctxp ask ./my-project "fix login bug"  # rank relevant files
ctxp find ./my-project "api_v2*"       # find files by name/path glob
ctxp find ./my-project "*Dialog*.tsx" --symbol Dialog* --recent  # + filters
ctxp find ./my-project --only-dirs api,ui            # only files under api/ or ui/
ctxp ask ./my-project "billing" --only-ext .py       # rank only Python files
ctxp ask ./my-project "billing" --no-tests           # rank code, not its tests
ctxp ask ./my-project "billing" --skip-ext .md,.txt --skip-dirs docs  # skip noise
ctxp pack ./my-project "add dark mode" -o context.md   # full context pack
ctxp graph ./my-project login           # call graph for a symbol
ctxp graph ./my-project --path app/auth.py           # neighbors of a file
```

## Scales to large projects

The map never hides a directory: the tree always shows **every folder** and only caps the number of files listed *per directory* (`… (+N more in this dir)`), so a repo with 3000 UI files still renders a complete structural map a human or agent can read. Use `--full` when you want every single file, or `--per-dir N` to tune it.

## Staying focused

Target exactly the files a task needs (`ctxp find "api_v2*"` instead of every API file across versions), restrict work to given areas (`--only-dirs api,ui` keeps only files under those folders, at any depth) or given file types (`--only-ext .py,.tsx` ranks only real code files), drop test files that hijack rankings (`--no-tests`), skip noise at search time (`--skip-ext .md,.txt`, `--skip-dirs docs,generated`), prefer recent edits (`--recent`), and exclude anything permanently from the index with a `.xzagentignore` file in the project root (gitignore syntax). The indexer also skips obvious noise **by name** out of the box: `tmp_*` probes, `*_bak.*` backups, `*.min.js` bundles, lockfiles, `*.log`, and framework artifact folders like `migrations/` (which still feed `db_schema`). Re-include any default with a `!pattern` rule in `.xzagentignore`, turn all defaults off with `ctxp --index-all`, or add your own one-off excludes with `ctxp --skip 'pattern/'`.

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

Tools exposed: `repo_map`, `find`, `find_files`, `recent_files`, `file_outline`, `call_graph`, `neighbors`, `context_pack`, `db_schema`. Full guide: [docs/USAGE.md](docs/USAGE.md).

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

أداة تعمل محلياً 100% بدون إنترنت أو مفاتيح API. عندما يعمل الـ agent على مشروع كبير، الأداة تبني خريطة للمشروع + graph للعلاقات + قائمة بالملفات المهمة للمهمة، فتوفر الوقت والـ tokens.