# U.S. federal statutory adjudication plan

This file defines where uncertainty lives.

The goal is to stop three different problems from collapsing into one vague
"mismatch" bucket:

1. source pathology (the Public Law / release-point bytes are defective or
   underdetermined),
2. compare-shape / oracle-shape noise (the USC release point wraps/notes
   structure differently than replay),
3. replay defect (US replay-owned logic is wrong).

It also keeps unsupported, skipped, and rejected source lanes visible.

---

## 1. Core rule

A frontend must not accuse replay when the evidence only supports a source or
oracle problem. Likewise, it must not hide replay defects under "source
weirdness". For the U.S. frontend specifically: a residual at a USC section is
**not** a replay defect just because the materialized text differs from the
release point — the difference may be an editorial note the oracle keeps and
replay strips, an effective date that has not yet arrived at the oracle pin, or a
quote-typography drift in the source payload.

---

## 2. Required families

### A. Source pathology
The Public Law or release-point bytes are missing, ambiguous, malformed, or
semantically insufficient.

US examples:
- quoted strike text does not occur verbatim in the target section body
  (quote/dash/NBSP drift),
- inserted-section body absent or truncated in the PLAW XML,
- effective-date clause absent / conditional / relative ("90 days after
  enactment"),
- prior release point missing for the window (no honest base tree),
- amendment instruction references a section the prior release point does not
  contain.

### B. Compare-shape / oracle-shape
Replay and the release-point oracle differ mostly because the oracle wraps or
projects structure differently.

US examples:
- USC `<note>` / Editorial Note / source-credit retained by the oracle but not
  enacted operative text,
- USLM synthetic eId / heading-catchline projection differences,
- table-layout-only differences in a dollar-threshold table,
- whitespace/typography normalization differences.

### C. Replay defect
US replay-owned logic has direct evidence of failure.

US examples:
- `TEXT_REPLACE` target found but the strike text was not removed / insert not
  applied,
- `REPEAL` left addressable structure behind,
- `INSERT` anchored at the wrong sibling position,
- `RENUMBER`/redesignation applied out of order (when that family is enabled),
- mutation-boundary violation (changed paths exceed the op's target region).

### D. Unsupported / skipped / rejected
The frontend recognizes a unit/lane/effect but does not accept it as an
executable replay claim.

US examples:
- non-positive-law title section (Act->USC classification mapping not yet built)
  -> `skipped` with reason,
- distributive multi-target instruction ("each amended by striking…") ->
  `unsupported`,
- table-cell edit -> `unsupported`,
- `RENUMBER` before its family is unblocked -> `rejected`/`blocked-on-frontier`,
- PLAW available only as plaintext (no USLM) -> `unsupported`.

These rows are evidence, not successes. Each links to `findings.jsonl` with a
stable `rule_id`, blocking status, and strict/quirks disposition.

---

## 3. Shared-first policy

Reuse shared core adjudications before inventing local ones:

- shared source pathology kinds: payload-shape loss, missing-body-support for a
  blamed replacement, target ambiguity (from
  `core/agreement_residual.py` + the source-pathology spec lanes).
- shared compare-shape kinds: collapsed/wrapped subtree, wrapper-only node absent
  in oracle, retained editorial heading (from `core/comparison_normalization.py`).
- shared replay bug kinds: target-not-found, payload-missing, unsupported action
  at replay, invariant violation, mutation-boundary violation (from the replay
  invariants model).
- shared residual dispositions: `lawvm_wrong` / `oracle_suspect` /
  `missing_source` (from `core/agreement_residual.py`).

Genuinely US-specific local kinds that remain local:

- `us_payload_quote_drift` — quoted strike text differs only by typography from
  the target body (source pathology; exists only because USLM quoting drifts from
  USC body typography — promote to a shared "quoted-payload typography drift" if
  another jurisdiction needs it).
- `us_editorial_note_wrapper` — USC `<note>`/Editorial-Note wrapper retained by
  oracle but not enacted operative text (compare-shape).
- `us_effective_date_unresolved` / `us_effective_date_conditional` — scattered/
  conditional effective date routed to ActivationRule (source pathology).
- `us_release_point_misstraddle` — chosen before/after release points do not
  straddle the amending Public Law (source/setup pathology).
- `us_classification_mapping_required` — section is non-positive-law and needs
  the Act->USC classification mapping (unsupported, first scope).
- `us_distributive_target` — distributive multi-target amendment instruction
  (unsupported).
- `us_dependency_unacquired` — classification table names a Public Law whose
  USLM XML is not yet acquired (skipped dependency).

Where a US-specific kind exists only because the shared kind does not yet exist
(notably `us_payload_quote_drift`), this is stated so it can be promoted to the
shared layer rather than calcifying as local.

---

## 4. Ownership map

| Claim | Phase owner | Allowed adjudication family |
|---|---|---|
| release-point / PLAW artifact missing | P1 | source pathology (`blocked`) |
| section identity ambiguous (non-positive-law) | P2 | source pathology / unsupported (`us_classification_mapping_required`) |
| amendment instruction could not be lowered | P5 | source pathology / unsupported |
| payload (strike/insert text) could not be extracted | P6 | source pathology (`us_payload_*`) |
| canonical effect family unsupported | P7 | unsupported (`us_distributive_target`, table edits) |
| replay target not found | P8 | replay defect |
| replay invariant / mutation boundary broken | P8 | replay defect |
| oracle retained editorial note | P9 | compare-shape (`us_editorial_note_wrapper`) |
| sparse / missing release-point window | P9/P10 | source-sparse / source pathology |
| skipped non-positive-law section | P0/P1 | skipped / source pathology |
| rejected parsed operation (frontier family) | P7/P8 | rejected / unsupported |
| effective date unresolved | P7 | source pathology (`us_effective_date_*`) |

---

## 5. Minimum local taxonomy for a new frontend

### Source pathology
- `us_payload_quote_drift` — quoted strike text present only modulo typography.
- `us_payload_missing` — inserted/replacement body absent or truncated in PLAW.
- `us_effective_date_unresolved` — no determinable effective date.
- `us_effective_date_conditional` — effective date is conditional/relative.
- `us_release_point_misstraddle` — before/after pins do not straddle the PL.
- `us_amendment_target_absent_in_base` — instruction targets a section the prior
  release point lacks.

### Compare-shape
- `us_editorial_note_wrapper` — oracle keeps a `<note>`/Editorial Note not
  enacted as operative text.
- `us_heading_catchline_projection` — section heading/catchline projection
  difference only.
- `us_table_layout_only` — dollar-threshold/fee table layout-only difference.

### Replay defect
- `us_text_patch_not_applied` — `TEXT_REPLACE`/`TEXT_REPEAL` target found but
  strike/insert not effected.
- `us_repeal_residue` — `REPEAL` left addressable structure behind.
- `us_insert_anchor_misplaced` — `INSERT` landed at the wrong sibling position.
- `us_renumber_order_violation` — redesignation applied out of order.

---

## 6. Partition rules for verification

When verification diverges, partition into:

- `consistent` — materialized after-tree matches the next release point under the
  named editorial projection.
- `replay_defect` — a US replay-owned adjudication fired or a deterministic
  invariant broke.
- `compare_shape_only` — difference is entirely an editorial-note/heading/table
  projection artifact.
- `source_sparse` — no straddling release point or no acquired PLAW for the edge.
- `untouched_drift` — a USC path differs but no operation in the window targeted
  it (oracle/editorial drift, not our replay).
- `blocked` / `unsupported` — frontier family or non-positive-law section.
- `skipped` — non-positive-law section excluded in first scope.
- `rejected` — parsed op rejected by a constraint.
- `error` — execution error.

Detection: editorial projection is applied before comparison; a path that differs
only inside a stripped `<note>` is `compare_shape_only`; a path no window op
targeted is `untouched_drift`; a path a window op targeted that still differs and
where replay emitted a replay-owned adjudication is `replay_defect`. Non-claim
rows (unsupported/skipped/rejected) stay separate from replay claims even when the
final text happens to align with the oracle.

---

## 7. Proof threshold for replay bugs

> A row is only upgraded to replay defect when replay emitted a replay-owned
> adjudication (e.g. `us_text_patch_not_applied`, `us_repeal_residue`,
> `us_insert_anchor_misplaced`) or when a deterministic invariant / mutation
> boundary was violated. Residual mismatch against the release point alone is not
> enough — it must first be cleared of editorial-note projection, effective-date
> timing, and quote-typography drift.

---

## 8. Review tests

Before merge, reviewers ask:

- Does this adjudication belong in source, compare, or replay? (A USC release
  point difference is compare-shape until proven otherwise.)
- Could this be shared instead of local? (`us_payload_quote_drift` is a promotion
  candidate.)
- Does it preserve the proved-vs-unresolved distinction? (Oracle is a witness;
  residuals carry `lawvm_wrong`/`oracle_suspect`/`missing_source`.)
- Does it narrow a phenomenon or create a catch-all bucket?

If the answer is "catch-all bucket", reject it.
