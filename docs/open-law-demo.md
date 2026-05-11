# Open Law Demo

This is the smallest archive-free public demo path for LawVM. It uses public
Open Law Library Maryland XML repositories as local git inputs, then audits
declared `codify:*` operations against publication snapshots.

This demo does not prove Maryland law correctness. It shows a structured-source
publication audit: body replay matches, metadata-lane replay, explicit
lifecycle non-claims, and evidence rows.

## Acquire Public Inputs

```bash
git clone https://github.com/maryland-dsd/law-xml.git .tmp/open_law/repos/law-xml
git clone https://github.com/maryland-dsd/law-xml-codified.git .tmp/open_law/repos/law-xml-codified
```

The commands below operate on the local clones. The generated evidence pack
records clone HEAD commits and remotes in `manifest.json`.

## Build Evidence

```bash
uv run lawvm open-law evidence-pack \
  --source-repo .tmp/open_law/repos/law-xml \
  --codified-repo .tmp/open_law/repos/law-xml-codified \
  --out .tmp/open_law/evidence-pack \
  --json
```

Useful follow-up queries:

```bash
uv run lawvm open-law explain \
  --report-dir .tmp/open_law/evidence-pack \
  --limit 5

uv run lawvm report query \
  .tmp/open_law/evidence-pack/operation_audits.jsonl \
  --status unsupported \
  --validate
```

## What To Look For

- `matched` rows are supported body operations whose replay matched the
  publication snapshot.
- `metadata_matched` rows are annotation metadata operations, not body-text
  claims.
- `lifecycle_unsupported` rows preserve lifecycle actions such as
  `codify:expire` without mutating legal body state.
- `diverged` rows, if present, are audit cases; they are not automatically
  official errors.

The current local-clone smoke on 2026-05-11 produced 39 operation rows: 27 body
matches, 11 metadata matches, 1 lifecycle unsupported row, and 0 divergences.
Treat those counts as dated observations from public repositories, not stable
release guarantees.
