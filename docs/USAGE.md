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

All commands take an optional project root (defaults to the current directory).
Positionals are forgiving: run inside the project and `ctxp find "tmp_*"`,
`ctxp ask "fix login"`, `ctxp graph login` all do the obvious thing; a
swapped `ctxp find "tmp_*" .` also works, and a bad root is a one-line
error, never a traceback.

```bash
ctxp index [ROOT]                 # build/refresh the index
ctxp --reindex index [ROOT]       # force full rebuild
ctxp map [ROOT] [-o map.md]       # repo map (every directory always shown)
ctxp find [ROOT] PATTERN          # find files by name/path glob (--only-dirs,
                                  #   --skip-ext, --skip-dirs, --symbol, --recent)
ctxp ask [ROOT] "task" --top 5    # rank most relevant files for a task
                                  #   (same targeting/skip filters)
ctxp pack [ROOT] "task" -o context.md   # markdown context pack for an LLM
ctxp graph [ROOT] SYMBOL          # definitions/callers/callees of a symbol
ctxp graph [ROOT] --path FILE     # imports + calls linked to a file
ctxp schema [ROOT] [--pg DSN]     # DB schema (tables, columns, FKs)
ctxp serve [ROOT]                 # MCP server (stdio) for AI apps
ctxp update [SRC]                 # update the CLI itself (see below)
```

Global options `--db PATH`, `--reindex`, `--index-all`, `--default-skips` and
`--skip PATTERN` may appear **before OR after the subcommand**
(`ctxp --reindex map .` and `ctxp map . --reindex` are equivalent — handy in
PowerShell, where options usually go at the end).

## Colors, ranking bars and live progress

On an interactive terminal the CLI is colored:

- EVERY command that needs to (re)build the index -- `index`, `map`, `ask`,
  `find`, `pack`, `graph`, `schema`, whether by explicit `--reindex` or the
  automatic rebuild after file changes -- renders a live one-line progress
  bar: spinner + violet→cyan gradient + `done/total` counts + percent +
  phase notes (`scanning files`, `indexing`, `resolving import/call graph`).
  A tiny background ticker keeps the spinner breathing during slow phases
  too, so the CLI never looks frozen; the line is erased before the result
  (`✔ Indexed 227 files in 0.50s → .xzagent/index.db`, `Wrote map.md`).
- `ctxp ask` prints a `✔ N most relevant files (out of TOTAL)` header followed
  by rows: a cyan rank number, the bold path, a violet→cyan gradient line bar
  (`━━━━━╸╌╌╌╌╌`, Rust-style rounded head, dotted gray track) showing the
  relative score of each hit, and its ranking reasons in gray.
  Rows are layout-aware and never wrap: bars always line up in one column,
  long paths clip from the left (`…le/auth.py`) and reasons clip to the
  terminal width, so narrow windows stay as clean as wide ones.

Colors never corrupt machine output:

- piped/redirected output (`ctxp ask ... | grep`, scripts, CI, the MCP
  server, file writes) is byte-for-byte the plain-text format it always was
- `NO_COLOR=1` (or `TERM=dumb`) disables colors; progress still redraws on
  the same line, without ANSI attributes
- `FORCE_COLOR=1` forces colors on (useful for demos/screenshots)
- the progress bar goes to **stderr**, so `ctxp pack ... -o context.md`
  writes clean files even while progress animates

## Targeting files: `ctxp find`

Use this when the task names specific files or a versioned subset, so the
agent works on `api_v2*` files instead of every API file across versions.
Globs match the basename AND the full relative path (case-insensitive).

```bash
ctxp find . "api_v2*"                 # files whose name starts with api_v2
ctxp find "tmp_*"                     # inside the project: root can be omitted
ctxp find . "*_v2"                    # files whose name ends with _v2
ctxp find . "*Dialog*.tsx"            # dialog components
ctxp find . "api_v2*, *Dialog*"       # multiple patterns, comma-separated
ctxp find . --ext .tsx,.ts            # by extension
ctxp find . --lang python,tsx         # by language
ctxp find . --symbol "Dialog*"        # files defining a class/function
ctxp find . --only-dirs api,ui        # only files under api/ or ui/ folders
ctxp find . --only-ext .py,.tsx       # only files with these extensions
ctxp find . --no-tests                # exclude test files (names AND tests/ dirs)
ctxp find . --recent                  # most recently modified first
ctxp find . --recent --top 5          # the last 5 edited files
```

Every result shows the file's last-modified time, and `--symbol` results also
show the matching symbol names.

Noise-skipped files (`tmp_*`, `package-lock.json`, `*.min.js`, ...) never
enter the index, but `find` is an explicit targeting command: a pattern
search ALSO scans the disk directly, so temp files show up when you name
them, marked `[not indexed]`:

```bash
ctxp find . "tmp_*"                  # temp files are found on disk, marked
ctxp find . "tmp_*" --skip-dirs logs  # disk matches honor every filter too
```

`.gitignore`, `.xzagentignore`, `--skip`, and all the filters above still
apply. Only `--symbol` searches stay index-only (symbols need the index).
The disk scan shows a live progress line (`⠙ scanning disk · 1200 files ·
3 matches ...`) so big trees never look stuck; piped output stays clean.

## Restricting search to given directories: `--only-dirs`

The inverse of `--skip-dirs`: instead of naming what to ignore, name the
directories the work is allowed to live in. A file is kept if **any** parent
folder in its path matches one of the names, at any depth:

```bash
ctxp find . --only-dirs api             # only files inside any "api" folder
ctxp find . --only-dirs api,ui          # ...or inside any "ui" folder
ctxp ask . "fix auth check" --only-dirs api
ctxp find . --only-dirs api --recent --top 5   # freshest 5 files under api/
```

- Matches a **directory name**, not a path: `--only-dirs api` matches
  `api/handlers.py`, `src/api/routes.py`, and `web/v2/api/mod.py` alike.
- Case-insensitive (`API`, `Api`, `api` all match), and it composes with the
  other filters (`--pattern`, `--lang`, `--symbol`, `--skip-ext`, ...).
- Available on `ctxp ask` and `ctxp find`, and as `only_dirs` in the MCP tools
  `find`, `find_files`, and `recent_files`.

## Restricting search to given extensions: `--only-ext`

The positive twin of `--skip-ext`: name the extensions you want, everything
else drops out. The classic use is telling the agent "rank only real code
files" when a task sits in a doc-heavy project:

```bash
ctxp ask . "billing charge flow" --only-ext .py    # only Python files ranked
ctxp ask . "fix ui" --only-ext .tsx,.ts            # TSX/TS only
ctxp find . --only-ext .py,.tsx                     # on find: same as --ext
```

- Works with or without the leading dot (`.py` == `py`), comma-separated.
- On `ctxp find` it is a full alias of the existing `--ext` (kept so both
  commands accept the same flag names); on `ctxp ask` it is brand-new
  filtering, since `ask` only had negative `--skip-ext` before.
- Composes with everything: `ctxp ask . "fix auth" --only-ext .py --only-dirs api`
  ranks only Python files under any `api` folder.
- Available on `ctxp ask` and `ctxp find`, and as `only_ext` in the MCP tools
  `find`, `find_files`, and `recent_files`.

## Excluding test files: `--no-tests`

Test files are notorious ranking hijackers: they name the thing you are
looking for (`test_billing`, `loginSpec`) without being the thing. The
unfiltered `ask` result is often 50% tests. One flag removes all of them:

```bash
ctxp ask . "billing charge flow" --no-tests
ctxp ask . "billing charge flow" --only-ext .py --no-tests     # common combo
ctxp find . --no-tests --recent --top 10
```

A file counts as a test if it matches either heuristic:

- **directory**: lives under `tests/`, `test/`, `__tests__/`, `__snapshots__/`,
  `spec/`, `specs/`, `e2e/`, `cypress/`, or `testing/`
- **name**: `test_*`, `tests_*`, `test-*`, `*_test`, `*_test.*`, `*_tests.*`,
  `*.test.*`, `*.spec.*`, `*Test.java`, `*Tests.java`, `conftest.py`

Works on `ctxp ask` and `ctxp find`, and as `no_tests: true` in the MCP tools
`find`, `find_files`, and `recent_files`. To find tests on purpose, simply
don't pass the flag (or search with `find pattern: "test_*"`).

## Skipping noise in searches (`ask` / `find`)

Keep results focused by excluding documentation, generated code, or any
folder by name:

```bash
ctxp ask . "billing charge flow" --skip-ext .md,.txt
ctxp ask . "login bug" --skip-dirs docs,tests,generated --skip-ext .md,.log
ctxp ask . "fix api" --pattern "api_v2*"       # only rank v2 API files
ctxp ask . "render dialog" --lang tsx         # only rank TSX files
ctxp ask . "fix auth" --only-dirs api         # only rank files under api/
ctxp ask . "fix auth" --only-ext .py          # only rank Python files
ctxp ask . "fix auth" --no-tests               # rank code, not its tests
ctxp ask . "continue work" --recent            # prefer recent modifications
ctxp ask . "task" --top 0                      # all matches, no cap
```

`--recent` adds a small recency penalty so, among equally relevant matches,
the most recently edited files come first.

## Permanently excluding files from the index: `.xzagentignore` + defaults

**Built-in defaults (no setup needed).** The indexer never reads, by name:

- temp/debug probe files: `tmp_*`, `temp_*`, `*_tmp.*`, `*_temp.*`, `*.tmp`
- backups & editor droppings: `*.bak`, `*_bak.*`, `*.orig`, `*.rej`, `*.patch`, `*.swp`
- minified/generated bundles: `*.min.js`, `*.min.css`, `*.bundle.js`, `*.chunk.js`
- lockfiles: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `poetry.lock`,
  `composer.lock`, `cargo.lock`, `go.sum`, ...
- `*.log` files
- framework artifact folders: `migrations/`, `__snapshots__/`, `.expo/`,
  `.angular/`, `.svelte-kit/`, `Pods/`, `.gradle/`, `.terraform/`, ... plus the
  usual `node_modules/`, `dist/`, `build/`, `coverage/`, caches, ...

> Django/Flask `migrations/` are skipped from *code search* (framework noise),
> but still feed the `db_schema` tool — schema intelligence keeps working.

**Re-include any default** with a `!pattern` negation rule in `.xzagentignore`:

```gitignore
# .xzagentignore
!migrations/        # index migrations after all (you actively edit them)
!tmp_*.mjs          # those tmp_ mjs probes are actually load-bearing
```

**Exclude anything else** with normal gitignore patterns:

```gitignore
docs/
generated/
**/*.test.tsx
```

`.gitignore` and its negation rules are always honored too.

## Index-time skip flags (CLI)

Control exclusions per project, straight from the command line:

```bash
# index EVERYTHING — disables the built-in noise skips entirely
# (tmp_*, *.min.js, lockfiles, migrations/, ... all get indexed)
ctxp --index-all index .

# your own extra excludes (gitignore syntax, repeatable)
ctxp --skip "generated/" --skip "*.g.dart" index .

# combine: turn defaults off but still skip one folder
ctxp --index-all --skip node_modules/ index .

# restore the built-in noise skips after a previous --index-all
ctxp --default-skips index .
```

- The flags work on any command that touches the index (`index`, `map`,
  `ask`, `find`, `pack`, `graph`), e.g. `ctxp --index-all map .`.
- The chosen configuration is **stored in the index**: later runs (and the
  MCP server) reuse it automatically, without rebuild churn. Passing a
  different flag value switches the configuration and rebuilds once.
- For per-project permanence prefer the `.xzagentignore` file; flags are
  meant for trying things out or one-off indexing rules.

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

### One command, inside the project (recommended)
```bash
ctxp setup              # writes .mcp.json (Claude Code) + .cursor/mcp.json
ctxp setup --client claude --client cursor    # pick clients explicitly
ctxp setup --client generic                  # prints a snippet for any client
ctxp setup --client antigravity    # this machine's Antigravity global config
ctxp setup --client windsurf        # ... and Windsurf's
ctxp setup --remove      # takes the xzagent server back out of the configs
ctxp setup --absolute   # THIS machine's paths instead (never commit it)
```
The configs live INSIDE the project and are PORTABLE by default:
`"command": "ctxp", "args": ["serve", "."]` -- no absolute paths, no
usernames, nothing machine-specific, so the committed file works for
anyone who has ctxp installed. (Project-scoped clients launch the
server with cwd = project root, which is what "." means.) The configs
travel with the project: any agent or
teammate opening the project finds the tools ready, no path fiddling.
Claude Code asks you to approve the project tools once (`/mcp` to check);
Cursor needs one restart. Existing servers in those files are preserved.

Also add `ctxp setup` to your notes for AI agents (AGENTS.md or similar):
"run ctxp setup once" is all an agent needs to wire itself up.

### Claude Code (manual / user scope)
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

### Antigravity / Windsurf
One command, inside the project:
```bash
ctxp setup --client antigravity     # also: --client windsurf
```
This edits Antigravity's global config at
`%USERPROFILE%\.gemini\config\mcp_config.json` (Google moved it there;
pre-migration installs still read `%USERPROFILE%\.gemini\antigravity\mcp_config.json`,
and `ctxp setup` picks whichever one your installed app actually uses)
and Windsurf's at `~/.codeium/windsurf/mcp_config.json`: an absolute
entry for this machine and THIS project, plus a per-project server key,
so a second project in the same file becomes `xzagent_<folder>` instead
of overwriting the first. These files are machine-global (not project
files, never committed). Restart the app afterwards and the tools appear
(Antigravity: Settings > Customizations > MCP Servers, or the `agy` CLI's
`/mcp` panel).

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
| `find` | The task names file patterns ("the api_v2 files", "*Dialog*.tsx", "files ending _v2"), or you must restrict work to a subset. Params: `pattern`, `lang`, `ext`/`only_ext`, `symbol`, `only_dirs`, `no_tests`, `skip_ext`, `skip_dirs`, `recent`, `top`. |
| `find_files` | You need the most relevant files for a natural-language task. Same restrict/skip params (`pattern`, `only_lang`, `only_dirs`, `only_ext`, `no_tests`, `skip_ext`, `skip_dirs`) plus `task` and `top_k`. Use `recent: true` to prefer recently edited matches. |
| `recent_files` | "What was I working on?" / continue-the-last-task. Returns the most recently modified files with timestamps, optional `pattern`/`lang`/`only_dirs`/`only_ext`/`no_tests`/`skip_*` filters. |
| `file_outline` | You are about to read or edit one file and want its classes/functions with line numbers first. |
| `call_graph` | Before changing a class/function: where it is defined, who calls it, what it references. |
| `neighbors` | Before editing a file: which files import/call it (blast radius). |
| `context_pack` | You need a ready-to-paste markdown pack (relevant files + map + excerpts) for a task. |
| `db_schema` | The task involves database tables, columns, or foreign keys. |

All filtering parameters at a glance:

| Param | CLI flag | Meaning |
|---|---|---|
| `pattern` | `--pattern` / positional in `find` | Glob(s) the file name or path must match (`api_v2*`, `*Dialog*.tsx`). |
| `lang` / `only_lang` | `--lang` | Keep only these languages (`python`, `typescript,tsx`). |
| `ext` / `only_ext` | `--ext` / `--only-ext` | Keep only files with these extensions (`.tsx,.ts`); both names accepted everywhere. |
| `symbol` | `--symbol` | Keep only files defining a class/function matching the glob (`Dialog*`). |
| `only_dirs` | `--only-dirs` | Keep only files whose path contains one of these directory names, at any depth (`api,ui`). |
| `no_tests` | `--no-tests` | Drop files that look like tests (name and directory heuristics, see above). |
| `skip_ext` | `--skip-ext` | Drop these extensions (`.md,.txt`). |
| `skip_dirs` | `--skip-dirs` | Drop files under these directory names (`docs,tests`). |
| `recent` | `--recent` | Sort (or bias) toward most recently modified files. |
| `top` / `top_k` / `count` | `--top` | Limit how many results come back; `0` = no cap. |

### Recommended agent workflows

- **Task targets specific filenames/versions** ("update all api_v2 handlers"):
  1. `find` with `pattern: "api_v2*"` to get the exact file list.
  2. `file_outline` on each before editing.
- **Task names the area instead of files** ("work only in the api folder"):
  pass `only_dirs: "api"` to `find` / `find_files` — nested folders like
  `src/api/...` are included automatically.
- **Doc-heavy project, task is code**: pass `only_ext: ".py"` (or
  `.tsx,.ts`) to `find_files` so documentation never outranks real code.
- **Tests keep hijacking the ranking**: pass `no_tests: true` to
  `find_files` / `find` / `recent_files` — test files name what you want
  without being it.
- **Task skips documentation / generated code**: pass
  `skip_ext: ".md,.txt"` and `skip_dirs: "docs,generated"` to `find_files` /
  `find` / `recent_files` — or better, add them to `.xzagentignore`.
- **"Continue what I was doing"**: `recent_files` first, then
  `find_files` with `recent: true`.
- **Refactoring a shared symbol**: `call_graph` → `neighbors` before editing.
- **New project, no idea where things live**: `repo_map` first, then
  `find_files` with the task keywords (use English code tokens for ranking).

Tips: use English keywords in `find_files` queries (ranking matches code
tokens); absolute or root-prefixed paths are accepted by `file_outline`,
`neighbors`, and `call_graph`.

## Updating the CLI: `ctxp update`

One command replaces the whole `git pull` + `pip uninstall` + `pip
install --no-cache-dir` ritual. Run it from anywhere:

```bash
ctxp update            # uses your clone, an enclosing checkout, or GitHub
ctxp update D:\path\to\xZagent   # explicit source folder (remembered for next time)
ctxp update --no-pull   # reinstall the clone's current files without pulling
```

What it does:

1. Resolves the source: the folder argument, the source remembered from
   your last update (`~/.xzagent/config.json`), an xZagent checkout
   containing your current directory, or the GitHub repo itself
   (`pip install git+...`, no local clone needed).
2. If the source is a git clone: `git pull --ff-only` (with the usual
   live progress line).
3. Reinstalls via `pip install --force-reinstall --no-deps --no-cache-dir`
   and reports the version jump: `✔ Updated xzagent 0.2.2 → 0.2.3`.
4. On Windows the running `ctxp.exe` is file-locked, but a running exe
   CAN be renamed: `ctxp update` renames it aside (ctxp.exe.oldN,
   swept at the next update) and installs the fresh exe right in place,
   inside the one visible command. Only if that rename is refused (rare,
   antivirus meddling) does the older detour kick in: the install is
   handed to a fresh interpreter that waits for the current one to exit,
   reinstalls in the SAME console with a live progress line (exactly like
   every other long ctxp phase), then prints the result. One command,
   nothing to uninstall by hand.

Concurrent `ctxp update` runs cannot race each other: the first one takes
an exclusive lock (`~/.xzagent/update.lock`, stale after 10 minutes) and
any other stands down with `✔ an update is already in progress · letting
it finish`.

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