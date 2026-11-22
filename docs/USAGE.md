# xzagent — Usage Guide

Local, offline code-context tool: indexes a project into SQLite (FTS5), ranks
relevant files for a task, finds files by pattern, and exposes the result via
CLI and MCP (for AI agents).

## Install

```bash
pip install -e .          # provides the `ctxp` command
# or without install:
python -m xzagent --help  # with PYTHONPATH=<repo>/src
```

Requires Python ≥ 3.9, stdlib only. No API keys, no internet.

The index lives at `<ROOT>/.xzagent/index.db` and rebuilds automatically when
files change or when the tool code changes.

## CLI commands

All commands take an optional project root (defaults to the current directory):

```bash
ctxp index [ROOT]                 # build/refresh the index
ctxp --reindex index [ROOT]       # force full rebuild
ctxp map [ROOT] [-o map.md]       # repo map (every directory always shown)
ctxp ask [ROOT] "task" --top 5    # rank most relevant files for a task
ctxp find [ROOT] PATTERN          # find files by name/path glob
ctxp pack [ROOT] "task" -o context.md   # markdown context pack for an LLM
ctxp graph [ROOT] SYMBOL          # definitions/callers/callees of a symbol
ctxp graph [ROOT] --path FILE     # imports + calls linked to a file
ctxp schema [ROOT] [--pg DSN]     # DB schema (tables, columns, FKs)
ctxp serve [ROOT]                 # MCP server (stdio) for AI apps
```

Global options: `--db PATH`, `--reindex`.

## Targeting files: `ctxp find`

Use this when the task names specific files or a versioned subset, so the
agent works on `api_v2*` files instead of every API file across versions.
Globs match the basename AND the full relative path (case-insensitive).

```bash
ctxp find . "api_v2*"                 # files whose name starts with api_v2
ctxp find . "*_v2"                    # files whose name ends with _v2
ctxp find . "*Dialog*.tsx"            # dialog components
ctxp find . "api_v2*, *Dialog*"       # multiple patterns, comma-separated
ctxp find . --ext .tsx,.ts            # by extension
ctxp find . --lang python,tsx         # by language
ctxp find . --symbol "Dialog*"        # files defining a class/function
ctxp find . --recent                  # most recently modified first
ctxp find . --recent --top 5          # the last 5 edited files
```

Every result shows the file's last-modified time, and `--symbol` results also
show the matching symbol names.

## Skipping noise in searches (`ask` / `find`)

Keep results focused by excluding documentation, generated code, or any
folder by name:

```bash
ctxp ask . "billing charge flow" --skip-ext .md,.txt
ctxp ask . "login bug" --skip-dirs docs,tests,generated --skip-ext .md,.log
ctxp ask . "fix api" --pattern "api_v2*"       # only rank v2 API files
ctxp ask . "render dialog" --lang tsx         # only rank TSX files
ctxp ask . "continue work" --recent            # prefer recent modifications
ctxp ask . "task" --top 0                      # all matches, no cap
```

`--recent` adds a small recency penalty so, among equally relevant matches,
the most recently edited files come first.

## Permanently excluding files from the index: `.xzagentignore`

Create a `.xzagentignore` file in the project root (same syntax as
`.gitignore`). Anything it matches is never indexed at all, so it never
appears in searches, maps, or packs:

```gitignore
# .xzagentignore — content unnecessary for code intelligence
*.md
docs/
generated/
**/*.min.js
```

`.gitignore` and built-in defaults (`node_modules/`, `dist/`, `build/`,
`vendor/`, `coverage/`, `target/`, caches, ...) are always honored too.

## The repo map

The tree always shows **every directory**; only the files inside each
directory are capped (`--per-dir`, default 25, with an explicit
`… (+N more in this dir)` marker). `--full` renders every single file.

```bash
ctxp map .                        # tree + files list, capped per directory
ctxp map . --per-dir 60 -o map.md # up to 60 files per directory
ctxp map . --full -o map.md       # every file, no capping
```

## MCP setup (AI apps)

### Claude Code
```bash
claude mcp add xzagent -- ctxp serve /path/to/project
```
or edit `~/.claude.json`:
```json
{
  "mcpServers": {
    "xzagent": { "command": "ctxp", "args": ["serve", "E:\\path\\to\\project"] }
  }
}
```
Verify inside Claude Code with `/mcp`.

### Cursor / Windsurf / generic stdio config
Same `mcpServers` block:
```json
{
  "mcpServers": {
    "xzagent": { "command": "ctxp", "args": ["serve", "/absolute/path/to/project"] }
  }
}
```

### Antigravity
Edit `%USERPROFILE%\.gemini\antigravity\mcp_config.json` with the same
`mcpServers` block, then restart the app.

## Multiple projects

Each MCP server points at ONE root, with its own independent index:

```json
{
  "mcpServers": {
    "xzagent_app": { "command": "ctxp", "args": ["serve", "E:\\proj\\app"] },
    "xzagent_bot": { "command": "ctxp", "args": ["serve", "E:\\proj\\bot"] }
  }
}
```

## MCP tools (for AI agents)

| Tool | Use it when |
|---|---|
| `repo_map` | You need the project's structure first. Every directory is shown; `per_dir` caps files per directory, `files: true` adds key symbols per file. |
| `find` | The task names file patterns ("the api_v2 files", "*Dialog*.tsx", "files ending _v2"), or you must restrict work to a subset. Params: `pattern`, `lang`, `ext`, `symbol`, `skip_ext`, `skip_dirs`, `recent`, `top`. |
| `find_files` | You need the most relevant files for a natural-language task. Same skip/restrict/pattern params plus `task` and `top_k`. Use `recent: true` to prefer recently edited matches. |
| `recent_files` | "What was I working on?" / continue-the-last-task. Returns the most recently modified files with timestamps, optional `pattern`/`lang`/`skip_*` filters. |
| `file_outline` | You are about to read or edit one file and want its classes/functions with line numbers first. |
| `call_graph` | Before changing a class/function: where it is defined, who calls it, what it references. |
| `neighbors` | Before editing a file: which files import/call it (blast radius). |
| `context_pack` | You need a ready-to-paste markdown pack (relevant files + map + excerpts) for a task. |
| `db_schema` | The task involves database tables, columns, or foreign keys. |

### Recommended agent workflows

- **Task targets specific filenames/versions** ("update all api_v2 handlers"):
  1. `find` with `pattern: "api_v2*"` to get the exact file list.
  2. `file_outline` on each before editing.
- **Task skips documentation / generated code**: pass
  `skip_ext: ".md,.txt"` and `skip_dirs: "docs,generated"` to `find_files` /
  `find` / `context_pack` — or better, add them to `.xzagentignore`.
- **"Continue what I was doing"**: `recent_files` first, then
  `find_files` with `recent: true`.
- **Refactoring a shared symbol**: `call_graph` → `neighbors` before editing.
- **New project, no idea where things live**: `repo_map` first, then
  `find_files` with the task keywords (use English code tokens for ranking).

Tips: use English keywords in `find_files` queries (ranking matches code
tokens); absolute or root-prefixed paths are accepted by `file_outline`,
`neighbors`, and `call_graph`.

## Troubleshooting

- `ModuleNotFoundError: No module named 'xzagent'` via `ctxp.exe`:
  delete the stale exe in `Python\Scripts\` and run
  `python -m pip install --user -e <repo>`.
- Empty tool results: restart the MCP server after `git pull` and after
  editing `.xzagentignore` (the index rebuilds automatically on next call).
- `ctxp map` shows an old tree: run `ctxp index` (auto-refresh) or `--reindex`.
- A file you expect is missing: check `.gitignore`, `.xzagentignore`, and the
  default ignored directory names (e.g. `docs/` is NOT ignored by default,
  but `generated/` patterns may match your custom ignores).
- PowerShell: don't paste `#` comments after commands on the same line.