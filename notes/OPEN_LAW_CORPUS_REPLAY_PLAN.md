# Open Law Corpus Replay Plan

Status: active implementation plan.
Kind: frontend work plan.

Goal:

> For every public Maryland Open Law publication transition that LawVM can
> honestly interpret, verify whether the codified XML snapshot follows from the
> declared `codify:*` operations, and report unsupported action families,
> target failures, unexplained publication mutations, and temporal metadata
> anomalies.

This is not a claim that LawVM independently interprets Maryland Register prose.
It is a structured-source audit over Open Law XML and publication snapshots.

## 1. Corpus Inventory

Inputs:

- `maryland-dsd/law-xml`
- `maryland-dsd/law-xml-codified`
- local git refs/branches cloned from the public repositories
- codified branch `index.xml`
- source repo `editorial-actions/*.xml`

Outputs:

- publication branch list;
- source repo commit referenced by each publication branch;
- build date, codified date, publication name, platform version;
- included editorial-action files per publication branch;
- operation counts by action family.

Command target:

```bash
uv run lawvm open-law inventory \
  --source-repo .tmp/open_law/repos/law-xml \
  --codified-repo .tmp/open_law/repos/law-xml-codified \
  --out .tmp/open_law/report
```

## 2. Snapshot Loader

LawVM should not scrape `regs.maryland.gov` or read raw GitHub URLs during
replay. The acquisition step clones the public git repositories, and replay
reads refs/blobs from those local repos.

Loader lanes:

- local git clones for full corpus runs;
- network fetch/clone as an explicit acquisition step only;
- JSON manifest output so downstream reports are reproducible.

Raw source bytes should remain distinct from parsed IR and replay reports.

Durable command shape:

```bash
git clone https://github.com/maryland-dsd/law-xml.git .tmp/open_law/repos/law-xml
git clone https://github.com/maryland-dsd/law-xml-codified.git .tmp/open_law/repos/law-xml-codified

uv run lawvm open-law inventory \
  --source-repo .tmp/open_law/repos/law-xml \
  --codified-repo .tmp/open_law/repos/law-xml-codified \
  --out .tmp/open_law/report
```

LawVM should read refs and blobs from these local repositories. Network reads
inside replay/audit would make evidence non-reproducible and should remain an
explicit acquisition step, not replay behavior.

## 3. Operation Coverage

Currently executable:

- `codify:replace`
- `codify:replace-or-insert`

Currently typed unsupported:

- `codify:expire`
- annotation metadata targets as a body-replay claim;
- unknown `codify:*`

Near-term additions:

- executable `expire` with tombstone/lifecycle semantics;
- metadata-lane audit for `annos` operations;
- `applicability` parsed into a frontend-local typed field, not interpreted by
  core until its vocabulary is known.

## 4. Path-To-File Planning

Open Law locator paths are frontend locators, not immediately generic
`LegalAddress` values.

For COMAR chapter-level files:

```text
10|41|02|.04 -> us/md/exec/comar/10/41/02.xml
               explicit path prefix: 10|41
               in-tree target after wrapping: 10|41|02|.04
```

The planner must emit typed planning failures for paths that cannot be mapped
without guessing.

Forbidden:

- broadening target search across the tree when a segment is absent;
- inferring missing parent labels from target uniqueness;
- treating Git file paths as legal identity without checking XML labels.

## 5. Per-Operation Replay Audit

For each planned operation:

1. load before XML from the previous publication branch;
2. load after XML from the target publication branch;
3. wrap explicit carried parent context if the XML file is a partial subtree;
4. replay supported operation;
5. compare replay tree to after tree;
6. emit replay findings and changed/unexplained paths.

The body-text comparison lane has named projections for known publication
presentation layers:

- `open_law_snapshot_annotation_projection`: annotations are metadata, not body
  replay text;
- `open_law_snapshot_typography_projection`: straight/curly quotation mark
  differences are typography, not legal target mutation.

Primary invariants:

```text
replay(before, declared_ops) == after
changed_paths(before, after) subset_of declared_operation_target_regions
```

## 6. Branch Transition Model

For each codified publication branch, parse `index.xml`.

The transition model is conservative:

- sort publication branches by parsed branch date/name;
- skip unsuffixed rolling `publication/YYYY-MM-DD` refs when suffixed snapshots
  exist for the same publication;
- require the before branch's included action set to be a subset of the after
  branch's included action set;
- use the after branch's included `editorial-actions/*.xml` as declared ops;
- if branch/action alignment is unclear, emit a branch-planning finding rather
  than assuming.

Resolved working assumption:

- `publication/YYYY-MM-DD` may be a rolling/current publication ref;
- `publication/YYYY-MM-DD.SNAPSHOT` is the safer corpus replay unit.

## 7. Corpus Report

Command target:

```bash
uv run lawvm open-law corpus-audit --out .tmp/open_law/report
```

Outputs:

- `manifest.json`
- `operation_audits.jsonl`
- `findings.jsonl`
- `summary.json`

Metrics:

- publication branches discovered;
- transitions planned;
- operations parsed;
- operations executable;
- unsupported operations;
- target failures;
- exact replay matches;
- publication mismatches;
- unexplained publication mutation paths;
- metadata/date anomalies.

## 8. Demo Target

Known real smoke:

```bash
uv run lawvm open-law audit \
  .tmp/open_law_demo/10-41-02-before.xml \
  .tmp/open_law_demo/10-41-02-after.xml \
  .tmp/open_law_demo/2026-01-22.xml \
  --path-prefix '10|41'
```

Expected current result:

```text
snapshot_matches_replay=True
changed_paths=2 unexplained_paths=0
```

This proves the useful seam: LawVM can independently replay a declared Open Law
structured codification action against a prior XML subtree and verify the later
publication subtree.

## 9. Stop Conditions

Minimum before the meeting:

- inventory command works against local clones of the public repositories;
- operation path planner covers all current `codify:replace` paths or emits
  typed planning failures;
- corpus audit runs over at least all supported `codify:replace` actions;
- report distinguishes replay success from unsupported and unplanned cases;
- spec notes clearly state claim boundaries.

Full target:

- all current Maryland `codify:*` action families executable or explicitly
  classified;
- adjacent publication branch transitions audited;
- explain command for each finding;
- local clone/cache mode for repeatable full-corpus runs.

## 10. Current Corpus Run

As of 2026-05-11, the local-clone corpus audit command:

```bash
uv run lawvm open-law corpus-audit \
  --source-repo .tmp/open_law/repos/law-xml \
  --codified-repo .tmp/open_law/repos/law-xml-codified \
  --out .tmp/open_law/report/full \
  --json
```

reports:

```json
{
  "operation_rows": 39,
  "matched": 27,
  "diverged": 0,
  "planning_failed": 1,
  "metadata_unsupported": 11,
  "snapshot_missing": 0,
  "findings": 66,
  "unexplained_paths": 0
}
```

Interpretation:

- all currently supported body-text COMAR operations replay against their
  publication snapshots;
- no supported body-text operation has unexplained publication mutation paths;
- 11 `annos` operations are intentionally classified as metadata-lane work;
- 1 `expire` operation targets a Maryland Register emergency regulation rather
  than COMAR and remains an explicit planning failure.
