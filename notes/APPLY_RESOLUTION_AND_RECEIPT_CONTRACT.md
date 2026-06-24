> **Status (2026-06-22):** Current. Kind: Normative draft (the semantic apply waist; vertical slice landed for chapter/part INSERT + FI typed relabel). All cited impl symbols verified present; the YAML's open items (ObservedWriteAudit still passive, OccupancyTransitionPolicy §6 still un-unified vs canonical_intent.OccupancyPolicy, receipt persistence/strict enforcement) remain accurately open. No stale paths or PEG3/§1.13 refs.

---
title: LawVM Apply Resolution and Receipt Contract — the Semantic Apply Waist
status: normative draft; vertical slice landed for the chapter/part INSERT
  family and Finland typed relabel family (binding consumption §3 step 3 +
  WriteReceipt §4 with receipt-derived mutation events). ObservedWriteAudit
  (§5) has a passive core helper and fire-drill tests. WriteReceipt now has a
  strict core projection into CertifiedTreeTransition certified-core rows (§9);
  receipt persistence/exporter consumption, strict enforcement, and occupancy
  enforcement (§6) remain future work.
---

# Apply Resolution and Receipt Contract

The epistemic plane already has its rule: no evidence row, diagnostic row,
candidate row, or oracle row may authorize replay merely because it exists —
replay authority requires explicit execution authorization
(LAWVM_PROOF_SURFACES.md, `ExecutionAuthorization`). This contract states the
symmetric rule for the **semantic apply plane**:

```text
No semantic write counts as properly executed unless the binding and the
write receipt agree.

nominal target ≠ landed write proof
```

The normative replay principle, frozen:

```text
The address you bind is the address you write, and the address you write is
the address you declare.
```

The resolved binding, the landed write, and the declared mutation-accounting
(and certificate-transition) address MUST either be identical or be connected
by a NAMED migration, recovery, scaffold, restoration, or container-placement
rule recorded in the receipt. Unexplained divergence is a mutation-boundary
failure, never an emitter or helper freedom.

## 1. The semantic apply plane

These are one architecture, not independent helper refactors:

```text
Semantic Apply Plane
  target binding            ScopedTargetResolver → ResolverBinding   (§2, §3)
  occupancy                 OccupancyTransitionPolicy                (§6)
  write receipt             WriteReceipt                             (§4)
  mutation-boundary proof   ObservedWriteAudit / MutationBoundaryProof (§5, §8)
  temporal effect           TemporalBoundExpr lane (separate doc)
  certificate transition    CertifiedTreeTransition leaf emission    (§9)
```

One source operation flows through the waist as:

```text
source operation
→ scoped binding / target resolution     (ResolverBinding)
→ canonical write                        (tree patch)
→ write receipt                          (WriteReceipt: landed footprint)
→ observed mutation-boundary proof       (independent before/after diff)
→ certificate transition leaf            (pre/post hashes from the receipt)
```

An apply helper MUST NOT derive mutation-accounting paths solely from the
nominal target. It MUST produce or consume a WriteReceipt that records the
landed footprint. Any mismatch between bound target and landed footprint MUST
be explained by a named migration, recovery, scaffold, or editorial-projection
rule, or become a blocking mutation-boundary residual in strict mode.

## 2. ScopedTargetResolver

One resolver owns target binding for apply lanes. The name is deliberately
NOT provision-only: sections/chapters/parts are today's hot recurrence, but
the same resolver serves schedule paragraphs, definitions, table entries,
headings, items, subsections, and cross-act targets. Jurisdiction specifics
(label grammars, normalization) enter as policy values, never as resolver API
shape.

```python
resolve(kind, label, scope, policy) -> ResolverBinding
```

### 2.1 ResolverBinding

```python
@dataclass(frozen=True, slots=True)
class ResolverBinding:
    binding_id: str
    target_path: IRPath | None
    target_address: LegalAddress | None
    status: Literal["resolved", "ambiguous", "not_found", "blocked_by_policy"]
    policy_id: str
    rung_id: str | None                  # the rung that produced the binding
    candidates_by_rung: tuple[CandidateSetCertificate, ...]
    rejected_candidates: tuple[RejectedCandidate, ...]
    fallback_used: bool
    fallback_rule_id: str | None
    source_scope: ScopeHierarchy         # the scope the source op declared
    normalized_label: str
    normalizer_id: str
    finding_refs: tuple[str, ...]
```

`ResolverBinding` PROJECTS into the existing proof-surface grammar
(`TargetResolutionCertificate` / `CandidateSetCertificate`); it does not
replace it. A row that would otherwise smuggle an implicit uniqueness claim
still requires its `CandidateSetCertificate` — the binding carries them.

### 2.2 Fallback rungs (named, never boolean)

Not all fallbacks are equal; a single `fallback_used` bool is forbidden as
the load-bearing record. Each rung is a named rule:

```text
rung                          example semantics
required_exact_binding        scoped exact match or fail
safe_scoped_fallback          unique within the declared scope hierarchy
unique_global_fallback        unique across the work; FORBIDDEN for some
                              families (policy decides)
placeholder_shadow_fallback   placeholder beats substantive duplicate or
                              vice versa, per occupancy policy
migration_ledger_follow       binding resolved through a lineage edge
uncovered_body_ambiguity      grammar gap left an uncovered body; binding
                              allowed only with a recorded ambiguity row
```

Every rung carries:

```text
rule_id
legal meaning (one sentence)
strict disposition    (binds | blocks | blocks_with_residual)
quirks disposition
candidate count
rejection reasons
```

A rung MUST NOT widen target scope without a proof-surface row. "Unique
global fallback" MAY be acceptable for one operation family and forbidden for
another — the disposition lives in the policy table, not in helper code.

### 2.3 The risk this guards against

The known failure mode is a large, clever resolver that silently reproduces
today's fallback behavior under a nicer type. Enforced antidotes:

```text
Every fallback rung is named.
Every rung has candidate counts.
Every rejection is visible.
Every rung has strict/quirks disposition.
No rung can widen target scope without a proof-surface row.
```

## 3. Resolver migration plan (vertical, not horizontal)

Do not rewrite all resolution sites at once. The rollout is VERTICAL: one
high-risk family end-to-end (binding → receipt → audit → certificate
transition leaf), then expand.

```text
1. Wrap the existing scoped-section resolution helper
   (finland apply_policy._resolve_section_path_with_fallbacks) as a
   ResolverBinding producer.                                    [DONE]
2. Add passive observational equality checks against existing path outputs.
                                                                [DONE]
3. Convert the known-bad chapter/part insert path first.        [DONE]
   _apply_container_op consumes the binding: _resolve_container_target
   wraps _find_container_path_with_part_scope with rung provenance and
   work-wide candidate counts; container_resolver_binding
   (fi.container_target.v0) projects it; the write target comes from
   binding.target_path. The container ladder has a single scoped-find
   rung — no widening fallback exists for this family. Conversion covers
   ALL container target ops (the resolution site is shared); receipts
   (§4) cover the INSERT family and the implemented Finland typed relabel
   branches (chapter, part, section, subsection).
4. Convert _find_scoped_section_insert_parent_path AND its duplicate
   same-name sibling (one in apply_structure_ops, one in
   apply_typed_dispatch) — the duplication itself is part of the bug class.
5. Convert move/rebind paths.
6. Replace direct provision-index lookups and top-level finds in apply code.
7. Lint: no raw provision-index lookup or top-level find in apply modules
   outside the resolver.
```

Tests for the conversion assert BOTH: the same final replay result, AND that
binding provenance includes the expected `rung_id` and candidate counts.
Required fixtures:

```text
same chapter label in multiple parts
same section label in multiple parts
placeholder vs substantive duplicate
migration-ledger rebind
unique global fallback allowed
unique global fallback forbidden
bare top-level find would pick the wrong part
```

## 4. WriteReceipt

`MutationEvent` and mutation accounting are carriers; the missing piece is
the PRODUCER contract. The receipt is the helper's record of what actually
landed — not what it intended.

Implementation state: `lawvm.core.write_receipt.WriteReceipt` exists and is
produced by `_apply_container_op` for the chapter/part whole-container INSERT
lane (scaffold consume, base-chapter merge, fresh placement with part-hint
scaffolding and placeholder consumption — each divergence carries its named
rule id). Finland typed relabel execution also produces receipts for chapter,
part, section, and subsection relabel/renumber writes; the old address is the
bound target, the new address is the landed primary path, and the migration
rule id explains that divergence. Both dispatch sites derive the op's
ApplyMutationEvent from the receipt (`_emit_apply_mutation_event_from_receipt`).
Pre/post structural hashes use the frozen §2.2 recipe
(`lawvm.core.ir_helpers.structural_subtree_hash`), computed at the write. Not
yet: the container child-section INSERT sub-lane, non-relabel op families,
receipt persistence, and the independent ObservedWriteAudit (§5).

```python
@dataclass(frozen=True, slots=True)
class WriteReceipt:
    op_id: str
    helper: str
    action: str
    bound_target_path: IRPath | None      # from ResolverBinding
    landed_primary_path: IRPath | None    # where the write actually landed

    created_paths: tuple[IRPath, ...] = ()
    replaced_paths: tuple[IRPath, ...] = ()
    removed_paths: tuple[IRPath, ...] = ()
    consumed_paths: tuple[IRPath, ...] = ()
    renumbered_paths: tuple[tuple[IRPath, IRPath], ...] = ()

    placeholder_created_paths: tuple[IRPath, ...] = ()
    placeholder_consumed_paths: tuple[IRPath, ...] = ()

    recovery_rule_ids: tuple[str, ...] = ()
    migration_rule_ids: tuple[str, ...] = ()
    fallback_rule_ids: tuple[str, ...] = ()

    pre_hashes: Mapping[str, str] = field(default_factory=dict)
    post_hashes: Mapping[str, str] = field(default_factory=dict)
```

Rules:

- The receipt is produced AT the write (helper boundary), from landed
  reality — the engine's existing `landed_paths_out` pattern (materialization
  returning the paths it actually wrote, post-renumber) is the seed of this
  contract; the receipt generalizes it.
- `bound_target_path` vs `landed_primary_path` divergence MUST be explained
  by a named rule in `recovery_rule_ids` / `migration_rule_ids` /
  `fallback_rule_ids` (moves/renumbers, lineage rebinds, placeholder
  recoveries, container placement, temporary-expiry restoration). Unexplained
  divergence → blocking mutation-boundary residual in strict mode.
- `pre_hashes` / `post_hashes` are the structural subtree hashes
  (CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.2) of every touched covering unit,
  keyed by address string. These are the values the certificate transition
  leaf carries — computed at the write, never reconstructed later.
- `ApplyMutationEvent` / `MutationEvent` rows are DERIVED from the receipt,
  not assembled independently — one producer, many projections.
- Receipts must not become "events but a new dataclass": a receipt without
  the independent observed diff (§5) is not a proof.

## 5. ObservedWriteAudit

The receipt records what the helper says landed; the audit is the
INDEPENDENT before/after diff that catches lying or incomplete receipts. It
remains permanently useful — it is not migration scaffolding.

```python
@dataclass(frozen=True, slots=True)
class ObservedWriteAudit:
    op_id: str
    observed_changed_paths: tuple[IRPath, ...]   # from actual tree diff
    receipt_declared_paths: tuple[IRPath, ...]   # union of receipt footprint
    undeclared_paths: tuple[IRPath, ...]         # observed - declared
    unobserved_declared_paths: tuple[IRPath, ...] # declared - observed
    status: Literal["clean", "qualified", "violation"]
```

```text
clean       observed == declared
qualified   divergence fully covered by named allowance/recovery/migration
            rules (mirrors MutationBoundaryProof.proved_with_allowance)
violation   undeclared changed paths, or declared-but-unobserved writes
```

The audit closes the self-referential gap the Fable audit found: mutation
accounting that compares helper-declared touched paths against
helper-declared allowed roots can pass a misdirected write by construction.
The audit's observed side comes from the tree, never from the helper.

Implementation state: `lawvm.core.observed_write_audit.ObservedWriteAudit`
and `build_observed_write_audit(...)` exist. The first implementation is
passive and relation-aware: exact observed/declared path equality is `clean`,
ancestor/descendant granularity differences with named receipt rules are
`qualified`, and unrelated observed or declared paths are `violation`. Finland
receipt-enabled typed apply branches append passive audits through
`write_audits_out`; replay metadata serializes them as `apply_write_audits`.
Tests cover clean writes, relabel-style parent/child granularity,
declared-but-unobserved writes, observed-outside-receipt writes, an actual
Finland container-insert receipt, and a section relabel audit emitted through
`apply_op`. The audit is not yet wired as a replay blocker.

## 6. Occupancy transition contract

Occupancy ENFORCEMENT waits for receipts (a gate that fires on declared
state repeats the vacuous-guard failure). Vocabulary UNIFICATION does not
wait:

- ONE `OccupancyTransitionPolicy` type; `core/occupancy.py` and
  `canonical_intent.OccupancyPolicy` stop diverging.
- Production intent builders stop constructing universally-permissive
  `allowed_from` sets for constrained actions — a policy that allows every
  transition class is a policy that can never fire, and MUST be lint-visible.
- Once receipts land, occupancy becomes checkable against reality: what kind
  of slot was consumed/replaced, did a tombstone become live text, did an
  insert consume absent/tombstone/substantive state. The receipt's
  placeholder/consumed path fields are the inputs.

Two NAMED evaluation lanes refine which slot/time the policy evaluates;
neither widens any `allowed_from` set:

- `section_move_replace_destination_rebind` (move-rider REPLACE): a
  johtolause move rider ("X §, joka samalla siirretään Y lukuun") resolves
  the target scope to the DESTINATION, which is absent by definition until
  the move lands. With the typed `move_clause_target_unit_kind` evidence
  present and a unique live origin elsewhere, occupancy is evaluated against
  the ORIGIN slot (the slot the move+replace recovery consumes). Without a
  live origin the REPLACE-on-absent violation stands.
- `temporally_disjoint_twin_insert` (staggered twin laws): a temporary
  gap-filler INSERT ("lisätään väliaikaisesti uusi X §", expires D) whose
  slot is occupied in document-fold order by a deferred-commencement twin
  effective strictly after D. The occupancies are disjoint in legal time;
  the collision exists only in fold order. Recorded as the typed
  `APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT` observation (evidence: the
  occupant installer's op-level effective date from replay history), not as
  a violation. Overlapping windows still violate.

## 7. Relationship to ExecutionAuthorization

Unchanged and explicitly restated: the resolver and receipt do NOT authorize
replay. `ResolverBinding` is pre-write authority over WHERE; a binding with
`status="resolved"` still requires the op's execution authorization to run at
all. `WriteReceipt.landed_*` paths MUST be contained within the
authorization's allowed mutation region:

```text
ResolverBinding.target_path
    must equal or relate by named rule to
WriteReceipt.landed paths
    which must be contained within
ExecutionAuthorization allowed mutation region
    and verified by
ObservedWriteAudit / MutationBoundaryProof
```

## 8. Relationship to MutationBoundaryProof

`MutationBoundaryProof` (proved / proved_with_allowance / unresolved /
violated) is the typed projection of the containment check. Under this
contract its inputs change producer, not shape: `selected_target_paths` come
from the ResolverBinding, declared paths come from the WriteReceipt, observed
paths come from the ObservedWriteAudit. The K1 cross-check (observed vs
declared) stays PASSIVE until receipts land for a family; it is promoted to
strict-blocking per family only after the family's receipts are clean (K1
undeclared-changed-paths = 0 for that family). No global flag-day.

## 9. Relationship to certificate transition leaves

WriteReceipt is the PRODUCER of CertifiedTreeTransition leaves
(CERTIFICATE_SCHEMA_V0.md §10, CERTIFIED_TREE_TRANSITION_TRACE_V0.md §5):

```text
receipt.landed paths + pre/post structural hashes
→ CertifiedTreeTransition certified core
  (target_address, pre_hash, post_hash, payload_hash)
```

`lawvm.core.certified_transition.certified_tree_transitions_from_receipt`
implements the strict producer projection for one receipt: every declared
footprint address must have a complete pre/post hash pair, hashes are rendered
with certificate `sha256:` spelling, `post != ""` becomes `set_subtree`, and
`post == ""` becomes `delete_subtree`. Declared no-op pairs (`pre == post`) and
undeclared hash keys are producer errors, not silently skipped rows.

Certificate v0 cannot be credible until exported transition leaves are produced
from WriteReceipts or an equivalent landed-footprint source — a bundle writer
that re-derives transitions from nominal targets can certify a write that never
happened where it claims. The experimental state-diff emitter carve-out
(certificate spec §10) remains only because receipts are not yet persisted as
replay products and because exporter granularity still needs alignment with
receipt granularity; it must die when those integration gaps close.

## 10. Acceptance criteria

For the resolver (per converted family):

```text
- zero direct provision-index lookups in apply code outside the resolver
- zero direct top-level finds in apply code outside the resolver
- all resolver fallbacks emit binding provenance (rung_id, candidate counts)
- multi-part duplicate-section fixtures pass
- binding candidate counts are available to proof surfaces
```

For the receipt (per converted family):

```text
- all converted helpers emit WriteReceipt
- ApplyMutationEvent is derived from WriteReceipt
- observed diff agrees with receipt (audit clean or qualified-by-named-rule)
- K1 undeclared changed paths = 0 for converted families
- receipt emits transition-leaf pre/post hashes
- receipt projects to CertifiedTreeTransition core rows without reconstructing
  paths from nominal targets
```

## 11. Fire-drill tests

Per the guard-liveness rule, every check in this contract must be shown to
fire through the production lane against deliberately broken inputs:

```text
- a helper that writes to a sibling of its bound target (no named rule)
  → audit violation, strict-blocking residual
- a receipt that omits a created path the diff observes
  → undeclared_paths nonempty, violation
- a receipt that declares a write the diff does not observe
  → unobserved_declared_paths nonempty, violation
- a rung that silently widens scope (global fallback where policy forbids)
  → blocked_by_policy, no binding
- a divergent landed_primary_path covered by a named migration rule
  → qualified, NOT a violation (the exception lane is also exercised)
- a universally-permissive occupancy policy in a production intent builder
  → lint failure
```
