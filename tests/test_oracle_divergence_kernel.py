"""Tests for ``core.oracle_divergence`` — the universal compare-plane kernel (Stream G).

Synthetic coverage of the jurisdiction-neutral typing algebra:

* each :class:`DivergenceKind` is reachable and correctly assigned from membership
  sets + frontend classifier-input predicates;
* ``oracle_suspect`` is kept FIRST-CLASS — an only-replay EID is never demoted into
  a "we're wrong" kind, even when no warrant evidence is supplied;
* the only-oracle promotion algebra (manual-frontier vs deterministic, with the
  deterministic-dominates tiebreak) matches the legacy UK precedence;
* the canonical-EID identity folds Roman/Arabic so a genuine cross-kind collision
  is caught by the embedded D10 parity audit;
* the output satisfies ``assert_compare_eid_parity`` by construction (clean inputs
  -> zero parity findings; overlapping inputs -> surfaced, never hidden);
* deterministic ordering and the fail-loud non-set guard.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lawvm.core.oracle_divergence import (
    DIVERGENCE_KIND_ORDER,
    DivergenceClassifierInputs,
    DivergenceKind,
    classify_divergences,
)
from lawvm.uk_legislation.canonicalize import canonicalize_compare_eid


def _covers(*eids: str) -> Callable[[str], bool]:
    members = {e.lower() for e in eids}

    def _pred(eid: str) -> bool:
        return eid.lower() in members

    return _pred


def test_each_kind_reachable() -> None:
    inputs = DivergenceClassifierInputs(
        only_oracle_covered_by_manual_frontier=_covers("section-5"),
        only_oracle_covered_by_deterministic=_covers("section-3"),
    )
    report = classify_divergences(
        only_oracle={"section-3", "section-5", "section-7"},
        only_replay={"section-9"},
        text_diff={"section-11"},
        classifier_inputs=inputs,
    )
    buckets = report.buckets
    # section-3 covered by deterministic -> deterministic_gap.
    # section-5 covered by manual-frontier only -> manual_frontier.
    # section-7 no evidence -> deterministic_gap (default for only_oracle).
    assert buckets[DivergenceKind.DETERMINISTIC_GAP] == ("section-3", "section-7")
    assert buckets[DivergenceKind.MANUAL_FRONTIER] == ("section-5",)
    assert buckets[DivergenceKind.ORACLE_SUSPECT] == ("section-9",)
    assert buckets[DivergenceKind.TEXT_DIFF] == ("section-11",)


def test_deterministic_dominates_manual_frontier_for_same_eid() -> None:
    # An EID covered by BOTH a manual-frontier and a deterministic rejection lands
    # in deterministic_gap (a hard blocking rejection dominates) — legacy UK rule.
    inputs = DivergenceClassifierInputs(
        only_oracle_covered_by_manual_frontier=_covers("section-2"),
        only_oracle_covered_by_deterministic=_covers("section-2"),
    )
    report = classify_divergences(
        only_oracle={"section-2"},
        only_replay=set(),
        text_diff=set(),
        classifier_inputs=inputs,
    )
    assert report.buckets[DivergenceKind.DETERMINISTIC_GAP] == ("section-2",)
    assert report.buckets[DivergenceKind.MANUAL_FRONTIER] == ()


def test_oracle_suspect_is_first_class_without_warrant_evidence() -> None:
    # only_replay EIDs are oracle_suspect even with NO warrant predicate supplied:
    # replay holding an EID the oracle lacks is, by construction, the oracle being
    # the suspect surface. The kernel must never fold these into a "we're wrong"
    # kind.
    report = classify_divergences(
        only_oracle=set(),
        only_replay={"section-4", "section-8"},
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(),
    )
    assert report.buckets[DivergenceKind.ORACLE_SUSPECT] == ("section-4", "section-8")
    assert report.buckets[DivergenceKind.DETERMINISTIC_GAP] == ()
    assert report.buckets[DivergenceKind.MANUAL_FRONTIER] == ()


def test_warrant_predicate_does_not_change_kind() -> None:
    # The not-source-warranted-drop predicate is the witness rationale; whether it
    # fires or not, the kind stays oracle_suspect.
    warranted = classify_divergences(
        only_oracle=set(),
        only_replay={"section-6"},
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(
            only_replay_oracle_dropped_without_warrant=_covers("section-6"),
        ),
    )
    plain = classify_divergences(
        only_oracle=set(),
        only_replay={"section-6"},
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(),
    )
    assert warranted.buckets == plain.buckets
    assert warranted.buckets[DivergenceKind.ORACLE_SUSPECT] == ("section-6",)


def test_clean_partition_emits_no_parity_findings() -> None:
    report = classify_divergences(
        only_oracle={"section-1"},
        only_replay={"section-2"},
        text_diff={"section-3"},
        classifier_inputs=DivergenceClassifierInputs(),
        canonicalize=canonicalize_compare_eid,
    )
    assert report.parity_findings == ()


def test_canonical_identity_folds_roman_arabic_collision() -> None:
    # The SAME provision arrives as Roman section-II in only_oracle and Arabic
    # section-2 in only_replay. Under raw identity these are two EIDs in two kinds
    # (no collision); under the UK canonical identity they fold to one canonical
    # EID landing in two kinds -> the embedded D10 audit fires. This proves the
    # kernel checks exclusivity under the SAME identity the scorer matches with,
    # and surfaces the contradiction rather than hiding it.
    report = classify_divergences(
        only_oracle={"section-II"},
        only_replay={"section-2"},
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(),
        canonicalize=canonicalize_compare_eid,
        source_statute="ukpga/1900/1",
    )
    assert len(report.parity_findings) == 1
    finding = report.parity_findings[0]
    assert finding.detail["canonical_eid"] == "section-2"
    assert finding.detail["colliding_buckets"] == ("deterministic_gap", "oracle_suspect")
    assert finding.source_statute == "ukpga/1900/1"


def test_output_satisfies_compare_eid_parity_by_construction() -> None:
    # Re-running the standalone D10 audit over the kernel's wire-dict output for a
    # disjoint-input run must agree with the kernel's own parity_findings: empty.
    from lawvm.core.compare_eid_parity_audit import assert_compare_eid_parity

    report = classify_divergences(
        only_oracle={"section-1", "section-3"},
        only_replay={"section-5"},
        text_diff={"section-7"},
        classifier_inputs=DivergenceClassifierInputs(
            only_oracle_covered_by_manual_frontier=_covers("section-3"),
        ),
        canonicalize=canonicalize_compare_eid,
    )
    external = assert_compare_eid_parity(
        report.as_wire_dict(), canonicalize=canonicalize_compare_eid
    )
    assert external == report.parity_findings == ()


def test_as_wire_dict_key_order_is_canonical() -> None:
    report = classify_divergences(
        only_oracle=set(),
        only_replay=set(),
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(),
    )
    assert list(report.as_wire_dict()) == [k.value for k in DIVERGENCE_KIND_ORDER]
    assert list(report.as_wire_dict()) == [
        "deterministic_gap",
        "manual_frontier",
        "oracle_suspect",
        "text_diff",
    ]


def test_sorted_within_kind_deterministic() -> None:
    report = classify_divergences(
        only_oracle={"section-9", "section-1", "section-5"},
        only_replay=set(),
        text_diff=set(),
        classifier_inputs=DivergenceClassifierInputs(),
    )
    assert report.buckets[DivergenceKind.DETERMINISTIC_GAP] == (
        "section-1",
        "section-5",
        "section-9",
    )


def test_non_set_membership_fails_loud() -> None:
    with pytest.raises(TypeError, match="requires a set for 'only_oracle'"):
        classify_divergences(
            only_oracle=["section-1"],  # ty: ignore[invalid-argument-type]
            only_replay=set(),
            text_diff=set(),
            classifier_inputs=DivergenceClassifierInputs(),
        )
