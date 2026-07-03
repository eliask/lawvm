"""Conformance + unit test for U.K. legislation's ``LabelAlgebra`` (#186, §4.2 item 4).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. Mirrors ``tests/test_label_algebra.py`` (the EE conformance test) and
``tests/test_label_algebra_fi.py``.

WHAT THIS GUARDS. ``uk_legislation/label_algebra.UK_LABEL_ALGEBRA`` is a DECLARED
SPEC of the U.K. inserted-provision label calculus (numeric stem + ``4A`` / ``4ZA``
letter insert). This test makes the algebra *first-class* WITHOUT a grafter
control-flow refactor: for each of the four §4.2 operations it asserts the declared
algebra reproduces UK's ACTUAL label decisions — bound to the SAME code UK uses
(``ordering._label_sort_key`` for order, ``canonicalize._clean_num`` for collision,
``source_parent_payloads._next_same_stem_alnum_label`` for the successor). If UK's
label logic later drifts from the declaration, THIS test FAILS — which is what makes
the parallel-first algebra a faithful mirror rather than dead documentation.

HONEST DIVERGENCE (ZA < A). The #186 brief names the UK order as ``ZA < A``. UK's
REAL ``_label_sort_key`` is a plain LEXICOGRAPHIC sort, so the actual runtime order
is ``4 < 4A < 4B < 4ZA``. The tests below pin the REAL behaviour (order() == the
``_label_sort_key`` compare), NOT the brief — a declared-but-honest mirror. See the
module's HONEST DIVERGENCE note.

It also pins the neutral-type invariants (``core/label_algebra``): parse
round-trip, order-relation laws, successor correctness, collision detection, and
the construction fail-loud.

PARALLEL-FIRST. The grafter is NOT routed through the algebra; this test is the
guardrail that keeps the declared algebra honest until any routing lands.
"""

from __future__ import annotations

import functools

import pytest

from lawvm.core.label_algebra import LabelAlgebra, ParsedLabel
from lawvm.uk_legislation.canonicalize import _clean_num
from lawvm.uk_legislation.label_algebra import UK_LABEL_ALGEBRA, uk_parse_label
from lawvm.uk_legislation.ordering import _label_sort_key
from lawvm.uk_legislation.source_parent_payloads import _next_same_stem_alnum_label


# ---------------------------------------------------------------------------
# UK conformance: the declared algebra mirrors UK's ACTUAL label code
# ---------------------------------------------------------------------------


class TestUKParseConformance:
    """parse() decomposes exactly as UK's real provision primitives do."""

    @pytest.mark.parametrize(
        "raw, expect_stem, expect_components",
        [
            ("4", "4", ()),
            ("4A", "4", (("letter", "a"),)),
            ("4a", "4", (("letter", "a"),)),
            ("(4A)", "4", (("letter", "a"),)),  # parenthesized surface
            ("4B", "4", (("letter", "b"),)),
            ("4ZA", "4", (("letter", "za"),)),  # double-letter interstitial
            ("12", "12", ()),
        ],
    )
    def test_parse_stem_and_components(
        self, raw: str, expect_stem: str, expect_components: tuple
    ) -> None:
        parsed = UK_LABEL_ALGEBRA.parse(raw)
        assert parsed.stem == expect_stem
        assert parsed.components == expect_components

    @pytest.mark.parametrize("raw", ["4", "4A", "4a", "(4A)", "4B", "4ZA", "12"])
    def test_sort_key_is_uk_label_sort_key(self, raw: str) -> None:
        """The parsed sort key IS UK's ``_label_sort_key`` — the algebra threads
        UK's real order primitive, not a re-implementation."""
        assert UK_LABEL_ALGEBRA.parse(raw).sort_key == _label_sort_key(raw)

    @pytest.mark.parametrize("raw", ["4", "4A", "4a", "(4A)", "4B", "4ZA", "12"])
    def test_collision_key_is_uk_clean_num(self, raw: str) -> None:
        """The collision key IS UK's ``_clean_num`` (the provision identity token)
        of the label."""
        assert UK_LABEL_ALGEBRA.parse(raw).collision_key == _clean_num(raw)


class TestUKOrderConformance:
    """order() reproduces UK's authoritative (lexicographic) sibling order."""

    def test_letter_suffix_sorts_after_stem(self) -> None:
        # UK's REAL order is lexicographic: 4 < 4A < 4B < 4ZA (NOT the brief's
        # ZA < A interstitial priority — see the module HONEST DIVERGENCE note).
        labels = ["4ZA", "4B", "4", "4A"]
        parsed = [UK_LABEL_ALGEBRA.parse(x) for x in labels]
        ordered = sorted(parsed, key=functools.cmp_to_key(UK_LABEL_ALGEBRA.order))
        assert [p.raw for p in ordered] == ["4", "4A", "4B", "4ZA"]

    def test_order_agrees_with_uk_label_sort_key(self) -> None:
        """order() is EXACTLY the compare UK's ``_label_sort_key`` induces on any
        pair (the UK provision insert ordering)."""
        labels = ["4", "4A", "4B", "4ZA", "5", "12", "12A"]
        parsed = [UK_LABEL_ALGEBRA.parse(x) for x in labels]
        for a in parsed:
            for b in parsed:
                ka, kb = _label_sort_key(a.raw), _label_sort_key(b.raw)
                expected = (ka > kb) - (ka < kb)
                assert UK_LABEL_ALGEBRA.order(a, b) == expected


class TestUKSuccessorConformance:
    """successor_set() reproduces UK's fresh letter-sibling calculus."""

    def test_first_letter_after_bare_stem(self) -> None:
        # Insert a letter sibling after §4 (no suffix): fresh label is 4a.
        fresh = UK_LABEL_ALGEBRA.successor_set([], anchor="4")
        assert fresh.collision_key == "4a"
        assert fresh.stem == "4"
        assert fresh.components == (("letter", "a"),)

    def test_next_letter_past_greatest(self) -> None:
        # §4a present → fresh is 4b, one letter past.
        fresh = UK_LABEL_ALGEBRA.successor_set([], anchor="4a")
        assert fresh.collision_key == "4b"
        assert fresh.components == (("letter", "b"),)

    def test_successor_is_uk_next_same_stem_alnum_label(self) -> None:
        """The fresh label IS UK's ``_next_same_stem_alnum_label`` of the anchor."""
        for anchor in ["4", "4a", "12", "3b"]:
            fresh = UK_LABEL_ALGEBRA.successor_set([], anchor=anchor)
            expected = _next_same_stem_alnum_label(_clean_num(anchor))
            assert fresh.collision_key == _clean_num(expected)

    def test_successor_infers_greatest_sibling_without_anchor(self) -> None:
        existing = [UK_LABEL_ALGEBRA.parse("4"), UK_LABEL_ALGEBRA.parse("4A")]
        fresh = UK_LABEL_ALGEBRA.successor_set(existing)
        assert fresh.collision_key == "4b"

    def test_successor_fails_loud_on_exhausted_series(self) -> None:
        # UK has no admissible fresh single-letter label past 'z'
        # (_next_same_stem_alnum_label -> '').
        with pytest.raises(ValueError):
            UK_LABEL_ALGEBRA.successor_set([], anchor="4z")

    def test_successor_fails_loud_without_anchor_or_siblings(self) -> None:
        with pytest.raises(ValueError):
            UK_LABEL_ALGEBRA.successor_set([])

    def test_fresh_successor_does_not_collide(self) -> None:
        existing = [UK_LABEL_ALGEBRA.parse("4"), UK_LABEL_ALGEBRA.parse("4A")]
        fresh = UK_LABEL_ALGEBRA.successor_set(existing, anchor="4A")
        assert not UK_LABEL_ALGEBRA.collides(fresh, existing)


class TestUKCollisionConformance:
    """collides() is UK's ``_clean_num`` (canonical-token) identity."""

    def test_surface_variants_collide(self) -> None:
        # "(4A)", "4A" and the lowercase "4a" are the SAME slot — they collide
        # (canonical-token equality).
        existing = [UK_LABEL_ALGEBRA.parse("4A")]
        assert UK_LABEL_ALGEBRA.collides(UK_LABEL_ALGEBRA.parse("(4A)"), existing)
        assert UK_LABEL_ALGEBRA.collides(UK_LABEL_ALGEBRA.parse("4a"), existing)

    def test_distinct_labels_do_not_collide(self) -> None:
        existing = [UK_LABEL_ALGEBRA.parse("4"), UK_LABEL_ALGEBRA.parse("4A")]
        assert not UK_LABEL_ALGEBRA.collides(UK_LABEL_ALGEBRA.parse("5"), existing)
        assert not UK_LABEL_ALGEBRA.collides(UK_LABEL_ALGEBRA.parse("4B"), existing)

    def test_stem_and_letter_are_distinct_slots(self) -> None:
        # 4 and 4A are DIFFERENT slots (stem family, not identity).
        assert not UK_LABEL_ALGEBRA.collides(
            UK_LABEL_ALGEBRA.parse("4"), [UK_LABEL_ALGEBRA.parse("4A")]
        )


# ---------------------------------------------------------------------------
# Neutral-type invariants (core/label_algebra), jurisdiction-agnostic
# ---------------------------------------------------------------------------


class TestNeutralTypeInvariants:
    def test_parse_round_trip_raw_preserved(self) -> None:
        for raw in ["4", "4A", "4ZA"]:
            assert UK_LABEL_ALGEBRA.parse(raw).raw == raw

    def test_order_irreflexive_and_antisymmetric(self) -> None:
        labels = [UK_LABEL_ALGEBRA.parse(x) for x in ["4", "4A", "5", "3a"]]
        for a in labels:
            assert UK_LABEL_ALGEBRA.order(a, a) == 0
        for a in labels:
            for b in labels:
                assert UK_LABEL_ALGEBRA.order(a, b) == -UK_LABEL_ALGEBRA.order(b, a)

    def test_order_transitive_and_total(self) -> None:
        labels = [UK_LABEL_ALGEBRA.parse(x) for x in ["4", "4A", "4B", "5"]]
        for a in labels:
            for b in labels:
                assert UK_LABEL_ALGEBRA.order(a, b) in (-1, 0, 1)
        a, b, c, d = labels
        assert UK_LABEL_ALGEBRA.order(a, b) < 0
        assert UK_LABEL_ALGEBRA.order(b, c) < 0
        assert UK_LABEL_ALGEBRA.order(a, c) < 0
        assert UK_LABEL_ALGEBRA.order(a, d) < 0

    def test_collides_empty_existing_is_false(self) -> None:
        assert not UK_LABEL_ALGEBRA.collides(UK_LABEL_ALGEBRA.parse("4"), [])

    def test_parsed_label_is_frozen_hashable(self) -> None:
        p = UK_LABEL_ALGEBRA.parse("4A")
        assert isinstance(p, ParsedLabel)
        assert {p, p} == {p}
        assert p == UK_LABEL_ALGEBRA.parse("4A")

    def test_construction_fails_loud_on_empty_jurisdiction(self) -> None:
        with pytest.raises(ValueError):
            LabelAlgebra(
                jurisdiction="",
                parse_fn=uk_parse_label,
                successor_fn=lambda existing, anchor: uk_parse_label("1"),
            )
