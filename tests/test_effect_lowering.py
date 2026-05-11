"""Tests for EffectIntent lowering from MetaClause nodes and johtolause text.

Covers:
  - lower_meta_clause: MetaClause → EffectIntent dispatch (Finland module)
  - Commencement with explicit date
  - Commencement with contingent date
  - Expiry (on-force-until) extraction
  - Applicability (siirtymä / transition)
  - Valtuutus → None (no EffectIntent)
  - extract_meta_clauses: heuristic MetaClause extraction from johto text
  - lower_johto_effects: full pipeline from text to EffectIntents
  - Wiring: compile_amendment_ops emits executable temporal_events and drops
    parse-layer effect_intents when that executable authority exists
  - Wiring: process_muutoslaki propagates transitional debug effect_intents
    only when no executable temporal_events exist
"""

from __future__ import annotations

import datetime as dt

from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.effect_intent import (
    Applicability,
    Commencement,
    EffectKind,
    Expiry,
)
from lawvm.core.effect_lowering import (
    lower_effect_intents_to_temporal_events,
    temporal_event_from_effect_intent,
)
from lawvm.core.compile_result import ActivationRule
from lawvm.core.phase_result import PhaseResult
from lawvm.core.clause_ast import MetaClause
from lawvm.core.semantic_types import MetaClauseKind, IRNodeKind
from lawvm.finland.effect_lowering import (
    UNSUPPORTED_META_CLAUSE_RULE_ID,
    UnsupportedMetaClause,
    extract_meta_clauses,
    lower_johto_effects,
    lower_meta_clause,
)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# lower_meta_clause: voimaantulo → Commencement (explicit date)
# ---------------------------------------------------------------------------


def test_lower_commencement_explicit_date():
    """Voimaantulo clause with explicit date → Commencement with correct date."""
    raw = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    clause = MetaClause(kind=MetaClauseKind.COMMENCEMENT, raw_text=raw)
    intent = lower_meta_clause(clause)

    assert isinstance(intent, Commencement)
    assert intent.kind == EffectKind.COMMENCEMENT
    assert intent.effective_date == dt.date(2025, 1, 1)
    assert intent.is_contingent is False
    assert intent.raw_text == raw


def test_lower_commencement_mid_year():
    """Commencement date in mid-year — month mapping correctness."""
    raw = "Tämä laki tulee voimaan 15 päivänä kesäkuuta 2023."
    clause = MetaClause(kind=MetaClauseKind.COMMENCEMENT, raw_text=raw)
    intent = lower_meta_clause(clause)

    assert isinstance(intent, Commencement)
    assert intent.effective_date == dt.date(2023, 6, 15)


# ---------------------------------------------------------------------------
# lower_meta_clause: voimaantulo → Commencement (contingent date)
# ---------------------------------------------------------------------------


def test_lower_commencement_contingent():
    """Decree-set commencement → is_contingent=True, effective_date=None."""
    raw = "Tämä laki tulee voimaan asetuksella säädettävänä ajankohtana."
    clause = MetaClause(kind=MetaClauseKind.COMMENCEMENT, raw_text=raw)
    intent = lower_meta_clause(clause)

    assert isinstance(intent, Commencement)
    assert intent.is_contingent is True
    assert intent.effective_date is None


# ---------------------------------------------------------------------------
# lower_meta_clause: voimaantulo → Expiry
# ---------------------------------------------------------------------------


def test_lower_expiry_clause():
    """On-force-until clause → Expiry with correct date."""
    raw = "Tämä laki on voimassa 31 päivään joulukuuta 2026."
    clause = MetaClause(kind=MetaClauseKind.EXPIRY, raw_text=raw)
    intent = lower_meta_clause(clause)

    assert isinstance(intent, Expiry)
    assert intent.kind == EffectKind.EXPIRY
    assert intent.expiry_date == dt.date(2026, 12, 31)
    assert intent.raw_text == raw


# ---------------------------------------------------------------------------
# lower_meta_clause: siirtyma → Applicability
# ---------------------------------------------------------------------------


def test_lower_siirtyma_to_applicability():
    """Siirtymäsäännös clause → Applicability."""
    raw = "Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tulleisiin asioihin."
    clause = MetaClause(kind=MetaClauseKind.TRANSITION, raw_text=raw)
    intent = lower_meta_clause(clause)

    assert isinstance(intent, Applicability)
    assert intent.kind == EffectKind.APPLICABILITY
    assert intent.raw_text == raw


# ---------------------------------------------------------------------------
# lower_meta_clause: valtuutus → None
# ---------------------------------------------------------------------------


def test_lower_valtuutus_returns_none():
    """Delegation clause (valtuutus) → None (no EffectIntent)."""
    raw = "Tarkemmista säännöksistä voidaan antaa valtioneuvoston asetuksella."
    clause = MetaClause(kind=MetaClauseKind.DELEGATION, raw_text=raw)
    result = lower_meta_clause(clause)
    assert result is None


# ---------------------------------------------------------------------------
# lower_meta_clause: other → None
# ---------------------------------------------------------------------------


def test_lower_other_returns_none():
    """Unrecognised kind → None."""
    clause = MetaClause(kind=MetaClauseKind.OTHER, raw_text="Muu säännös.")
    assert lower_meta_clause(clause) is None


# ---------------------------------------------------------------------------
# lower_meta_clause: bad month name → date is None, still Commencement
# ---------------------------------------------------------------------------


def test_lower_commencement_unrecognised_month():
    """Unrecognised month name → effective_date is None, but Commencement is returned."""
    raw = "Tämä laki tulee voimaan 1 päivänä foobarskuuta 2025."
    clause = MetaClause(kind=MetaClauseKind.COMMENCEMENT, raw_text=raw)
    intent = lower_meta_clause(clause)
    # Still returns Commencement but with no date
    assert isinstance(intent, Commencement)
    assert intent.effective_date is None
    assert intent.is_contingent is False


# ---------------------------------------------------------------------------
# extract_meta_clauses: heuristic pattern matching
# ---------------------------------------------------------------------------


def test_extract_meta_clauses_commencement():
    """Johtolause with 'tulee voimaan' → one voimaantulo MetaClause."""
    johto = "muutetaan rikoslain 6 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    clauses = extract_meta_clauses(johto)
    assert any(c.kind == MetaClauseKind.COMMENCEMENT for c in clauses)
    voimaantulo_clauses = [c for c in clauses if c.kind == MetaClauseKind.COMMENCEMENT]
    assert len(voimaantulo_clauses) == 1
    assert "tammikuuta" in voimaantulo_clauses[0].raw_text


def test_extract_meta_clauses_siirtyma():
    """Siirtymäsäännös sentence → one siirtyma MetaClause."""
    johto = "kumotaan 3 §. Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tulleisiin asioihin."
    clauses = extract_meta_clauses(johto)
    siirtyma = [c for c in clauses if c.kind == MetaClauseKind.TRANSITION]
    assert len(siirtyma) == 1


def test_extract_meta_clauses_empty():
    """Pure structural johtolause (no meta patterns) → empty list."""
    johto = "muutetaan 3, 5 ja 7 §."
    clauses = extract_meta_clauses(johto)
    assert clauses == []


def test_extract_meta_clauses_empty_string():
    """Empty string → empty list."""
    assert extract_meta_clauses("") == []


def test_extract_meta_clauses_valtuutus():
    """Delegation clause → one valtuutus MetaClause."""
    johto = "muutetaan 1 §. Tarkemmista säännöksistä voidaan antaa tarkempia säännöksiä asetuksella."
    clauses = extract_meta_clauses(johto)
    valtuutus = [c for c in clauses if c.kind == MetaClauseKind.DELEGATION]
    assert len(valtuutus) == 1


# ---------------------------------------------------------------------------
# lower_johto_effects: full pipeline
# ---------------------------------------------------------------------------


def test_lower_johto_effects_commencement():
    """Full pipeline: commencement clause in johto → Commencement EffectIntent."""
    johto = "muutetaan 5 §. Tämä laki tulee voimaan 1 päivänä maaliskuuta 2026."
    intents = lower_johto_effects(johto)
    assert len(intents) == 1
    assert isinstance(intents[0], Commencement)
    assert intents[0].effective_date == dt.date(2026, 3, 1)


def test_temporal_event_from_effect_intent_commencement() -> None:
    intent = Commencement(effective_date=dt.date(2026, 3, 1), raw_text="raw")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:1",
        group_id="ei",
        source_ref="2026/1",
        target_statute="1991/1",
    )

    assert event.event_id == "ei:1"
    assert event.group_id == "ei"
    assert event.kind == "commence"
    assert isinstance(event.activation_rule, ActivationRule)
    assert event.activation_rule.kind == "fixed_date"
    assert event.activation_rule.effective_date == "2026-03-01"
    assert event.activation_rule.raw_text == "raw"
    assert event.effective == "2026-03-01"
    assert event.source is not None
    assert event.source.effective == ""
    assert event.scope.target_statute == "1991/1"
    assert event.derived_from_effect_intent == "commencement"


def test_temporal_event_from_effect_intent_immediate_keeps_provenance_separate() -> None:
    intent = Commencement(effective_date=None, raw_text="raw")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:immediate",
        group_id="ei",
        source_ref="2026/1",
        source_issue_date=dt.date(2026, 3, 1),
        source_effective_date=dt.date(2026, 6, 1),
        target_statute="1991/1",
    )

    assert event.event_id == "ei:immediate"
    assert event.activation_rule is not None
    assert event.activation_rule.kind == "immediate"
    assert event.activation_rule.effective_date == ""
    assert event.effective == "2026-06-01"
    assert event.source is not None
    assert event.source.enacted == "2026-03-01"
    assert event.source.effective == "2026-06-01"


def test_temporal_event_from_effect_intent_immediate_no_explicit_effective_keeps_empty() -> None:
    intent = Commencement(effective_date=None, raw_text="raw")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:immediate_issue",
        group_id="ei",
        source_ref="2026/1",
        source_issue_date=dt.date(2026, 3, 1),
        target_statute="1991/1",
    )

    assert event.effective == "2026-03-01"


def test_temporal_event_from_effect_intent_immediate_no_source_date_keeps_empty() -> None:
    intent = Commencement(effective_date=None, raw_text="raw")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:immediate_none",
        group_id="ei",
        source_ref="2026/1",
        target_statute="1991/1",
    )

    assert event.effective == ""


def test_temporal_event_from_effect_intent_applicability_preserves_predicate() -> None:
    intent = Applicability(raw_text="tätä lakia sovelletaan vain AX:ssa")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:3",
        group_id="ei",
        source_ref="2026/1",
        target_statute="1991/1",
    )

    assert event.kind == "set_applicability"
    assert event.scope.target_statute == "1991/1"
    assert len(event.scope.predicates) == 1
    predicate = event.scope.predicates[0]
    assert predicate.dimension == "applicability"
    assert "tätä lakia sovelletaan vain AX:ssa" in predicate.includes
    assert event.derived_from_effect_intent == "applicability"


def test_temporal_event_from_effect_intent_applicability_derives_effective_from_source() -> None:
    intent = Applicability(raw_text="tätä lakia sovelletaan vain AX:ssa")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:3b",
        group_id="ei",
        source_ref="2026/1",
        source_issue_date=dt.date(2026, 3, 1),
        source_effective_date=dt.date(2026, 6, 1),
        target_statute="1991/1",
    )

    assert event.kind == "set_applicability"
    assert event.effective == "2026-06-01"


def test_temporal_event_from_effect_intent_contingent_commencement() -> None:
    intent = Commencement(is_contingent=True, raw_text="raw")

    event = temporal_event_from_effect_intent(intent, event_id="ei:2")

    assert event.kind == "commence"
    assert isinstance(event.activation_rule, ActivationRule)
    assert event.activation_rule.kind == "pending_decree"
    assert event.activation_rule.raw_text == "raw"
    assert event.source is None or event.source.effective == ""


def test_temporal_event_from_effect_intent_expiry_uses_explicit_end_payload() -> None:
    intent = Expiry(expiry_date=dt.date(2026, 12, 31), raw_text="raw")

    event = temporal_event_from_effect_intent(
        intent,
        event_id="ei:expiry",
        group_id="ei",
        source_ref="2026/1",
        target_statute="1991/1",
    )

    assert event.kind == "expire"
    assert event.expires == "2026-12-31"
    assert event.source is not None
    assert event.source.expires == ""


def test_lower_effect_intents_to_temporal_events_projects_multiple_variants() -> None:
    events = lower_effect_intents_to_temporal_events(
        [
            Commencement(effective_date=dt.date(2025, 1, 1), raw_text="a"),
            Expiry(expiry_date=dt.date(2026, 12, 31), raw_text="b"),
            Applicability(raw_text="c"),
        ],
        source_ref="2025/1",
        source_title="Lowering boundary title",
        source_issue_date=dt.date(2024, 1, 1),
        source_effective_date=dt.date(2024, 2, 1),
        group_id_prefix="g",
        target_statute="1991/1",
    )

    assert [event.event_id for event in events] == ["g:1", "g:2", "g:3"]
    assert [event.kind for event in events] == ["commence", "expire", "set_applicability"]
    assert all(event.group_id == "g" for event in events)
    assert all(event.scope.target_statute == "1991/1" for event in events)
    assert all(event.source is not None and event.source.title == "Lowering boundary title" for event in events)
    assert all(event.source is not None and event.source.enacted == "2024-01-01" for event in events)
    assert all(event.source is not None and event.source.effective == "2024-02-01" for event in events)
    assert events[0].effective == "2025-01-01"
    assert events[1].expires == "2026-12-31"


def test_lower_johto_effects_records_unsupported_valtuutus():
    """Valtuutus clauses are non-executable effects, but the drop is visible."""
    johto = "muutetaan 1 §. Tarkemmista säännöksistä voidaan antaa tarkempia säännöksiä asetuksella."
    unsupported: list[UnsupportedMetaClause] = []

    intents = lower_johto_effects(johto, unsupported_out=unsupported)

    assert len(intents) == 0
    assert len(unsupported) == 1
    assert unsupported[0].rule_id == UNSUPPORTED_META_CLAUSE_RULE_ID
    assert unsupported[0].reason_code == "delegation_clause_not_executable_effect"
    assert unsupported[0].clause_kind == "delegation"
    assert unsupported[0].blocking is False
    assert get_finding_spec(unsupported[0].rule_id) is not None


def test_lower_johto_effects_multiple():
    """Commencement + siirtymä in same johto → two EffectIntents."""
    johto = (
        "kumotaan 3 §. "
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025. "
        "Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tulleisiin asioihin."
    )
    intents = lower_johto_effects(johto)
    kinds = [i.kind for i in intents]
    assert EffectKind.COMMENCEMENT in kinds
    assert EffectKind.APPLICABILITY in kinds


def test_lower_johto_effects_empty():
    """Empty johtolause → empty list."""
    assert lower_johto_effects("") == []


# ---------------------------------------------------------------------------
# Wiring tests: compile_amendment_ops and process_muutoslaki EffectIntent wiring
# ---------------------------------------------------------------------------


def _make_minimal_replay_state():
    """Build the minimal ReplayState needed for compile_amendment_ops."""
    from lawvm.finland.statute import ReplayState
    from lawvm.core.ir import IRNode

    # Minimal IRNode tree: body with one section
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Tämä on perusteksti."),),
    )
    return ReplayState(ir=body)


def test_compile_amendment_ops_emits_temporal_events_without_retaining_effect_intents():
    """compile_amendment_ops keeps only executable temporal authority when available."""
    from lawvm.finland.grafter import compile_amendment_ops
    import lxml.etree as etree

    # Minimal muutos_tree (empty amendment body)
    muutos_xml = b"""<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act name="amendment">
        <meta><identification><FRBRWork><FRBRtitle>Amendment Title</FRBRtitle></FRBRWork></identification></meta>
        <docTitle>Amendment Title</docTitle>
        <body/>
      </act>
    </akomaNtoso>"""
    muutos_tree = etree.fromstring(muutos_xml)

    # Johtolause that contains both a structural verb AND a voimaantulo clause
    johto = "muutetaan 1 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2026."

    state = _make_minimal_replay_state()

    result = compile_amendment_ops(
        master=state,
        ops=[],
        muutos_tree=muutos_tree,
        johto=johto,
        replay_mode="finlex_oracle",
        source_ref="2026/1",
        source_title="Amendment Title",
        target_statute="1991/1",
    )

    assert len(result.temporal_events) == 1
    assert result.temporal_events[0].kind == "commence"
    assert result.temporal_events[0].effective == "2026-01-01"
    assert result.temporal_events[0].source is not None
    assert result.temporal_events[0].source.effective == ""
    assert result.temporal_events[0].source is not None
    assert result.temporal_events[0].source.title == "Amendment Title"

    # Observations should not contain EffectIntent typed objects
    effect_obs = [
        o
        for o in result.findings()
        if o.role == "observation"
        and o.kind in {"Commencement", "Expiry", "Suspension", "Applicability", "Revival"}
    ]
    assert effect_obs == []


def test_compile_amendment_ops_surfaces_unsupported_meta_clause() -> None:
    from lawvm.finland.grafter import compile_amendment_ops
    import lxml.etree as etree

    muutos_xml = b"""<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act name="amendment"><body/></act>
    </akomaNtoso>"""
    muutos_tree = etree.fromstring(muutos_xml)
    state = _make_minimal_replay_state()

    result = compile_amendment_ops(
        master=state,
        ops=[],
        muutos_tree=muutos_tree,
        johto="Asetuksella voidaan antaa tarkempia säännöksiä.",
        replay_mode="finlex_oracle",
        source_ref="2026/1",
    )

    findings = [
        finding
        for finding in result.findings()
        if finding.kind == UNSUPPORTED_META_CLAUSE_RULE_ID
    ]
    assert len(findings) == 1
    assert findings[0].role == "observation"
    assert findings[0].source_statute == "2026/1"
    assert findings[0].detail["reason_code"] == "delegation_clause_not_executable_effect"
    assert result.temporal_events == ()


def test_compile_amendment_ops_no_effect_intents_without_johto():
    """compile_amendment_ops with empty johto produces no temporal rails."""
    from lawvm.finland.grafter import compile_amendment_ops
    import lxml.etree as etree

    muutos_xml = b"""<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
      <act name="amendment"><body/></act>
    </akomaNtoso>"""
    muutos_tree = etree.fromstring(muutos_xml)

    state = _make_minimal_replay_state()
    result = compile_amendment_ops(
        master=state,
        ops=[],
        muutos_tree=muutos_tree,
        johto="",
        replay_mode="finlex_oracle",
    )
    assert result.temporal_events == ()


def test_lower_effect_intents_to_temporal_events_is_the_explicit_boundary() -> None:
    events = lower_effect_intents_to_temporal_events(
        (Commencement(effective_date=dt.date(2025, 1, 1), raw_text="a"),),
        source_ref="fi/2025/1",
        source_title="Explicit boundary title",
        source_effective_date=dt.date(2025, 1, 1),
        group_id_prefix="phase-result-effect-intent",
        target_statute="fi/2025/1",
    )

    assert len(events) == 1
    assert events[0].kind == "commence"
    assert events[0].source is not None
    assert events[0].source.effective == "2025-01-01"
    assert events[0].event_id == "phase-result-effect-intent:1"
    assert events[0].source is not None
    assert events[0].source.title == "Explicit boundary title"
    assert events[0].source.effective == "2025-01-01"


def test_phase_result_merge_accumulates_temporal_events() -> None:
    event_a = TemporalEvent(event_id="a", kind="commence", scope=TemporalScope())
    event_b = TemporalEvent(event_id="b", kind="expire", scope=TemporalScope())

    pr_a = PhaseResult(output="a", temporal_events=(event_a,))
    pr_b = PhaseResult(output="b", temporal_events=(event_b,))

    merged = pr_a.merge(pr_b)

    assert merged.temporal_events == (event_a, event_b)
