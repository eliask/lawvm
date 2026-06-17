"""Tests for the H3 temporal/applicability SURFACE recognizer.

Covers :func:`recognize_temporal_exprs` from
``lawvm.finland.references.temporal`` — one assertion family per
:class:`TemporalKind`, plus the fail-loud residual contract (a cue that cannot
be typed to a determinate bound is emitted as a residual, never a guessed date,
never silently dropped).

This exercises the SURFACE lens only — it does not touch the replay / expiry
engine (``legal_pit``, fixed-term expiry apply path, ``core.temporal``).
"""
from __future__ import annotations

from datetime import date

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.temporal import (
    TemporalExpr,
    TemporalKind,
    TemporalStatus,
    recognize_temporal_exprs,
)


def _by_kind(text: str, kind: TemporalKind) -> list[TemporalExpr]:
    return [e for e in recognize_temporal_exprs(text) if e.kind is kind]


# ---------------------------------------------------------------------------
# FIXED_DATE — numeric
# ---------------------------------------------------------------------------


def test_fixed_date_numeric_parses_iso() -> None:
    text = "Tämä laki tulee voimaan 1.1.2027."
    fixed = _by_kind(text, TemporalKind.FIXED_DATE)
    assert len(fixed) == 1
    expr = fixed[0]
    assert expr.status is TemporalStatus.RESOLVED
    assert expr.bound == date(2027, 1, 1)
    assert expr.surface_text == "1.1.2027"
    assert expr.rule_id == "fixed_date.numeric"


def test_fixed_date_numeric_span_points_at_match() -> None:
    text = "voimaan 15.6.2030 saakka"
    expr = _by_kind(text, TemporalKind.FIXED_DATE)[0]
    span = expr.source_span
    assert isinstance(span, SourceSpan)
    assert text[span.byte_offset : span.byte_offset + span.byte_len] == "15.6.2030"


def test_fixed_date_impossible_is_unsupported_not_guessed() -> None:
    # 32.13.2027 is calendrically impossible: fail loud (residual), never a guess.
    text = "alkaen 32.13.2027"
    expr = _by_kind(text, TemporalKind.FIXED_DATE)[0]
    assert expr.status is TemporalStatus.UNSUPPORTED
    assert expr.bound is None


# ---------------------------------------------------------------------------
# FIXED_DATE — Finnish long form
# ---------------------------------------------------------------------------


def test_fixed_date_long_form_parses_iso() -> None:
    text = "Laki tulee voimaan 1 päivänä tammikuuta 2027."
    fixed = _by_kind(text, TemporalKind.FIXED_DATE)
    assert len(fixed) == 1
    expr = fixed[0]
    assert expr.status is TemporalStatus.RESOLVED
    assert expr.bound == date(2027, 1, 1)
    assert expr.surface_text == "1 päivänä tammikuuta 2027"
    assert expr.rule_id == "fixed_date.long_form"


def test_fixed_date_long_form_month_table() -> None:
    text = "30 päivänä syyskuuta 2025"
    expr = _by_kind(text, TemporalKind.FIXED_DATE)[0]
    assert expr.bound == date(2025, 9, 30)


def test_long_form_does_not_double_count_year_as_numeric() -> None:
    # The trailing "2027" must not also produce a spurious numeric FIXED_DATE.
    text = "1 päivänä tammikuuta 2027"
    fixed = _by_kind(text, TemporalKind.FIXED_DATE)
    assert len(fixed) == 1
    assert fixed[0].rule_id == "fixed_date.long_form"


# ---------------------------------------------------------------------------
# COMMENCEMENT
# ---------------------------------------------------------------------------


def test_commencement_tulee_voimaan() -> None:
    text = "Tämä laki tulee voimaan myöhemmin."
    com = _by_kind(text, TemporalKind.COMMENCEMENT)
    assert len(com) == 1
    expr = com[0]
    assert expr.status is TemporalStatus.RESOLVED
    assert expr.bound is None
    assert "tulee voimaan" in expr.surface_text.lower()
    assert expr.rule_id == "commencement.cue"


def test_commencement_voimaantulo_noun() -> None:
    text = "Lain voimaantulosta säädetään erikseen."
    com = _by_kind(text, TemporalKind.COMMENCEMENT)
    assert len(com) == 1
    assert com[0].kind is TemporalKind.COMMENCEMENT


# ---------------------------------------------------------------------------
# DURATION_FROM_COMMENCEMENT (residual: structural anchor, not a date)
# ---------------------------------------------------------------------------


def test_duration_from_commencement_is_unsupported_residual() -> None:
    text = "Säännöstä sovelletaan voimaantulosta lukien."
    dur = _by_kind(text, TemporalKind.DURATION_FROM_COMMENCEMENT)
    assert len(dur) >= 1
    expr = dur[0]
    assert expr.status is TemporalStatus.UNSUPPORTED
    assert expr.bound is None
    assert expr.rule_id == "duration_from_commencement.anchor"


# ---------------------------------------------------------------------------
# EVENT_BOUND (residual: until-an-event, no date)
# ---------------------------------------------------------------------------


def test_event_bound_kunnes_no_date() -> None:
    text = "Säännöstä sovelletaan, kunnes uusi laki tulee voimaan"
    ev = _by_kind(text, TemporalKind.EVENT_BOUND)
    assert len(ev) == 1
    expr = ev[0]
    assert expr.status is TemporalStatus.EVENT_BOUND
    assert expr.bound is None
    assert expr.surface_text.lower().startswith("kunnes")
    assert expr.rule_id == "event_bound.kunnes"


# ---------------------------------------------------------------------------
# VALIDITY_OPEN (residual: open-ended)
# ---------------------------------------------------------------------------


def test_validity_open_on_voimassa() -> None:
    text = "Tämä laki on voimassa toistaiseksi."
    op = _by_kind(text, TemporalKind.VALIDITY_OPEN)
    assert len(op) == 1
    expr = op[0]
    assert expr.status is TemporalStatus.OPEN
    assert expr.bound is None
    assert expr.rule_id == "validity_open.cue"


# ---------------------------------------------------------------------------
# Fail-loud invariant + general behaviour
# ---------------------------------------------------------------------------


def test_no_temporal_cue_returns_empty() -> None:
    assert recognize_temporal_exprs("Tässä pykälässä säädetään asiasta.") == []


def test_results_are_in_document_order() -> None:
    text = "Tämä laki tulee voimaan 1.1.2027. Se on voimassa toistaiseksi."
    exprs = recognize_temporal_exprs(text)
    offsets = [e.source_span.byte_offset for e in exprs]
    assert offsets == sorted(offsets)


def test_resolved_requires_bound_invariant() -> None:
    # The frozen node enforces the bound/status contract directly.
    import pytest

    with pytest.raises(ValueError):
        TemporalExpr(
            kind=TemporalKind.FIXED_DATE,
            surface_text="x",
            source_span=SourceSpan(source_file="", byte_offset=0, byte_len=1),
            bound=None,
            status=TemporalStatus.RESOLVED,
            rule_id="test",
        )

    with pytest.raises(ValueError):
        TemporalExpr(
            kind=TemporalKind.EVENT_BOUND,
            surface_text="x",
            source_span=SourceSpan(source_file="", byte_offset=0, byte_len=1),
            bound=date(2027, 1, 1),
            status=TemporalStatus.EVENT_BOUND,
            rule_id="test",
        )


def test_combined_clause_recognizes_each_kind() -> None:
    text = (
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027 ja on voimassa "
        "toistaiseksi, kunnes erikseen toisin säädetään."
    )
    kinds = {e.kind for e in recognize_temporal_exprs(text)}
    assert TemporalKind.COMMENCEMENT in kinds
    assert TemporalKind.FIXED_DATE in kinds
    assert TemporalKind.VALIDITY_OPEN in kinds
    assert TemporalKind.EVENT_BOUND in kinds
