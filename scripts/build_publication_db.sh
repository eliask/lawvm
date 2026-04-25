{
set -Eeuo pipefail

echo Running publication database build script... if it exits without "Done", it failed.

rm -rf .tmp/evidence_bundle_cache_fi_pub .tmp/finlex_errors_publication.db .tmp/structural_corpus_scan.json .tmp/evidence_bundle_cache .tmp/publication_section_structure_cache/ .tmp/finlex_publication_html_cache.farchive*
#LAWVM_VERSION_DRIFT=1 time nice -n10 uv run lawvm evidence-review -j fi --oracle-corpus --ready-oracle-artifacts-only --bundle-cache-dir .tmp/evidence_bundle_cache_fi_pub --workers 16 --cache-only
LAWVM_VERSION_DRIFT=1 time nice -n10 uv run lawvm evidence-review -j fi --oracle-corpus --bundle-cache-dir .tmp/evidence_bundle_cache_fi_pub --workers 16 --cache-only
time nice -n10 uv run scripts/build_publication_db.py  --cache-dir .tmp/evidence_bundle_cache_fi_pub --workers 16
echo "Built .tmp/finlex_errors_publication.db"

echo Done
exit 0
}
