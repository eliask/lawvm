"""Tests for the SCHED-01/02/03 disjoint-window scheduling totality sweep.

The sweep (``schedule_window_totality.sweep_disjoint_window_materialization``) is
a READ-ONLY audit over the finished replay output: for every temporary legal-
effect window reconstructed from ``ReplayProducts.temporal_events`` (the legal-
effect event plane, distinct from fold order — SCHED-01), it checks whether the
materialized ``timelines`` carry a matching ``[effective, expires)`` version
interval (SCHED-02); a window neither materialized nor carried as a typed
residual is ``SCHED.WINDOW_UNMATERIALIZED`` (SCHED-03). It mutates nothing.
"""

from __future__ import annotations

from lawvm.core.ir import LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.observation_registry import FINDING_REGISTRY
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.ir import IRNode
from lawvm.core.temporal import (
    FIXED_DATE_KIND,
    ActivationRule,
    TemporalEvent,
    TemporalScope,
)
from lawvm.core.timeline_results import TimelineIssue
from lawvm.finland.legal_surface.schedule_window_totality import (
    SCHED_WINDOW_UNMATERIALIZED,
    DisjointWindowFinding,
    sweep_disjoint_window_materialization,
)
from lawvm.finland.replay_products import ReplayProducts
from lawvm.finland.statute import ReplayState


_ADDR = LegalAddress(path=(("section", "5"),))


def _state() -> ReplayState:
    return ReplayState(ir=IRNode(kind=IRNodeKind.BODY))


def _commence(group_id: str = "grp", effective: str = "2024-01-01") -> TemporalEvent:
    return TemporalEvent(
        event_id=f"fi-temporal:{group_id}:commence",
        kind="commence",
        scope=TemporalScope(target_statute="0001/2024"),
        effective=effective,
        activation_rule=ActivationRule(kind=FIXED_DATE_KIND, effective_date=effective),
        group_id=group_id,
    )


def _expire(
    group_id: str = "grp",
    expires: str = "2024-07-01",
    address: LegalAddress = _ADDR,
) -> TemporalEvent:
    return TemporalEvent(
        event_id=f"fi-temporal:{group_id}:expire:{address}",
        kind="expire",
        scope=TemporalScope(
            target_statute="0001/2024", exact_addresses=(address,)
        ),
        expires=expires,
        group_id=group_id,
    )


def _products(
    *,
    timelines: dict[LegalAddress, ProvisionTimeline] | None,
    temporal_events: tuple[TemporalEvent, ...] = (),
    materialization_issues: tuple[TimelineIssue, ...] = (),
) -> ReplayProducts:
    state = _state()
    return ReplayProducts(
        replay_fold_state=state,
        materialized_state=state,
        timelines=timelines,
        temporal_events=temporal_events,
        materialization_issues=materialization_issues,
    )


# ---------------------------------------------------------------------------
# SCHED-03: registry wiring
# ---------------------------------------------------------------------------


def test_sched_window_code_registered_as_nonblocking_observation() -> None:
    spec = FINDING_REGISTRY[SCHED_WINDOW_UNMATERIALIZED]
    assert spec.role == "observation"
    assert spec.default_enforcement == "warn"


# ---------------------------------------------------------------------------
# SCHED-02: disjoint, unmaterialized window is surfaced
# ---------------------------------------------------------------------------


def test_disjoint_window_not_materialized_fires() -> None:
    # The timeline holds only a later-effective (2025) fold occupant; the
    # temporary [2024-01-01, 2024-07-01) window is not a version interval.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[ProvisionVersion(effective="2025-01-01", variant_kind="permanent")],
    )
    findings = sweep_disjoint_window_materialization(
        _products(
            timelines={_ADDR: timeline},
            temporal_events=(_commence(), _expire()),
        )
    )
    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, DisjointWindowFinding)
    assert finding.code == SCHED_WINDOW_UNMATERIALIZED
    assert finding.group_id == "grp"
    assert finding.target_address == str(_ADDR)
    assert finding.window_effective == "2024-01-01"
    assert finding.window_expires == "2024-07-01"
    # SCHED-02 evidence: the later-effective fold occupant that holds the slot.
    assert finding.fold_occupant_effective == "2025-01-01"
    assert not finding.materialized
    assert not finding.residualized
    # self-evidencing detail names the window + occupant.
    assert "2024-01-01" in finding.detail
    assert "2024-07-01" in finding.detail
    assert "2025-01-01" in finding.detail


def test_window_materialized_as_version_interval_is_silent() -> None:
    # SCHED-02 satisfied: the window IS a real version interval.
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[
            ProvisionVersion(
                effective="2024-01-01", expires="2024-07-01", variant_kind="temporary"
            ),
            ProvisionVersion(effective="2025-01-01", variant_kind="permanent"),
        ],
    )
    findings = sweep_disjoint_window_materialization(
        _products(
            timelines={_ADDR: timeline},
            temporal_events=(_commence(), _expire()),
        )
    )
    assert findings == ()


def test_window_with_no_timeline_entry_at_all_fires() -> None:
    # No timeline for the address: the window is unmaterialized (and there is no
    # occupant to report).
    findings = sweep_disjoint_window_materialization(
        _products(timelines={}, temporal_events=(_commence(), _expire()))
    )
    assert len(findings) == 1
    assert findings[0].fold_occupant_effective == ""
    assert "no occupant" in findings[0].detail


# ---------------------------------------------------------------------------
# SCHED-03: a window carried as a typed residual is accounted-for (silent)
# ---------------------------------------------------------------------------


def test_window_carried_as_typed_residual_is_silent() -> None:
    timeline = ProvisionTimeline(
        address=_ADDR,
        versions=[ProvisionVersion(effective="2025-01-01", variant_kind="permanent")],
    )
    # The replay output carries a typed timeline issue for the address -> the
    # window is tagged, not silently dropped, so SCHED-03 is satisfied.
    issue = TimelineIssue(
        kind="temporal_authority_source_expires",
        message="temporary window unresolved for section 5",
        address=_ADDR,
        source_statute="0001/2024",
    )
    findings = sweep_disjoint_window_materialization(
        _products(
            timelines={_ADDR: timeline},
            temporal_events=(_commence(), _expire()),
            materialization_issues=(issue,),
        )
    )
    assert findings == ()


# ---------------------------------------------------------------------------
# SCHED-01: the legal-effect window is read off the temporal-event plane, not
# fold order. A degenerate / non-window event class is not a SCHED fact.
# ---------------------------------------------------------------------------


def test_no_expire_event_no_window() -> None:
    # A commence with no paired expire is a permanent op, not a temporary window.
    findings = sweep_disjoint_window_materialization(
        _products(timelines={}, temporal_events=(_commence(),))
    )
    assert findings == ()


def test_degenerate_interval_is_not_a_scheduling_window() -> None:
    # expires <= effective is an LS-19 bad-interval class, not a disjoint window;
    # the SCHED sweep does not claim it.
    findings = sweep_disjoint_window_materialization(
        _products(
            timelines={},
            temporal_events=(
                _commence(effective="2024-07-01"),
                _expire(expires="2024-01-01"),
            ),
        )
    )
    assert findings == ()


def test_expire_event_uses_own_source_effective_when_no_commence() -> None:
    from lawvm.core.provenance import OperationSource

    # No commence event on the plane; the expire event's own source-effective
    # dates the window (SCHED-01: the legal-effect interval still resolves).
    expire = TemporalEvent(
        event_id="fi-temporal:grp:expire:section/5",
        kind="expire",
        scope=TemporalScope(target_statute="0001/2024", exact_addresses=(_ADDR,)),
        expires="2024-07-01",
        source=OperationSource(statute_id="0001/2024", effective="2024-02-01"),
        group_id="grp",
    )
    findings = sweep_disjoint_window_materialization(
        _products(timelines={}, temporal_events=(expire,))
    )
    assert len(findings) == 1
    assert findings[0].window_effective == "2024-02-01"


def test_sweep_is_deterministic_sorted() -> None:
    addr_a = LegalAddress(path=(("section", "1"),))
    addr_b = LegalAddress(path=(("section", "2"),))
    events = (
        _commence(group_id="g2", effective="2024-03-01"),
        _expire(group_id="g2", expires="2024-09-01", address=addr_b),
        _commence(group_id="g1", effective="2024-01-01"),
        _expire(group_id="g1", expires="2024-06-01", address=addr_a),
    )
    findings = sweep_disjoint_window_materialization(
        _products(timelines={}, temporal_events=events)
    )
    assert [f.target_address for f in findings] == [str(addr_a), str(addr_b)]
