"""Tests for parse witness coverage and rule catalog (rule_registry)."""
from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.parse_witness import ParseWitness, ResolutionWitness
from lawvm.finland.johtolause.rule_registry import ALL_RULES, all_rule_ids, get_rule


class TestRuleCatalogInventory:
    """Test the rule catalog is well-formed."""

    def test_all_rules_registered(self):
        assert len(ALL_RULES) >= 25, f"Expected 25+ rules, got {len(ALL_RULES)}"

    def test_all_rule_ids_returns_frozenset(self):
        ids = all_rule_ids()
        assert isinstance(ids, frozenset)
        assert "fi.section_ref" in ids

    def test_get_rule_returns_rule(self):
        rule = get_rule("fi.section_ref")
        assert rule is not None
        assert rule.category == "structural"
        assert rule.description != ""

    def test_get_rule_returns_none_for_unknown(self):
        assert get_rule("nonexistent.rule") is None

    def test_rule_categories(self):
        categories = {r.category for r in ALL_RULES.values()}
        assert "structural" in categories
        assert "insertion" in categories
        assert "resolution" in categories
        assert "renumber" in categories
        assert "sub_ref" in categories

    def test_all_rules_have_valid_prefix(self):
        _allowed = ("fi.", "meta_parse:")
        for rule_id in ALL_RULES:
            assert any(rule_id.startswith(p) for p in _allowed), (
                f"Rule ID {rule_id!r} missing valid prefix (expected one of {_allowed!r})"
            )

    def test_legacy_id_lookup(self):
        """Legacy IDs (old construction_rules format) resolve via get_rule."""
        rule = get_rule("target.section_ref")
        assert rule is not None
        assert rule.rule_id == "fi.section_ref"

        rule2 = get_rule("resolution.backref_singular")
        assert rule2 is not None
        assert rule2.rule_id == "fi.backref_singular"


class TestParseWitnessCoverage:
    """Verify that all parsed ops get witnesses across the curated corpus."""

    def test_100_percent_witness_coverage(self):
        from lawvm.finland.johtolause.peg3 import parse, tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from tests.fixtures.fi_curated_cases import CURATED_CASES

        missing = []
        total = 0
        for case in CURATED_CASES:
            tokens = tokenize(cast(str, case["text"]))
            filtered = apply_annotations(tokens)
            ops = parse(filtered)
            for i, op in enumerate(ops):
                total += 1
                if op.witness is None:
                    missing.append(f"[{case['name']}] op {i}: {op.code()}")

        assert not missing, (
            f"{len(missing)}/{total} ops missing witness:\n" + "\n".join(missing[:20])
        )

    def test_witnesses_have_valid_source_spans(self):
        from lawvm.finland.johtolause.peg3 import parse, tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from tests.fixtures.fi_curated_cases import CURATED_CASES

        invalid = []
        for case in CURATED_CASES:
            tokens = tokenize(cast(str, case["text"]))
            filtered = apply_annotations(tokens)
            ops = parse(filtered)
            for i, op in enumerate(ops):
                witness = op.witness
                if witness is not None:
                    span = cast(Any, witness).source_span
                    if span:
                        start, end = span
                        if start < 0 or end < start:
                            invalid.append(f"[{case['name']}] op {i}: span=({start},{end})")

        assert not invalid, "Invalid spans:\n" + "\n".join(invalid)

    def test_witnesses_reference_known_rule_ids(self):
        """Every witness rule_id should be in the rule catalog."""
        from lawvm.finland.johtolause.peg3 import parse, tokenize
        from lawvm.finland.johtolause.scan import apply_annotations
        from tests.fixtures.fi_curated_cases import CURATED_CASES

        known = all_rule_ids()
        unknown = set()
        for case in CURATED_CASES:
            tokens = tokenize(cast(str, case["text"]))
            filtered = apply_annotations(tokens)
            for op in parse(filtered):
                witness = op.witness
                if witness is not None:
                    rid = cast(Any, witness).rule_id
                    if rid not in known:
                        unknown.add(rid)

        assert not unknown, f"Unknown rule IDs: {unknown}"


def test_parse_witness_rejects_empty_rule_id() -> None:
    with pytest.raises(ValueError, match="rule_id must be non-empty"):
        ParseWitness(rule_id="")


def test_parse_witness_rejects_invalid_source_span() -> None:
    with pytest.raises(ValueError, match="source_span must be a non-empty half-open token span"):
        ParseWitness(rule_id="fi.section_ref", source_span=(2, 2))


def test_resolution_witness_rejects_empty_rule_id() -> None:
    with pytest.raises(ValueError, match="resolver_rule_id must be non-empty"):
        ResolutionWitness(resolver_rule_id="")


def test_resolution_witness_rejects_invalid_antecedent_span() -> None:
    with pytest.raises(ValueError, match="antecedent_span must be a non-empty half-open token span"):
        ResolutionWitness(resolver_rule_id="resolution.backref", antecedent_span=(-1, 3))
