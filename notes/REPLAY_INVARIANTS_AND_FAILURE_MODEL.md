# Replay Invariants And Failure Model

Status: living spec, intentionally partial.
Kind: normative.

Purpose:

- define the first replay invariants worth enforcing
- separate hard replay failures from warnings and proof-surface signals
- define the smallest useful apply-accounting contract

Related docs:

- [CANONICAL_OP_SEMANTICS.md](CANONICAL_OP_SEMANTICS.md)
- [SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md](SOURCE_PATHOLOGY_AND_ADJUDICATION_SPEC.md)
- [CONFORMANCE_CORPUS.md](CONFORMANCE_CORPUS.md)

## 1. Core Rule

Replay should execute canonical meaning and report what it actually touched.
It should not perform opaque tree surgery and force later tooling to infer what
happened from whole-tree diffs.

## 2. First Invariant Slices

The first rollout should focus on small, crisp invariants.

### 2.1 `REPLAY_DUPLICATE_SIBLING_LABEL`

Meaning:

- under one parent, two addressable siblings of the same kind normalize to the
  same label

Why first:

- cheap
- high value
- directly guards addressability regressions

This should run both:

- after replay fold
- after product/materialization normalization

### 2.2 `REPLAY_APPLY_ACCOUNTING_BASIC`

Meaning:

- a successful op reports one clear semantic target
- a failed op is a no-op
- extra touched paths must be declared rather than guessed later

This is the minimum execution contract that makes replay auditable.

### 2.3 `REPLAY_PLACEHOLDER_STATE_CONTRADICTION`

Meaning:

- a provision cannot be simultaneously a repeal placeholder and substantive
  content
- placeholder normalization cannot shadow separately addressable provisions

This catches the class of bugs where replay/timeline is right but product
rendering corrupts the result.

## 3. Apply Mutation Event

Replay helpers should emit explicit mutation events while they still know why a
tree change happened.

Suggested minimal shape:

```python
@dataclass(frozen=True)
class ApplyMutationEvent:
    op_id: str
    source_statute: str
    action: str
    helper: str
    outcome: str

    resolved_target_path: tuple[tuple[str, str], ...] | None = None
    parent_path: tuple[tuple[str, str], ...] | None = None

    consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    created_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    removed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    replaced_paths: tuple[tuple[tuple[str, str], ...], ...] = ()

    renumbered_paths: tuple[tuple[
        tuple[tuple[str, str], ...],
        tuple[tuple[str, str], ...],
    ], ...] = ()

    placeholder_created_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
    placeholder_consumed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()

    used_fallback_tags: tuple[str, ...] = ()
    failure_reason: str = ""
```

Minimum fields worth threading first:

- `outcome`
- `helper`
- `resolved_target_path`
- `parent_path` for inserts
- `consumed_paths`
- `created_paths`
- `removed_paths`
- `replaced_paths`
- `renumbered_paths`
- `placeholder_created_paths`
- `placeholder_consumed_paths`
- `failure_reason`
- `used_fallback_tags`

## 4. Hard Failures vs Warnings vs Proof Signals

### 4.1 Hard failures

These should eventually fail strict mode:

- `REPLAY_DUPLICATE_SIBLING_LABEL`
- `REPLAY_SKIPPED_OP_MUTATED_TREE`
- `REPLAY_FAILED_OP_MUTATED_TREE`
- `REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION`
- `REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET`
- `REPLAY_MULTIPLE_PRIMARY_TARGET_CONSUMPTIONS`
- `REPLAY_PLACEHOLDER_STATE_CONTRADICTION`
- `REPLAY_PLACEHOLDER_ADDRESS_SHADOW`

### 4.2 Warnings

These are valuable but still recovery-sensitive:

- duplicate text / duplicate tract warnings
- suspicious broad-clobber residue
- undeclared secondary touches
- unexpected scaffold consumption while semantics remain corpus-sensitive

### 4.3 Proof-surface signals

These should not be treated as replay invariant failures:

- `CONTAINER_MEMBERSHIP_MISMATCH`
- `PARTIAL_WHOLE_SECTION_PAYLOAD`
- `SPARSE_ITEM_BODY_MISSING`
- `PAYLOAD_PREFERS_REPLAY`
- `repeal_only_without_payload`
- commencement ambiguity

Those belong in frontend/elaboration or evidence, not as hard replay-failure
logic.

## 5. Rollout Plan

### Stage 0

- add `ApplyMutationEvent`
- add optional `mutation_events_out`
- emit events passively
- preserve passive events in replay metadata as `apply_mutation_events`
  before any invariant logic depends on them

### Stage 1

- add passive replay-lint checks
- expose warnings without changing replay results

### Stage 2

- promote the cleanest invariants to strict-mode failures
- keep heuristic warnings non-fatal

### Stage 3

- feed apply events into proof/evidence so later layers stop reconstructing
  replay causality from end-state diffs alone
