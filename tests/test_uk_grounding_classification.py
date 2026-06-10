"""Tests for the UK oracle-grounding classification contract.

The contract (totality over the negative space): every replay node the
oracle-alignment pass leaves unmatched (alignment event with ``after_eid`` ==
None) carries exactly one of the four grounding classifications. Suppression
defaults to ``unresolved`` (never the source-faithful claim). ``uk_oracle_check``
fails loud on any unmatched node that carries no usable classification mechanism.

Unit tests (no archive required):
  - the four classification values and the default
  - the mechanism → classification rule table is total over emitted mechanisms
  - suppression default is unresolved, never source-faithful
  - matched events carry no classification
  - unclassified_suppression_events flags missing/unknown mechanisms

Report tests (no archive required):
  - every after_eid=None change carries a classification in the contract set
  - report counts are total over unmatched changes; unclassified_count == 0
  - transparent wrappers classify as non_commensurable; suppressed as unresolved

Property test (requires data/uk_legislation.farchive):
  - over a small real UK statute, every suppression event is classified and the
    bucket counts sum to the suppression total with zero unclassified
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.grounding_classification import (
    GROUNDING_CLASSIFICATIONS,
    GROUNDING_DEFAULT_CLASSIFICATION,
    GROUNDING_NON_COMMENSURABLE,
    GROUNDING_SOURCE_FAITHFUL_ORACLE_ABSENT,
    GROUNDING_SUPPRESSION_MECHANISMS,
    GROUNDING_UNRESOLVED,
    classify_suppression_mechanism,
    grounding_classification_for_event,
    is_suppression_event,
    unclassified_suppression_events,
)
from lawvm.uk_legislation.oracle_align import align_uk_replay_to_oracle_with_report

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"


# ---------------------------------------------------------------------------
# Unit: the four classification values
# ---------------------------------------------------------------------------


def test_exactly_four_classifications() -> None:
    assert GROUNDING_CLASSIFICATIONS == {
        "source_faithful_oracle_absent",
        "parser_structure_desync",
        "non_commensurable",
        "unresolved",
    }


def test_default_classification_is_unresolved_not_source_faithful() -> None:
    # Conservative default: suppression proves we did not assign an EID, never
    # that the oracle lacks the node.
    assert GROUNDING_DEFAULT_CLASSIFICATION == GROUNDING_UNRESOLVED
    assert GROUNDING_DEFAULT_CLASSIFICATION != GROUNDING_SOURCE_FAITHFUL_ORACLE_ABSENT


# ---------------------------------------------------------------------------
# Unit: mechanism → classification totality
# ---------------------------------------------------------------------------


def test_every_emitted_mechanism_has_an_explicit_rule() -> None:
    # Every mechanism the grounding pass emits on a suppression event must map
    # to one of the four classifications via the explicit rule table — never
    # silently fall through to the default.
    for mechanism in GROUNDING_SUPPRESSION_MECHANISMS:
        classification = classify_suppression_mechanism(mechanism)
        assert classification in GROUNDING_CLASSIFICATIONS


def test_transparent_wrapper_is_non_commensurable() -> None:
    assert (
        classify_suppression_mechanism("transparent_wrapper_cleared")
        == GROUNDING_NON_COMMENSURABLE
    )


def test_local_fallback_mechanisms_are_unresolved() -> None:
    assert (
        classify_suppression_mechanism("local_fallback_suppressed")
        == GROUNDING_UNRESOLVED
    )
    assert (
        classify_suppression_mechanism("local_fallback_unlabeled_blocked")
        == GROUNDING_UNRESOLVED
    )


def test_no_mechanism_classifies_as_source_faithful() -> None:
    # The grounding pass never mints the strong source-faithful claim. That is
    # reserved for callers supplying an explicit oracle-absence proof.
    for mechanism in GROUNDING_SUPPRESSION_MECHANISMS:
        assert (
            classify_suppression_mechanism(mechanism)
            != GROUNDING_SOURCE_FAITHFUL_ORACLE_ABSENT
        )


def test_unknown_mechanism_falls_back_to_unresolved_not_source_faithful() -> None:
    assert classify_suppression_mechanism("some_new_unregistered_method") == (
        GROUNDING_UNRESOLVED
    )
    assert classify_suppression_mechanism(None) == GROUNDING_UNRESOLVED
    assert classify_suppression_mechanism("") == GROUNDING_UNRESOLVED


# ---------------------------------------------------------------------------
# Unit: event-level classification
# ---------------------------------------------------------------------------


def test_matched_event_carries_no_classification() -> None:
    event = {"after_eid": "section-1", "match_method": "flat"}
    assert is_suppression_event(event) is False
    assert grounding_classification_for_event(event) is None


def test_suppression_event_is_classified() -> None:
    event = {"after_eid": None, "match_method": "local_fallback_suppressed"}
    assert is_suppression_event(event) is True
    assert grounding_classification_for_event(event) == GROUNDING_UNRESOLVED


def test_unclassified_suppression_events_flags_missing_mechanism() -> None:
    events = [
        {"after_eid": "section-1", "match_method": "flat"},  # matched: fine
        {"after_eid": None, "match_method": "local_fallback_suppressed"},  # ok
        {"after_eid": None, "match_method": None},  # violation: no mechanism
        {"after_eid": None, "match_method": "totally_unknown_mech"},  # violation
    ]
    offenders = unclassified_suppression_events(events)
    assert len(offenders) == 2
    assert all(o["after_eid"] is None for o in offenders)


def test_unclassified_suppression_events_empty_when_all_known() -> None:
    events = [
        {"after_eid": None, "match_method": m} for m in GROUNDING_SUPPRESSION_MECHANISMS
    ]
    assert unclassified_suppression_events(events) == []


# ---------------------------------------------------------------------------
# Report: totality over an aligned statute (no archive required)
# ---------------------------------------------------------------------------


def _demo_statute() -> IRStatute:
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="A section.",
                    attrs={"eId": "local-section-one"},
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="A subsection oracle lacks.",
                            attrs={"eId": "local-subsection-one"},
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.P1GROUP,
                    text="Wrapper",
                    attrs={"eId": "local-wrapper"},
                ),
            ),
        ),
    )


def test_report_classifies_every_unmatched_change() -> None:
    result = align_uk_replay_to_oracle_with_report(
        _demo_statute(),
        eid_map={"body:section-1": "section-1"},
        text_map={},
    )
    report = result.report

    # Totality: every unmatched (after_eid=None) change carries exactly one of
    # the four classifications; no matched change carries one.
    for change in report.changes:
        if change.after_eid is None:
            assert change.grounding_classification in GROUNDING_CLASSIFICATIONS
        else:
            assert change.grounding_classification is None

    # The bucket counts are total over unmatched changes, with zero unclassified.
    unmatched = [c for c in report.changes if c.after_eid is None]
    assert sum(report.grounding_classification_counts.values()) == len(unmatched)
    assert report.unclassified_count == 0
    assert set(report.grounding_classification_counts) == GROUNDING_CLASSIFICATIONS


def test_report_transparent_wrapper_non_commensurable_subsection_unresolved() -> None:
    result = align_uk_replay_to_oracle_with_report(
        _demo_statute(),
        eid_map={"body:section-1": "section-1"},
        text_map={},
    )
    counts = result.report.grounding_classification_counts
    # The p1group wrapper is non_commensurable; the unmatched subsection is the
    # conservative unresolved default — never source-faithful.
    assert counts["non_commensurable"] == 1
    assert counts["unresolved"] >= 1
    assert counts["source_faithful_oracle_absent"] == 0
    assert result.report.to_jsonable_dict()["unclassified_count"] == 0


# ---------------------------------------------------------------------------
# Property: totality over a small real UK statute
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_grounding_totality_over_real_statute() -> None:
    """Every suppression event over a real statute is classified; no node is
    left unclassified and none is optimistically called source-faithful."""
    from lawvm.tools.uk_oracle_check import oracle_check_uk_statute

    blocking: list[str] = []
    out = oracle_check_uk_statute(
        "ukpga/1978/30", db_path=_DB_PATH, blocking_findings_out=blocking
    )

    # The check ran, surfaced the totality section, and found no contract
    # violation (so no blocking finding fires).
    assert "GROUNDING CLASSIFICATION TOTALITY" in out
    assert "UNCLASSIFIED (contract violation): 0" in out
    assert blocking == []

    # Re-derive the totality directly from the alignment events to prove the
    # printed counts are not the only enforcement: every suppression event maps
    # to a classification in the contract set.
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )

    statute_id = "ukpga/1978/30"
    repo_root = _DB_PATH.resolve().parents[1]
    with Farchive(_DB_PATH) as archive:
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_ir = parse_uk_statute_ir_bytes(
            archive.get(enacted_url),
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )
        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_data = extract_eid_map_bytes(archive.get(oracle_url), pit_date=None)
        eid_map = oracle_data.get("eid_map", {})
        text_map = oracle_data.get("text_map", {})

        pipeline = uk_replay_module.UKReplayPipeline(repo_root)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
        )
        alignment_events: list[dict] = []
        pipeline.apply_ops(
            base_ir,
            ops,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=True,
            oracle_alignment_events_out=alignment_events,
        )

    suppression = [e for e in alignment_events if is_suppression_event(e)]
    assert suppression, "expected the real statute to produce suppression events"
    # Totality: every suppression event is classified into the contract set.
    for event in suppression:
        classification = grounding_classification_for_event(event)
        assert classification in GROUNDING_CLASSIFICATIONS
        # The grounding pass also stamps the classification directly on the
        # event dict; it must agree with the rule-derived value.
        assert event["grounding_classification"] == classification
    # And none is left unclassified.
    assert unclassified_suppression_events(alignment_events) == []


# ---------------------------------------------------------------------------
# Guard-liveness: the fail-loud path actually fires on an unclassified node
# ---------------------------------------------------------------------------


def test_main_exits_nonzero_on_unclassified_suppression_event(monkeypatch) -> None:
    """Fire-drill: if any unmatched node escapes classification, the check must
    fail loud (non-zero exit), not pass silently."""
    import lawvm.tools.uk_oracle_check as mod

    def _fake_check(statute_id, *, db_path=None, max_sample=5, blocking_findings_out=None):
        if blocking_findings_out is not None:
            blocking_findings_out.append(
                f"UK_GROUNDING_CLASSIFICATION_INCOMPLETE: 1 suppression event(s) for "
                f"{statute_id}"
            )
        return "=== fake oracle-check ===\n"

    monkeypatch.setattr(mod, "oracle_check_uk_statute", _fake_check)

    class _Args:
        statute_id = "ukpga/1978/30"
        db = None

    with pytest.raises(SystemExit) as exc:
        mod.main(_Args())
    assert exc.value.code == 1
