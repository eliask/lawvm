"""Tests for typed temporal activation rules and status derivation.

Covers:
  - ActivationRule construction and validation for each kind
  - ResolutionFact construction and validation
  - derive_temporal_status: immediate, fixed_date (past/future), pending_decree
    (unresolved/resolved/superseded), pending_condition
  - project_temporal_status: multi-rule conservative projection
  - Finland-specific lowering: Commencement → ActivationRule
  - Finland-specific lowering: TemporalEvent → ActivationRule
"""

from __future__ import annotations

import datetime as dt

import pytest

from lawvm.core.ir import (
    IRNode,
    IRNodeKind,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
    StructuralAction,
)
from lawvm.core.temporal import (
    TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION,
    TRIGGER_COVERAGE_INCOMPLETE,
    UNTRIGGERED_CERTIFIED_STATUS,
    TriggerCoverageCertificate,
    derive_temporal_status,
    project_temporal_status,
)
from lawvm.core.temporal import ActivationRule, ResolutionFact, TemporalEvent, TemporalScope
from lawvm.core.timeline_temporal_events import (
    apply_standalone_temporal_event,
    matching_temporal_events_for_op,
    op_sort_date,
    scope_target_addresses_for_event,
    temporal_event_execution_date,
    temporal_overrides_for_op,
)


# ---------------------------------------------------------------------------
# ActivationRule construction
# ---------------------------------------------------------------------------


class TestActivationRuleConstruction:
    """ActivationRule construction and validation for each kind."""

    def test_immediate(self) -> None:
        rule = ActivationRule(kind="immediate")
        assert rule.kind == "immediate"
        assert rule.effective_date == ""
        assert rule.condition_ref == ""
        assert rule.raw_text == ""

    def test_fixed_date(self) -> None:
        rule = ActivationRule(kind="fixed_date", effective_date="2027-01-01")
        assert rule.kind == "fixed_date"
        assert rule.effective_date == "2027-01-01"

    def test_fixed_date_requires_effective_date(self) -> None:
        with pytest.raises(ValueError, match="requires a non-empty effective_date"):
            ActivationRule(kind="fixed_date")

    def test_fixed_date_rejects_condition_ref(self) -> None:
        with pytest.raises(ValueError, match="should not have a condition_ref"):
            ActivationRule(kind="fixed_date", effective_date="2027-01-01", condition_ref="decree/1")

    def test_immediate_rejects_condition_ref(self) -> None:
        with pytest.raises(ValueError, match="should not have a condition_ref"):
            ActivationRule(kind="immediate", condition_ref="some_ref")

    def test_pending_decree(self) -> None:
        rule = ActivationRule(
            kind="pending_decree",
            raw_text="tulee voimaan asetuksella saadettavana ajankohtana",
        )
        assert rule.kind == "pending_decree"
        assert rule.condition_ref == ""

    def test_pending_decree_with_condition_ref(self) -> None:
        rule = ActivationRule(
            kind="pending_decree",
            condition_ref="VN/2026/123",
        )
        assert rule.condition_ref == "VN/2026/123"

    def test_pending_condition(self) -> None:
        rule = ActivationRule(
            kind="pending_condition",
            condition_ref="laki X",
            raw_text="tulee voimaan samanaikaisesti kuin laki X",
        )
        assert rule.kind == "pending_condition"
        assert rule.condition_ref == "laki X"

    def test_with_raw_text(self) -> None:
        raw = "Tama laki tulee voimaan 1 paivana tammikuuta 2027."
        rule = ActivationRule(kind="fixed_date", effective_date="2027-01-01", raw_text=raw)
        assert rule.raw_text == raw

    def test_frozen(self) -> None:
        rule = ActivationRule(kind="immediate")
        with pytest.raises(AttributeError):
            rule.kind = "fixed_date"  # type: ignore[misc]  # ty:ignore[invalid-assignment]


def test_temporal_event_and_scope_live_in_temporal_module_with_compile_result_compat() -> None:
    from lawvm.core.compile_result import TemporalEvent as CompatTemporalEvent
    from lawvm.core.compile_result import TemporalScope as CompatTemporalScope

    scope = TemporalScope(target_statute="1991/1")
    event = TemporalEvent(event_id="ev1", kind="commence", scope=scope)

    assert isinstance(scope, CompatTemporalScope)
    assert isinstance(event, CompatTemporalEvent)


def test_timeline_temporal_event_helpers_match_group_and_scope() -> None:
    op = LegalOperation(
        op_id="op-1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"),)),
        group_id="grp-1",
        source=OperationSource(statute_id="2026/1"),
    )
    event = TemporalEvent(
        event_id="ev-1",
        kind="commence",
        group_id="grp-1",
        effective="2026-02-01",
        scope=TemporalScope(
            target_statute="1991/1",
            exact_addresses=(LegalAddress(path=(("section", "5"),)),),
        ),
    )
    other = TemporalEvent(
        event_id="ev-2",
        kind="commence",
        group_id="grp-2",
        effective="2026-03-01",
        scope=TemporalScope(target_statute="1991/1"),
    )

    matched = matching_temporal_events_for_op(
        op,
        (event, other),
        target_statute="1991/1",
    )

    assert matched == (event,)
    assert op_sort_date(op, (event, other), target_statute="1991/1") == "2026-02-01"
    assert temporal_event_execution_date(event) == "2026-02-01"
    overrides = temporal_overrides_for_op(op, (event, other), target_statute="1991/1")
    assert overrides.matched is True
    assert overrides.effective == "2026-02-01"


def test_apply_standalone_temporal_event_commence_reuses_prior_substantive_content() -> None:
    address = LegalAddress(path=(("section", "5"),))
    prior = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        expires="2025-01-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="5", text="content"),
    )
    timelines = {
        address: ProvisionTimeline(address=address, versions=[prior]),
    }
    event = TemporalEvent(
        event_id="ev-commence",
        kind="commence",
        effective="2026-02-01",
        scope=TemporalScope(
            target_statute="1991/1",
            exact_addresses=(address,),
        ),
        source=OperationSource(statute_id="2026/1", enacted="2026-02-01"),
    )
    issues: list[tuple[str, str, LegalAddress | None, str]] = []

    def _record_issue(issue_sink, *, kind, message, address=None, source_statute="", emit_warnings=True) -> None:
        del emit_warnings
        issue_sink.append((kind, message, address, source_statute))

    def _latest_eligible(timeline: ProvisionTimeline, as_of: str) -> ProvisionVersion | None:
        for version in reversed(timeline.versions):
            if version.effective <= as_of and (not version.expires or version.expires > as_of):
                return version
        return None

    def _latest_substantive(timeline: ProvisionTimeline, as_of: str) -> ProvisionVersion | None:
        for version in reversed(timeline.versions):
            if version.effective <= as_of and version.content is not None:
                return version
        return None

    assert scope_target_addresses_for_event(
        event,
        target_statute="1991/1",
        timelines=timelines,
    ) == (address,)

    apply_standalone_temporal_event(
        event,
        timelines,
        target_statute="1991/1",
        issue_sink=issues,
        emit_warnings=False,
        record_issue=_record_issue,
        latest_eligible_version_without_scope=_latest_eligible,
        latest_substantive_version_at_or_before=_latest_substantive,
    )

    assert issues == []
    assert len(timelines[address].versions) == 2
    commenced = timelines[address].versions[-1]
    assert commenced.effective == "2026-02-01"
    assert commenced.enacted == "2026-02-01"
    assert commenced.content is prior.content


def test_finland_temporal_lowering_exports_only_live_helpers() -> None:
    from lawvm.finland import temporal_lowering

    assert "build_temporary_activation" not in temporal_lowering.__all__
    assert "activation_rule_from_commencement" in temporal_lowering.__all__
    assert "lower_temporal_events_to_activation_rules" in temporal_lowering.__all__


def test_finland_ops_temporary_signal_is_coarse_and_live() -> None:
    from lawvm.finland import ops as finland_ops
    from lawvm.finland.ops import AmendmentOp, temporary_signal_for_op

    op = AmendmentOp(
        op_id="tmp-op",
        op_type="REPLACE",
        target_unit_kind="section",
        target_section="5",
        is_temporary=True,
    )

    assert temporary_signal_for_op(op) is True
    assert "TemporalActivation" not in vars(finland_ops)
    assert "TemporalRuleKind" not in vars(finland_ops)


# ---------------------------------------------------------------------------
# ResolutionFact construction
# ---------------------------------------------------------------------------


class TestTriggerCoverageCertificateConstruction:
    """TriggerCoverageCertificate validates trigger source coverage evidence."""

    def test_complete_no_resolution_requires_as_of_and_checked_sources(self) -> None:
        cert = TriggerCoverageCertificate(
            certificate_id="coverage-1",
            status=TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION,
            as_of="2026-04-07",
            activation_rule_ref="event:1",
            checked_sources=("decree-register",),
            source_scope=("commencement-instruments",),
        )

        assert cert.certifies_untriggered is True
        assert cert.to_dict()["status"] == "complete_no_resolution"

    def test_complete_coverage_requires_checked_sources(self) -> None:
        with pytest.raises(ValueError, match="checked_sources"):
            TriggerCoverageCertificate(
                certificate_id="coverage-1",
                status=TRIGGER_COVERAGE_COMPLETE_NO_RESOLUTION,
                as_of="2026-04-07",
            )

    def test_incomplete_coverage_requires_missing_sources(self) -> None:
        with pytest.raises(ValueError, match="missing_sources"):
            TriggerCoverageCertificate(
                certificate_id="coverage-1",
                status=TRIGGER_COVERAGE_INCOMPLETE,
            )


class TestResolutionFactConstruction:
    """ResolutionFact construction and validation."""

    def test_resolved(self) -> None:
        fact = ResolutionFact(
            status="resolved",
            resolved_effective="2027-06-01",
            authority_source="VN/2027/50",
        )
        assert fact.status == "resolved"
        assert fact.resolved_effective == "2027-06-01"
        assert fact.authority_source == "VN/2027/50"

    def test_resolved_requires_effective(self) -> None:
        with pytest.raises(ValueError, match="requires a non-empty resolved_effective"):
            ResolutionFact(status="resolved")

    def test_unresolved(self) -> None:
        fact = ResolutionFact(status="unresolved")
        assert fact.status == "unresolved"
        assert fact.resolved_effective == ""

    def test_untriggered_certified(self) -> None:
        fact = ResolutionFact(
            status=UNTRIGGERED_CERTIFIED_STATUS,
            coverage_certificate_id="coverage-1",
        )
        assert fact.status == "untriggered_certified"
        assert fact.coverage_certificate_id == "coverage-1"

    def test_untriggered_certified_requires_evidence_pointer(self) -> None:
        with pytest.raises(ValueError, match="coverage_certificate_id"):
            ResolutionFact(status=UNTRIGGERED_CERTIFIED_STATUS)

    def test_superseded(self) -> None:
        fact = ResolutionFact(
            status="superseded",
            authority_source="2027/200",
        )
        assert fact.status == "superseded"
        assert fact.authority_source == "2027/200"

    def test_frozen(self) -> None:
        fact = ResolutionFact(status="unresolved")
        with pytest.raises(AttributeError):
            fact.status = "resolved"  # type: ignore[misc]  # ty:ignore[invalid-assignment]

    def test_status_predicates_reflect_current_status(self) -> None:
        resolved = ResolutionFact(status="resolved", resolved_effective="2027-06-01")
        unresolved = ResolutionFact(status="unresolved")
        untriggered = ResolutionFact(
            status=UNTRIGGERED_CERTIFIED_STATUS,
            coverage_certificate_id="coverage-1",
        )
        superseded = ResolutionFact(status="superseded")

        assert resolved.is_resolved is True
        assert resolved.is_unresolved is False
        assert resolved.is_untriggered_certified is False
        assert resolved.is_superseded is False
        assert unresolved.is_resolved is False
        assert unresolved.is_unresolved is True
        assert unresolved.is_untriggered_certified is False
        assert unresolved.is_superseded is False
        assert untriggered.is_resolved is False
        assert untriggered.is_unresolved is False
        assert untriggered.is_untriggered_certified is True
        assert untriggered.is_superseded is False
        assert superseded.is_resolved is False
        assert superseded.is_unresolved is False
        assert superseded.is_untriggered_certified is False
        assert superseded.is_superseded is True


# ---------------------------------------------------------------------------
# derive_temporal_status
# ---------------------------------------------------------------------------


class TestDeriveTemporalStatus:
    """derive_temporal_status for each ActivationRule kind and resolution state."""

    def test_immediate_is_always_active(self) -> None:
        rule = ActivationRule(kind="immediate")
        assert derive_temporal_status(rule, None, "2020-01-01") == "active"
        assert derive_temporal_status(rule, None, "2099-12-31") == "active"

    def test_fixed_date_in_past_is_active(self) -> None:
        rule = ActivationRule(kind="fixed_date", effective_date="2025-01-01")
        assert derive_temporal_status(rule, None, "2026-04-07") == "active"

    def test_fixed_date_on_date_is_active(self) -> None:
        """Effective on the exact date should be active."""
        rule = ActivationRule(kind="fixed_date", effective_date="2025-06-15")
        assert derive_temporal_status(rule, None, "2025-06-15") == "active"

    def test_fixed_date_in_future_is_scheduled(self) -> None:
        rule = ActivationRule(kind="fixed_date", effective_date="2027-01-01")
        assert derive_temporal_status(rule, None, "2026-04-07") == "scheduled"

    def test_pending_decree_no_resolution_is_pending(self) -> None:
        rule = ActivationRule(kind="pending_decree")
        assert derive_temporal_status(rule, None, "2026-04-07") == "pending_external_resolution"

    def test_pending_decree_unresolved_is_pending(self) -> None:
        rule = ActivationRule(kind="pending_decree")
        res = ResolutionFact(status="unresolved")
        assert derive_temporal_status(rule, res, "2026-04-07") == "pending_external_resolution"

    def test_pending_decree_resolved_past_is_active(self) -> None:
        rule = ActivationRule(kind="pending_decree")
        res = ResolutionFact(
            status="resolved",
            resolved_effective="2026-01-01",
            authority_source="VN/2026/10",
        )
        assert derive_temporal_status(rule, res, "2026-04-07") == "active"

    def test_pending_decree_resolved_future_is_scheduled(self) -> None:
        rule = ActivationRule(kind="pending_decree")
        res = ResolutionFact(
            status="resolved",
            resolved_effective="2027-06-01",
            authority_source="VN/2027/50",
        )
        assert derive_temporal_status(rule, res, "2026-04-07") == "scheduled"

    def test_pending_decree_superseded_is_inactive(self) -> None:
        rule = ActivationRule(kind="pending_decree")
        res = ResolutionFact(
            status="superseded",
            authority_source="2027/200",
        )
        assert derive_temporal_status(rule, res, "2026-04-07") == "inactive"

    def test_pending_decree_certified_untriggered_is_inactive(self) -> None:
        rule = ActivationRule(kind="pending_decree")
        res = ResolutionFact(
            status=UNTRIGGERED_CERTIFIED_STATUS,
            coverage_certificate_id="coverage-1",
        )
        assert derive_temporal_status(rule, res, "2026-04-07") == "inactive"

    def test_pending_condition_no_resolution(self) -> None:
        rule = ActivationRule(kind="pending_condition", condition_ref="laki X")
        assert derive_temporal_status(rule, None, "2026-04-07") == "pending_external_resolution"

    def test_pending_condition_resolved(self) -> None:
        rule = ActivationRule(kind="pending_condition", condition_ref="laki X")
        res = ResolutionFact(
            status="resolved",
            resolved_effective="2025-06-01",
        )
        assert derive_temporal_status(rule, res, "2026-04-07") == "active"


# ---------------------------------------------------------------------------
# project_temporal_status (multi-rule)
# ---------------------------------------------------------------------------


class TestProjectTemporalStatus:
    """project_temporal_status conservative projection over multiple rules."""

    def test_empty_rules_is_inactive(self) -> None:
        assert project_temporal_status([], [], "2026-04-07") == "inactive"

    def test_single_active(self) -> None:
        rules = [ActivationRule(kind="immediate")]
        assert project_temporal_status(rules, [], "2026-04-07") == "active"

    def test_single_scheduled(self) -> None:
        rules = [ActivationRule(kind="fixed_date", effective_date="2027-01-01")]
        assert project_temporal_status(rules, [], "2026-04-07") == "scheduled"

    def test_pending_dominates_active(self) -> None:
        """Uncertainty (pending) dominates active."""
        rules = [
            ActivationRule(kind="immediate"),
            ActivationRule(kind="pending_decree"),
        ]
        assert project_temporal_status(rules, [], "2026-04-07") == "pending_external_resolution"

    def test_active_dominates_scheduled(self) -> None:
        rules = [
            ActivationRule(kind="immediate"),
            ActivationRule(kind="fixed_date", effective_date="2027-01-01"),
        ]
        assert project_temporal_status(rules, [], "2026-04-07") == "active"

    def test_mixed_with_resolution(self) -> None:
        """Pending decree resolved + fixed date scheduled → active dominates."""
        rules = [
            ActivationRule(kind="pending_decree"),
            ActivationRule(kind="fixed_date", effective_date="2028-01-01"),
        ]
        resolutions = [
            ResolutionFact(status="resolved", resolved_effective="2025-01-01"),
        ]
        assert project_temporal_status(rules, resolutions, "2026-04-07") == "active"

    def test_all_inactive(self) -> None:
        rules = [ActivationRule(kind="pending_decree")]
        resolutions = [ResolutionFact(status="superseded", authority_source="2027/1")]
        assert project_temporal_status(rules, resolutions, "2026-04-07") == "inactive"

    def test_certified_untriggered_projects_inactive(self) -> None:
        rules = [ActivationRule(kind="pending_decree")]
        resolutions = [
            ResolutionFact(
                status=UNTRIGGERED_CERTIFIED_STATUS,
                coverage_certificate_id="coverage-1",
            )
        ]
        assert project_temporal_status(rules, resolutions, "2026-04-07") == "inactive"

    def test_resolution_list_shorter_than_rules(self) -> None:
        """Missing resolutions default to None (pending)."""
        rules = [
            ActivationRule(kind="pending_decree"),
            ActivationRule(kind="pending_decree"),
        ]
        resolutions = [
            ResolutionFact(status="resolved", resolved_effective="2025-01-01"),
        ]
        # First rule resolved+active, second rule has no resolution → pending dominates
        assert project_temporal_status(rules, resolutions, "2026-04-07") == "pending_external_resolution"


# ---------------------------------------------------------------------------
# Finland lowering: Commencement → ActivationRule
# ---------------------------------------------------------------------------


class TestFinlandCommencementLowering:
    """Finland-specific lowering from Commencement to ActivationRule."""

    def test_fixed_date_commencement(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_commencement
        from lawvm.core.effect_intent import Commencement

        intent = Commencement(
            effective_date=dt.date(2027, 1, 1),
            raw_text="Tama laki tulee voimaan 1 paivana tammikuuta 2027.",
        )
        rule = activation_rule_from_commencement(intent)
        assert rule.kind == "fixed_date"
        assert rule.effective_date == "2027-01-01"
        assert "tammikuuta" in rule.raw_text

    def test_immediate_commencement(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_commencement
        from lawvm.core.effect_intent import Commencement

        intent = Commencement(
            effective_date=None,
            is_contingent=False,
            raw_text="",
        )
        rule = activation_rule_from_commencement(intent)
        assert rule.kind == "immediate"

    def test_contingent_decree_set(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_commencement
        from lawvm.core.effect_intent import Commencement

        intent = Commencement(
            is_contingent=True,
            raw_text="Tama laki tulee voimaan valtioneuvoston asetuksella saadettavana ajankohtana.",
        )
        rule = activation_rule_from_commencement(intent)
        assert rule.kind == "pending_decree"
        assert rule.raw_text != ""

    def test_contingent_simultaneous_entry(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_commencement
        from lawvm.core.effect_intent import Commencement

        intent = Commencement(
            is_contingent=True,
            raw_text="Tama laki tulee voimaan samanaikaisesti kuin rikoslain muutos.",
        )
        rule = activation_rule_from_commencement(intent)
        assert rule.kind == "pending_condition"
        assert "rikoslain muutos" in rule.condition_ref

    def test_contingent_no_pattern_defaults_to_decree(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_commencement
        from lawvm.core.effect_intent import Commencement

        intent = Commencement(
            is_contingent=True,
            raw_text="Tama laki tulee voimaan erikseen.",
        )
        rule = activation_rule_from_commencement(intent)
        assert rule.kind == "pending_decree"


# ---------------------------------------------------------------------------
# Finland lowering: TemporalEvent → ActivationRule
# ---------------------------------------------------------------------------


class TestFinlandTemporalEventLowering:
    """Finland-specific lowering from TemporalEvent to ActivationRule."""

    def test_commence_with_date(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e1",
            kind="commence",
            scope=TemporalScope(),
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2026-01-01"),
        )
        rule = activation_rule_from_temporal_event(event)
        assert rule is not None
        assert rule.kind == "fixed_date"
        assert rule.effective_date == "2026-01-01"

    def test_commence_with_explicit_effective_payload(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope
        from lawvm.core.ir import OperationSource

        event = TemporalEvent(
            event_id="e1-explicit",
            kind="commence",
            scope=TemporalScope(),
            effective="2027-01-01",
            source=OperationSource(statute_id="2027/1", effective="2030-01-01"),
        )
        rule = activation_rule_from_temporal_event(event)
        assert rule is not None
        assert rule.kind == "fixed_date"
        assert rule.effective_date == "2027-01-01"

    def test_commence_contingent(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e2",
            kind="commence",
            scope=TemporalScope(),
            activation_rule=ActivationRule(kind="pending_decree"),
        )
        rule = activation_rule_from_temporal_event(event)
        assert rule is not None
        assert rule.kind == "pending_decree"

    def test_commence_contingent_from_activation_rule(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e2-legacy",
            kind="commence",
            scope=TemporalScope(),
            activation_rule=ActivationRule(kind="pending_decree"),
        )
        rule = activation_rule_from_temporal_event(event)
        assert rule is not None
        assert rule.kind == "pending_decree"

    def test_commence_immediate(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e3",
            kind="commence",
            scope=TemporalScope(),
        )
        rule = activation_rule_from_temporal_event(event)
        assert rule is not None
        assert rule.kind == "immediate"

    def test_commence_immediate_does_not_need_provenance_effective_date(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope
        from lawvm.core.ir import OperationSource

        event = TemporalEvent(
            event_id="e3-provenance",
            kind="commence",
            scope=TemporalScope(),
            activation_rule=ActivationRule(kind="immediate"),
            source=OperationSource(statute_id="2026/1", enacted="2026-03-01"),
        )
        rule = activation_rule_from_temporal_event(event)
        assert rule is not None
        assert rule.kind == "immediate"

    def test_non_commencement_returns_none(self) -> None:
        from lawvm.finland.temporal_lowering import activation_rule_from_temporal_event
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e4",
            kind="expire",
            scope=TemporalScope(),
        )
        assert activation_rule_from_temporal_event(event) is None

    def test_temporal_event_can_carry_activation_rule_directly(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        rule = ActivationRule(kind="fixed_date", effective_date="2027-01-01")
        event = TemporalEvent(
            event_id="e5",
            kind="commence",
            scope=TemporalScope(),
            activation_rule=rule,
        )
        assert event.activation_rule is rule
        assert event.activation_rule.kind == "fixed_date"

    def test_temporal_overrides_prefer_embedded_activation_rule(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope
        from lawvm.core.ir import LegalAddress, LegalOperation
        from lawvm.core.semantic_types import StructuralAction
        from lawvm.core.timeline import _temporal_overrides_for_op

        op = LegalOperation(
            op_id="op-1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            group_id="g-1",
        )
        event = TemporalEvent(
            event_id="e6",
            kind="commence",
            scope=TemporalScope(),
            group_id="g-1",
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2027-01-01"),
        )
        overrides = _temporal_overrides_for_op(op, (event,), target_statute="")
        assert overrides.matched is True
        assert overrides.effective == "2027-01-01"

    def test_temporal_overrides_prefer_explicit_expiry_payload_over_provenance(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope
        from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
        from lawvm.core.semantic_types import StructuralAction
        from lawvm.core.timeline import _temporal_overrides_for_op

        op = LegalOperation(
            op_id="op-2",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            group_id="g-2",
            source=OperationSource(statute_id="2020/1", expires="2030-01-01"),
        )
        event = TemporalEvent(
            event_id="e7",
            kind="expire",
            scope=TemporalScope(),
            group_id="g-2",
            expires="2027-12-31",
            source=OperationSource(statute_id="2020/1", expires="2030-01-01"),
        )
        overrides = _temporal_overrides_for_op(op, (event,), target_statute="")
        assert overrides.matched is True
        assert overrides.expires == "2027-12-31"

    def test_temporal_overrides_use_explicit_effective_payload_for_revival(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope
        from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
        from lawvm.core.semantic_types import StructuralAction
        from lawvm.core.timeline import _temporal_overrides_for_op

        op = LegalOperation(
            op_id="op-3",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            group_id="g-3",
            source=OperationSource(statute_id="2020/1", effective="2030-01-01"),
        )
        event = TemporalEvent(
            event_id="e8",
            kind="revive",
            scope=TemporalScope(),
            group_id="g-3",
            effective="2027-01-01",
            source=OperationSource(statute_id="2020/1", effective="2030-01-01"),
        )
        overrides = _temporal_overrides_for_op(op, (event,), target_statute="")
        assert overrides.matched is True
        assert overrides.effective == "2027-01-01"

    def test_finland_batch_scoped_expiry_only_matches_targeted_address(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope
        from lawvm.core.ir import LegalAddress, LegalOperation
        from lawvm.core.semantic_types import StructuralAction
        from lawvm.core.timeline import _temporal_overrides_for_op

        scoped_addr = LegalAddress(path=(("chapter", "5"), ("section", "21b")))
        other_addr = LegalAddress(path=(("chapter", "5"), ("section", "21a")))
        event = TemporalEvent(
            event_id="fi-expire",
            kind="expire",
            scope=TemporalScope(target_statute="1999/488", exact_addresses=(scoped_addr,)),
            group_id="finland-johto:2021/984",
            expires="2022-01-31",
        )

        targeted = LegalOperation(
            op_id="op-targeted",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=scoped_addr,
            group_id="finland-johto:2021/984",
        )
        untargeted = LegalOperation(
            op_id="op-untargeted",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=other_addr,
            group_id="finland-johto:2021/984",
        )

        targeted_overrides = _temporal_overrides_for_op(targeted, (event,), target_statute="1999/488")
        untargeted_overrides = _temporal_overrides_for_op(untargeted, (event,), target_statute="1999/488")

        assert targeted_overrides.matched is True
        assert targeted_overrides.expires == "2022-01-31"
        assert untargeted_overrides.matched is False
        assert untargeted_overrides.expires == ""


class TestTemporalEventAccessors:
    def test_activation_rule_accessors_reflect_embedded_rule(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e-summary",
            kind="commence",
            scope=TemporalScope(),
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2025-01-01"),
        )
        assert event.has_activation_rule is True
        assert event.activation_rule_kind == "fixed_date"

    def test_activation_rule_accessors_handle_absence(self) -> None:
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        event = TemporalEvent(
            event_id="e-summary-none",
            kind="expire",
            scope=TemporalScope(),
        )
        assert event.has_activation_rule is False
        assert event.activation_rule_kind == ""


# ---------------------------------------------------------------------------
# Bulk lowering helpers
# ---------------------------------------------------------------------------


class TestBulkLowering:
    """Bulk lowering helpers for lists of intents/events."""

    def test_lower_commencement_intents_filters(self) -> None:
        from lawvm.finland.temporal_lowering import lower_commencement_intents
        from lawvm.core.effect_intent import Applicability, Commencement, Expiry

        intents = [
            Commencement(effective_date=dt.date(2025, 1, 1), raw_text="a"),
            Expiry(expiry_date=dt.date(2026, 12, 31), raw_text="b"),
            Applicability(raw_text="c"),
            Commencement(is_contingent=True, raw_text="d"),
        ]
        rules = lower_commencement_intents(intents)
        assert len(rules) == 2
        assert rules[0].kind == "fixed_date"
        assert rules[1].kind == "pending_decree"

    def test_lower_commencement_intents_with_findings_records_skipped_inputs(self) -> None:
        from lawvm.finland.temporal_lowering import lower_commencement_intents_with_findings
        from lawvm.core.effect_intent import Applicability, Commencement, Expiry

        intents = [
            Commencement(effective_date=dt.date(2025, 1, 1), raw_text="a"),
            Expiry(expiry_date=dt.date(2026, 12, 31), raw_text="b"),
            Applicability(raw_text="c"),
        ]
        result = lower_commencement_intents_with_findings(intents)
        assert len(result.activation_rules) == 1
        assert result.activation_rules[0].kind == "fixed_date"
        skipped = [f for f in result.findings if f.kind == "TIME.ACTIVATION_RULE_INPUT_SKIPPED"]
        assert [f.detail.get("input_kind") for f in skipped] == ["Expiry", "Applicability"]

    def test_lower_temporal_events_to_activation_rules(self) -> None:
        from lawvm.finland.temporal_lowering import lower_temporal_events_to_activation_rules
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        events = (
            TemporalEvent(
                event_id="e1",
                kind="commence",
                scope=TemporalScope(),
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2025-01-01"),
            ),
            TemporalEvent(event_id="e2", kind="expire", scope=TemporalScope()),
            TemporalEvent(
                event_id="e3",
                kind="commence",
                scope=TemporalScope(),
                activation_rule=ActivationRule(kind="pending_decree"),
            ),
        )
        rules = lower_temporal_events_to_activation_rules(events)
        assert len(rules) == 2
        assert rules[0].kind == "fixed_date"
        assert rules[1].kind == "pending_decree"

    def test_lower_temporal_events_to_activation_rules_with_findings_records_skipped_inputs(self) -> None:
        from lawvm.finland.temporal_lowering import lower_temporal_events_to_activation_rules_with_findings
        from lawvm.core.compile_result import TemporalEvent, TemporalScope

        events = (
            TemporalEvent(
                event_id="e1",
                kind="commence",
                scope=TemporalScope(),
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2025-01-01"),
            ),
            TemporalEvent(event_id="e2", kind="expire", scope=TemporalScope()),
        )
        result = lower_temporal_events_to_activation_rules_with_findings(events)
        assert len(result.activation_rules) == 1
        assert result.activation_rules[0].kind == "fixed_date"
        skipped = [f for f in result.findings if f.kind == "TIME.ACTIVATION_RULE_INPUT_SKIPPED"]
        assert len(skipped) == 1
        assert skipped[0].detail.get("input_kind") == "expire"
