# Jurisdiction CLI Tooling Contract

Status: living descriptive contract.
Kind: descriptive.

Purpose: define the minimum CLI/debug surface a LawVM jurisdiction frontend
needs before agents can iterate replay quality without overfitting, hiding
source gaps, or optimizing a misleading full-corpus score.

This is not a claim that every command must have the same implementation. It is
a contract for the questions every frontend must be able to answer locally from
archived sources.

## 1. Benchmark Layers

A jurisdiction needs three benchmark loops.

1. Smoke/canary set: 20-50 rows that run quickly and include at least one known
   hard or expected-failing case.
2. Tight-loop curated corpus: usually 100-400 rows, source-complete, stratified
   across source regime, date range, instrument type, amendment/effect density,
   and known frontier families.
3. Full corpus: broad out-of-band regression guard and coverage monitor.

The full corpus is not the main optimization loop. It is too noisy: source
unavailability, oracle/editorial mismatch, unsupported effect families, and
non-commensurable rows can dominate the average. Local family fixes should be
judged first against the tight-loop corpus and then checked against full-corpus
regression summaries.

## 2. Source-Complete Means

For a row to belong in the tight-loop corpus, the frontend must be able to prove
which truth surfaces are present in the local replay substrate.

Minimum fields:

- base/enacted source locator and status
- amendment/effect source locator and status, or explicit statement that the
  jurisdiction supplies amendment semantics another way
- oracle/verifier locator and status
- source sizes and stable hashes where available
- source regime and authority lane
- unsupported/skipped/rejected source-unit counts

Rows missing a required truth surface are still useful, but they belong in
`pending`, `source_sparse`, `notruth`, or equivalent partitions. They must not
silently dilute the replay-quality loop.

## 3. Required Tool Families

Every serious frontend should have a local command or command mode for each
family below.

- Inventory: list source records, source statuses, locators, hashes, dependency
  closure, and acquisition-frontier state before replay claims.
- Corpus curation: build source-complete canary/tight/full corpus files and
  show stratification statistics.
- Benchmark history: run a labeled bench, show a past run, compare two runs,
  and fail or warn on regressions.
- Per-row source dump: show the exact local source artifacts and parse
  observations used for one statute or effect.
- Operation/effect inspection: list compiled operations/effects with source,
  target, action family, payload summary, and rejected rows.
- Phase diagnosis: explain acquisition, parse, payload, elaboration, lowering,
  replay, timeline, and oracle outcomes separately.
- Bisect or blame: identify the source unit or operation that first introduced
  a divergence or structural invariant violation.
- Frontier ranking: rank fixable candidates separately from source-sparse,
  non-textual, unsupported, editorial-only, compare-shape-only, and oracle-risk
  rows.
- Evidence export: emit JSON/JSONL proof bundles that another agent can inspect
  without scraping terminal prose.
- Structural review: non-interactive dump/compact output for tree-shape
  anomalies, duplicate labels, illegal edges, text duplication, and flattened
  list families.

Existing examples are uneven but instructive: Finland has `bench`, `frontier`,
`step-attribution`, `failures`, `oracle-check`, `ops`, `bisect`,
`diagnose-phase`, and `structural-review`; Estonia has pair/corpus consistency
tools such as `verify-consistency`, `ee-frontier`, `ee-pair-status`, and
`ee-chain-quality`; UK has `uk-effect`, `uk-effects`, `uk-candidates`,
`bench -j uk --corpus-stats`, `bench -j uk --curate-corpus`,
`bench -j uk --curate-preset
canary|tight|stress|modern-canary|modern-tight|hard-canary|hard-tight|hard-stress`,
`bench-regression-guard -j uk` with optional `duration_s` and per-phase
regression limits, saved-run phase timing delta summaries when both UK runs
were produced with `--phase-timings`, and replay diagnostics.

Regression guards must fail on structurally incomparable inputs. In particular,
a saved-run comparison with zero common scored rows is not a successful
zero-regression run, and an enabled duration guard with zero common `duration_s`
rows is invalid timing evidence.
An enabled phase guard with zero common phase timing rows, or zero comparable
non-total phase cells, is also invalid timing evidence. If `--phase NAME` is
provided, only the selected phase names are guarded; selected names with no
comparable timing cells must fail rather than silently pass.
Thresholds and allowed-regression counts must be nonnegative command inputs.

## 4. CLI Shape

Prefer jurisdiction-parameterized common commands when the artifact kind is
shared:

```bash
uv run lawvm bench -j <code> --corpus <csv> --label <label>
uv run lawvm ops -j <code> <id> --source <source-id> --target <target>
uv run lawvm explain -j <code> <id>
uv run lawvm evidence -j <code> <id> --json
```

Jurisdiction-local commands are appropriate when the source regime is genuinely
local:

```bash
uv run lawvm uk-effect <target-statute> <effect-key>
uv run lawvm ee-pair-status --base <base-id> --oracle <oracle-id>
```

Shared flags should keep the same meaning across jurisdictions:

- `--corpus` selects a curated corpus input; it must not be ignored.
- `--label` names a saved run.
- `--show` reads a saved run without rerunning.
- `--compare A B` compares saved runs.
- `--no-save` runs a smoke check without writing history.
- `--json` or `--jsonl` emits machine-readable evidence.

## 5. Regression Discipline

A family-level replay change should be evaluated in this order:

1. targeted synthetic tests
2. real witness canary
3. tight-loop curated corpus
4. full-corpus regression guard
5. frontier/evidence export for remaining rows

If a local fix improves one witness but regresses the tight-loop corpus, the
default assumption is that the rule is over-broad or phase-local ownership is
wrong. The fix should be narrowed, moved earlier in the pipeline, or converted
into an explicit blocked/source-pathology finding.

## 6. Structural Review

`structural-review --dump --compact` is a useful late-stage review shape because
it produces scan-friendly, non-interactive evidence. Interactive-first output is
not enough for agent workflows. New frontends should expose a compact
non-interactive structural reporter before large replay-quality work begins.
