"""Tests for the D10 ``COMPARE.DETERMINISTIC_GAP_VS_MANUAL_FRONTIER_PARITY`` audit.

Per :file:`notes_internal/audit_impl_D10.md` + AGENTS.md §0: every
replay-vs-oracle divergence must classify into EXACTLY ONE of
{deterministic_gap, manual_compilation_frontier, oracle_suspect}. Per-EID
taxonomic uniqueness is the contract. The audit surfaces double-classified
EIDs (an EID in >=2 of the three classes within one statute row) as blocking
``COMPARE.EID_DOUBLE_CLASSIFIED`` Observations.

The audit helper lives in :mod:`scripts.uk_broad_baseline` (per spec §1 the
audit lives next to its emitter — the broad-baseline driver — and not as
core; a generic core compile-adjudication-parity audit is deferred until
FI/EE/NZ emit an equivalent per-EID triple-classification surface).

Honest scope: the audit helper + AdjudicationRow/EidClassificationConflict
carriers + this regression test are LANDED here; the wire into
``summarize_results`` (after manual_frontier_records + the per-EID
oracle_suspect / deterministic_gap projections exist) is staged follow-up
per the D7/D8/D11 staged-wire discipline, declared via ``NO_FIRE_DRILL_YET``
in ``tests/test_fi_guard_liveness.py``.
"""

from __future__ import annotations

import scripts.uk_broad_baseline as bb
from lawvm.core.observation_registry import FINDING_REGISTRY


# --------------------------------------------------------------------------- #
# Firing case — the load-bearing §2.9 test (one EID in >=2 classes).          #
# --------------------------------------------------------------------------- #


def test_eid_in_two_classes_for_same_statute_fires_one_observation() -> None:
    """Per audit_impl_D10 §6: an EID classified into >=2 of the three §0 classes
    for the same statute yields exactly one
    ``COMPARE.EID_DOUBLE_CLASSIFIED`` Observation.

    Drives a hardcoded synthetic 2-class adjudication set:
    ``section-5`` carries BOTH ``deterministic_gap`` +
    ``manual_compilation_frontier`` classes, both under the same statute
    ``ukpga/2020/1``. The audit MUST surface exactly one Observation with the
    offending classes set.

    ``section-6`` (single class ``oracle_suspect``) is the §2.9 negative-witness:
    a single-class EID does NOT fire.
    """
    adjs = [
        bb.AdjudicationRow(
            statute_id="ukpga/2020/1",
            eid="section-5",
            classification="deterministic_gap",
            source_rule_id="uk_broad_residual_after_grounding",
            witness="broad_baseline grounding #1",
        ),
        bb.AdjudicationRow(
            statute_id="ukpga/2020/1",
            eid="section-5",
            classification="manual_compilation_frontier",
            source_rule_id="uk_manual_frontier_missing_payload_source_insufficient",
            witness="frontier #1",
        ),
        bb.AdjudicationRow(
            statute_id="ukpga/2020/1",
            eid="section-6",
            classification="oracle_suspect",
            source_rule_id="uk_compare_text_patch_preimage_consumed_by_replay_chain",
            witness="oracle #1",
        ),
    ]
    n, observations = bb.assert_classification_exclusive(adjs)
    assert n == 1, (
        f"only the double-classified section-5 EID MUST fire; got {n} conflicts"
    )
    assert len(observations) == 1
    obs = observations[0]
    assert obs.kind == "COMPARE.EID_DOUBLE_CLASSIFIED"
    assert obs.stage == "compare_oracle_classification"
    assert obs.source_statute == "ukpga/2020/1"
    detail = obs.detail
    assert detail["statute_id"] == "ukpga/2020/1"
    assert detail["eid"] == "section-5"
    assert set(detail["classes"]) == {
        "deterministic_gap",
        "manual_compilation_frontier",
    }
    # The two source_rule_id values map parallel to the classes tuple.
    sources_by_class = dict(zip(detail["classes"], detail["sources"], strict=False))
    assert (
        sources_by_class["deterministic_gap"]
        == "uk_broad_residual_after_grounding"
    )
    assert (
        sources_by_class["manual_compilation_frontier"]
        == "uk_manual_frontier_missing_payload_source_insufficient"
    )
    assert detail["rule_id"] == "COMPARE.EID_DOUBLE_CLASSIFIED"
    assert detail["owner"] == "compare_oracle_classification"
    assert "§0 disjoint-partition contract break" in detail["reason"]


# --------------------------------------------------------------------------- #
# Negative — single-class EID, multi-statute single-class, no EID-repeat.     #
# --------------------------------------------------------------------------- #


def test_single_class_per_eid_yields_zero_conflicts() -> None:
    """§2.9 negative: an EID in exactly one class is the compliant shape — never
    fires. A multi-statute baseline where each EID is uniquely classified is the
    clean witness (zero conflicts, zero Observations).
    """
    adjs = [
        bb.AdjudicationRow(
            "ukpga/2020/1", "section-5", "deterministic_gap", "rule-A"
        ),
        bb.AdjudicationRow(
            "ukpga/2020/1", "section-6", "manual_compilation_frontier", "rule-B"
        ),
        bb.AdjudicationRow(
            "ukpga/2020/2", "section-1", "oracle_suspect", "rule-C"
        ),
    ]
    n, observations = bb.assert_classification_exclusive(adjs)
    assert n == 0
    assert observations == ()


def test_three_classes_on_same_eid_fires_one_observation_with_all_three() -> None:
    """An EID in all three classes fires exactly ONE observation (not three).

    The audit reports per-(statute, eid) conflict pairs, not per-class pair.
    The full per-class projection (all three classes) is carried in the
    Observation's detail so triage can see the §0 contract break fully.
    """
    adjs = [
        bb.AdjudicationRow(
            "ukpga/1999/22",
            "section-5",
            "deterministic_gap",
            "grounding-id",
            "grounding-witness",
        ),
        bb.AdjudicationRow(
            "ukpga/1999/22",
            "section-5",
            "manual_compilation_frontier",
            "frontier-id",
            "frontier-witness",
        ),
        bb.AdjudicationRow(
            "ukpga/1999/22",
            "section-5",
            "oracle_suspect",
            "oracle-id",
            "oracle-witness",
        ),
    ]
    n, observations = bb.assert_classification_exclusive(adjs)
    assert n == 1, (
        f"the three-class conflict MUST surface as exactly one observation; "
        f"got {n}"
    )
    obs = observations[0]
    assert set(obs.detail["classes"]) == {
        "deterministic_gap",
        "manual_compilation_frontier",
        "oracle_suspect",
    }


# --------------------------------------------------------------------------- #
# Discriminators — closed-set, cross-statute isolation, duplicate-row dedup.  #
# --------------------------------------------------------------------------- #


def test_unknown_classification_class_is_silently_skipped() -> None:
    """A classification outside the closed-set known classes is NOT absorbed
    into a conflict by this audit.

    The audit only compares the three §0 classes. An EID classified into
    e.g. "weird_class" + "deterministic_gap" does NOT fire — the closed set
    of comparable classes is {deterministic_gap, manual_compilation_frontier,
    oracle_suspect} per audit_impl_D10 §2. The caller's projection owns the
    receipt for unknown-class rows (audit does not silent-swallow; it skips
    rows whose ``classification`` isn't in the known set).
    """
    adjs = [
        bb.AdjudicationRow(
            "ukpga/2020/1",
            "section-5",
            "weird_class",
            "rule-X",
        ),
        bb.AdjudicationRow(
            "ukpga/2020/1",
            "section-5",
            "deterministic_gap",
            "rule-A",
        ),
    ]
    n, observations = bb.assert_classification_exclusive(adjs)
    # Only the deterministic_gap row is in the known set; the weird_class row
    # is skipped — zero multi-class conflicts.
    assert n == 0


def test_cross_statute_eid_collision_is_not_a_conflict() -> None:
    """Two statute rows each carrying the SAME eid under DIFFERENT classes is
    NOT a §0 conflict — conflicts are per-(statute_id, eid), not per-eid alone.

    An EID classified differently in different statutes is legal: replay-vs-
    oracle divergence is per-statute (the source statute whose baseline is
    being scored); the same EID is a different concept in different statutes.
    """
    adjs = [
        bb.AdjudicationRow(
            "ukpga/2020/1", "section-5", "deterministic_gap", "rule-A"
        ),
        bb.AdjudicationRow(
            "ukpga/1988/1", "section-5", "manual_compilation_frontier", "rule-B"
        ),
    ]
    n, observations = bb.assert_classification_exclusive(adjs)
    assert n == 0


def test_duplicate_same_class_same_eid_does_not_fire() -> None:
    """An EID emitted under one class TWICE (duplicate projection rows) is NOT
    a multi-class conflict — it's a duplicate projection row.

    The audit dedups the (classification, source_rule_id) pair per EID before
    counting. A single-class EID with duplicates stays at zero conflicts. The
    caller's projection owns the duplicate-row receipt (this audit only
    reports the §0 disjoint-partition contract break).
    """
    adjs = [
        bb.AdjudicationRow(
            "ukpga/2020/1",
            "section-5",
            "deterministic_gap",
            "rule-A",
            "first witness",
        ),
        bb.AdjudicationRow(
            "ukpga/2020/1",
            "section-5",
            "deterministic_gap",
            "rule-A",
            "second witness (duplicate)",
        ),
    ]
    n, observations = bb.assert_classification_exclusive(adjs)
    assert n == 0
    assert observations == ()


def test_empty_adjudication_set_returns_zero_observations() -> None:
    """An empty adjudication set is the clean-state witness.

    Empty in → zero conflicts + empty Observations tuple. Per §1.10 the
    function never returns None (the absence of a conflict is a valid result,
    but None would be silent folklore).
    """
    n, observations = bb.assert_classification_exclusive([])
    assert n == 0
    assert observations == ()


# --------------------------------------------------------------------------- #
# Registry row presence + the closed-set is load-bearing.                    #
# --------------------------------------------------------------------------- #


def test_registry_row_registered_for_observation_finding() -> None:
    """The FindingSpec row is registered so the Observation validates.

    Sanity for the validate_finding_projection carrier contract: a
    role=``"observation"`` Observation requires a non-blocking carrier (per
    audit_impl_D10 §5, the wire consumer in summarize_results enforces the
    strict-mode hard-gate via fail_on_compare_eid_double_classified).
    """
    spec = FINDING_REGISTRY.get("COMPARE.EID_DOUBLE_CLASSIFIED")
    assert spec is not None
    assert spec.role == "observation"
    assert spec.family == "violation"
    assert spec.default_enforcement == "strict_fail"
    assert spec.owner == "compare_oracle_classification"
    assert spec.proof_categories == ("comparative",)


def test_known_classification_classes_is_closed_set_of_three() -> None:
    """The known-classes frozenset is the three exhaustive/disjoint §0 classes.

    Closed set per audit_impl_D10 §0: ``{deterministic_gap, manual_compilation_
    frontier, oracle_suspect}``. A fourth class would be a §0 contract change,
    not an inline improvisation.
    """
    assert bb.COMPARE_CLASSIFICATION_KNOWN_CLASSES == frozenset(
        {
            "deterministic_gap",
            "manual_compilation_frontier",
            "oracle_suspect",
        }
    )
