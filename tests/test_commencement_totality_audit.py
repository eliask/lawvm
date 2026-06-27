"""Tests for ``core.commencement_totality_audit`` (D7 / LS-23).

Per :file:`notes_internal/audit_impl_D7.md` §6 — the synthetic regression
covers four cases:

* firing: an op with no commence TemporalEvent and no pending tag → exactly
  one ``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` observation;
* negative commenced: the op IS temporally authorized by a matching commence
  event via ``group_id`` + scope → zero findings;
* negative pending: the op carries a ``pending_amendment`` provenance tag →
  zero findings;
* negative unresolved: the op's source ``legal_status`` is ``pending_condition``
  → zero findings.

Audit-plane-only contract: the function emits observations and never raises.
``Observation.kind`` is the registered FindingSpec code and matches
:data:`FINDING_REGISTRY`, so the obligation-kind anti-drift check at
``tests/test_finding_registry.py::test_every_obligation_kind_has_finding_spec``
covers the wire-to-registry binding here.
"""

from __future__ import annotations

from lawvm.core.commencement_totality_audit import (
    COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION,
    assert_effect_totality,
)
from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.temporal import TemporalEvent, TemporalScope


_TARGET = LegalAddress(path=(("section", "1"),))


def _op(
    *,
    op_id: str = "d7-firing",
    group_id: str | None = "g1",
    provenance_tags: tuple[str, ...] = (),
    legal_status: str | None = None,
) -> LegalOperation:
    if legal_status is None:
        source = OperationSource(statute_id="ukpga/2020/1", effective="2024-01-01")
    else:
        # ``legal_status`` is typed as a ``LegalStatus`` Literal in core; the
        # tests here pass only values from that closed set (``pending_condition``
        # / ``uncommenced``). The cast keeps the helper API ergonomic without
        # widening the public OperationSource constructor.
        from lawvm.core.branch_authority import LegalStatus  # noqa: PLC0415
        import typing as _typing  # noqa: PLC0415
        source = OperationSource(
            statute_id="ukpga/2020/1",
            effective="2024-01-01",
            legal_status=_typing.cast(LegalStatus, legal_status),
        )
    return LegalOperation(
        op_id=op_id,
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_TARGET,
        source=source,
        group_id=group_id,
        provenance_tags=provenance_tags,
    )


def _commence(
    *,
    event_id: str = "e1",
    group_id: str = "g1",
    target_statute: str = "",
    exact_addresses: tuple[LegalAddress, ...] = (_TARGET,),
) -> TemporalEvent:
    return TemporalEvent(
        event_id=event_id,
        kind="commence",
        scope=TemporalScope(
            target_statute=target_statute,
            exact_addresses=exact_addresses,
        ),
        effective="2024-01-01",
        group_id=group_id,
    )


# --------------------------------------------------------------------------- #
# Firing case — the load-bearing guard-liveness test.                          #
# --------------------------------------------------------------------------- #


def test_op_without_commencement_event_or_pending_tag_fires_one_observation() -> None:
    """An op reaching compile-timelines without temporal authority fires exactly one observation.

    Drives the direct audit lane with a single op that has no matching
    commence/revive TemporalEvent and no pending/unresolved classification.
    The op MUST surface as a typed
    ``COMMENCEMENT.OP_WITHOUT_TEMPORAL_AUTHORIZATION`` observation rather than
    be silently effective-dated — the §0 over-retention-safe direction.
    """
    op = _op()
    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
    )
    assert len(findings) == 1, (
        "an op with no temporal authority and no pending classification MUST "
        "surface exactly one observation; got: {}".format(findings)
    )
    observation = findings[0]
    assert observation.kind == COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION
    assert observation.stage == "compile-timelines"
    assert observation.source_statute == "ukpga/2020/1"
    detail = observation.detail
    assert detail["op_id"] == "d7-firing"
    assert detail["group_id"] == "g1"
    assert detail["reason"] == "no_matching_commencement_event"
    assert detail["owner"] == "commencement_totality_audit"
    # Source provenance carried so a triager can answer §3.2's evidence path.
    assert detail["source"]["statute_id"] == "ukpga/2020/1"
    assert detail["source"]["legal_status"] == "commenced"


# --------------------------------------------------------------------------- #
# Negative: commenced (matching TemporalEvent by group_id + scope).            #
# --------------------------------------------------------------------------- #


def test_op_with_matching_commencement_event_emits_zero_observations() -> None:
    """A commence TemporalEvent matching the op's group_id + scope authorizes it.

    Mirrors the matching logic ``compile_timelines`` itself uses via
    :func:`matching_temporal_events_for_op`. The audit must agree with the
    wire on what "temporally authorized" means — a `kind='commence'` event
    with matching ``group_id`` and ``scope.exact_addresses`` containing the
    op's ``target`` is authority and emits zero findings.
    """
    op = _op()
    findings = assert_effect_totality(
        (op,),
        temporal_events=(_commence(),),
        source_statute="ukpga/2020/1",
    )
    assert findings == ()


def test_op_with_contingent_unfired_commence_event_emits_zero_observations() -> None:
    """A matching commence ``with contingent / unfired activation is NOT a D7 gap.

    Regression pin: when a commence TemporalEvent matches the op's ``group_id``
    and scope but its ``activation_rule.kind`` is ``pending_decree`` (or
    ``pending_condition``), the op is NOT missing temporal authority — the
    commence EXISTS and its trigger is unfired, owned by the existing
    ``skipped_contingent_unresolved`` / ``TIME.TIMELINE_EXECUTION_ISSUE``
    issue lane in ``compile_timelines`` / ``materialize_pit_ex``. D7 must NOT
    double-fire here; doing so would split contingent-unfired accounting across
    two issue kinds (the timeline already owns the firing surface).

    Mirrors the ``test_materialize_pit_ex_preserves_unresolved_contingent_skip``
    fixture in ``tests/test_fi_compile_facade.py`` (``activation_rule=
    ActivationRule(kind="pending_decree")``) where D7 incorrectly fired before
    this fix was landed.
    """
    from lawvm.core.temporal import ActivationRule  # noqa: PLC0415

    op = _op()
    contingent_commence = TemporalEvent(
        event_id="e-contingent",
        kind="commence",
        scope=TemporalScope(target_statute="ukpga/2020/1", exact_addresses=(_TARGET,)),
        effective="2024-01-01",
        group_id="g1",
        activation_rule=ActivationRule(kind="pending_decree"),
    )
    findings = assert_effect_totality(
        (op,),
        temporal_events=(contingent_commence,),
        source_statute="ukpga/2020/1",
    )
    assert findings == (), (
        "a contingent-unfired commence is the existing skipped_contingent_"
        "unresolved issue lane; D7 must NOT double-fire on it: {}".format(findings)
    )


# --------------------------------------------------------------------------- #
# Negative: pending (provenance tag carries the pending classification).       #
# --------------------------------------------------------------------------- #


def test_op_with_pending_amendment_provenance_tag_emits_zero_observations() -> None:
    """A ``pending_amendment`` provenance tag is the explicit pending classification.

    Per audit_impl_D7 §9 the audit must NOT infer pending status from a blank
    ``op.source.effective`` (blank dates may indicate missing metadata). The
    closed set :data:`_PENDING_PROVENANCE_TAGS` is the typed authority: an op
    carrying one is commencement-deferred-but-owned, not a missing-authority
    gap. ``pending_amendment`` is the EE/FI effect-lifecycle precomposition
    chain signal.
    """
    op = _op(provenance_tags=("pending_amendment",))
    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
    )
    assert findings == ()


def test_op_with_manual_frontier_commencement_tag_emits_zero_observations() -> None:
    """The UK manual-frontier commencement-effect-out-of-scope classification is pending authority.

    AGENTS.md §0 manual-compilation frontier row. Distinct from a missing-
    authority gap: the frontend has explicitly classified this op as outside
    structural replay's commencement scope.
    """
    op = _op(
        provenance_tags=("manual_frontier_commencement_effect_out_of_scope",)
    )
    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Negative: unresolved (op.source.legal_status carries an unresolved status). #
# --------------------------------------------------------------------------- #


def test_op_with_pending_condition_legal_status_emits_zero_observations() -> None:
    """``op.source.legal_status == 'pending_condition'`` is an owned unresolved classification.

    Per audit_impl_D7 §9 — when the frontend emitted a
    ``pending_condition`` (or ``uncommenced``) typed status AND an unresolved
    temporal finding, the audit verifies the carried ``legal_status`` and
    tolerates the op. The frontend owns that emission; the audit only
    asserts the classification carried through.
    """
    op = _op(legal_status="pending_condition")
    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
    )
    assert findings == ()


def test_op_with_uncommenced_legal_status_emits_zero_observations() -> None:
    """``op.source.legal_status == 'uncommenced'`` is the other owned pending classification."""
    op = _op(legal_status="uncommenced")
    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Discipline — the function emits observations only; never raises.             #
# --------------------------------------------------------------------------- #


def test_audit_never_raises_on_empty_inputs() -> None:
    """An empty ops stream or empty events stream returns zero observations without raising."""
    assert assert_effect_totality((), temporal_events=()) == ()
    assert assert_effect_totality((), temporal_events=(_commence(),)) == ()


def test_audit_processes_multiple_ops_in_stream_order() -> None:
    """A multi-op stream emits one observation per violating op in op-stream order.

    Mixing authorized + capitalized + pending + firing ops in one call surfaces
    exactly the firing ops, in order — the audit does NOT reorder, dedupe, or
    collapse findings. The §1.8 receipt contract for the audit lane: every
    violating op is owned, none silently dropped.
    """
    firing_a = _op(op_id="fire-a", group_id="g-fire-a")
    commenced = _op(op_id="comm", group_id="g-comm")
    pending = _op(op_id="pend", group_id="g-pend", provenance_tags=("pending_amendment",))
    firing_b = _op(op_id="fire-b", group_id="g-fire-b")
    events = (
        _commence(event_id="e-comm", group_id="g-comm"),
    )
    findings = assert_effect_totality(
        (firing_a, commenced, pending, firing_b),
        temporal_events=events,
        source_statute="ukpga/2020/1",
    )
    assert [o.detail["op_id"] for o in findings] == ["fire-a", "fire-b"]
    assert all(
        o.kind == COMMENCEMENT_OP_WITHOUT_TEMPORAL_AUTHORIZATION for o in findings
    )


# --------------------------------------------------------------------------- #
# Discriminators — the closed vocabulary is load-bearing.                     #
# --------------------------------------------------------------------------- #


def test_blank_effective_date_alone_is_NOT_pending_authority() -> None:
    """Per audit_impl_D7 §9: a blank ``op.source.effective`` is NOT pending status.

    Blank dates may indicate missing metadata, not a deliberate deferral. The
    §0 safe default for a no-authority op is a manual-frontier classification,
    not a guessed effective date — the audit must surface the gap rather than
    silently absorb the op via the empty-date branch.
    """
    op = LegalOperation(
        op_id="blank-effective",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_TARGET,
        # effective="" left blank deliberately: NOT pending authority.
        source=OperationSource(statute_id="ukpga/2020/1", effective=""),
        group_id="g-blank",
        provenance_tags=(),
    )
    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
    )
    assert len(findings) == 1
    assert findings[0].detail["op_id"] == "blank-effective"


def test_pending_classifier_extension_hook_accepts_frontend_specific_tags() -> None:
    """A custom ``pending_classifier`` is the per-frontend pending-amendment extension hook.

    Kept out of the closed vocabulary above so a frontend's own taxonomy
    doesn't widen the audit's universal contract — only a typed
    ``Callable[[LegalOperation], bool]`` argument extends it per call.
    """
    op = _op(op_id="custom-pending", group_id="g-custom", provenance_tags=("prefront_custom_pending",))

    def recognizes_custom_pending(operation: LegalOperation) -> bool:
        return "prefront_custom_pending" in operation.provenance_tags

    findings = assert_effect_totality(
        (op,),
        temporal_events=(),
        source_statute="ukpga/2020/1",
        pending_classifier=recognizes_custom_pending,
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Production-lane fire-drill (AGENTS.md §2.9 guard-liveness SOTA).            #
# --------------------------------------------------------------------------- #


def test_audit_fires_through_compile_timelines_production_lane() -> None:
    """§2.9 production-lane guard-liveness: drive a known-violating op through
    ``compile_timelines_ex`` and assert the audit fires on the production lane,
    not just under the unit helper.

    Mirrors the FI guard-liveness SOTA pattern
    (``tests/test_fi_guard_liveness.py::drill_*``): a guard that fires only
    under a unit harness but not on the production path is the §2.9 worst-class
    failure (looks real, passes review, false confidence). The D7 wire in
    ``compile_timelines`` must surface ``commencement_op_without_temporal_
    authorization`` on its issue_sink for an op that has no matching commence
    event and no pending classification.
    """
    from lawvm.core.ir import IRNode, IRNodeKind, IRStatute  # noqa: PLC0415
    from lawvm.core.timeline import compile_timelines_ex  # noqa: PLC0415

    body = IRNode(kind=IRNodeKind.SECTION, label="1")
    base = IRStatute(statute_id="ukpga/2020/1", title="test", body=body)
    op = LegalOperation(
        op_id="prod-lane-fire",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_TARGET,
        source=OperationSource(statute_id="ukpga/2020/1", effective="2024-01-01"),
        group_id="g-prod-no-event",
    )
    result = compile_timelines_ex(base, [op])
    d7_issues = [
        issue
        for issue in result.issues
        if issue.kind == "commencement_op_without_temporal_authorization"
    ]
    assert len(d7_issues) == 1, (
        "the D7 audit MUST fire through the production compile_timelines lane "
        "for an op with no commencement event and no pending classification; "
        f"got {len(d7_issues)} matching issues out of {len(result.issues)}"
    )
    issue = d7_issues[0]
    assert "op_id=prod-lane-fire" in issue.message
    assert "group_id=g-prod-no-event" in issue.message
    assert issue.source_statute == "ukpga/2020/1"


def test_audit_does_not_fire_through_compile_timelines_when_op_is_commenced() -> None:
    """Negative production-lane: a matching commence event suppresses the
    audit's TimelineIssue so authorised replay traffic stays quiet.

    Pairs with the positive fire-drill to assert the wire is discriminating,
    not blanket-firing on every op (the §0 over-retention-safe direction cuts
    both ways — silent absorption of unauthorised ops is forbidden, but so is
    noisy emission on authorised traffic).
    """
    from lawvm.core.ir import IRNode, IRNodeKind, IRStatute  # noqa: PLC0415
    from lawvm.core.timeline import compile_timelines_ex  # noqa: PLC0415

    body = IRNode(kind=IRNodeKind.SECTION, label="1")
    base = IRStatute(statute_id="ukpga/2020/1", title="test", body=body)
    op = LegalOperation(
        op_id="prod-lane-commenced",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_TARGET,
        source=OperationSource(statute_id="ukpga/2020/1", effective="2024-01-01"),
        group_id="g-prod-commenced",
    )
    commence = TemporalEvent(
        event_id="e1",
        kind="commence",
        scope=TemporalScope(target_statute="ukpga/2020/1", exact_addresses=(_TARGET,)),
        effective="2024-01-01",
        group_id="g-prod-commenced",
    )
    result = compile_timelines_ex(
        base, [op], temporal_events=(commence,)
    )
    d7_issues = [
        issue
        for issue in result.issues
        if issue.kind == "commencement_op_without_temporal_authorization"
    ]
    assert d7_issues == [], (
        "the D7 audit MUST NOT fire when a matching commence TemporalEvent "
        "authorises the op; got: {}".format(d7_issues)
    )
