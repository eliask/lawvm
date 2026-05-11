#!/usr/bin/env bash
# build_release_archive.sh - build a tracked-file-only source archive.
#
# Usage:
#   ./scripts/build_release_archive.sh
#   ./scripts/build_release_archive.sh .tmp/release/lawvm-v0.1.tar.gz

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

head_short="$(git rev-parse --short HEAD)"
out="${1:-.tmp/release/lawvm-${head_short}.tar.gz}"
prefix="lawvm-${head_short}/"

./scripts/release_hygiene.sh
mkdir -p "$(dirname "$out")"
git archive --format=tar.gz --prefix="$prefix" --output="$out" HEAD
echo "Wrote tracked-file-only archive: $out"
