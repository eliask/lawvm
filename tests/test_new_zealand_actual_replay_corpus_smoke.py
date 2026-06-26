"""NZ replay-actual smoke corpus pin (@slow).

Drives `nz-corpus replay-actual` in-process across the curated 33-work smoke corpus
and pins the structural e2e invariants:
* the slice-reconfirm defence-in-depth (`nz_actual_replay_refused_materialized_target_slice_diverges_from_oracle`)
  NEVER fires on a verified op (the §1.12 fix made the block-insert carveout flow from
  dry-run proof to re-confirm verbatim so the agreement notion is not silently re-derived
  under a stricter rule than the proof was verified under);
* any materialized transition's target slice fully agrees with the archived on-or-after
  oracle (`target_slice_agrees=True`);
* `replay_claims=True` whenever at least one transition materialized;
* every emitted refusal rule_id is one of the three known per-op refused classes
  plus the family-level surface_missing path.

Pins structural invariants, not fragile exact counts (AGENTS §2.9): a future
improvement that promotes more ops to verified should INCREASE the replayed-work
count without lowering any invariant here. The sweep diagnostic
`scripts/nz_actual_replay_classify.py` records detailed per-work outcome counts.

The smoke corpus is the curated dev slice pinned by `nz-corpus build-corpus`
under `data/nz/bench_corpus_smoke.csv`; skip if absent.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from lawvm.new_zealand.actual_replay import (
    NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
    NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
    NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
    build_archived_work_actual_replay,
)

_REAL_DB = (
    Path(__import__("os").environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz_legislation.farchive"
)
_SMOKE_CORPUS = (
    Path(__import__("os").environ.get("LAWVM_CANONICAL_DATA_ROOT") or Path(__file__).resolve().parents[1])
    / "data"
    / "nz"
    / "bench_corpus_smoke.csv"
)

_KNOWN_REFUSAL_RULES = frozenset(
    {
        NZ_ACTUAL_REPLAY_REFUSED_OP_NOT_DRY_RUN_VERIFIED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_OP_NEIGHBOURS_PERTURBED_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_SURFACE_MISSING_RULE_ID,
        NZ_ACTUAL_REPLAY_REFUSED_FAMILY_NOT_PROMOTABLE_RULE_ID,
    }
)


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.skipif(not _SMOKE_CORPUS.exists(), reason="NZ smoke corpus CSV not present")
@pytest.mark.slow
def test_replay_actual_smoke_corpus_pins_structural_invariants() -> None:
    works = [row["work_id"] for row in csv.DictReader(_SMOKE_CORPUS.open())]
    assert works, "smoke corpus CSV present but had no work rows"

    n_cleanly_replayed = 0
    n_transitioned_works = 0
    n_refused_works = 0
    n_neither = 0
    unknown_refusal_rules: set[str] = set()
    slice_diverge_refusals_seen = 0

    for work_id in works:
        report = build_archived_work_actual_replay(
            _REAL_DB, work_id=work_id, families=NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES
        )
        summary = report.summary()

        # Invariant 1: the §1.12 slice-reconfirm defence-in-depth NEVER fires on a
        # verified op — its rule_id must never appear in ANY work's refusals.
        slice_diverge_refusals_seen += sum(
            1 for ref in report.refusals if ref.rule_id == NZ_ACTUAL_REPLAY_REFUSED_MATERIALIZED_SLICE_DIVERGES_RULE_ID
        )

        # Invariant 2: every refusal rule_id is a KNOWN class — an unknown rule_id is a
        # silent gap in the contract or an unaccounted lane.
        for ref in report.refusals:
            if ref.rule_id not in _KNOWN_REFUSAL_RULES:
                unknown_refusal_rules.add(ref.rule_id)

        # Invariant 3: any materialized transition fully agrees with the archived
        # on-or-after oracle on its target slice.
        for transition in report.transitions:
            assert transition.target_slice_node_count > 0, (
                f"{work_id}: materialized a transition with an empty target slice (no ops)"
            )
            assert transition.target_slice_agreements == transition.target_slice_node_count, (
                f"{work_id}: materialized transition whose target slice does NOT fully agree "
                f"({transition.target_slice_agreements}/{transition.target_slice_node_count})"
            )
            assert transition.target_slice_agrees is True, (
                f"{work_id}: materialized transition with target_slice_agrees=False"
            )

        replayed = summary["transitions_replayed"] > 0
        refused = summary["transitions_refused"] > 0
        if replayed and not refused:
            n_cleanly_replayed += 1
        if replayed:
            n_transitioned_works += 1
        if refused:
            n_refused_works += 1
        if not replayed and not refused:
            n_neither += 1

        # Invariant 4: replay_claims=True iff at least one transition materialized.
        assert summary["replay_claims"] is (summary["transitions_replayed"] > 0), (
            f"{work_id}: replay_claims={summary['replay_claims']} inconsistent with "
            f"transitions_replayed={summary['transitions_replayed']}"
        )

    # Invariant 5 (smoke ground state lower bound): at least nine works now
    # materialize cleanly (this cycle's §1.12 fix promoted act_public_1956_47 from
    # fail-closed to cleanly replayed; the smoke corpus has nine such positive
    # cases). A regression that breaks any of these will fail this pin. Raising
    # the floor (NOT lowering it) is the legitimate direction of motion; do not
    # pin an exact count a concurrent improvement would break (AGENTS §2.9).
    assert n_transitioned_works >= 9, (
        f"smoke corpus pin: only {n_transitioned_works} works replayed at least one transition "
        f"(expected >=9); ground state regressed."
    )

    # Invariant 6: the §1.12 fix must hold enitrely — no slice-diverge refusals
    # anywhere on the smoke corpus, ever.
    assert slice_diverge_refusals_seen == 0, (
        f"smoke corpus pin: {slice_diverge_refusals_seen} materialized-slice-diverge refusals "
        f"fired — the §1.12 slice-reconfirm carveout propagation regressed."
    )

    # Invariant 7: every refusal is an accounted-for rule_id.
    assert not unknown_refusal_rules, (
        f"smoke corpus pin: actual-replay emitted an unknown refusal rule_id "
        f"(every refusal must be one of the known named classes): {sorted(unknown_refusal_rules)}"
    )

    # Invariant 8 (§0 evidence propagation, smoke ground state): the smoke corpus
    # carries at least one actual-replay refusal where divergence_class was propagated
    # to the actual-replay refusal receipt (the §0 source-truth-bucket signal flowing
    # dry-run → promotion plane). The propagation contract itself (zero loss when
    # dry-run had a class, zero fabrication when dry-run had None) is pinned by the
    # synthetic struct test on the synthetic replay-residual shape; this smoke pin
    # just verifies the path fires on real archived data.
    any_divergence_propagated = any(
        "divergence_class" in (ref.detail or {})
        for work_id in works
        for ref in build_archived_work_actual_replay(
            _REAL_DB, work_id=work_id, families=NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES
        ).refusals
        if ref.rule_id == NZ_ACTUAL_REPLAY_REFUSED_OP_DRY_RUN_RESIDUAL_RULE_ID
    )
    assert any_divergence_propagated, (
        "smoke corpus pin: no actual-replay residual refusal carries divergence_class. "
        "The §0 propagation path either regressed OR the smoke corpus lost the "
        "structural-divergence case it grounded (witness: act_public_1992_122 should "
        "emit several residual_replacement_mismatch refusals carrying divergence_class=structural_nodeset)."
    )


@pytest.mark.skipif(not _REAL_DB.exists(), reason="archived NZ farchive not present")
@pytest.mark.skipif(not _SMOKE_CORPUS.exists(), reason="NZ smoke corpus CSV not present")
@pytest.mark.slow
def test_replay_actual_surfaces_family_level_dry_run_refusals_as_receipt() -> None:
    """Family-level dry-run refusals (no candidate for repeal/replace/insert,
    preflight not ready) MUST be carried onto the actual-replay plane as a
    work-level receipt (NOT a fail-closed refusal that blocks a transition).

    Pins the §1.8 receipt-conservation contract surfaced in this cycle:
    before the fix, ``_partition_dry_run_outcomes`` silently ``continue``-d
    past family-level dry-run refusals (they declared nothing-to-replay rather
    than per-op blocks, so they could be skipped without breaking the
    fail-closed invariant). Filtering them silently violated the
    receipt-conservation law (AGENTS §1.8 — every filtered lane stays visible
    with a receipt) AND made a benchmark unable to distinguish "the family
    declared nothing" from "the family had candidates but they all failed
    verification" (the latter IS observable in ``refusals``; the former was
    invisible).

    The receipt's contract pinned here:

    * Invariant A (emission liveness): at least one work in the smoke corpus
      surfaces ``family_level_dry_run_refusals`` non-empty (the
      ``act_public_1858_*`` family of unamended works carries no-candidate
      dry-run refusals across all four promotable families).
    * Invariant B (rule_id-only-carried): the per-op fail-closed ``refusals``
      tuple does NOT contain the carried receipt rule_id
      ``nz_actual_replay_carried_family_level_dry_run_refusal`` — that rule_id
      lives ONLY in ``family_level_dry_run_refusals`` and the
      agreement-residual projection (typed as ``accepted_non_executable_frontier``
      per ``classify_refusal_family``: family declared nothing → no mutation →
      not a ``replay_bug``, not ``temporal_mismatch``, not ``source_footing_gap``).
    * Invariant C (family-attribution): every carried receipt carries the
      original dry-run refusal rule_id in ``detail["dry_run_refusal_rule_id"]``
      so a benchmark can attribute the "nothing to replay" event to the right
      family (repeal/replace/insert/preflight) without re-running the dry-run.

    Guard-liveness (AGENTS §2.9): Invariant A is the failure-surface —
    reverting ``_partition_dry_run_outcomes``'s family-level carry branch to
    the old silent ``continue`` makes ``family_level_dry_run_refusal_counts``
    empty for every work, failing Invariant A.
    """
    from lawvm.new_zealand.actual_replay import (
        NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID,
    )
    from lawvm.new_zealand.dry_run_oracle import classify_refusal_family

    works = [row["work_id"] for row in csv.DictReader(_SMOKE_CORPUS.open())]
    assert works, "smoke corpus CSV present but had no work rows"

    any_family_level_receipt_emitted = False
    for work_id in works:
        report = build_archived_work_actual_replay(
            _REAL_DB, work_id=work_id, families=NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES
        )

        # Invariant B (per-op refusal-rule_id isolation): the carry rule_id
        # appears ONLY in family_level_dry_run_refusals, NEVER in the per-op
        # fail-closed `refusals` tuple (which would inflate transitions_refused).
        carry_rule_id = NZ_ACTUAL_REPLAY_CARRIED_FAMILY_LEVEL_DRY_RUN_REFUSAL_RULE_ID
        per_op_rule_ids = {ref.rule_id for ref in report.refusals}
        assert carry_rule_id not in per_op_rule_ids, (
            f"{work_id}: the carry rule_id `{carry_rule_id}` appeared in `report.refusals` "
            f"-- it MUST live ONLY in `report.family_level_dry_run_refusals` so it never "
            f"inflates the fail-closed transition count (§1.8 receipts vs §1.12 fail-closed)."
        )

        # Invariant B': carried-refusal rule_id isolation. Every entry in
        # `family_level_dry_run_refusals` is the carry rule_id (no other rule_id
        # accidentally lands in the work-level receipt bucket).
        carried_rule_ids = {ref.rule_id for ref in report.family_level_dry_run_refusals}
        assert carried_rule_ids <= {carry_rule_id}, (
            f"{work_id}: `family_level_dry_run_refusals` carries rule_ids "
            f"{carried_rule_ids - {carry_rule_id}} that are NOT the carry rule_id; "
            f"only `nz_actual_replay_carried_family_level_dry_run_refusal` should appear here."
        )

        # Invariant C (family-attribution): each carried receipt has the
        # dry_run_refusal_rule_id + family in detail, and classifies as
        # `accepted_non_executable_frontier`.
        for carried_ref in report.family_level_dry_run_refusals:
            drr = carried_ref.detail.get("dry_run_refusal_rule_id", "")
            family = carried_ref.detail.get("family", "")
            assert drr, (
                f"{work_id}: a family_level_dry_run_refusal is missing "
                f"`detail.dry_run_refusal_rule_id` -- required so a benchmark can "
                f"attribute the nothing-to-replay event without re-running the dry-run."
            )
            assert family in {"repeal", "text_replace", "replace", "insert"}, (
                f"{work_id}: family_level_dry_run_refusal carries `detail.family={family!r}` "
                f"-- expected one of repeal/text_replace/replace/insert."
            )
            residual_family = classify_refusal_family(
                refusal_rule_id=carried_ref.rule_id,
                dry_run_refusal_rule_id=drr,
            )
            assert residual_family == "accepted_non_executable_frontier", (
                f"{work_id}: family_level_dry_run_refusal classified as "
                f"{residual_family!r} -- expected `accepted_non_executable_frontier` "
                f"because the family declared nothing-to-replay (no mutation, not a "
                f"replay_bug; the source is well-formed, this is the manual-frontier class)."
            )

        if report.family_level_dry_run_refusals:
            any_family_level_receipt_emitted = True

    # Invariant A (emission liveness): the `act_public_1858_*` and
    # `act_public_1875_*` family of unamended works surface the receipt across
    # multiple promotable families -- without this evidence, the carry path is
    # dead code (a regression that silently swallows the receipt would make
    # this check fail).
    assert any_family_level_receipt_emitted, (
        "smoke corpus pin: no work surfaces a family_level_dry_run_refusal receipt. "
        "The `_partition_dry_run_outcomes` carry branch has regressed to silent `continue` "
        "OR the smoke corpus lost the unamended-act witnesses it grounded (the "
        "`act_public_1858_*` and `act_public_1875_*` family of works MUST surface at "
        "least one carried receipt each -- they declare nothing to replay across multiple "
        "promotable families per the smoke corpus scan on 2026-06-24)."
    )
