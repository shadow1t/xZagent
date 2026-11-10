import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SAMPLE = {
    "app/__init__.py": "",
    "app/main.py": '''"""Entry point of the app."""
import os
from app.auth import login, logout
from .utils import helper


def main():
    user = login("admin", "secret")
    helper()
    return user
''',
    "app/auth.py": '''"""Authentication module."""


def login(username, password):
    if password == "secret":
        return {"user": username}
    return None


def logout(user):
    return True
''',
    "app/utils.py": '''"""Utility helpers."""


def helper():
    return 42


def prefix_helper():
    return "x"
''',
    "web/script.js": '''import { fetchUser } from './api';

function render() {
    return fetchUser(1);
}
''',
    "web/api.js": '''export function fetchUser(id) {
    return { id };
}
''',
    "lib/main.dart": """import 'package:app_name/services/storage.dart';
import 'services/utils.dart';

void main() {
  runApp();
}

class MyApp {}
""",
    "lib/services/storage.dart": """class StorageService {
  Future<void> saveFile() async {}
}
""",
    "lib/services/utils.dart": """void helperFn() {}
""",
    "README.md": "# Sample project\n\nA demo project for tests.\n",
    ".gitignore": "ignored_dir/\n*.log\n",
    "ignored_dir/secret.py": "def hidden():\n    pass\n",
    "debug.log": "log line\n",
}


def make_sample_project(root):
    for rel, content in SAMPLE.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return root
