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

echo "=== [1/5] release docs ==="
uv run python -m pytest tests/test_release_docs.py -q --override-ini="addopts="

echo ""
echo "=== [2/5] package build metadata ==="
build_log="$(mktemp .tmp/release-build-log.XXXXXX)"
tmp_build_dir="$(mktemp -d .tmp/release-build.XXXXXX)"
trap 'rm -rf "$tmp_build_dir" "$build_log"' EXIT
UV_CACHE_DIR="${UV_CACHE_DIR:-.tmp/uv-cache}" uv build --out-dir "$tmp_build_dir" >"$build_log" 2>&1 || {
    cat "$build_log"
    echo "FAIL: package build failed"
    exit 1
}
python3 - "$build_log" "$tmp_build_dir" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path
import tarfile
import zipfile

log_path = Path(sys.argv[1])
build_dir = Path(sys.argv[2])
log = log_path.read_text(encoding="utf-8", errors="replace")
allowed = {
    "warning: build_py: byte-compiling is disabled, skipping.",
    "warning: install_lib: byte-compiling is disabled, skipping.",
}
offenders = [
    line
    for line in log.splitlines()
    if (
        line.strip().lower().startswith("warning:")
        or "deprecationwarning" in line.lower()
        or "setuptoolsdeprecationwarning" in line.lower()
    )
    and line.strip().lower() not in allowed
]
if offenders:
    print(log)
    raise SystemExit("FAIL: package build emitted release-relevant warnings")

wheel_paths = sorted(build_dir.glob("*.whl"))
sdist_paths = sorted(build_dir.glob("*.tar.gz"))
if len(wheel_paths) != 1 or len(sdist_paths) != 1:
    raise SystemExit("FAIL: package build did not produce exactly one wheel and one sdist")

with zipfile.ZipFile(wheel_paths[0]) as wheel:
    wheel_names = set(wheel.namelist())
    metadata_name = next(name for name in wheel_names if name.endswith(".dist-info/METADATA"))
    metadata = wheel.read(metadata_name).decode("utf-8")
    entry_points_name = next(name for name in wheel_names if name.endswith(".dist-info/entry_points.txt"))
    entry_points = wheel.read(entry_points_name).decode("utf-8")

required_metadata = (
    "License-Expression: MIT",
    "Requires-Dist: farchive @ git+https://github.com/eliask/farchive.git@5ec162e9d80a1ba96b0e6116198bc396b2950430",
    'Provides-Extra: analytics',
    'Requires-Dist: duckdb>=1.0; extra == "analytics"',
    'Requires-Dist: pyarrow>=15.0; extra == "analytics"',
)
missing_metadata = [line for line in required_metadata if line not in metadata]
if missing_metadata:
    raise SystemExit("FAIL: wheel metadata missing: " + ", ".join(missing_metadata))

required_entry_points = (
    "lawvm = lawvm.tools.cli:main",
    "lawvm-uk-bootstrap = lawvm.uk_legislation.bootstrap:main",
    "lawvm-eu-cellar = lawvm.eu.cellar:main",
    "lawvm-us-bootstrap = lawvm.us_federal.bootstrap:main",
    "lawvm-fi-amendments = lawvm.finland.amendment_index:main",
)
missing_entry_points = [line for line in required_entry_points if line not in entry_points]
if missing_entry_points:
    raise SystemExit("FAIL: wheel entry points missing: " + ", ".join(missing_entry_points))

required_package_data = (
    "lawvm/finland/rulebook/generated/RULEBOOK.md",
    "lawvm/finland/rulebook/generated/RULE_INDEX.json",
)
missing_package_data = [name for name in required_package_data if name not in wheel_names]
if missing_package_data:
    raise SystemExit("FAIL: wheel missing package data: " + ", ".join(missing_package_data))

with tarfile.open(sdist_paths[0], "r:gz") as sdist:
    sdist_names = set(sdist.getnames())
sdist_missing = [
    name
    for name in required_package_data
    if not any(member.endswith("/" + name) for member in sdist_names)
]
if sdist_missing:
    raise SystemExit("FAIL: sdist missing package data: " + ", ".join(sdist_missing))
PY
echo "PASS: package build metadata"

echo ""
echo "=== [3/5] credential patterns in tracked files ==="
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
echo "=== [4/5] developer-local paths in tracked files ==="
python3 - <<'PY'
from __future__ import annotations

import subprocess
from pathlib import Path

excluded_prefixes = ("viewer/vendor/",)
excluded_paths = {"tests/test_release_docs.py"}
developer_path_markers = ("/" + "home" + "/", "/" + "Users" + "/")
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
        if any(marker in line for marker in developer_path_markers):
            offenders.append(f"{path}:{line_no}: {line.strip()}")
if offenders:
    print("\n".join(offenders))
    raise SystemExit("FAIL: developer-local path found in tracked files")
print("PASS: no developer-local tracked-file paths")
PY

echo ""
echo "=== [5/5] large tracked files ==="
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
