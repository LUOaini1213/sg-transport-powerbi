"""Repository hygiene: no local machine paths in tracked text, ASCII-only requirements files."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", ".schemas", ".tom", "__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {".md", ".py", ".ps1", ".sh", ".txt", ".ini", ".yml", ".yaml", ".json", ".tmdl", ".pbir",
                 ".pbism", ".pbip", ".csv", ".gitignore", ".gitattributes", ".platform"}
# A user profile or home directory, e.g. C:\Users\<name>, /home/<name>, /Users/<name>.
SEP = r"[/\\]"
LOCAL_PATH = re.compile(r"\b[A-Za-z]:" + SEP + r"+Users" + SEP + r"+[\w.-]+|/home/[a-z][\w.-]*/|/Users/[A-Za-z][\w.-]*/")


def text_files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and not SKIP_DIRS & set(rel.parts) and (p.suffix or p.name) in TEXT_SUFFIXES:
            yield p


def test_no_local_machine_paths():
    hits = []
    for p in text_files():
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if LOCAL_PATH.search(line):
                hits.append(f"{p.relative_to(ROOT).as_posix()}:{n}")
    assert not hits, "local machine paths in tracked text: " + ", ".join(hits[:20])


@pytest.mark.parametrize("name", sorted(p.name for p in ROOT.glob("requirements*.txt")))
def test_requirements_are_ascii(name):
    raw = (ROOT / name).read_bytes()
    assert raw.isascii(), f"{name} has non-ASCII bytes; pip on Windows may misread it"
