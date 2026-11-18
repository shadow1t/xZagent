# xzagent — Usage Guide

Local, offline code-context tool: indexes a project into SQLite (FTS5), ranks
relevant files for a task, and exposes the result via CLI and MCP (for AI agents).

## Install

```bash
pip install -e .          # provides the `ctxp` command
# or without install:
python -m xzagent --help  # with PYTHONPATH=<repo>/src
```

## CLI commands

All commands take an optional project root (defaults to the current directory):

```bash
ctxp index [ROOT]                 # build/refresh the index (auto-refreshes on file changes)
ctxp --reindex index [ROOT]       # force full rebuild
ctxp map [ROOT] [-o map.md]       # repo map (tree + key symbols); -o writes to a file
ctxp ask [ROOT] "task" --top 5    # rank most relevant files for a task
ctxp pack [ROOT] "task" -o context.md   # markdown context pack for an LLM prompt
ctxp graph [ROOT] SYMBOL          # definitions/callers/callees of a symbol
ctxp graph [ROOT] --path FILE     # imports + calls linked to a file
ctxp serve [ROOT]                 # MCP server (stdio) for AI apps
```

Global options: `--db PATH` (custom index location), `--reindex`.

The index lives at `<ROOT>/.xzagent/index.db` and rebuilds automatically when
files change or when the tool code changes (code-version stamp).

## MCP setup (AI apps)

### Claude Code
```bash
claude mcp add xzagent -- ctxp serve /path/to/project
```
or edit `~/.claude.json`:
```json
{
  "mcpServers": {
    "xzagent": {
      "command": "ctxp",
      "args": ["serve", "E:\\path\\to\\project"]
    }
  }
}
```
Verify inside Claude Code with `/mcp`.

### Antigravity
Edit `%USERPROFILE%\.gemini\antigravity\mcp_config.json` with the same
`mcpServers` block, then restart the app.

### Craft Anything (Plugins)
Settings → Plugins → Add a plugin:
- id: `xzagent_project` (lowercase letters, digits, `_` only — no hyphens)
- command: `ctxp` (or full path to `python.exe`)
- arguments: `serve E:\path\to\project`
- env (if using `python -m xzagent`): `PYTHONPATH=E:\path\to\xZagent\src`

## Multiple projects (multiple paths)

Each MCP server points at ONE root. Add one entry per project:

```json
{
  "mcpServers": {
    "xzagent_app":   { "command": "ctxp", "args": ["serve", "E:\\proj\\app"] },
    "xzagent_bot":   { "command": "ctxp", "args": ["serve", "E:\\proj\\bot"] },
    "xzagent_other": { "command": "ctxp", "args": ["serve", "E:\\proj\\other"] }
  }
}
```

Each server has its own independent index (`<root>/.xzagent/index.db`).

## MCP tools (6)

| Tool | Purpose |
|---|---|
| `repo_map` | Whole-project tree + key symbols per file |
| `find_files` | Rank files for a natural-language task (`task` or `query`) |
| `file_outline` | Classes/functions in a file (`path` or `file_path`; absolute paths OK) |
| `call_graph` | Definitions/callers/callees of a symbol (`symbol`/`symbol_name`) |
| `neighbors` | Files linked to a file via imports/calls |
| `context_pack` | Markdown pack of top files for a task |

Tips for agents: use English keywords in `find_files` queries (ranking matches
code tokens); call `call_graph` before editing shared symbols.

## Troubleshooting

- `ModuleNotFoundError: No module named 'xzagent'` via `ctxp.exe`:
  delete the stale exe in `Python\Scripts\` and run
  `python -m pip install --user -e <repo>`.
- Empty tool results: check the MCP server was restarted after `git pull`.
- `ctxp map` shows an old tree: run `ctxp index` (auto-refresh) or `--reindex`.
- PowerShell: don't paste `#` comments after commands on the same line.
