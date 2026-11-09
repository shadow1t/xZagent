import os

EXT_LANG = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cxx": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".sql": "sql", ".html": "html", ".css": "css", ".scss": "css",
    ".vue": "vue", ".svelte": "svelte",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".md": "markdown", ".rst": "markdown", ".txt": "text",
    ".xml": "xml", ".lua": "lua", ".dart": "dart",
    ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".hrl": "erlang",
    ".clj": "clojure", ".groovy": "groovy", ".tf": "hcl", ".proto": "protobuf",
}

SPECIAL_FILES = {"Dockerfile": "dockerfile", "Makefile": "makefile", "CMakeLists.txt": "cmake"}

TEXT_EXTS = set(EXT_LANG) | {".env.example", ".gitignore", ".dockerignore"}

MAX_FILE_BYTES = 1_000_000


def lang_for(path):
    base = os.path.basename(path)
    if base in SPECIAL_FILES:
        return SPECIAL_FILES[base]
    ext = os.path.splitext(base)[1].lower()
    return EXT_LANG.get(ext)
