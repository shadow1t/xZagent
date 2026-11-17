# Agent instructions for xzagent

- Use the `xzagent` MCP tools before reading files:
  1. `repo_map` — get the project structure.
  2. `find_files` with **English keywords** from the task — get top relevant files.
  3. `file_outline` on those files — see classes/functions with line numbers.
  4. `call_graph` on any symbol you plan to modify — check callers first.
- Paths: relative to project root, absolute paths, or root-prefixed all work.
- Argument aliases accepted: `task`/`query`, `path`/`file_path`, `symbol`/`symbol_name`.
- Index auto-rebuilds on file changes; no manual reindex needed.
- CLI equivalent (terminal): `ctxp index|map|ask|pack|graph|serve [ROOT]`.
- Full docs: `docs/USAGE.md`.
