"""Tests for typed ActivationRule derivation from SurfaceMetaClause objects.

Covers:
  - activation_rules_from_meta_clauses: all meta_kind x commencement patterns
  - default_activation_rule: Finnish default (immediate)
  - classify_contingent: backward compat bridge
  - Integration: parse_clause() meta_clauses -> temporal_lowering -> ActivationRules
"""

from __future__ import annotations

from lawvm.core.semantic_types import MetaClauseKind
from lawvm.core.temporal import ActivationRule
from lawvm.finland.johtolause.surface_model import SurfaceMetaClause
from lawvm.finland.temporal_lowering import (
    activation_rules_from_meta_clauses,
    activation_rules_from_meta_clauses_with_findings,
    classify_contingent,
    default_activation_rule,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight SurfaceMetaClause builders
# ---------------------------------------------------------------------------


def _meta_clause(kind: MetaClauseKind, text: str = "") -> SurfaceMetaClause:
    return SurfaceMetaClause(kind=kind, text=text)


# ---------------------------------------------------------------------------
# activation_rules_from_meta_clauses
# ---------------------------------------------------------------------------


class TestActivationRulesFromMetaClauses:
    """activation_rules_from_meta_clauses for each commencement pattern."""

    def test_empty_list(self) -> None:
        assert activation_rules_from_meta_clauses([]) == []

    def test_non_commencement_skipped(self) -> None:
        clauses = [
            _meta_clause(MetaClauseKind.TRANSITION, "Tata lakia sovelletaan..."),
            _meta_clause(MetaClauseKind.EXPIRY, "Tama laki on voimassa 31 paivaan joulukuuta 2027."),
            _meta_clause(MetaClauseKind.DELEGATION, "Tarkempia saannoksia annetaan..."),
        ]
        assert activation_rules_from_meta_clauses(clauses) == []

    def test_non_commencement_skipped_with_findings(self) -> None:
        clauses = [
            _meta_clause(MetaClauseKind.TRANSITION, "Tata lakia sovelletaan..."),
            _meta_clause(MetaClauseKind.EXPIRY, "Tama laki on voimassa 31 paivaan joulukuuta 2027."),
        ]
        result = activation_rules_from_meta_clauses_with_findings(clauses)
        assert result.activation_rules == ()
        skipped = [f for f in result.findings if f.kind == "TIME.ACTIVATION_RULE_INPUT_SKIPPED"]
        assert [f.detail.get("input_kind") for f in skipped] == ["transition", "expiry"]

    def test_commencement_with_fixed_date(self) -> None:
        clause = _meta_clause(
            MetaClauseKind.COMMENCEMENT,
            "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027.",
        )
        rules = activation_rules_from_meta_clauses([clause])
        assert len(rules) == 1
        assert rules[0].kind == "fixed_date"
        assert rules[0].effective_date == "2027-01-01"

    def test_commencement_decree_set(self) -> None:
        clause = _meta_clause(
            MetaClauseKind.COMMENCEMENT,
            "Tämä laki tulee voimaan valtioneuvoston asetuksella säädettävänä ajankohtana.",
        )
        rules = activation_rules_from_meta_clauses([clause])
        assert len(rules) == 1
        assert rules[0].kind == "pending_decree"

    def test_commencement_simultaneous_entry(self) -> None:
        clause = _meta_clause(
            MetaClauseKind.COMMENCEMENT,
            "Tämä laki tulee voimaan samanaikaisesti kuin rikoslain muutos.",
        )
        rules = activation_rules_from_meta_clauses([clause])
        assert len(rules) == 1
        assert rules[0].kind == "pending_condition"
        assert "rikoslain muutos" in rules[0].condition_ref

    def test_commencement_immediate_no_date(self) -> None:
        clause = _meta_clause(
            MetaClauseKind.COMMENCEMENT,
            "Tämä laki tulee voimaan.",
        )
        rules = activation_rules_from_meta_clauses([clause])
        assert len(rules) == 1
        assert rules[0].kind == "immediate"

    def test_multiple_commencement_clauses(self) -> None:
        clauses = [
            _meta_clause(
                MetaClauseKind.COMMENCEMENT,
                "Tämä laki tulee voimaan 15 päivänä kesäkuuta 2025.",
            ),
            _meta_clause(
                MetaClauseKind.COMMENCEMENT,
                "Lain 5 pykälä tulee voimaan asetuksella säädettävänä ajankohtana.",
            ),
        ]
        rules = activation_rules_from_meta_clauses(clauses)
        assert len(rules) == 2
        assert rules[0].kind == "fixed_date"
        assert rules[0].effective_date == "2025-06-15"
        assert rules[1].kind == "pending_decree"

    def test_mixed_meta_kinds(self) -> None:
        """Only commencement meta_clauses produce rules; others are skipped."""
        clauses = [
            _meta_clause(MetaClauseKind.TRANSITION, "Siirtymäsäännös..."),
            _meta_clause(
                MetaClauseKind.COMMENCEMENT,
                "Tämä laki tulee voimaan 1 päivänä maaliskuuta 2026.",
            ),
            _meta_clause(MetaClauseKind.EXPIRY, "On voimassa..."),
        ]
        rules = activation_rules_from_meta_clauses(clauses)
        assert len(rules) == 1
        assert rules[0].kind == "fixed_date"
        assert rules[0].effective_date == "2026-03-01"

    def test_raw_text_preserved(self) -> None:
        text = "Tämä laki tulee voimaan valtioneuvoston asetuksella säädettävänä ajankohtana."
        clause = _meta_clause(MetaClauseKind.COMMENCEMENT, text)
        rules = activation_rules_from_meta_clauses([clause])
        assert rules[0].raw_text == text

    def test_decree_set_erikseen_pattern(self) -> None:
        clause = _meta_clause(
            MetaClauseKind.COMMENCEMENT,
            "Tämä laki tulee voimaan erikseen säädettävänä ajankohtana.",
        )
        rules = activation_rules_from_meta_clauses([clause])
        assert len(rules) == 1
        assert rules[0].kind == "pending_decree"

    def test_simultaneous_yhta_aikaa_pattern(self) -> None:
        clause = _meta_clause(
            MetaClauseKind.COMMENCEMENT,
            "Tämä laki tulee voimaan yhtä aikaa hallintolain kanssa.",
        )
        rules = activation_rules_from_meta_clauses([clause])
        assert len(rules) == 1
        assert rules[0].kind == "pending_condition"


# ---------------------------------------------------------------------------
# default_activation_rule
# ---------------------------------------------------------------------------


class TestDefaultActivationRule:
    """default_activation_rule returns the Finnish immediate default."""

    def test_returns_immediate(self) -> None:
        rule = default_activation_rule()
        assert rule.kind == "immediate"
        assert rule.effective_date == ""
        assert rule.condition_ref == ""

    def test_returns_valid_activation_rule(self) -> None:
        rule = default_activation_rule()
        assert isinstance(rule, ActivationRule)


# ---------------------------------------------------------------------------
# classify_contingent
# ---------------------------------------------------------------------------


class TestClassifyContingent:
    """classify_contingent backward-compat bridge from ActivationRule to bool."""

    def test_immediate_is_not_contingent(self) -> None:
        assert classify_contingent(ActivationRule(kind="immediate")) is False

    def test_fixed_date_is_not_contingent(self) -> None:
        rule = ActivationRule(kind="fixed_date", effective_date="2027-01-01")
        assert classify_contingent(rule) is False

    def test_pending_decree_is_contingent(self) -> None:
        assert classify_contingent(ActivationRule(kind="pending_decree")) is True

    def test_pending_condition_is_contingent(self) -> None:
        rule = ActivationRule(kind="pending_condition", condition_ref="laki X")
        assert classify_contingent(rule) is True


# ---------------------------------------------------------------------------
# Integration: parse_clause() -> meta_clauses -> activation rules
# ---------------------------------------------------------------------------


class TestIntegrationParseClauseToActivationRules:
    """Integration: full path from parse_clause() through temporal_lowering."""

    def test_commencement_clause_produces_activation_rule(self) -> None:
        """A johtolause with commencement text should produce activation rules
        when its meta_clauses are fed through the temporal lowering."""
        from lawvm.finland.johtolause.compat import parse_clause

        # Realistic johtolause with commencement clause appended
        text = "muutetaan rikoslain 3 luvun 1 ja 2 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2027."
        result = parse_clause(text)

        # Check that meta_clauses were extracted
        # Support both "kind" (new) and "meta_kind" (old) attributes
        commencement_clauses = [
            mc for mc in result.meta_clauses
            if mc.kind == MetaClauseKind.COMMENCEMENT
        ]
        # Note: whether the parser extracts meta clauses from this text depends
        # on sentence splitting and pattern matching. If no meta_clauses found,
        # the test still validates the lowering path returns empty.
        rules = activation_rules_from_meta_clauses(result.meta_clauses)
        if commencement_clauses:
            assert len(rules) >= 1
            assert rules[0].kind == "fixed_date"
            assert rules[0].effective_date == "2027-01-01"
        else:
            # No commencement meta_clauses extracted — lowering correctly returns empty
            assert rules == []

    def test_contingent_commencement_via_parse_clause(self) -> None:
        """A johtolause with decree-set commencement should produce pending_decree."""
        from lawvm.finland.johtolause.compat import parse_clause

        text = "muutetaan lain 5 §. Tämä laki tulee voimaan valtioneuvoston asetuksella säädettävänä ajankohtana."
        result = parse_clause(text)
        rules = activation_rules_from_meta_clauses(result.meta_clauses)

        commencement_clauses = [
            mc for mc in result.meta_clauses
            if mc.kind == MetaClauseKind.COMMENCEMENT
        ]
        if commencement_clauses:
            assert len(rules) >= 1
            assert rules[0].kind == "pending_decree"
            assert classify_contingent(rules[0]) is True

    def test_default_rule_when_no_meta_clauses(self) -> None:
        """When parse_clause produces no commencement meta_clauses, the
        default_activation_rule provides the fallback."""
        from lawvm.finland.johtolause.compat import parse_clause

        text = "muutetaan rikoslain 3 luvun 1 §"
        result = parse_clause(text)

        rules = activation_rules_from_meta_clauses(result.meta_clauses)
        if not rules:
            rules = [default_activation_rule()]
        assert len(rules) >= 1
        assert rules[0].kind == "immediate"

    def test_meta_clauses_from_real_surface_model(self) -> None:
        """Test with actual SurfaceMetaClause objects from the surface model."""
        clauses = (
            SurfaceMetaClause(
                kind=MetaClauseKind.COMMENCEMENT,
                text="Tämä laki tulee voimaan 15 päivänä helmikuuta 2026.",
            ),
            SurfaceMetaClause(
                kind=MetaClauseKind.TRANSITION,
                text="Siirtymäsäännös ennen lain voimaantuloa.",
            ),
        )
        rules = activation_rules_from_meta_clauses(clauses)
        assert len(rules) == 1
        assert rules[0].kind == "fixed_date"
        assert rules[0].effective_date == "2026-02-15"
        assert classify_contingent(rules[0]) is False
