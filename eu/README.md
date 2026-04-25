# EU Assets

This directory is the EU jurisdiction adapter area inside LawVM.

Initial scope is not full EU-law ingestion.
Initial scope is:

- identify EU acts that intersect directly with Finnish law,
- fetch a small official sample corpus from official Cellar machine-data routes,
- preserve identifiers and metadata cleanly,
- compile stable source-record artifacts,
- and prepare a future EU frontend.

## Subdirectories

- `manifests/`
  tracked fetch manifests for EU sample corpora
- `data/raw/`
  fetched EU source artifacts
- `compiled/`
  stable machine-usable source-record artifacts derived from official notices

## Caveat

- Discovery CELEX ordering is deterministic but lexical: affected acts are
  de-duplicated and sorted by CELEX string.
- If you need legal chronology (effective-date order), add explicit temporal metadata
  to the downstream replay pipeline instead of trusting discovery order.

## EU Frontend Quick Start

```bash
uv run lawvm eu-replay 32016R0679 --pit-date 2023-06-30 --json
uv run lawvm eu-replay 32016R0679 --pit-date 2023-06-30 --format markdown
uv run lawvm eu-replay 32016R0679 --pit-date 2023-06-30 --format text
uv run python scripts/eu_replay_smoke_check.py --celex 32016R0679 --pit-date 2023-06-30 --format json
uv run python scripts/eu_replay_smoke_check.py --celex 32016R0679 --pit-date 2023-06-30 --format markdown \
  --expect-kind eu_replay_parent_not_found=0 --expect-kind eu_replay_target_not_found=0
```

One-shot EU adjudication smoke matrix:

```bash
for KIND in eu_replay_parent_not_found eu_replay_target_not_found eu_replay_reference_not_found; do
  UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/eu_replay_smoke_check.py \
    --celex 32016R0679 \
    --format markdown \
    --pit-date 2023-06-30 \
    --cache-dir /tmp/eu-smoke-cache \
  --expect-kind "${KIND}=0" \
  --expect-kind eu_replay_parent_not_found=0
done
```

Full smoke sweep with explicit cache and PIT forwarding across all output formats:

```bash
for MODE in json markdown text; do
  UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/eu_replay_smoke_check.py \
    --celex 32016R0679 \
    --pit-date 2023-06-30 \
    --cache-dir /tmp/eu-smoke-cache \
    --format "$MODE" \
    --expect-kind eu_replay_parent_not_found=0 \
    --expect-kind eu_replay_target_not_found=0 \
    --expect-kind eu_replay_reference_not_found=0
done
```

Replay output contract:

- `--json` returns machine-readable payload with adjudication summary and details (takes precedence over `--format` if both are given).
- `--format markdown` returns a compact table-oriented report.
- `--format text` returns a human-readable text summary.

Smoke checks also support exact adjudication-kind assertions via repeatable:

- `--expect-kind KIND=COUNT`

The smoke check exits non-zero when an exact expected count does not match observed adjudication histogram values.

Raw downloaded EU source files are usually gitignored through the top-level policy of not
 committing large corpora casually.
