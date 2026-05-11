# Getting Started

LawVM uses Python 3.11+ and `uv`.

## Install

```bash
uv sync
uv run lawvm --help
```

The package exposes the `lawvm` CLI plus a few jurisdiction-specific helper
commands through `pyproject.toml`.

## Smoke Checks

Run a small test module first:

```bash
uv run pytest tests/test_ir_jsonable.py -q --override-ini="addopts="
```

For the canonical bounded local CI gate:

```bash
./scripts/ci.sh
```

For release-surface checks while you have local edits:

```bash
./scripts/release_hygiene.sh --allow-dirty
```

Many corpus tests and replay commands depend on local archived source data.
The public repository includes small corpus indexes and fixtures, not the full
source archives. A full unfiltered pytest run is useful before major releases,
but it is heavier and may require optional local corpora or network-marked
lanes.

## Archive-Free Demo

The Open Law Maryland demo uses public git repositories and does not require
Finlex `.farchive` data:

```bash
git clone https://github.com/maryland-dsd/law-xml.git .tmp/open_law/repos/law-xml
git clone https://github.com/maryland-dsd/law-xml-codified.git .tmp/open_law/repos/law-xml-codified

uv run lawvm open-law evidence-pack \
  --source-repo .tmp/open_law/repos/law-xml \
  --codified-repo .tmp/open_law/repos/law-xml-codified \
  --out .tmp/open_law/evidence-pack \
  --json

uv run lawvm open-law explain \
  --report-dir .tmp/open_law/evidence-pack \
  --limit 5
```

See [open-law-demo.md](open-law-demo.md) for the claim boundary and follow-up
queries.

## Archive-Backed Finland Commands

```bash
uv run lawvm replay 2002/738 --as-of 2024-01-01
uv run lawvm explain 2002/738
uv run lawvm diff 2002/738
uv run lawvm oracle-check 2002/738
```

If a command reports a missing archive, install or build the relevant local
`data/*.farchive` artifact and rerun. Archive-first replay is intentional:
LawVM should know exactly which source surface produced a result.

## What To Inspect

- `replay` materializes point-in-time text-state from amendment sources.
- `explain` shows the evidence path and replay decisions for a statute.
- `diff` compares replay output against a witness or oracle surface.
- `oracle-check` classifies replay-vs-witness disagreement where supported.

v0.1 is a research preview. Treat command flags, JSON shapes, and import paths
as unstable until the v1.0 contracts are frozen.
