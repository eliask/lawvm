"""Tests for the canonical SurfaceClause serializer + parser differential harness.

These pin the harness behaviour the rewrite is gated on: canonicalization is
identity-free and stable, it captures the WHOLE contract (verb codes, node
fields, witness rule_ids + spans, source_text, consumed_count), and the diff
reports field-path-qualified deltas so a divergence is never silent.
"""

from __future__ import annotations

from lawvm.finland.johtolause.grammar.diff import (
    canonicalize_surface_model,
    compare_canonical,
    compare_surface_models,
    compare_surface_parsers,
    parse_text_with,
)
from lawvm.finland.johtolause.surface_parse import parse as surface_parse

_AMEND = "Muutetaan lain 5 §:n 1 momentti seuraavasti:"
_REPEAL = "Kumotaan lain 7 §."


def _canon(text: str) -> dict:
    return canonicalize_surface_model(parse_text_with(text, surface_parse))


# --- canonicalization ---
def test_canonical_is_json_safe_and_captures_full_contract() -> None:
    c = _canon(_AMEND)
    # whole-contract keys present
    assert set(c) == {
        "verb_groups",
        "meta_clauses",
        "text_amend_clauses",
        "target_version_bindings",
        "source_text",
        "consumed_count",
    }
    assert c["consumed_count"] == 7
    # enums rendered as Type.NAME (json-safe), tuples as lists
    vg = c["verb_groups"][0]
    assert vg["verb"] == "VerbKind.MUUTTAA"
    node = vg["nodes"][0]
    assert node["kind"] == "TargetKind.SECTION" and node["label"] == "5"
    # witness rule_id + span survive as a plain dict/list
    assert node["witness"]["rule_id"] == "fi.section_ref"
    assert node["witness"]["source_span"] == [2, 7]


def test_canonical_is_stable_across_reparse() -> None:
    # Same text parsed twice -> byte-identical canonical form (identity-free).
    assert _canon(_AMEND) == _canon(_AMEND)


# --- diff reporting ---
def test_identical_parser_reports_no_delta() -> None:
    rep = compare_surface_parsers(_AMEND, surface_parse, surface_parse)
    assert rep.equal and rep.summary() == "no delta"


def test_diff_reports_field_path_for_each_divergence() -> None:
    rep = compare_surface_models(
        parse_text_with(_REPEAL, surface_parse),
        parse_text_with(_AMEND, surface_parse),
    )
    assert not rep.equal
    joined = "\n".join(rep.deltas)
    # the verb, the label, and consumed_count each surface with their path
    assert "model.verb_groups[0].verb:" in joined
    assert "model.verb_groups[0].nodes[0].label:" in joined
    assert "model.consumed_count:" in joined


def test_diff_detects_a_single_mutated_field() -> None:
    a = _canon(_AMEND)
    b = _canon(_AMEND)
    b["consumed_count"] = 999
    rep = compare_canonical(a, b)
    assert rep.deltas == ["model.consumed_count: 7 != 999"]


def test_diff_reports_list_length_mismatch() -> None:
    a = {"xs": [1, 2, 3]}
    b = {"xs": [1, 2]}
    rep = compare_canonical(a, b)
    assert "model.xs: length 3 != 2" in rep.deltas
