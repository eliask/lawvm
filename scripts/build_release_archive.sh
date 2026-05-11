#!/usr/bin/env bash
# build_release_archive.sh - build a tracked-file-only source archive.
#
# Usage:
#   ./scripts/build_release_archive.sh
#   ./scripts/build_release_archive.sh .tmp/release/lawvm-v0.1.tar.gz
#
# Writes OUT_TAR_GZ plus:
#   OUT_TAR_GZ.sha256
#   OUT_TAR_GZ.manifest.json

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    sed -n '2,7p' "$0"
    exit 0
fi

if [ "$#" -gt 1 ]; then
    echo "Usage: $0 [OUT_TAR_GZ]" >&2
    exit 2
fi

head_sha="$(git rev-parse HEAD)"
head_short="$(git rev-parse --short HEAD)"
out="${1:-.tmp/release/lawvm-${head_short}.tar.gz}"
prefix="lawvm-${head_short}/"

./scripts/release_hygiene.sh
mkdir -p "$(dirname "$out")"
git archive --format=tar.gz --prefix="$prefix" --output="$out" HEAD
digest="$(sha256sum "$out" | awk '{print $1}')"
archive_name="$(basename "$out")"
printf '%s  %s\n' "$digest" "$archive_name" >"${out}.sha256"
python3 - "$out" "$archive_name" "$head_sha" "$head_short" "$prefix" "$digest" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
payload = {
    "archive": sys.argv[2],
    "git_commit": sys.argv[3],
    "git_short": sys.argv[4],
    "archive_prefix": sys.argv[5],
    "sha256": sys.argv[6],
}
out.with_suffix(out.suffix + ".manifest.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
echo "Wrote tracked-file-only archive: $out"
echo "SHA256: $digest"
echo "Wrote checksum: ${out}.sha256"
echo "Wrote manifest: ${out}.manifest.json"
