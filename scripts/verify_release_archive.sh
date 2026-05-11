#!/usr/bin/env bash
# verify_release_archive.sh - verify a LawVM tracked-file source archive.
#
# Usage:
#   ./scripts/verify_release_archive.sh .tmp/release/lawvm-<commit>.tar.gz
#
# Requires adjacent sidecars:
#   ARCHIVE.tar.gz.sha256
#   ARCHIVE.tar.gz.manifest.json

set -euo pipefail

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    sed -n '2,8p' "$0"
    exit 0
fi

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 ARCHIVE.tar.gz" >&2
    exit 2
fi

archive="$1"
checksum="${archive}.sha256"
manifest="${archive}.manifest.json"

if [ ! -f "$archive" ]; then
    echo "FAIL: missing archive: $archive" >&2
    exit 1
fi
if [ ! -f "$checksum" ]; then
    echo "FAIL: missing checksum sidecar: $checksum" >&2
    exit 1
fi
if [ ! -f "$manifest" ]; then
    echo "FAIL: missing manifest sidecar: $manifest" >&2
    exit 1
fi

archive_dir="$(dirname "$archive")"
archive_name="$(basename "$archive")"
(cd "$archive_dir" && sha256sum -c "${archive_name}.sha256")
digest="$(sha256sum "$archive" | awk '{print $1}')"

python3 - "$archive" "$manifest" "$digest" <<'PY'
from __future__ import annotations

import json
import re
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
manifest = Path(sys.argv[2])
digest = sys.argv[3]
payload = json.loads(manifest.read_text(encoding="utf-8"))

errors: list[str] = []
if not isinstance(payload, dict):
    errors.append("manifest is not an object")
else:
    archive_name = payload.get("archive")
    archive_prefix = payload.get("archive_prefix")
    git_commit = payload.get("git_commit")
    git_short = payload.get("git_short")
    sha256 = payload.get("sha256")
    if archive_name != archive.name:
        errors.append(f"manifest archive mismatch: expected {archive.name!r}, got {archive_name!r}")
    if sha256 != digest:
        errors.append(f"manifest sha256 mismatch: expected {digest}, got {sha256!r}")
    if not isinstance(archive_prefix, str) or not archive_prefix.endswith("/"):
        errors.append(f"invalid archive_prefix: {archive_prefix!r}")
    if not isinstance(git_commit, str) or re.fullmatch(r"[0-9a-f]{40}", git_commit) is None:
        errors.append(f"invalid git_commit: {git_commit!r}")
    if not isinstance(git_short, str) or not isinstance(git_commit, str) or not git_commit.startswith(git_short):
        errors.append(f"invalid git_short: {git_short!r}")
    if isinstance(archive_prefix, str):
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
        if not members:
            errors.append("archive has no members")
        root_entry = archive_prefix.rstrip("/")
        for member in members:
            parts = Path(member.name).parts
            if Path(member.name).is_absolute() or ".." in parts:
                errors.append(f"unsafe archive member path: {member.name}")
            if member.name != root_entry and not member.name.startswith(archive_prefix):
                errors.append(f"archive member outside prefix {archive_prefix!r}: {member.name}")
            if member.issym() or member.islnk():
                link_parts = Path(member.linkname).parts
                if Path(member.linkname).is_absolute() or ".." in link_parts:
                    errors.append(f"unsafe archive link target for {member.name}: {member.linkname}")

if errors:
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)

print(f"PASS: release archive verified: {archive}")
PY
