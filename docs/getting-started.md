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

For the broader local gate:

```bash
uv run pytest tests/ -q --override-ini="addopts="
```

Many corpus tests and replay commands depend on local archived source data.
The public repository includes small corpus indexes and fixtures, not the full
source archives.

## Example Commands

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
