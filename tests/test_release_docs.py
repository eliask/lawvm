from __future__ import annotations

import re
import subprocess
from pathlib import Path


_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_tracked_markdown_local_file_links_resolve() -> None:
    broken: list[str] = []
    for path in _tracked_markdown_files():
        text = path.read_text(encoding="utf-8")
        for match in _MARKDOWN_LINK_RE.finditer(text):
            target = match.group(1).split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            target_path = Path(target)
            if target_path.is_absolute():
                broken.append(f"{path}: absolute local link {target}")
                continue
            if not (path.parent / target_path).exists():
                broken.append(f"{path}: missing local link {target}")
    assert broken == []


def test_release_docs_do_not_expose_developer_local_paths() -> None:
    offenders: list[str] = []
    for path in _tracked_markdown_files():
        text = path.read_text(encoding="utf-8")
        if "/home/" in text or "/Users/" in text:
            offenders.append(str(path))
    assert offenders == []


def _tracked_markdown_files() -> tuple[Path, ...]:
    output = subprocess.check_output(("git", "ls-files", "*.md"), text=True)
    return tuple(Path(line) for line in output.splitlines() if line)
