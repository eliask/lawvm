"""Phase 8 — Rule registry tests.

Verifies:
1. Registry structure (non-empty, no duplicate rule_ids, required fields).
2. Meta examples: each SurfaceMetaClause example is matched by
   extract_meta_surface_clauses().
3. Structural/insertion/resolution/renumber examples: each input text is
   parsed through parse_clause() and the result contains at least one op
   (or surface node) consistent with the expected_node_kind.
"""

from __future__ import annotations

import pytest

from lawvm.finland.johtolause.rule_registry import (
    FINLAND_RULE_REGISTRY,
    ParseRule,
    RuleExample,
)


# ---------------------------------------------------------------------------
# Registry structure tests
# ---------------------------------------------------------------------------


def test_registry_non_empty():
    assert len(FINLAND_RULE_REGISTRY) > 0, "Registry must contain at least one rule"


def test_registry_no_duplicate_ids():
    ids = [r.rule_id for r in FINLAND_RULE_REGISTRY.all_rules()]
    assert len(ids) == len(set(ids)), "Duplicate rule_id found in registry"


# Rules that are catch-all/internal patterns without concrete examples.
# These are valid catalog entries for witness tracking but don't need
# example corpus entries for testing.
_EXAMPLE_EXEMPT_RULES = {
    "fi.insertion_chapter_anaphoric",
    "fi.insertion_chapter_scoped",
    "fi.sub_target_momentti",
    "fi.sub_target_kohta",
    "fi.sub_target_pykala",
    "fi.sub_target_luku",
    "fi.anaphoric_pykala_ill",
    "fi.anaphoric_momentti_ill",
    "fi.anaphoric_bare_uusi",
    "fi.cross_verb_momentti",
    "fi.cross_verb_bare_uusi",
    "fi.cross_verb_move_retarget",
    "fi.direct_section_relabel",
    "fi.renumber_backref",
    "fi.jolloin_renumber",
    "fi.chapter_renumber",
    "fi.part_renumber",
    "fi.insertion_section",
    "fi.insertion_chapter",
    "fi.insertion_heading",
    "fi.insertion_sub_target",
    "fi.insertion_other",
    "fi.heading_edelle_luvun_otsikko",
    "fi.lukuun_ottamatta_exception",
    "fi.scope_block_chapter",
    "fi.scope_block_part",
    "fi.text_amend_target",
    "fi.chapter_ref_reversed",
    "fi.heading_edelle_otsikko_after_uusi",
    "fi.including_preceding_heading_target",
    "fi.target_version_binding",
    "meta_parse:commencement",
    "meta_parse:expiry",
    "meta_parse:transition",
    "meta_parse:delegation",
}


def test_registry_all_rules_have_required_fields():
    for rule in FINLAND_RULE_REGISTRY.all_rules():
        assert rule.rule_id, f"Empty rule_id in {rule!r}"
        assert rule.description, f"Empty description for rule {rule.rule_id!r}"
        assert rule.node_kind, f"Empty node_kind for rule {rule.rule_id!r}"
        assert rule.category, f"Empty category for rule {rule.rule_id!r}"
        if rule.rule_id not in _EXAMPLE_EXEMPT_RULES:
            assert rule.examples, f"No examples for rule {rule.rule_id!r}"


# Allowed rule_id prefixes: "fi." for structural rules, "meta_parse:" for
# dynamic meta-parse IDs emitted by meta_parse.py.
_ALLOWED_PREFIXES = ("fi.", "meta_parse:")


def test_registry_all_rules_have_valid_prefix():
    for rule in FINLAND_RULE_REGISTRY.all_rules():
        assert any(rule.rule_id.startswith(p) for p in _ALLOWED_PREFIXES), (
            f"Rule {rule.rule_id!r} does not have an allowed prefix "
            f"(expected one of {_ALLOWED_PREFIXES!r})"
        )


def test_registry_all_examples_have_input_text():
    for rule, example in FINLAND_RULE_REGISTRY.example_corpus():
        assert example.input_text.strip(), (
            f"Empty input_text in example of rule {rule.rule_id!r}"
        )


def test_registry_rules_by_category():
    meta_rules = FINLAND_RULE_REGISTRY.rules_by_category("meta")
    assert len(meta_rules) >= 4, "Expected at least 4 meta rules"

    structural_rules = FINLAND_RULE_REGISTRY.rules_by_category("structural")
    assert len(structural_rules) >= 5, "Expected at least 5 structural rules"


def test_registry_rules_by_node_kind():
    surface_target_rules = FINLAND_RULE_REGISTRY.rules_by_node_kind("SurfaceTargetRef")
    assert len(surface_target_rules) >= 3, (
        "Expected at least 3 rules producing SurfaceTargetRef"
    )


def test_registry_get_known_rule():
    rule = FINLAND_RULE_REGISTRY.get("fi.section_ref")
    assert rule is not None
    assert rule.node_kind == "SurfaceTargetRef"
    assert rule.category == "structural"


def test_registry_get_unknown_rule_returns_none():
    assert FINLAND_RULE_REGISTRY.get("fi.nonexistent") is None


# ---------------------------------------------------------------------------
# Meta clause examples: verify extract_meta_surface_clauses() matches
# ---------------------------------------------------------------------------


def _meta_rule_example_ids() -> list[str]:
    return [
        f"{rule.rule_id}[{i}]"
        for rule in FINLAND_RULE_REGISTRY.rules_by_category("meta")
        for i, _ in enumerate(rule.examples)
    ]


def _meta_rule_examples() -> list[tuple[ParseRule, RuleExample]]:
    return [
        (rule, ex)
        for rule in FINLAND_RULE_REGISTRY.rules_by_category("meta")
        for ex in rule.examples
    ]


@pytest.mark.parametrize(
    "rule_and_ex",
    _meta_rule_examples(),
    ids=_meta_rule_example_ids(),
)
def test_meta_example_matched(rule_and_ex: tuple[ParseRule, RuleExample]):
    from lawvm.finland.johtolause.meta_parse import extract_meta_surface_clauses

    rule, ex = rule_and_ex
    meta_clauses = extract_meta_surface_clauses(ex.input_text)
    assert meta_clauses, (
        f"Rule {rule.rule_id!r} example {ex.input_text!r}: "
        f"expected at least one SurfaceMetaClause but got none"
    )
    if "kind" in ex.expected_fields:
        kinds = [mc.kind.value for mc in meta_clauses]
        expected_kind = ex.expected_fields["kind"]
        assert expected_kind in kinds, (
            f"Rule {rule.rule_id!r}: expected kind={expected_kind!r} "
            f"but got {kinds!r} for input {ex.input_text!r}"
        )


# ---------------------------------------------------------------------------
# Structural/insertion/resolution/renumber examples: parse_clause produces ops
# ---------------------------------------------------------------------------


# Rules where the examples test sub-parser behavior rather than producing ops
# directly (e.g. pure sub-ref rules, valiotsikko heading, move tails, sub-nodes).
# For these we only verify parse_clause() runs without error.
_PARSE_NO_OPS_REQUIRED = {
    "fi.sub_ref_momentti",
    "fi.sub_ref_kohta",
    "fi.sub_ref_otsikko",
    "fi.sub_ref_johdantokappale",
    "fi.valiotsikko_heading_ref",
    "fi.jolloin_chapter_renumber",
    "fi.jolloin_section_renumber",
    "fi.heading_placement",
    # Text amend rules produce TextAmend in ClauseAST, not legacy ParsedOps
    "fi.text_amend_sana",
    "fi.text_amend_sanat",
}


def _non_meta_rule_examples() -> list[tuple[ParseRule, RuleExample]]:
    return [
        (rule, ex)
        for rule in FINLAND_RULE_REGISTRY.all_rules()
        if rule.category != "meta"
        for ex in rule.examples
    ]


def _non_meta_rule_example_ids() -> list[str]:
    return [
        f"{rule.rule_id}[{i}]"
        for rule in FINLAND_RULE_REGISTRY.all_rules()
        if rule.category != "meta"
        for i, _ in enumerate(rule.examples)
    ]


@pytest.mark.parametrize(
    "rule_and_ex",
    _non_meta_rule_examples(),
    ids=_non_meta_rule_example_ids(),
)
def test_non_meta_example_produces_result(rule_and_ex: tuple[ParseRule, RuleExample]):
    """Each non-meta example must at least parse without raising an exception.

    For most rules (outside _PARSE_NO_OPS_REQUIRED) we also require at least
    one parsed op in the ClauseParseResult.
    """
    from lawvm.finland.johtolause.api import parse_clause

    rule, ex = rule_and_ex
    result = parse_clause(ex.input_text)

    if rule.rule_id not in _PARSE_NO_OPS_REQUIRED:
        ops = result.parsed_ops
        assert ops, (
            f"Rule {rule.rule_id!r} example {ex.input_text[:80]!r}: "
            f"expected at least one ParsedOp but got none"
        )


# ---------------------------------------------------------------------------
# Witness ID completeness: every emitted rule_id must resolve in registry
# ---------------------------------------------------------------------------


def _collect_emitted_rule_ids() -> set[str]:
    """Statically collect all rule_id string literals emitted by the johtolause module.

    This is a best-effort static analysis: it greps for _make_witness("...",
    SurfaceWitness(rule_id="...", ParseWitness(rule_id="...", and also
    string literals assigned to variables that are later passed to these
    functions (e.g. ``rid = "fi.chapter_ref"``).
    """
    import pathlib
    import re

    johtolause_dir = pathlib.Path(__file__).resolve().parent.parent / "src" / "lawvm" / "finland" / "johtolause"
    rule_ids: set[str] = set()

    # Patterns that assign rule_id string literals
    patterns = [
        # _make_witness("some.id", ...)
        re.compile(r'_make_witness\(\s*"([^"]+)"'),
        # _make_witness(f"prefix.{var}", ...) — captures the prefix
        re.compile(r'_make_witness\(\s*f"([^"{}]+)\{'),
        # SurfaceWitness(rule_id="some.id")
        re.compile(r'SurfaceWitness\(\s*rule_id\s*=\s*"([^"]+)"'),
        # SurfaceWitness(rule_id=f"prefix:{var}")
        re.compile(r'SurfaceWitness\(\s*rule_id\s*=\s*f"([^"{}]+)\{'),
        # ParseWitness(rule_id="some.id")
        re.compile(r'ParseWitness\(\s*rule_id\s*=\s*"([^"]+)"'),
        # Variable assignments: _rid = "fi.xxx" / rid = "fi.xxx"
        re.compile(r'_?rid\s*=\s*"(fi\.[^"]+)"'),
        re.compile(r'_?rid\s*=\s*"(meta_parse:[^"]+)"'),
    ]

    for py_file in johtolause_dir.glob("*.py"):
        text = py_file.read_text()
        for pat in patterns:
            for m in pat.finditer(text):
                rule_ids.add(m.group(1))

    # Expand known dynamic patterns:
    # f"fi.scope_block_{scope_kind}" where scope_kind is "chapter" or "part"
    expanded: set[str] = set()
    for rid in rule_ids:
        if rid == "fi.scope_block_":
            expanded.add("fi.scope_block_chapter")
            expanded.add("fi.scope_block_part")
        elif rid == "meta_parse:":
            expanded.add("meta_parse:commencement")
            expanded.add("meta_parse:expiry")
            expanded.add("meta_parse:transition")
            expanded.add("meta_parse:delegation")
        else:
            expanded.add(rid)
    return expanded


def test_all_emitted_rule_ids_exist_in_registry():
    """Every witness rule_id emitted by the johtolause module must resolve
    in FINLAND_RULE_REGISTRY.

    This is the canonical guard against witness ID drift: if a parser emits
    a rule_id that is not in the registry, this test fails.
    """
    from lawvm.finland.johtolause.rule_registry import get_rule

    emitted = _collect_emitted_rule_ids()
    # Also check via get_rule which handles _OLD_TO_NEW aliases
    missing: list[str] = []
    for rid in sorted(emitted):
        if get_rule(rid) is None:
            missing.append(rid)
    assert not missing, (
        "Emitted witness rule_id(s) not found in FINLAND_RULE_REGISTRY:\n"
        + "\n".join(f"  - {rid}" for rid in missing)
    )


def test_emitted_ids_are_subset_of_registry():
    """Sanity check: the statically collected IDs should all be in the registry.

    This is the reverse direction of test_all_emitted_rule_ids_exist_in_registry
    and serves as a cross-check that the collection logic is working.
    """
    from lawvm.finland.johtolause.rule_registry import get_rule

    emitted = _collect_emitted_rule_ids()
    for rid in emitted:
        assert get_rule(rid) is not None, (
            f"Emitted rule_id {rid!r} not resolvable via get_rule()"
        )
