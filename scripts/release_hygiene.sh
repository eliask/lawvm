#!/usr/bin/env bash
# release_hygiene.sh - public release-safety checks.
#
# Usage:
#   ./scripts/release_hygiene.sh
#   ./scripts/release_hygiene.sh --allow-dirty

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

ALLOW_DIRTY=0
if [ "${1:-}" = "--allow-dirty" ]; then
    ALLOW_DIRTY=1
elif [ "$#" -ne 0 ]; then
    echo "usage: $0 [--allow-dirty]" >&2
    exit 2
fi

if [ "$ALLOW_DIRTY" = "0" ] && [ -n "$(git status --short)" ]; then
    echo "FAIL: worktree is dirty; rerun from a clean release commit or pass --allow-dirty for local checks." >&2
    git status --short >&2
    exit 1
fi

echo "=== [1/4] release docs ==="
uv run python -m pytest tests/test_release_docs.py -q --override-ini="addopts="

echo ""
echo "=== [2/4] credential patterns in tracked files ==="
python3 - <<'PY'
from __future__ import annotations

import re
import subprocess
from pathlib import Path

private_key_re = re.compile(r"BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY")
literal_secret_re = re.compile(
    r"""(?ix)
    \b(api[_-]?key|secret|token|password)\b
    \s* [:=] \s*
    (?P<quote>['"])
    (?!(?:test|example|dummy|placeholder|changeme)(?P=quote))
    [^'"]{12,}
    (?P=quote)
    """
)
offenders: list[str] = []
for raw in subprocess.check_output(("git", "ls-files", "-z")).split(b"\0"):
    if not raw:
        continue
    path = Path(raw.decode())
    if not path.exists() or not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for line_no, line in enumerate(text.splitlines(), start=1):
        if private_key_re.search(line) or literal_secret_re.search(line):
            offenders.append(f"{path}:{line_no}: {line.strip()}")
if offenders:
    print("\n".join(offenders))
    raise SystemExit("FAIL: suspicious credential-like literal found in tracked files")
print("PASS: no credential-like tracked-file literals")
PY

echo ""
echo "=== [3/4] developer-local paths in tracked files ==="
python3 - <<'PY'
from __future__ import annotations

import subprocess
from pathlib import Path

excluded_prefixes = ("viewer/vendor/",)
excluded_paths = {"tests/test_release_docs.py"}
offenders: list[str] = []
for raw in subprocess.check_output(("git", "ls-files", "-z")).split(b"\0"):
    if not raw:
        continue
    name = raw.decode()
    if name in excluded_paths or name.startswith(excluded_prefixes):
        continue
    path = Path(name)
    if not path.exists() or not path.is_file():
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for line_no, line in enumerate(text.splitlines(), start=1):
        if "/home/" in line or "/Users/" in line:
            offenders.append(f"{path}:{line_no}: {line.strip()}")
if offenders:
    print("\n".join(offenders))
    raise SystemExit("FAIL: developer-local path found in tracked files")
print("PASS: no developer-local tracked-file paths")
PY

echo ""
echo "=== [4/4] large tracked files ==="
python3 - <<'PY'
from __future__ import annotations

import subprocess
from pathlib import Path

limit = 25 * 1024 * 1024
paths = subprocess.check_output(("git", "ls-files", "-z")).split(b"\0")
large: list[tuple[str, int]] = []
for raw in paths:
    if not raw:
        continue
    path = Path(raw.decode())
    if path.exists() and path.is_file():
        size = path.stat().st_size
        if size > limit:
            large.append((str(path), size))
if large:
    for path, size in large:
        print(f"{path}\t{size}")
    raise SystemExit("FAIL: large tracked files found")
print("PASS: no tracked files over 25 MiB")
PY

echo ""
echo "=== RELEASE HYGIENE GREEN ==="
