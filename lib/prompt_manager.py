"""Prompt store backed by local files in prompts/ directory.

File layout:
    prompts/
        v1.prompt.md
        v2.prompt.md
        ...
        current.prompt.md   # always points to the latest version (atomic rename)

All public functions are synchronous; the store is just the filesystem.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CURRENT_FILE = PROMPTS_DIR / "current.prompt.md"


def _version_files() -> list[tuple[int, Path]]:
    """Return sorted list of (version_number, path) for all vN.prompt.md files."""
    pattern = re.compile(r"^v(\d+)\.prompt\.md$")
    results: list[tuple[int, Path]] = []
    for f in PROMPTS_DIR.iterdir():
        m = pattern.match(f.name)
        if m:
            results.append((int(m.group(1)), f))
    results.sort(key=lambda x: x[0])
    return results


def current_version() -> int:
    """Return the latest version number, or 0 if no versions exist."""
    versions = _version_files()
    return versions[-1][0] if versions else 0


def read_current() -> str:
    """Read the current prompt text."""
    return CURRENT_FILE.read_text(encoding="utf-8")


def read_version(version: int) -> str:
    """Read a specific version's prompt text."""
    path = PROMPTS_DIR / f"v{version}.prompt.md"
    return path.read_text(encoding="utf-8")


def list_versions() -> list[dict]:
    """Return list of {version, path, mtime} dicts, sorted by version."""
    return [
        {
            "version": v,
            "path": str(p),
            "mtime": os.path.getmtime(p),
        }
        for v, p in _version_files()
    ]


def save_new_version(prompt_text: str) -> int:
    """Write a new prompt version and atomically update current.prompt.md.

    Returns the new version number.
    """
    next_v = current_version() + 1
    new_file = PROMPTS_DIR / f"v{next_v}.prompt.md"
    new_file.write_text(prompt_text, encoding="utf-8")

    # Atomic rename: write to temp file in same dir, then replace current
    fd, tmp_path = tempfile.mkstemp(
        dir=PROMPTS_DIR, prefix=".current_", suffix=".tmp"
    )
    try:
        os.write(fd, prompt_text.encode("utf-8"))
        os.close(fd)
        shutil.move(tmp_path, CURRENT_FILE)
    except BaseException:
        os.close(fd) if not os.get_inheritable(fd) else None
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return next_v


def current_mtime() -> float:
    """Return mtime of current.prompt.md (for polling)."""
    return os.path.getmtime(CURRENT_FILE)
