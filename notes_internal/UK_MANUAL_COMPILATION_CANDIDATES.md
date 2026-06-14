# UK Manual-Compilation Frontier Candidates

Status: analysis + proposal. Not normative. Does not change replay/compile code.
Author lane: stream-uk-manual-compilation-candidates.

## Scope and method

The terminal UK product is a correct-by-construction consolidation
(AGENTS §2.1). Where the source does **not** deterministically specify the
result even in theory, the divergence is the **manual-compilation frontier**: it
needs an owned CLAIM that becomes authoritative input, not a guessed op.

This note lists concrete UK frontier candidates that are non-deterministic *in
principle* (no amount of parsing of the effect feed + affecting-act XML can
supply the missing determination), sourced from the `uk_manual_frontier_*`
classes in `src/lawvm/uk_legislation/source_adjudication.py`. Each candidate was
located by archive-backed replay/triage:

```
export LAWVM_CANONICAL_DATA_ROOT=<DATA_ROOT>
uv run lawvm uk-effects  <id> --manual-compile-status manual_compile_candidate --evidence-jsonl <out>
uv run lawvm uk-effect   <id> <effect_id> --show-text
```

Statutes compiled for real instances: ukpga/2008/17 (Housing and Regeneration
Act 2008), ukpga/2003/44 (Criminal Justice Act 2003), ukpga/2002/29 (Proceeds
of Crime Act 2002), ukpga/1992/4 (Social Security Contributions and Benefits
Act 1992), ukpga/2018/12, ukpga/2020/17.

The existing manual-claims machinery is:

- the per-family claim **templates** in `src/lawvm/tools/uk_claim_templates.py`
  (gated by `UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS` in
  `src/lawvm/uk_legislation/manual_claim_templates.py`),
- the **action-family → proof-semantic** map in
  `_required_operation_family_proof_semantics` (same file), and
- the deterministic, provenance-only **validator**
  `lawvm uk-semantic-claims-validate` in `src/lawvm/tools/uk_semantic_claims.py`
  (per `notes/MANUAL_COMPILATION_CLAIMS.md` §4.1).

A candidate "fits existing machinery" if its `manual_compile_rule_id` is in
`UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`, has an action family with a proof semantic,
and the validator already checks that proof family. It "needs machinery" if any
of those is missing.

---

## Candidate 1 — Appropriate-place definition-entry insert (no named anchor)

**Concrete instance.** `ukpga/2008/17` s. 31(12) ← `uksi/2018/1040` Sch. para.
5(c), effect `key-f205c0adb8bda34da6a003ace58489b1`, effect type *words
inserted*, effective 2018-10-01. Source witness (P3 `schedule-paragraph-5-c`):

> "in subsection (12), after "In this section—", insert— "low cost home
> ownership accommodation" has the meaning given by section 70, and ."

Pathology `unhandled_instruction_text`; lowering rejection
`uk_effect_appropriate_place_definition_entry_insert_rejected`;
`manual_compile_rule_id = uk_manual_frontier_appropriate_place_definition_entry_candidate`.

**Why non-deterministic in theory.** The source fixes the *anchor only to the
start of the definition block* ("after 'In this section—'") and supplies the new
entry, but does **not** name a predecessor/successor sibling. The drafting
convention is that the new term sits in **alphabetical order** among the existing
definitions — but that ordering is an *editorial determination of the
publisher*, not a fact in the feed or the affecting XML. The list at s. 31(12)
in the live tree is the only place the order can be read, and replay is forbidden
to infer placement from live text (the very thing that makes it editorial). Two
different correct editors could place a new "l…" term at materially different
indices if the live list is itself non-alphabetical or contains run-in terms.

**Manual-compilation proposal.** The missing fact is the **exact insertion
anchor** (predecessor or successor definition term, or an explicit ordering
rule). This maps to the existing machinery:
`uk_manual_frontier_appropriate_place_definition_entry_candidate` →
`manual_compile_suggested_claim_template` emits a `definition_entry_insert`
template with `placement_family = appropriate_place_requires_anchor_claim`,
required ownership `validated_predecessor_or_successor_anchor`, and proof
semantics `("definition_entry_insert_term_boundary_claim",
"appropriate_place_anchor_or_ordering_claim")`. The owned claim supplies
`predecessor_anchor`/`successor_anchor` (or an ordering rule id) plus a live
`subtree_sha256` precondition on the definition list carrier.

**Validator.** `lawvm uk-semantic-claims-validate` with
`--live-targets-jsonl` from `lawvm uk-live-target-index`. The
`appropriate_place_anchor_or_ordering_claim` semantic checks the claim references
source payload evidence, declares a validated predecessor/successor anchor *or*
an ordering rule listed in `required_validator_checks`, that any explicit anchor
live path sits inside the proof's referenced live carrier, and that inserts land
under the declared live definition-list parent. The anchor is thus checked
against the live list, not free-form.

**Fits existing machinery: YES.**

---

## Candidate 2 — Appropriate-place index/list-entry insert (no named anchor)

**Concrete instance.** `ukpga/2008/17` s. 276 (index of defined expressions) ←
`ukpga/2014/14` Sch. 4 para. 137(b), effect
`key-006071d4bbac345161c87a6c2756e2c6`, *words inserted*, effective 2014-08-01.
Source witness (P3 `schedule-4-paragraph-137-b`):

> "in the appropriate place insert— Registered society Section 275"

Pathology `appropriate_place_insert_unsupported`;
`manual_compile_rule_id = uk_manual_frontier_appropriate_place_candidate`.

**Why non-deterministic in theory.** The phrase "in the appropriate place" is an
explicit *delegation of placement to the editor*. The feed and affecting XML give
the new index row ("Registered society | Section 275") and the target table
(s. 276 index), but the row's position is whatever alphabetical/structural slot
the publisher chooses. Nothing in the source enumerates it. This is the textbook
"appropriate place" frontier in AGENTS §2.1.

**Manual-compilation proposal.** Missing fact: the **anchor row** (the existing
index entry the new row follows/precedes) or the table's ordering rule. Maps to
the existing `appropriate_place_mutation` template
(`uk_manual_frontier_appropriate_place_candidate`) with proof semantic
`appropriate_place_anchor_or_ordering_claim`; required ownership
`validated_predecessor_or_successor_anchor` + `target_container_boundary`.

**Validator.** Same `appropriate_place_anchor_or_ordering_claim` proof family;
the validator confirms `source_witness_uses_appropriate_place_formula`, that the
claim supplies an exact anchor or ordering rule, that inserts stay within the
declared index/list carrier, and that unclaimed sibling rows are preserved. The
ordering rule (if claimed instead of an anchor) must be one of the proof's
declared `required_validator_checks`, so "alphabetical" cannot be smuggled in as
prose.

**Fits existing machinery: YES.**

---

## Candidate 3 — Span-vs-enumeration + cross-act placement in a repeal table

**Concrete instance.** `ukpga/2003/44` (CJA 2003) s. 337(13)(a)(i)-(iii) ←
`ukpga/2006/52` "Sch. 16 para. 233(4)(a)(i) Sch. 17", effect
`key-9e6f6698f9c15fdeb66c9527fbea98a8`, *repealed*, effective 2009-03-28.
`manual_compile_rule_id = uk_manual_frontier_repeal_table_candidate`;
acquisition observation `uk_affecting_act_compound_reference_split_fallback`.
The affecting Schedule 17 (Repeals and Revocations) row for CJA 2003 actually
reads:

> "In section 337(13), in paragraph (a) sub-paragraphs (i) to (iii), (v), (vii)
> and (viii), and paragraph (b)."

**Why non-deterministic in theory.** Two distinct non-determinisms compound:

1. **Cross-act placement.** The feed's affecting reference is the *compound*
   string "Sch. 16 para. 233(4)(a)(i) **Sch. 17**". Which schedule carries the
   authoritative repeal instruction (the textual amendment in Sch. 16 vs. the
   repeal table in Sch. 17) is not deterministically resolvable from the
   concatenated citation — the parser already had to `…compound_reference_split`
   it.
2. **Span-vs-enumeration.** The feed encodes the affected provisions as the
   *range* "(i)-(iii)", but the Schedule 17 table actually repeals the
   *enumeration* "(i) to (iii), (v), (vii) and (viii)". The hyphen notation in
   the feed is lossy: a span "(i)-(iii)" does not, even in principle, recover the
   non-contiguous members (v),(vii),(viii) or paragraph (b). No parse of the feed
   row can know whether "(i)-(iii)" means the literal contiguous span or is the
   feed's truncated rendering of a longer enumeration.

**Manual-compilation proposal.** Missing facts: (a) which schedule is the
authority surface, and (b) the **exact enumerated set of repealed sub-units**
read from the repeal-table cell, not the feed's range. The repeal-table family
already exists (`uk_manual_frontier_repeal_table_candidate` is in
`UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`); the claim should own the matched repeal
table row, its `Extent of repeal` cell, and the enumerated child set, and emit
one `TEXT_REPEAL`/structural-repeal op per enumerated unit. The range-feed value
becomes evidence to *reconcile against*, not the authority.

**Validator.** `lawvm uk-semantic-claims-validate` against the repeal-table
source preview: `source_text_preconditions` require the quoted "Extent of
repeal" cell text and the enumerated sub-unit list as exact snippets (with
SHA-256), and a live-target precondition per repealed path. This deterministically
checks that the claimed enumeration is *literally in the Sch. 17 cell* and that
each claimed live path exists — so the editor cannot widen the feed's "(i)-(iii)"
without the table cell witnessing the extra members.

**Fits existing machinery: MOSTLY** — the repeal-table template exists, but it
has **no dedicated cross-act-placement proof semantic** to record *which* of two
cited schedules is the authority surface. See machinery-change note M3.

---

## Candidate 4 — Amendment-program (deictic) target: insert anchored to a
provision created by a sibling instruction

**Concrete instance.** `ukpga/1992/4` (SSCBA 1992) Sch. 5 para. 3C ←
`ukpga/2004/35` Sch. 11 para. 9, effect
`key-9af68bbd96112717e01dde96faafde13`, *inserted*, effective 2005-04-06.
Source witness (P1 `schedule-11-paragraph-9`):

> "9 After paragraph 3B **(inserted by paragraph 8 of this Schedule)** insert—
> … 3C …"

Pathology `amendment_text_target_unsupported`;
`manual_compile_rule_id = uk_manual_frontier_amendment_program_target_candidate`.

**Why non-deterministic in theory.** The insert is anchored "After paragraph 3B",
but paragraph 3B does **not exist in the base SSCBA 1992** — it is created by
paragraph 8 of the *same* affecting Schedule. Resolving the anchor requires
executing the sibling instruction (para. 8) first and proving the program order,
target identity, and that 3B was indeed materialised at the cited position. The
single effect row, read in isolation, points at a target that is absent from the
live tree; the meaning lives in the *program* (the ordered set of paragraphs of
Sch. 11), which the feed does not deterministically sequence relative to the
base. Replaying it against current text "happens to work" only because the
oracle already contains 3B — that is oracle-assisted, not source-deterministic.

**Manual-compilation proposal.** Missing fact: the proven **parent instruction
that creates the anchor** and the resulting target identity/boundary. Maps to the
existing `amendment_program_target_mutation` family
(`uk_manual_frontier_amendment_program_target_candidate`, proof semantic
`amendment_program_target_source_payload_and_boundary`). The claim owns the
amendment-program target id (the inserted-by chain), the inserted payload (3C),
and the live amendment-program carrier.

**Validator.** The `amendment_program_target_source_payload_and_boundary`
semantic checks the claim references source target evidence (the "inserted by
paragraph 8" deixis) and inserted-payload evidence, declares program
target-boundary + payload ownership, requires an operation-level
amendment-program target id or source target, and keeps insert/replacement ops
under declared live carriers. This makes the deictic chain auditable rather than
recovered from the oracle.

**Fits existing machinery: YES** (proof-plan integrity only; full program-order
proof remains a non-replayable obligation, which is the correct honest state).

---

## Candidate 5 — Savings/exception-qualified text omission

**Family + recognized shape.** `uk_manual_frontier_savings_qualified_text_omission_candidate`
(source pathology `savings_qualified_text_omission_unsupported`). The recognizer
`_savings_qualified_omission_parts` keys on the drafting shape:

> "omit the reference to <X> **except** <savings/applicability condition>"

I could not bind this to a live `effect_id` in the six statutes I had time to
compile (it is a genuinely rare family — 0 instances across ukpga/2003/44,
ukpga/2002/29, ukpga/1992/4, which together expose ~33 appropriate-place and ~28
repeal-table candidates). It is retained as a candidate because the classifier,
template, and proof family all exist and the non-determinism is structural; a
targeted sweep of tax/commencement-heavy statutes would surface a concrete id.

**Why non-deterministic in theory.** The instruction deletes printed text *only
within the scope carved out by an exception/savings clause*. The exception's
**scope** (which occurrences, which transactions, from which date, for which
class of person) is a legal-applicability determination. Compiling it as an
unconditional `TEXT_REPEAL` over-deletes: it removes text the savings clause
preserves for in-scope cases. No parse can decide the boundary because the
boundary is an applicability predicate, not a text span.

**Manual-compilation proposal.** Missing fact: the **applicability scope** of the
savings condition, represented as scope metadata on the op rather than an
unconditional deletion. Maps to existing
`savings_qualified_text_omission` (template
`uk_manual_frontier_savings_qualified_text_omission_candidate`, proof semantic
`savings_qualified_omission_applicability_scope`). Required ownership includes
`savings_or_exception_condition` and `temporal_or_applicability_scope`.

**Validator.** The `savings_qualified_omission_applicability_scope` semantic
(`uk_semantic_claims.py`) requires **separate** source preconditions for the
omitted reference and the savings condition (`omitted_reference_precondition_ids`
and `savings_condition_precondition_ids`/`applicability_scope_precondition_ids`
must each be listed in `source_text_precondition_ids`), a live text-carrier
precondition, that the op is a text-omission action, and that the op *declares an
applicability or savings scope* (`_has_savings_scope_qualification`). So a claim
that tries to drop the exception and delete unconditionally is rejected.

**Fits existing machinery: YES.**

---

## Candidate 6 — Prospective / contingent commencement ("repealed at the end of
YEAR if not brought into force")

**Family + recognized shape.** Source pathology
`conditional_temporal_repeal_unsupported`, recognizer
`_looks_like_conditional_temporal_repeal_source`, keyed on:

> "… is repealed at/before/after the end of <YEAR> **if** … has **not been
> brought into force**."

(The sunset-clause pattern.) Classified
`uk_manual_frontier_conditional_temporal_repeal_out_of_scope`, status
`non_textual_or_out_of_scope`. The sibling `prospective_effect_applied` warrant
(`prospective_effect_warrant.py`) and the SI commencement audit
(`si_commencement_audit.py`, state `prospective_unresolved`) are the *sensors*
for the broader prospective family.

**Why non-deterministic in theory.** The result depends on an **external trigger
not in the feed**: whether some other provision was brought into force before the
cut-off date. The repeal is conditional on a future, out-of-band commencement
state (often set by a later SI, or never). The effect feed records the
conditional repeal as a dated effect, but the *condition's truth value* — and
therefore whether the printed text should be deleted at all — is not a fact any
parse of the affected statute or affecting act can supply. Applying it
unconditionally over-applies (deletes text the condition would have kept);
ignoring it under-applies. The warrant module's own docstring says the
application "becomes an owned claim rather than a silent default" — but that
claim form does not yet exist.

**Manual-compilation proposal.** Missing fact: the **resolved value of the
commencement trigger as of the compiled point-in-time** (did the referenced
provision commence before the cut-off? what date?). This does **not** fit
existing machinery:
- `uk_manual_frontier_conditional_temporal_repeal_out_of_scope` and
  `uk_manual_frontier_commencement_effect_out_of_scope` are **absent** from
  `UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`, so `manual_compile_suggested_claim_template`
  returns nothing;
- there is **no** `commencement`/`prospective`/`conditional_temporal` action
  family in `_required_operation_family_proof_semantics` and **no** proof
  semantic in `uk_semantic_claims.py` (`grep` count 0).
The work-item entry (`frontier_work_items.py`) only asks the claim to "route
effect to temporal applicability model" — a model that has no executable claim
form. See machinery-change note **M1** (highest value).

**Validator (proposed, M1).** A new `contingent_commencement_resolution` proof
semantic: the claim must reference (a) source evidence for the conditional clause
(the recognizer's YEAR + "not brought into force" snippets), (b) a declared
**trigger provision id** and (c) an **owned commencement determination** — the
date the trigger commenced, or an explicit "did not commence by <cut-off>"
finding, with a witness (the commencing SI id, or absence-of-commencement
evidence). A deterministic validator checks the snippets are literally in the
source, that the declared cut-off matches the source YEAR, and that the
determination carries a witness id (commencing SI) when it asserts commencement —
so the trigger resolution is auditable, and replay applies the repeal **iff** the
owned determination says the condition fired by the PIT.

**Fits existing machinery: NO — needs new claim kind + proof semantic + validator
(M1).**

---

## Candidate 7 — Point-in-time selection: competing cross-act effects sharing an
effective moment

**Family + locus.** Not a `uk_manual_frontier_*` source-pathology class (which is
itself the finding) — it lives in the **ordering** layer,
`src/lawvm/uk_legislation/ordering.py`. `_order_uk_effects_for_replay` sorts
effects by `(_effective_date, modified, affecting_act_id,
source_provision_order, effect_id)`. The same-moment diagnostic
`uk_effect_source_provision_order_normalized` is emitted **only** for groups
keyed by `_EffectOrderingGroupKey(effective_date, affected_target,
affecting_act_id)` — i.e. **same affecting act**.

**Why non-deterministic in theory.** When **two different affecting acts** amend
the **same target** with the **same effective date** and **incompatible
payloads** (e.g. two acts each substitute the same subsection text on the same
commencement day), there is no source fact that orders them: the feed gives both
the identical effective moment, and "which wins" is a legal-precedence question
(commencement-time-of-day, express disapplication, later-Act-prevails) that the
feed does not encode. The current code resolves it by the **`affecting_act_id`
string comparison** in `_sort_key` — a Python-accident tie-break that AGENTS §1.7
explicitly forbids — and emits **no ambiguity finding**, because the grouping key
includes `affecting_act_id` and so never forms a cross-act group.

**Manual-compilation proposal.** Missing fact: the **version precedence** between
two same-moment, same-target, incompatible effects. This does **not** fit
existing machinery: there is no detector for the cross-act same-moment
collision, and no `version_precedence`/`point_in_time` claim or proof semantic
(`grep` count 0). See machinery-change note **M2**.

**Validator (proposed, M2).** Two parts: (i) a deterministic **collision
detector** that groups by `(effective_date, affected_target)` *across*
affecting acts and flags incompatible-payload pairs as a blocking
`uk_effect_same_moment_cross_act_conflict` finding (turning a silent tie-break
into a visible ambiguity per §1.7/§1.8); (ii) a `version_precedence_claim` whose
validator checks the claim names both effect ids, declares the chosen ordering
with a witness (an express precedence clause, a commencement-time witness, or an
"oracle/editorial adjudication" outcome), and that the losing effect's payload is
recorded as superseded rather than dropped. Until the claim exists the pair stays
a non-replayable conflict finding.

**Fits existing machinery: NO — needs detector + new claim kind + validator
(M2).**

---

## Candidate 8 — Range-to-container substitution (section range → higher
container) requiring lineage

**Family + recognized shape.** Source pathology
`range_to_container_target_unsupported`, rule
`uk_manual_frontier_range_to_container_candidate`, lowering rejection
`uk_effect_range_to_container_substitution_rejected`. Shape: the source
substitutes a *range of sections* (e.g. "for sections 12 to 17 substitute …")
with a payload that is actually a *higher-level container* (a Part/Chapter) whose
new children re-enumerate the displaced sections.

I did not bind a live `effect_id` in the compiled set (also a rare family — 0
across the three rich statutes, though `structural_child_range_substitution`
appeared 3× as the sibling within-parent variant, e.g. ukpga/2003/44 s. 201(3)(a)
← ukpga/2008/4 Sch. 4 para. 85). Retained because the non-determinism is
structural and the template + proof family exist.

**Why non-deterministic in theory.** Replacing "ss. 12–17" with a container does
not deterministically specify the **lineage**: which displaced section maps to
which new child, which EIDs are reborn vs newly minted, and whether crossheadings
spanning the old range survive. The feed's range notation ("12–17") and the flat
container payload under-determine the child-to-child migration; §1.6 requires
migration/lineage evidence that the source does not enumerate.

**Manual-compilation proposal.** Missing fact: the **lineage/migration map** from
each replaced source unit to each container child. Maps to existing
`range_to_container_substitution` (proof semantic
`range_to_container_source_range_payload_and_lineage`); required ownership
includes lineage/migration ownership, and ops must declare migration paths + a
lineage/migration event id.

**Validator.** The `range_to_container_source_range_payload_and_lineage` semantic
checks source-range evidence + container-payload evidence, requires replacement
ops to declare migration paths and a lineage event id, and confines changed paths
to the source range or declared migration paths. So the migration is auditable
and cannot silently reparent unclaimed siblings.

**Fits existing machinery: YES.**

---

## Summary: fits-existing vs needs-machinery

| # | Candidate | Family / rule | Fits existing? |
|---|-----------|---------------|----------------|
| 1 | Appropriate-place definition entry | `appropriate_place_definition_entry_candidate` | YES |
| 2 | Appropriate-place index entry | `appropriate_place_candidate` | YES |
| 3 | Span-vs-enumeration + cross-act repeal table | `repeal_table_candidate` | MOSTLY (no cross-act placement proof — M3) |
| 4 | Amendment-program deictic target | `amendment_program_target_candidate` | YES |
| 5 | Savings/exception-qualified omission | `savings_qualified_text_omission_candidate` | YES |
| 6 | Prospective / contingent commencement | `conditional_temporal_repeal_out_of_scope` | **NO — M1** |
| 7 | Point-in-time cross-act same-moment conflict | ordering layer (no class) | **NO — M2** |
| 8 | Range-to-container substitution + lineage | `range_to_container_candidate` | YES |

**5 of 8 fit existing machinery cleanly** (1, 2, 4, 5, 8). **One is partial**
(3 — repeal-table template exists but lacks a cross-act-placement proof). **Two
need new machinery** (6, 7).

### Machinery changes

- **M1 (highest value) — Contingent/prospective commencement claim.** Add
  `uk_manual_frontier_conditional_temporal_repeal_out_of_scope` (and
  `commencement_effect_out_of_scope`) to `UK_MANUAL_CLAIM_TEMPLATE_RULE_IDS`, a
  `contingent_commencement_resolution` action family + proof semantic in
  `_required_operation_family_proof_semantics`, and a validator that ties the
  conditional-repeal source snippets to an **owned commencement determination**
  (trigger provision id, commenced-by date or did-not-commence finding, witness
  SI id) gated to the compiled PIT. This is the single highest-value change: the
  prospective family already has *sensors* (`prospective_effect_warrant.py`,
  `si_commencement_audit.py`) whose explicit design goal is that "application of a
  prospective effect becomes an owned claim" — the claim form is the missing
  half, and it is the family most likely to cause **over-application** (deleting
  text the source has not commenced), the forbidden direction under §2.1.

- **M2 — Same-moment cross-act conflict detector + version-precedence claim.**
  Add a deterministic detector that groups effects by `(effective_date,
  affected_target)` across affecting acts, flags incompatible-payload pairs as a
  blocking `uk_effect_same_moment_cross_act_conflict` finding (today they are
  resolved by `affecting_act_id` string order — a §1.7 violation that emits no
  finding), plus a `version_precedence_claim` whose validator records the chosen
  ordering with a witness and marks the losing payload superseded, not dropped.

- **M3 — Cross-act placement proof for compound affecting references.** Extend
  the repeal-table / placement families with a proof obligation that owns *which*
  of several cited surfaces (e.g. "Sch. 16 … Sch. 17") is the authority, witnessed
  by the matched table cell, so the
  `uk_affecting_act_compound_reference_split_fallback` no longer resolves
  authority implicitly.

All three preserve the discipline of `notes/MANUAL_COMPILATION_CLAIMS.md`: the
claim proposes meaning, a deterministic validator checks it against source
witnesses + live target state, and replay executes only validated claims.
