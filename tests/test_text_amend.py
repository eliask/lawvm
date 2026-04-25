"""Tests for SurfaceTextAmend grammar — text-level word/phrase substitution.

Covers:
  - _extract_text_amend_clauses: regex-based extraction from raw text
  - Pipeline integration: SurfaceTextAmend → ResolvedTextAmend → TextAmend
  - parse_clause(): text amend nodes appear in ClauseAST
  - Target section/momentti scoping
  - Unscoped (law-level) text amends
  - Curly/straight quote handling
"""

from __future__ import annotations

from lawvm.core.semantic_types import StructuralAction
from lawvm.finland.johtolause.api import parse_clause, _extract_text_amend_clauses
from lawvm.finland.johtolause.surface_model import SurfaceTextAmend


# ---------------------------------------------------------------------------
# _extract_text_amend_clauses: basic extraction
# ---------------------------------------------------------------------------


def test_extract_sana_korvataan_sanalla():
    """Single word replacement: sana "X" korvataan sanalla "Y"."""
    text = '5 §:n 2 momentissa sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    ta = results[0]
    assert isinstance(ta, SurfaceTextAmend)
    assert ta.old_text == "lääninhallitus"
    assert ta.new_text == "aluehallintovirasto"
    assert ta.target is not None
    assert ta.target.label == "5"
    assert ta.target.sub_refs[0].momentti == 2


def test_extract_sanat_korvataan_sanoilla():
    """Multi-word replacement: sanat "X" korvataan sanoilla "Y"."""
    text = 'sanat "kauppa- ja teollisuusministeriö" korvataan sanoilla "työ- ja elinkeinoministeriö"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    ta = results[0]
    assert ta.old_text == "kauppa- ja teollisuusministeriö"
    assert ta.new_text == "työ- ja elinkeinoministeriö"
    # No target specified → law-level text amend
    assert ta.target is None


def test_extract_section_inessive():
    """Section in inessive: N §:ssä sana "X" korvataan sanalla "Y"."""
    text = '3 §:ssä sana "terveyskeskus" korvataan sanalla "hyvinvointialue"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    ta = results[0]
    assert ta.old_text == "terveyskeskus"
    assert ta.new_text == "hyvinvointialue"
    assert ta.target is not None
    assert ta.target.label == "3"


def test_extract_curly_quotes():
    """Curly quotes (\u201c...\u201d) are recognized."""
    text = "5 §:n 2 momentissa sana \u201clääninhallitus\u201d korvataan sanalla \u201caluehallintovirasto\u201d"
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    assert results[0].old_text == "lääninhallitus"
    assert results[0].new_text == "aluehallintovirasto"


def test_extract_empty_string():
    """Empty string → empty list."""
    assert _extract_text_amend_clauses("") == []


def test_extract_no_text_amend():
    """Pure structural johtolause → empty list."""
    text = "muutetaan 3, 5 ja 7 §."
    assert _extract_text_amend_clauses(text) == []


def test_extract_witness_rule_id():
    """Witness carries fi.text_amend_sana rule_id."""
    text = 'sana "vanha" korvataan sanalla "uusi"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    assert results[0].witness is not None
    assert results[0].witness.rule_id == "fi.text_amend_sana"


# ---------------------------------------------------------------------------
# Pipeline integration: parse_clause produces TextAmend in ClauseAST
# ---------------------------------------------------------------------------


def test_text_amend_in_clause_ast():
    """Text amend flows through pipeline to TextAmend in ClauseAST."""
    from lawvm.core.clause_ast import TextAmend as ClauseASTTextAmend

    text = 'muutetaan 5 §:n 2 momentissa sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"'
    result = parse_clause(text)
    all_nodes = [n for vg in result.clause_ast.verb_groups for n in vg.nodes]
    text_amends = [n for n in all_nodes if isinstance(n, ClauseASTTextAmend)]
    assert len(text_amends) >= 1
    ta = text_amends[0]
    assert ta.action == StructuralAction.TEXT_REPLACE
    assert ta.text_patch is not None
    assert ta.text_patch.selector.match_text == "lääninhallitus"
    assert ta.text_patch.replacement == "aluehallintovirasto"


def test_text_amend_in_resolved_surface():
    """Text amend appears as ResolvedTextAmend in the resolved surface."""
    from lawvm.finland.johtolause.surface_resolve import (
        ResolvedSurfaceClause,
    )

    text = 'muutetaan 5 §:n 2 momentissa sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"'
    result = parse_clause(text)
    assert result.resolved is not None
    assert isinstance(result.resolved, ResolvedSurfaceClause)
    resolved_ta = list(result.resolved.text_amend_clauses)
    assert len(resolved_ta) >= 1
    assert resolved_ta[0].old_text == "lääninhallitus"
    assert resolved_ta[0].new_text == "aluehallintovirasto"


def test_text_amend_structural_ops_preserved():
    """Structural ops are preserved alongside text amend injection."""
    text = 'muutetaan 5 §:n 2 momentissa sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"'
    result = parse_clause(text)
    # The structural parse should still produce a section ref
    assert len(result.parsed_ops) >= 1


def test_text_amend_unscoped_in_clause_ast():
    """Unscoped text amend inherits the surrounding structural target."""
    from lawvm.core.clause_ast import TextAmend as ClauseASTTextAmend

    text = 'muutetaan 3 §. sanat "vanha" korvataan sanoilla "uusi"'
    result = parse_clause(text)
    all_nodes = [n for vg in result.clause_ast.verb_groups for n in vg.nodes]
    text_amends = [n for n in all_nodes if isinstance(n, ClauseASTTextAmend)]
    assert len(text_amends) >= 1
    ta = text_amends[0]
    assert ta.text_patch.selector.match_text == "vanha"
    assert ta.text_patch.replacement == "uusi"
    assert ta.target.path == (("section", "3"),)


# ---------------------------------------------------------------------------
# Pro audit #8: item-level scope, section label normalisation, å/ä/ö suffix
# ---------------------------------------------------------------------------


def test_extract_kohta_scope_captured():
    """Item ref in 'N §:n M momentin K kohdassa' is captured as SurfaceSubRef.item."""
    text = '5 §:n 2 momentin 3 kohdassa sana "vanha" korvataan sanalla "uusi"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    ta = results[0]
    assert ta.target is not None
    assert ta.target.label == "5"
    assert len(ta.target.sub_refs) == 1
    sr = ta.target.sub_refs[0]
    assert sr.momentti == 2
    assert sr.item == "3"


def test_extract_kohdan_variant_captured():
    """'momentin N kohdan' genitive variant also captures item."""
    text = '7 §:n 1 momentin 2 kohdan sana "vanha" korvataan sanalla "uusi"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    ta = results[0]
    assert ta.target.sub_refs[0].momentti == 1
    assert ta.target.sub_refs[0].item == "2"


def test_extract_momentti_without_kohta_has_empty_item():
    """When only momentti is present, item is empty string (not None)."""
    text = '5 §:n 2 momentissa sana "vanha" korvataan sanalla "uusi"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    sr = results[0].target.sub_refs[0]
    assert sr.momentti == 2
    assert sr.item == ""


def test_extract_section_label_normalised_space():
    """Section label with internal space ('5 a §') is normalised to '5a'."""
    text = '5 a §:ssä sana "vanha" korvataan sanalla "uusi"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    assert results[0].target.label == "5a"


def test_extract_section_label_normalised_no_space():
    """Section label without space ('5a §') is also accepted and left as '5a'."""
    text = '5a §:ssä sana "vanha" korvataan sanalla "uusi"'
    results = _extract_text_amend_clauses(text)
    assert len(results) == 1
    assert results[0].target.label == "5a"
