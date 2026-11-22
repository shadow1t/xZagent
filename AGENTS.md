# Agent instructions for xzagent

You have fast, offline MCP tools for this project (`ctxp serve`). Use them
BEFORE reading files manually. Full details: `docs/USAGE.md`.

## Tools and when to use them

1. `repo_map` — project structure first. Every directory is always shown;
   `per_dir` caps files per directory (default 25), `files: true` adds key
   symbols per file.
2. `find_files` — rank the most relevant files for a natural-language `task`.
   Use **English code keywords** in the task. Returns paths + why they match.
3. `find` — find files by NAME/PATH pattern (`pattern: "api_v2*"`,
   `"*Dialog*.tsx"`, `"*_v2"`), not content. Use this BEFORE `find_files`
   when the task names specific files or a versioned subset, so you target
   the api_v2 files instead of every api file.
4. `recent_files` — the most recently modified files (newest first). Use for
   "continue what I was doing" or when the user says "this file here".
5. `file_outline` — classes/functions (with line numbers) of one file, before
   you read or edit it.
6. `call_graph` — where a symbol is defined, who calls it, what it references.
7. `neighbors` — files importing/calling a given file (blast radius before edit).
8. `context_pack` — ready-to-paste markdown pack (relevant files + map +
   excerpts) for a task.
9. `db_schema` — tables, columns, foreign keys from the project's DB sources.

## Targeting and filter parameters

Compose these on `find`, `find_files`, and `recent_files`:

| Param | Meaning |
|---|---|
| `pattern` | comma-separated globs on basename or full path: `api_v2*`, `*Dialog*.tsx` |
| `lang` / `only_lang` | keep only these languages: `python`, `typescript,tsx` |
| `ext` / `only_ext` | keep only files with these extensions: `.tsx,.ts` (dot optional) |
| `symbol` | keep only files DEFINING a class/function matching the glob: `Dialog*` |
| `only_dirs` | keep only files living under one of these directory names, at ANY depth: `api,ui` matches `api/x.py`, `src/api/y.py` |
| `no_tests` | drop files that look like tests: `test_*`, `tests_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `*Test.java`, `conftest.py`, plus `tests/`, `__tests__/`, `spec/`, `e2e/`, `cypress/` dirs. Use it for almost every 'where is the code' question — tests name what you want without being it |
| `skip_ext` | drop these extensions: `.md,.txt,.log` |
| `skip_dirs` | drop files under these directory names: `docs,tests,generated` |
| `recent` | sort/bias toward most recently modified files |
| `top` / `top_k` / `count` | limit result count (`0` = no cap) |

Typical combos: `find_files` + `pattern: "api_v2*"` (work on v2 only);
`find` + `only_dirs: "api"` + `recent: true` (freshest files in the api area);
`find_files` + `only_ext: ".py"` + `no_tests: true` (rank real code, not docs
or tests); `recent_files` + `skip_ext: ".md"` (what I edited, skip docs).

## Conventions

- Paths: relative to project root, absolute, or root-prefixed all work.
- Argument aliases accepted: `task`/`query`, `path`/`file_path`,
  `symbol`/`symbol_name`.
- Index auto-rebuilds on file changes; no manual reindex needed.
- Noise (tmp_*, *.min.js, lockfiles, migrations/, ...) is excluded from
  search by default; `.xzagentignore` at the root (gitignore syntax, `!`
  re-includes) makes per-project excludes permanent.
- CLI equivalent (terminal): `ctxp index|map|find|ask|pack|graph|schema|serve`
  with the same filters as flags (`--only-dirs`, `--only-ext`, `--no-tests`,
  `--skip-ext`, `--skip-dirs`, `--pattern`, `--symbol`, `--recent`, `--top`). `ctxp -h` lists everything.
- Terminal UX lives in `xzagent.tui` (colors, gradient bars, live progress).
  Everything shows on a **stdout/stderr TTY only** and degrades to plain text
  when piped: never parse colored output, and never assume a progress bar in
  the stream. Machines consume `tui`-free output: module docstrings are the
  contract.