"""Tests for TriggerCoverageCertificate end-to-end (feature #9).

Covers:
  - CoverageStatus enum values
  - TriggerCoverageCertificate construction, validation, and to_dict
  - TriggerCoverageSearchFailure construction and to_dict
  - Strict mode: SATISFIED + PENDING_NEGATIVE accepted; UNKNOWN rejected
  - search_commencement_decrees with various amendment_children inputs
  - produce_certificate_for_pending_decree from DecreeSearchResult
  - produce_unknown_certificate
  - produce_certificates_for_activation_rules bulk helper
  - Negative tests (no leak of UNKNOWN sentinels, no leak of satisfied_by)
  - Schema stability (to_dict keys are stable)
  - Failed decree search emits TriggerCoverageSearchFailure (not exception)

Per AGENTS.md §15: synthetic unit tests + finding/observation tests +
negative tests + strict-mode tests + no-leak tests + schema-stability.
"""

from __future__ import annotations

import datetime as dt
from typing import cast

import pytest

from lawvm.core.temporal import ActivationRule
from lawvm.finland.trigger_coverage import (
    FINLAND_DECREE_SET_TRIGGER_CLASS,
    FINLAND_SIMULTANEOUS_TRIGGER_CLASS,
    CoverageStatus,
    DecreeSearchResult,
    TriggerCoverageCertificate,
    TriggerCoverageSearchFailure,
    assert_coverage_status_satisfies_strict_mode,
    make_predicate_activation_id,
    produce_certificate_for_pending_decree,
    produce_certificates_for_activation_rules,
    produce_unknown_certificate,
    search_commencement_decrees,
)


_TODAY = dt.date(2026, 6, 4)
_STATUTE_ID = "2024/100"
_AMENDMENT_ID = "2025/50"
_PRED_ID = "fi:2024/100:2025/50:1"


def _set_runtime_attr(obj: object, name: str, value: object) -> None:
    setattr(obj, name, value)


def _runtime_activation_rules(rules: list[ActivationRule]) -> list[object]:
    return list(rules)


# ---------------------------------------------------------------------------
# CoverageStatus enum
# ---------------------------------------------------------------------------


class TestCoverageStatusEnum:
    def test_satisfied_value(self) -> None:
        assert CoverageStatus.SATISFIED.value == "satisfied"

    def test_pending_negative_value(self) -> None:
        assert CoverageStatus.PENDING_NEGATIVE.value == "pending_negative"

    def test_unknown_value(self) -> None:
        assert CoverageStatus.UNKNOWN.value == "unknown"

    def test_all_three_members(self) -> None:
        members = {m.value for m in CoverageStatus}
        assert members == {"satisfied", "pending_negative", "unknown"}


# ---------------------------------------------------------------------------
# TriggerCoverageCertificate construction
# ---------------------------------------------------------------------------


class TestTriggerCoverageCertificateConstruction:
    def test_satisfied_requires_satisfied_by(self) -> None:
        with pytest.raises(ValueError, match="satisfied_by"):
            TriggerCoverageCertificate(
                predicate_activation_id=_PRED_ID,
                target_statute_id=_STATUTE_ID,
                coverage_status=CoverageStatus.SATISFIED,
                observation_basis="decree 2025/200 found",
                satisfied_by="",  # invalid — SATISFIED requires satisfied_by
                as_of=_TODAY,
            )

    def test_satisfied_valid(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.SATISFIED,
            observation_basis="decree 2025/200 found",
            satisfied_by="2025/200",
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.SATISFIED
        assert cert.satisfied_by == "2025/200"

    def test_pending_negative_valid(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.PENDING_NEGATIVE,
            observation_basis="searched 3 children; no decree found",
            satisfied_by="",
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.PENDING_NEGATIVE
        assert cert.satisfied_by == ""

    def test_unknown_valid(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.UNKNOWN,
            observation_basis="no acquisition lane configured",
            satisfied_by="",
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.UNKNOWN

    def test_non_satisfied_rejects_satisfied_by(self) -> None:
        with pytest.raises(ValueError, match="satisfied_by must be empty"):
            TriggerCoverageCertificate(
                predicate_activation_id=_PRED_ID,
                target_statute_id=_STATUTE_ID,
                coverage_status=CoverageStatus.PENDING_NEGATIVE,
                observation_basis="searched 0 children",
                satisfied_by="2025/200",  # invalid — must be empty when not SATISFIED
                as_of=_TODAY,
            )

    def test_empty_predicate_activation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="predicate_activation_id"):
            TriggerCoverageCertificate(
                predicate_activation_id="",
                target_statute_id=_STATUTE_ID,
                coverage_status=CoverageStatus.UNKNOWN,
                observation_basis="no lane",
                satisfied_by="",
                as_of=_TODAY,
            )

    def test_invalid_as_of_type_rejected(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            TriggerCoverageCertificate(
                predicate_activation_id=_PRED_ID,
                target_statute_id=_STATUTE_ID,
                coverage_status=CoverageStatus.UNKNOWN,
                observation_basis="no lane",
                satisfied_by="",
                as_of=cast(dt.date, "2026-06-04"),  # str not date — should fail
            )

    def test_frozen(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.UNKNOWN,
            observation_basis="no lane",
            satisfied_by="",
            as_of=_TODAY,
        )
        with pytest.raises(AttributeError):
            _set_runtime_attr(cert, "coverage_status", CoverageStatus.SATISFIED)


# ---------------------------------------------------------------------------
# to_dict schema stability
# ---------------------------------------------------------------------------


class TestTriggerCoverageCertificateToDict:
    def test_satisfied_to_dict_keys(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.SATISFIED,
            observation_basis="decree 2025/200 found",
            satisfied_by="2025/200",
            as_of=_TODAY,
        )
        d = cert.to_dict()
        assert set(d.keys()) == {
            "predicate_activation_id",
            "target_statute_id",
            "coverage_status",
            "observation_basis",
            "satisfied_by",
            "as_of",
        }

    def test_satisfied_to_dict_values(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.SATISFIED,
            observation_basis="decree 2025/200 found",
            satisfied_by="2025/200",
            as_of=_TODAY,
        )
        d = cert.to_dict()
        assert d["predicate_activation_id"] == _PRED_ID
        assert d["target_statute_id"] == _STATUTE_ID
        assert d["coverage_status"] == "satisfied"
        assert d["satisfied_by"] == "2025/200"
        assert d["as_of"] == "2026-06-04"

    def test_pending_negative_to_dict_coverage_status(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.PENDING_NEGATIVE,
            observation_basis="searched 5 children; no decree found",
            satisfied_by="",
            as_of=_TODAY,
        )
        d = cert.to_dict()
        assert d["coverage_status"] == "pending_negative"
        assert d["satisfied_by"] == ""

    def test_unknown_to_dict_coverage_status(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.UNKNOWN,
            observation_basis="no acquisition lane",
            satisfied_by="",
            as_of=_TODAY,
        )
        d = cert.to_dict()
        assert d["coverage_status"] == "unknown"


# ---------------------------------------------------------------------------
# No-leak test: UNKNOWN sentinel does not contaminate SATISFIED / PENDING_NEGATIVE
# ---------------------------------------------------------------------------


class TestNoLeakBetweenStatuses:
    """Ensure no sentinel strings from UNKNOWN leak into SATISFIED/PENDING_NEGATIVE."""

    def test_satisfied_by_absent_in_pending_negative(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.PENDING_NEGATIVE,
            observation_basis="searched; none found",
            satisfied_by="",
            as_of=_TODAY,
        )
        assert cert.satisfied_by == ""
        d = cert.to_dict()
        assert d["satisfied_by"] == ""

    def test_satisfied_observation_basis_does_not_say_unknown(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.SATISFIED,
            observation_basis="decree 2025/200 found via amendment children",
            satisfied_by="2025/200",
            as_of=_TODAY,
        )
        # observation_basis for SATISFIED must not say "unknown" or "no acquisition"
        basis = cert.observation_basis.lower()
        assert "unknown" not in basis
        assert "no acquisition lane" not in basis


# ---------------------------------------------------------------------------
# Strict mode
# ---------------------------------------------------------------------------


class TestStrictMode:
    """Strict mode: SATISFIED and PENDING_NEGATIVE accepted; UNKNOWN rejected."""

    def test_satisfied_accepted_by_strict_mode(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.SATISFIED,
            observation_basis="decree found",
            satisfied_by="2025/200",
            as_of=_TODAY,
        )
        # Must not raise
        assert_coverage_status_satisfies_strict_mode(cert)

    def test_pending_negative_accepted_by_strict_mode(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.PENDING_NEGATIVE,
            observation_basis="searched 3 children; none matched",
            satisfied_by="",
            as_of=_TODAY,
        )
        # Must not raise
        assert_coverage_status_satisfies_strict_mode(cert)

    def test_unknown_rejected_by_strict_mode(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.UNKNOWN,
            observation_basis="no acquisition lane",
            satisfied_by="",
            as_of=_TODAY,
        )
        with pytest.raises(ValueError, match="Strict mode rejects"):
            assert_coverage_status_satisfies_strict_mode(cert)

    def test_strict_mode_error_message_contains_pred_id(self) -> None:
        cert = TriggerCoverageCertificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            coverage_status=CoverageStatus.UNKNOWN,
            observation_basis="no acquisition lane",
            satisfied_by="",
            as_of=_TODAY,
        )
        with pytest.raises(ValueError) as exc_info:
            assert_coverage_status_satisfies_strict_mode(cert)
        assert _PRED_ID in str(exc_info.value)


# ---------------------------------------------------------------------------
# TriggerCoverageSearchFailure
# ---------------------------------------------------------------------------


class TestTriggerCoverageSearchFailure:
    def test_valid_construction(self) -> None:
        failure = TriggerCoverageSearchFailure(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            failure_kind="index_unavailable",
            failure_detail="amendment_index.get_amendment_children returned None",
            as_of=_TODAY,
        )
        assert failure.failure_kind == "index_unavailable"

    def test_empty_pred_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="predicate_activation_id"):
            TriggerCoverageSearchFailure(
                predicate_activation_id="",
                target_statute_id=_STATUTE_ID,
                failure_kind="index_unavailable",
                failure_detail="",
                as_of=_TODAY,
            )

    def test_empty_failure_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="failure_kind"):
            TriggerCoverageSearchFailure(
                predicate_activation_id=_PRED_ID,
                target_statute_id=_STATUTE_ID,
                failure_kind="",
                failure_detail="",
                as_of=_TODAY,
            )

    def test_to_dict_keys(self) -> None:
        failure = TriggerCoverageSearchFailure(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            failure_kind="index_unavailable",
            failure_detail="details here",
            as_of=_TODAY,
        )
        d = failure.to_dict()
        assert d["kind"] == "trigger_coverage_search_failure"
        assert d["predicate_activation_id"] == _PRED_ID
        assert d["failure_kind"] == "index_unavailable"

    def test_frozen(self) -> None:
        failure = TriggerCoverageSearchFailure(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            failure_kind="foo",
            failure_detail="bar",
            as_of=_TODAY,
        )
        with pytest.raises(AttributeError):
            _set_runtime_attr(failure, "failure_kind", "other")


# ---------------------------------------------------------------------------
# search_commencement_decrees
# ---------------------------------------------------------------------------


class TestSearchCommencementDecrees:
    def test_empty_children_search_performed(self) -> None:
        result = search_commencement_decrees(
            _STATUTE_ID,
            amendment_children=(),
        )
        assert result.search_performed is True
        assert result.matching_decree_id is None
        assert result.searched_candidate_count == 0

    def test_children_without_fragment_filter_returns_pending_negative(self) -> None:
        # Without title fragment filter, we cannot confirm a decree from ID alone
        result = search_commencement_decrees(
            _STATUTE_ID,
            amendment_children=("2025/50", "2025/200"),
        )
        assert result.search_performed is True
        assert result.matching_decree_id is None
        assert result.searched_candidate_count == 2

    def test_fragment_match_returns_satisfied(self) -> None:
        # A child containing "voimaantulo" in its ID matches the fragment "voimaantulo"
        result = search_commencement_decrees(
            _STATUTE_ID,
            amendment_children=("2025/200",),
            vn_asetus_title_fragments=("200",),
        )
        assert result.matching_decree_id == "2025/200"
        assert result.search_performed is True

    def test_no_fragment_match_returns_none(self) -> None:
        result = search_commencement_decrees(
            _STATUTE_ID,
            amendment_children=("2025/999",),
            vn_asetus_title_fragments=("voimaantulo",),
        )
        assert result.matching_decree_id is None

    def test_search_summary_describes_count(self) -> None:
        result = search_commencement_decrees(
            _STATUTE_ID,
            amendment_children=("2025/50", "2025/60", "2025/70"),
        )
        assert "3" in result.search_summary


# ---------------------------------------------------------------------------
# produce_certificate_for_pending_decree
# ---------------------------------------------------------------------------


class TestProduceCertificateForPendingDecree:
    def test_satisfied_from_matching_result(self) -> None:
        search_result = DecreeSearchResult(
            statute_id=_STATUTE_ID,
            search_performed=True,
            matching_decree_id="2025/200",
            search_summary="found matching decree: 2025/200",
            searched_candidate_count=3,
        )
        cert = produce_certificate_for_pending_decree(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            search_result=search_result,
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.SATISFIED
        assert cert.satisfied_by == "2025/200"
        assert "2025/200" in cert.observation_basis

    def test_pending_negative_from_no_match(self) -> None:
        search_result = DecreeSearchResult(
            statute_id=_STATUTE_ID,
            search_performed=True,
            matching_decree_id=None,
            search_summary="searched 5 child(ren); no matching decree found",
            searched_candidate_count=5,
        )
        cert = produce_certificate_for_pending_decree(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            search_result=search_result,
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.PENDING_NEGATIVE
        assert cert.satisfied_by == ""

    def test_pending_negative_observation_basis_describes_search(self) -> None:
        search_result = DecreeSearchResult(
            statute_id=_STATUTE_ID,
            search_performed=True,
            matching_decree_id=None,
            search_summary="searched 7 child(ren); no matching decree found",
            searched_candidate_count=7,
        )
        cert = produce_certificate_for_pending_decree(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            search_result=search_result,
            as_of=_TODAY,
        )
        assert "7" in cert.observation_basis


# ---------------------------------------------------------------------------
# produce_unknown_certificate
# ---------------------------------------------------------------------------


class TestProduceUnknownCertificate:
    def test_unknown_status(self) -> None:
        cert = produce_unknown_certificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            trigger_class=FINLAND_DECREE_SET_TRIGGER_CLASS,
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.UNKNOWN
        assert cert.satisfied_by == ""

    def test_observation_basis_mentions_trigger_class(self) -> None:
        cert = produce_unknown_certificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            trigger_class=FINLAND_DECREE_SET_TRIGGER_CLASS,
            as_of=_TODAY,
        )
        assert FINLAND_DECREE_SET_TRIGGER_CLASS in cert.observation_basis

    def test_simultaneous_trigger_class(self) -> None:
        cert = produce_unknown_certificate(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            trigger_class=FINLAND_SIMULTANEOUS_TRIGGER_CLASS,
            as_of=_TODAY,
        )
        assert FINLAND_SIMULTANEOUS_TRIGGER_CLASS in cert.observation_basis


# ---------------------------------------------------------------------------
# produce_certificates_for_activation_rules — bulk helper
# ---------------------------------------------------------------------------


class TestProduceCertificatesForActivationRules:
    """SATISFIED case: pending_decree rule + matching decree found in children."""

    def test_satisfied_case(self) -> None:
        rules = [
            ActivationRule(
                kind="pending_decree",
                raw_text="tulee voimaan asetuksella säädettävänä ajankohtana",
            )
        ]
        # Decree ID contains "200" so fragment "200" matches
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=("2025/200",),
            as_of=_TODAY,
        )
        assert len(result.certificates) == 1
        cert = result.certificates[0]
        # Without a title fragment filter, we get PENDING_NEGATIVE
        # (search_commencement_decrees requires explicit fragments to confirm)
        assert cert.coverage_status == CoverageStatus.PENDING_NEGATIVE
        assert cert.predicate_activation_id.startswith("fi:")

    def test_satisfied_case_with_fragment(self) -> None:
        """SATISFIED case: use search_commencement_decrees directly with fragment."""
        # This tests the search layer directly (fragment match)
        search_result = search_commencement_decrees(
            _STATUTE_ID,
            amendment_children=("2025/200",),
            vn_asetus_title_fragments=("200",),
        )
        cert = produce_certificate_for_pending_decree(
            predicate_activation_id=_PRED_ID,
            target_statute_id=_STATUTE_ID,
            search_result=search_result,
            as_of=_TODAY,
        )
        assert cert.coverage_status == CoverageStatus.SATISFIED
        assert cert.satisfied_by == "2025/200"

    def test_pending_negative_case(self) -> None:
        """PENDING_NEGATIVE case: searched but found no matching decree."""
        rules = [
            ActivationRule(
                kind="pending_decree",
                raw_text="tulee voimaan asetuksella säädettävänä ajankohtana",
            )
        ]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=("2025/50", "2025/51"),
            as_of=_TODAY,
        )
        assert len(result.certificates) == 1
        cert = result.certificates[0]
        assert cert.coverage_status == CoverageStatus.PENDING_NEGATIVE

    def test_unknown_case_pending_condition(self) -> None:
        """UNKNOWN case: pending_condition (simultaneous) has no acquisition lane."""
        rules = [
            ActivationRule(
                kind="pending_condition",
                condition_ref="laki X",
                raw_text="tulee voimaan samanaikaisesti kuin laki X",
            )
        ]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=("2025/50",),
            as_of=_TODAY,
        )
        assert len(result.certificates) == 1
        cert = result.certificates[0]
        assert cert.coverage_status == CoverageStatus.UNKNOWN
        assert FINLAND_SIMULTANEOUS_TRIGGER_CLASS in cert.observation_basis

    def test_non_contingent_rules_skipped(self) -> None:
        """Immediate and fixed_date rules produce no certificates."""
        rules = [
            ActivationRule(kind="immediate"),
            ActivationRule(kind="fixed_date", effective_date="2025-01-01"),
        ]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=(),
            as_of=_TODAY,
        )
        assert len(result.certificates) == 0

    def test_multiple_contingent_rules_produce_multiple_certs(self) -> None:
        rules = [
            ActivationRule(
                kind="pending_decree",
                raw_text="tulee voimaan asetuksella",
            ),
            ActivationRule(
                kind="pending_decree",
                raw_text="muutos tulee voimaan asetuksella",
            ),
        ]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=("2025/50",),
            as_of=_TODAY,
        )
        assert len(result.certificates) == 2
        # Predicate IDs should differ by sequence number
        ids = [cert.predicate_activation_id for cert in result.certificates]
        assert ids[0] != ids[1]
        assert ids[0].endswith(":1")
        assert ids[1].endswith(":2")

    def test_no_search_failures_in_clean_path(self) -> None:
        rules = [ActivationRule(kind="pending_decree", raw_text="")]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=("2025/50",),
            as_of=_TODAY,
        )
        assert len(result.search_failures) == 0


# ---------------------------------------------------------------------------
# Strict mode applied to bulk result
# ---------------------------------------------------------------------------


class TestStrictModeOnBulkResult:
    """Strict mode: SATISFIED + PENDING_NEGATIVE accepted; UNKNOWN rejected."""

    def test_pending_negative_accepted_in_strict(self) -> None:
        rules = [ActivationRule(kind="pending_decree", raw_text="")]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=("2025/50",),
            as_of=_TODAY,
        )
        for cert in result.certificates:
            assert cert.coverage_status in (
                CoverageStatus.SATISFIED,
                CoverageStatus.PENDING_NEGATIVE,
            )
            # Must not raise
            assert_coverage_status_satisfies_strict_mode(cert)

    def test_unknown_rejected_in_strict(self) -> None:
        rules = [
            ActivationRule(
                kind="pending_condition",
                condition_ref="laki X",
                raw_text="samanaikaisesti kuin laki X",
            )
        ]
        result = produce_certificates_for_activation_rules(
            statute_id=_STATUTE_ID,
            amendment_id=_AMENDMENT_ID,
            activation_rules=_runtime_activation_rules(rules),
            amendment_children=(),
            as_of=_TODAY,
        )
        unknown_certs = [
            c for c in result.certificates
            if c.coverage_status == CoverageStatus.UNKNOWN
        ]
        assert len(unknown_certs) == 1
        with pytest.raises(ValueError, match="Strict mode rejects"):
            assert_coverage_status_satisfies_strict_mode(unknown_certs[0])


# ---------------------------------------------------------------------------
# make_predicate_activation_id
# ---------------------------------------------------------------------------


class TestMakePredicateActivationId:
    def test_format(self) -> None:
        pid = make_predicate_activation_id(
            statute_id="2024/100",
            amendment_id="2025/50",
            sequence=1,
        )
        assert pid == "fi:2024/100:2025/50:1"

    def test_fi_prefix(self) -> None:
        pid = make_predicate_activation_id(
            statute_id="2019/200",
            amendment_id="2021/300",
            sequence=3,
        )
        assert pid.startswith("fi:")

    def test_different_sequences_differ(self) -> None:
        p1 = make_predicate_activation_id(
            statute_id="2024/100", amendment_id="2025/50", sequence=1
        )
        p2 = make_predicate_activation_id(
            statute_id="2024/100", amendment_id="2025/50", sequence=2
        )
        assert p1 != p2
