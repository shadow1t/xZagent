import ast
import re

_SYMBOL_RES = {
    "javascript": [
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    ],
    "typescript": [
        r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)",
        r"^\s*(?:export\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    ],
    "go": [
        r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)",
        r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)",
    ],
    "rust": [
        r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)",
        r"^\s*(?:pub\s+)?struct\s+([A-Za-z_]\w*)",
        r"^\s*(?:pub\s+)?enum\s+([A-Za-z_]\w*)",
        r"^\s*impl(?:<[^>]*>)?\s+([A-Za-z_]\w*)",
    ],
    "java": [
        r"^\s*(?:public|private|protected|static|final|abstract|synchronized|\s)*class\s+(\w+)",
        r"^\s*(?:public|private|protected|static|final|synchronized|\s)*[\w<>\[\],\s]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]+)?\{",
    ],
    "c": [
        r"^\s*(?:static\s+)?[\w\s\*]+\s+(\w+)\s*\([^;]*\)\s*\{",
    ],
    "cpp": [
        r"^\s*(?:template\s*<[^>]*>\s*)?[\w:\s<>\*&]+\s+(\w+)\s*\([^;]*\)\s*(?:const)?\s*\{",
        r"^\s*(?:class|struct)\s+(\w+)",
    ],
    "ruby": [
        r"^\s*def\s+([A-Za-z_]\w*[?!=]?)",
        r"^\s*(?:class|module)\s+([A-Za-z_]\w*)",
    ],
    "php": [
        r"^\s*(?:public|private|protected|static|\s)*function\s+([A-Za-z_]\w*)",
        r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)",
    ],
    "shell": [
        r"^\s*(?:function\s+)?([A-Za-z_]\w*)\s*\(\s*\)\s*\{",
    ],
}

_IMPORT_RES = {
    "javascript": [r"^\s*import\s+.*?from\s+['\"]([^'\"]+)['\"]", r"^\s*import\s+['\"]([^'\"]+)['\"]", r"require\(\s*['\"]([^'\"]+)['\"]\s*\)"],
    "typescript": [r"^\s*import\s+.*?from\s+['\"]([^'\"]+)['\"]", r"^\s*import\s+['\"]([^'\"]+)['\"]", r"require\(\s*['\"]([^'\"]+)['\"]\s*\)"],
    "go": [r"^\s*import\s+(?:\w+\s+)?\"([^\"]+)\""],
    "rust": [r"^\s*(?:pub\s+)?use\s+([^;]+);"],
    "java": [r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;"],
    "ruby": [r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]"],
    "php": [r"^\s*(?:use|require|include)(?:_once)?\s+['\"\\]?([\w\\]+)"],
    "c": [r"^\s*#\s*include\s*[\"<]([^\">]+)[\">]"],
    "cpp": [r"^\s*#\s*include\s*[\"<]([^\">]+)[\">]"],
}


def _regex_extract(text, lang):
    symbols, imports, calls = [], [], []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for pat in _SYMBOL_RES.get(lang, []):
            m = re.search(pat, line)
            if m:
                kind = "class" if ("class" in pat or "struct" in pat or "enum" in pat or "interface" in pat or "type" in pat) else "function"
                symbols.append((kind, m.group(1), i))
                break
        for pat in _IMPORT_RES.get(lang, []):
            m = re.search(pat, line)
            if m:
                imports.append((m.group(1), i))
                break
        m = re.search(r"([A-Za-z_]\w*)\s*\(", line)
        if m and not line.lstrip().startswith(("if", "for", "while", "switch", "return", "def", "func", "function")):
            calls.append((m.group(1), i))
    return symbols, imports, calls, ""


def _python_extract(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _regex_extract(text, "python")
    symbols, imports, calls = [], [], []
    doc = ast.get_docstring(tree) or ""
    summary = doc.strip().splitlines()[0] if doc.strip() else ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(("function", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            symbols.append(("class", node.name, node.lineno))
        elif isinstance(node, ast.Import):
            for a in node.names:
                imports.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = ("." * (node.level or 0)) + (node.module or "")
            imports.append((mod, node.lineno))
            for a in node.names:
                if a.name != "*":
                    calls.append((a.name, node.lineno))
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.append((f.id, node.lineno))
            elif isinstance(f, ast.Attribute):
                calls.append((f.attr, node.lineno))
    return symbols, imports, calls, summary


def extract(text, lang):
    """Return (symbols, imports, calls, summary).

    symbols: [(kind, name, lineno)], imports: [(module, lineno)],
    calls: [(name, lineno)], summary: first docstring line for python.
    """
    if lang == "python":
        return _python_extract(text)
    return _regex_extract(text, lang)
