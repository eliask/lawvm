"""Tests for Phase 7 meta/effect surface clause extraction and parse_clause() integration.

Covers:
  - extract_meta_surface_clauses: commencement, expiry, transition, delegation
  - Empty input / purely structural input → empty meta_clauses
  - parse_clause(): returns ClauseParseResult with meta_clauses populated
  - parse_clause(): structural parse still works alongside meta extraction
  - SurfaceMetaClause meta_kind values and rule_id witness tracking
"""

from __future__ import annotations

from lawvm.core.semantic_types import MetaClauseKind
from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses
from lawvm.finland.johtolause.surface_model import SurfaceMetaClause
from lawvm.finland.johtolause.compat import parse_clause, ClauseParseResult


# ---------------------------------------------------------------------------
# extract_meta_surface_clauses: basic classification
# ---------------------------------------------------------------------------


def test_extract_commencement_clause():
    """'tulee voimaan' → one SurfaceMetaClause(meta_kind='commencement')."""
    text = "muutetaan rikoslain 6 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    clauses = extract_meta_surface_clauses(text)
    commencement = [c for c in clauses if c.kind == MetaClauseKind.COMMENCEMENT]
    assert len(commencement) == 1
    assert "voimaan" in commencement[0].text.lower()
    assert isinstance(commencement[0], SurfaceMetaClause)


def test_extract_expiry_clause():
    """'on voimassa' → one SurfaceMetaClause(meta_kind='expiry')."""
    text = "lisätään 3 §. Tämä laki on voimassa 31 päivään joulukuuta 2026."
    clauses = extract_meta_surface_clauses(text)
    expiry = [c for c in clauses if c.kind == MetaClauseKind.EXPIRY]
    assert len(expiry) == 1
    assert "voimassa" in expiry[0].text.lower()


def test_extract_transition_clause():
    """siirtymäsäännös/tätä lakia sovelletaan → meta_kind='transition'."""
    text = "kumotaan 3 §. Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tulleisiin asioihin."
    clauses = extract_meta_surface_clauses(text)
    transition = [c for c in clauses if c.kind == MetaClauseKind.TRANSITION]
    assert len(transition) == 1


def test_extract_delegation_clause():
    """valtuutus (antaa säännöksiä) → meta_kind='delegation'."""
    text = "muutetaan 1 §. Tarkemmista säännöksistä voidaan antaa tarkempia säännöksiä asetuksella."
    clauses = extract_meta_surface_clauses(text)
    delegation = [c for c in clauses if c.kind == MetaClauseKind.DELEGATION]
    assert len(delegation) == 1


def test_extract_empty_string():
    """Empty string → empty list."""
    assert extract_meta_surface_clauses("") == []


def test_extract_purely_structural():
    """Pure structural johtolause → empty list."""
    text = "muutetaan 3, 5 ja 7 §."
    assert extract_meta_surface_clauses(text) == []


def test_extract_multiple_meta_clauses():
    """Commencement + transition → two SurfaceMetaClause nodes."""
    text = (
        "kumotaan 3 §. "
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025. "
        "Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tulleisiin asioihin."
    )
    clauses = extract_meta_surface_clauses(text)
    kinds = [c.kind for c in clauses]
    assert MetaClauseKind.COMMENCEMENT in kinds
    assert MetaClauseKind.TRANSITION in kinds
    assert len(clauses) == 2


def test_extract_witness_rule_id():
    """Witness rule_id carries 'meta_parse:<meta_kind>'."""
    text = "Tämä laki tulee voimaan 1 päivänä maaliskuuta 2026."
    clauses = extract_meta_surface_clauses(text)
    assert len(clauses) == 1
    assert clauses[0].witness is not None
    assert clauses[0].witness.rule_id == "meta_parse:commencement"


def test_extract_text_preserved():
    """The raw sentence text is preserved in SurfaceMetaClause.text."""
    sentence = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    text = "muutetaan 3 §. " + sentence
    clauses = extract_meta_surface_clauses(text)
    assert any(sentence in c.text for c in clauses)


def test_expiry_does_not_match_commencement():
    """'on voimassa' pattern → expiry, not commencement."""
    text = "Tämä laki on voimassa 31 päivään joulukuuta 2026."
    clauses = extract_meta_surface_clauses(text)
    assert len(clauses) == 1
    assert clauses[0].kind == MetaClauseKind.EXPIRY
    assert MetaClauseKind.COMMENCEMENT not in [c.kind for c in clauses]


def test_one_classification_per_sentence():
    """A sentence matching multiple patterns is classified once (first match wins)."""
    # Transition pattern appears before commencement in _META_PATTERNS, but
    # this sentence only has 'tulee voimaan' so it's commencement.
    text = "Tämä laki tulee voimaan asetuksella säädettävänä ajankohtana."
    clauses = extract_meta_surface_clauses(text)
    assert len(clauses) == 1


# ---------------------------------------------------------------------------
# parse_clause(): integration — ClauseParseResult with meta_clauses
# ---------------------------------------------------------------------------


def test_parse_clause_returns_clause_parse_result():
    """parse_clause() returns a ClauseParseResult instance."""
    text = "muutetaan 5 §."
    result = parse_clause(text)
    assert isinstance(result, ClauseParseResult)


def test_parse_clause_meta_clauses_empty_for_structural_only():
    """Pure structural johtolause → meta_clauses is empty tuple."""
    text = "muutetaan 3, 5 ja 7 §."
    result = parse_clause(text)
    assert result.meta_clauses == ()


def test_parse_clause_meta_clauses_populated_for_commencement():
    """'tulee voimaan' in johtolause → meta_clauses contains commencement node."""
    text = "muutetaan 5 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    result = parse_clause(text)
    assert len(result.meta_clauses) == 1
    assert isinstance(result.meta_clauses[0], SurfaceMetaClause)
    assert result.meta_clauses[0].kind == MetaClauseKind.COMMENCEMENT


def test_parse_clause_structural_parse_still_works():
    """Structural parse still produces clause_ast alongside meta extraction."""
    text = "muutetaan 5 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    result = parse_clause(text)
    # clause_ast is populated (not None) for a structural johtolause
    assert result.clause_ast is not None
    # surface_clause is populated
    assert result.surface_clause is not None


# token_tape tests removed — Phase 6 ClauseParseResult does not carry
# token_tape (that is Phase 2 bridge work, not Phase 7 scope).


def test_parse_clause_empty_string():
    """Empty string input → ClauseParseResult with empty meta_clauses."""
    result = parse_clause("")
    assert isinstance(result, ClauseParseResult)
    assert result.meta_clauses == ()


def test_parse_clause_multiple_meta_clauses():
    """Multiple meta clauses → meta_clauses tuple with multiple entries."""
    text = (
        "kumotaan 3 §. "
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025. "
        "Tätä lakia sovelletaan lain voimaantulon jälkeen vireille tulleisiin asioihin."
    )
    result = parse_clause(text)
    assert len(result.meta_clauses) == 2
    kinds = {c.kind for c in result.meta_clauses}
    assert MetaClauseKind.COMMENCEMENT in kinds
    assert MetaClauseKind.TRANSITION in kinds


# ---------------------------------------------------------------------------
# Phase 7 integration: meta clauses flow through resolve → lower pipeline
# ---------------------------------------------------------------------------


def test_meta_clauses_in_clause_ast():
    """Meta clauses injected into SurfaceClause produce MetaClause in ClauseAST."""
    from lawvm.core.clause_ast import MetaClause as ClauseASTMetaClause

    text = "muutetaan 5 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    result = parse_clause(text)
    # ClauseAST contains MetaClause nodes alongside structural RefAmend nodes
    all_nodes = [n for vg in result.clause_ast.verb_groups for n in vg.nodes]
    meta_in_ast = [n for n in all_nodes if isinstance(n, ClauseASTMetaClause)]
    assert len(meta_in_ast) == 1
    assert meta_in_ast[0].kind == MetaClauseKind.COMMENCEMENT
    assert "voimaan" in meta_in_ast[0].raw_text.lower()


def test_meta_clauses_in_clause_ast_structural_preserved():
    """Structural nodes are not lost when meta clauses are injected."""
    from lawvm.core.clause_ast import RefAmend

    text = "kumotaan 3 §, muutetaan 5 §. Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    result = parse_clause(text)
    all_nodes = [n for vg in result.clause_ast.verb_groups for n in vg.nodes]
    ref_amends = [n for n in all_nodes if isinstance(n, RefAmend)]
    # At least the structural ops are present
    assert len(ref_amends) >= 2


def test_pure_meta_text_produces_clause_ast_meta_nodes():
    """Pure meta text with no structural verb produces MetaClause in ClauseAST."""
    from lawvm.core.clause_ast import MetaClause as ClauseASTMetaClause

    text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    result = parse_clause(text)
    all_nodes = [n for vg in result.clause_ast.verb_groups for n in vg.nodes]
    meta_in_ast = [n for n in all_nodes if isinstance(n, ClauseASTMetaClause)]
    assert len(meta_in_ast) == 1
    assert meta_in_ast[0].kind == MetaClauseKind.COMMENCEMENT


def test_meta_clauses_in_resolved_surface():
    """Meta clauses appear as ResolvedMetaClause in the resolved surface."""
    from lawvm.finland.johtolause.surface_resolve import (
        ResolvedSurfaceClause,
    )

    text = "muutetaan 5 §. Tämä laki on voimassa 31 päivään joulukuuta 2026."
    result = parse_clause(text)
    assert result.resolved is not None
    assert isinstance(result.resolved, ResolvedSurfaceClause)
    resolved_meta = list(result.resolved.meta_clauses)
    assert len(resolved_meta) == 1
    assert resolved_meta[0].kind == MetaClauseKind.EXPIRY
