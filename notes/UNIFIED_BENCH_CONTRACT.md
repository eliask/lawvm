# Unified Cross-Jurisdiction Benchmark Contract

Status: normative. Governs `lawvm.core.bench_contract` and the per-jurisdiction
benches that emit into it (FI, UK, EE, NZ, US).

## Problem

Each jurisdiction historically shipped its own bench with an incommensurable
headline number and a reinvented aggregation / history / non-scored story:

- **FI** (`tools/bench.py`): structural section-diff similarity (primary) plus a
  Levenshtein text similarity (secondary). Stores *similarity*; displays
  *error* = `(1 - similarity) * 100`. Already has non-scored statuses
  (`NO_TRUTH`, `SOURCE_UNAVAILABLE`, `ORACLE_STALE`) that are excluded, not
  failures, plus distribution buckets, history, and a regression guard.
- **UK** (`tools/uk_bench.py`): EID-set Jaccard `|∩| / max(|enacted|,|oracle|)`
  plus a separate text-Levenshtein lane.
- **EE** (`tools/ee_bench.py`): section exact-match accuracy
  (consolidation-vs-base consistency).
- **NZ** (`tools/nz_bench.py`, `new_zealand/benchmark.py`): per-transition dual
  similarity (text + tree) plus an oracle residual-family taxonomy.
- **US** (`us_federal/bench.py`): verified-agreement section counts
  (`agreements / oracle_changed`).

The *comparators* are genuinely different because the oracle ontologies differ.
A single universal metric formula would be a category error. What is **not**
jurisdiction-specific is the contract every comparator should honour, the
non-scored status story, the worst-of headline, and the
aggregation/history/regression machinery.

## Decision

Unify the **contract** and the **harness**, not the **metric formula**.

### 1. Canonical quantity is ERROR, not accuracy

Each scored unit stores **error** in `[0, 1]` (`0` = perfect). Accuracy
(`1 - error`) is derived only for display and the headline. Error is the honest
canonical quantity: it is what residue must reconcile against.

### 2. Two canonical axes per scored unit

Every scored unit carries two errors in the same units across all jurisdictions:

- `structural_err` — divergence of structure / units (sections, EIDs, tree
  paths, agreement sets). `0` = structurally perfect.
- `text_err` — divergence of text content (`1 - Levenshtein-style similarity`).
  `0` = text-identical.

An axis a jurisdiction does not compute is reported as **not attempted**
(`None`), never as `0` (which would falsely claim perfection) and never folded
into the other axis.

### 3. Shared result row

`lawvm.core.bench_contract.BenchUnitResult`:

```
unit_id: str
status: BenchStatus
structural_err: float | None   # [0,1], 0 = perfect; None = axis not attempted
text_err: float | None         # [0,1], 0 = perfect; None = axis not attempted
residue_buckets: Mapping[str, int]   # typed residue families -> count
witnesses: tuple[str, ...]     # opaque sampled evidence pointers
```

### 4. Uniform status enum

`BenchStatus`: `SCORED | NO_TRUTH | SOURCE_UNAVAILABLE | ORACLE_STALE | CRASH`.

`NO_TRUTH`, `SOURCE_UNAVAILABLE`, `ORACLE_STALE` are **non-scored**: excluded
from scoring, *not* failures. Only `CRASH` is a genuine failure (an unexpected
exception). `SCORED` is the only status carrying axis errors. This is exactly
FI's existing policy; the contract makes it uniform and enforceable.

### 5. Headline is worst-of (Liebig binding constraint), not mean

A unit's headline error is the **max** of its attempted axes
(`max(structural_err, text_err)` over the non-`None` axes). The binding
constraint dominates: a unit that is structurally perfect but textually wrong is
not "half right". Per-axis numbers are retained alongside the worst-of headline.

### 6. Shared aggregation / reporting in core

Distribution buckets (`mean`, `perfect`, `>=99%`, `>=95%`, `<90%`, `errors`),
error framing, history append/load, and the regression guard live in
`lawvm.core.bench_aggregate` and operate on `BenchUnitResult`s. Buckets are
computed over headline **accuracy** (`1 - headline_error`) so the existing FI
bucket semantics are preserved byte-for-byte.

### 7. Residue-reconciliation invariant (the honesty property)

For a `SCORED` unit, `structural_err` must be **explained** by `residue_buckets`:

- `structural_err > 0`  ⟺  `sum(residue_buckets.values()) > 0`.

No silent unexplained error (positive error with no typed residue), and no
phantom residue (typed residue with zero error). `residue_buckets` holds the
typed structural event families the comparator emitted for that unit
(`structural:<kind>` families for FI; comparator-defined families elsewhere).
`text_err` is a continuous text-similarity axis and is **not** required to
reconcile against the discrete residue buckets — only the structural axis is
event-typed and therefore reconcilable. `check_residue_reconciliation` enforces
this and is exercised by contract tests; benches assert it per scored unit.

### 8. Comparator stays per-jurisdiction behind the contract

The comparator is the only jurisdiction-specific part. Each jurisdiction
registers a comparator in `lawvm.core.bench_comparator_registry` that, given the
jurisdiction's own oracle/replay inputs, returns a `BenchUnitResult`. The shared
harness consumes those rows. FI's tree-diff, UK's EID-set Jaccard, EE's
exact-match, NZ's dual similarity, and US's agreement counts all map onto the
two error axes:

| Juris | structural_err | text_err |
|-------|----------------|----------|
| FI | `1 - structural section similarity` | `1 - adjusted Levenshtein` |
| UK | `1 - EID Jaccard` | `1 - text Levenshtein` (or `None`) |
| EE | `1 - section exact-match accuracy` | `None` (exact-match only) |
| NZ | `1 - tree similarity` | `1 - text similarity` |
| US | `1 - (agreements / oracle_changed)` | `None` (count-based) |

## Migration discipline

This is a **new shared layer plus an adapter** at each bench, not a rewrite. No
underlying fidelity number changes: each comparator re-houses the number it
already computes into `BenchUnitResult`. If a scored number would move, that is a
red flag to surface, not to paper over. Existing CLI behaviour and existing
bench tests stay green unless deliberately and defensibly updated.

Never silently delete, reroute, widen, invent, or repair legal state or bench
results. Any unexplained error or unscored unit is a typed, visible
`status` / `residue_bucket` — never silently dropped or folded into `SCORED`.
Fail loud.
