"""Conformance + unit test for U.S. federal's ``LabelAlgebra`` (#186, §4.2 item 4).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. Mirrors ``tests/test_label_algebra.py`` (the EE conformance test) and
``tests/test_label_algebra_fi.py``.

WHAT THIS GUARDS. ``us_federal/label_algebra.US_LABEL_ALGEBRA`` is a DECLARED SPEC
of the U.S. Code section-label calculus (numeric stem + trailing ``106A`` letter
insert). This test makes the algebra *first-class* WITHOUT a grafter control-flow
refactor: for each of the four §4.2 operations it asserts the declared algebra
reproduces the ACTUAL label decisions — bound to the SAME shared kernel code the US
frontend orders on (``core.tree_ops.default_label_sort_key`` for order,
``normalized_label_key`` for collision). If that label logic later drifts from the
declaration (the sort key changes, the normalization changes), THIS test FAILS —
which is what makes the parallel-first algebra a faithful mirror rather than dead
documentation.

The US successor is SYNTHESIZED (US has no standalone next-section-label helper —
see the module's HONEST GAP note), so its conformance is pinned by binding the
successor's letter progression to the shared sort-key decomposition it is built
from (``106`` → ``106A`` decomposes to ``(106, 'a', 0)``), not to a fictional
primitive.

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
from lawvm.core.tree_ops import default_label_sort_key, normalized_label_key
from lawvm.us_federal.label_algebra import US_LABEL_ALGEBRA, us_parse_label


# ---------------------------------------------------------------------------
# US conformance: the declared algebra mirrors the ACTUAL US label code
# ---------------------------------------------------------------------------


class TestUSParseConformance:
    """parse() decomposes exactly as the shared US section primitives do."""

    @pytest.mark.parametrize(
        "raw, expect_stem, expect_components",
        [
            ("1181", "1181", ()),
            ("106", "106", ()),
            ("106A", "106", (("letter", "a"),)),
            ("106a", "106", (("letter", "a"),)),
            ("106 A", "106", (("letter", "a"),)),  # whitespace surface
            ("106B", "106", (("letter", "b"),)),
            ("1552a", "1552", (("letter", "a"),)),
            ("107", "107", ()),
        ],
    )
    def test_parse_stem_and_components(
        self, raw: str, expect_stem: str, expect_components: tuple
    ) -> None:
        parsed = US_LABEL_ALGEBRA.parse(raw)
        assert parsed.stem == expect_stem
        assert parsed.components == expect_components

    @pytest.mark.parametrize(
        "raw", ["1181", "106", "106A", "106a", "106 A", "106B", "1552a", "107"]
    )
    def test_sort_key_is_default_label_sort_key(self, raw: str) -> None:
        """The parsed sort key IS the shared ``default_label_sort_key`` — the
        algebra threads the real order primitive, not a re-implementation."""
        assert US_LABEL_ALGEBRA.parse(raw).sort_key == default_label_sort_key(raw)

    @pytest.mark.parametrize(
        "raw", ["1181", "106", "106A", "106a", "106 A", "106B", "1552a", "107"]
    )
    def test_collision_key_is_normalized_label_key(self, raw: str) -> None:
        """The collision key IS the shared ``normalized_label_key`` of the label."""
        assert US_LABEL_ALGEBRA.parse(raw).collision_key == normalized_label_key(raw)


class TestUSOrderConformance:
    """order() reproduces the authoritative interleaved section order."""

    def test_letter_suffix_sorts_after_stem_before_next(self) -> None:
        # A letter-suffixed inserted section sorts immediately after its numeric
        # stem, before the next stem — 106 < 106A < 106B < 107.
        labels = ["107", "106B", "106", "106A"]
        parsed = [US_LABEL_ALGEBRA.parse(x) for x in labels]
        ordered = sorted(parsed, key=functools.cmp_to_key(US_LABEL_ALGEBRA.order))
        assert [p.raw for p in ordered] == ["106", "106A", "106B", "107"]

    def test_order_agrees_with_default_label_sort_key(self) -> None:
        """order() is EXACTLY the compare ``default_label_sort_key`` induces on any
        pair (the shared US section insert ordering)."""
        labels = ["106", "106A", "106B", "107", "1181", "1181A", "3", "3a"]
        parsed = [US_LABEL_ALGEBRA.parse(x) for x in labels]
        for a in parsed:
            for b in parsed:
                ka, kb = default_label_sort_key(a.raw), default_label_sort_key(b.raw)
                expected = (ka > kb) - (ka < kb)
                assert US_LABEL_ALGEBRA.order(a, b) == expected


class TestUSSuccessorConformance:
    """successor_set() reproduces the fresh letter-sibling calculus."""

    def test_first_letter_after_bare_stem(self) -> None:
        # Insert a letter sibling after §106 (no suffix): fresh label is 106A.
        fresh = US_LABEL_ALGEBRA.successor_set([], anchor="106")
        assert fresh.collision_key == "106a"
        assert fresh.stem == "106"
        assert fresh.components == (("letter", "a"),)

    def test_next_letter_past_greatest(self) -> None:
        # §106A present → fresh is 106B, one letter past.
        fresh = US_LABEL_ALGEBRA.successor_set([], anchor="106A")
        assert fresh.collision_key == "106b"
        assert fresh.components == (("letter", "b"),)

    def test_successor_letter_matches_shared_sort_key_decomposition(self) -> None:
        """The fresh label's (stem, letter) is EXACTLY the shared
        ``default_label_sort_key`` decomposition of ``stem + next_letter`` — the
        synthesized successor is pinned to the real primitive it is built from."""
        for anchor, expect in [("106", "106A"), ("106A", "106B"), ("3", "3A")]:
            fresh = US_LABEL_ALGEBRA.successor_set([], anchor=anchor)
            assert fresh.sort_key == default_label_sort_key(expect)
            assert fresh.collision_key == normalized_label_key(expect)

    def test_successor_infers_greatest_sibling_without_anchor(self) -> None:
        existing = [US_LABEL_ALGEBRA.parse("106"), US_LABEL_ALGEBRA.parse("106A")]
        fresh = US_LABEL_ALGEBRA.successor_set(existing)
        assert fresh.collision_key == "106b"

    def test_successor_fails_loud_on_exhausted_series(self) -> None:
        # No admissible fresh single-letter label past 'z'.
        with pytest.raises(ValueError):
            US_LABEL_ALGEBRA.successor_set([], anchor="106Z")

    def test_successor_fails_loud_without_anchor_or_siblings(self) -> None:
        with pytest.raises(ValueError):
            US_LABEL_ALGEBRA.successor_set([])

    def test_successor_fails_loud_on_multi_stem_without_anchor(self) -> None:
        existing = [US_LABEL_ALGEBRA.parse("106"), US_LABEL_ALGEBRA.parse("107")]
        with pytest.raises(ValueError):
            US_LABEL_ALGEBRA.successor_set(existing)

    def test_fresh_successor_does_not_collide(self) -> None:
        existing = [US_LABEL_ALGEBRA.parse("106"), US_LABEL_ALGEBRA.parse("106A")]
        fresh = US_LABEL_ALGEBRA.successor_set(existing, anchor="106A")
        assert not US_LABEL_ALGEBRA.collides(fresh, existing)


class TestUSCollisionConformance:
    """collides() is the shared ``normalized_label_key`` identity."""

    def test_surface_variants_collide(self) -> None:
        # "106 A", "106A" and the lowercase "106a" are the SAME section slot.
        existing = [US_LABEL_ALGEBRA.parse("106A")]
        assert US_LABEL_ALGEBRA.collides(US_LABEL_ALGEBRA.parse("106 A"), existing)
        assert US_LABEL_ALGEBRA.collides(US_LABEL_ALGEBRA.parse("106a"), existing)

    def test_distinct_labels_do_not_collide(self) -> None:
        existing = [US_LABEL_ALGEBRA.parse("106"), US_LABEL_ALGEBRA.parse("106A")]
        assert not US_LABEL_ALGEBRA.collides(US_LABEL_ALGEBRA.parse("107"), existing)
        assert not US_LABEL_ALGEBRA.collides(US_LABEL_ALGEBRA.parse("106B"), existing)

    def test_stem_and_letter_are_distinct_slots(self) -> None:
        # 106 and 106A are DIFFERENT slots (stem family, not identity).
        assert not US_LABEL_ALGEBRA.collides(
            US_LABEL_ALGEBRA.parse("106"), [US_LABEL_ALGEBRA.parse("106A")]
        )


# ---------------------------------------------------------------------------
# Neutral-type invariants (core/label_algebra), jurisdiction-agnostic
# ---------------------------------------------------------------------------


class TestNeutralTypeInvariants:
    def test_parse_round_trip_raw_preserved(self) -> None:
        for raw in ["1181", "106A", "106"]:
            assert US_LABEL_ALGEBRA.parse(raw).raw == raw

    def test_order_irreflexive_and_antisymmetric(self) -> None:
        labels = [US_LABEL_ALGEBRA.parse(x) for x in ["106", "106A", "107", "3a"]]
        for a in labels:
            assert US_LABEL_ALGEBRA.order(a, a) == 0
        for a in labels:
            for b in labels:
                assert US_LABEL_ALGEBRA.order(a, b) == -US_LABEL_ALGEBRA.order(b, a)

    def test_order_transitive_and_total(self) -> None:
        labels = [US_LABEL_ALGEBRA.parse(x) for x in ["106", "106A", "106B", "107"]]
        for a in labels:
            for b in labels:
                assert US_LABEL_ALGEBRA.order(a, b) in (-1, 0, 1)
        a, b, c, d = labels
        assert US_LABEL_ALGEBRA.order(a, b) < 0
        assert US_LABEL_ALGEBRA.order(b, c) < 0
        assert US_LABEL_ALGEBRA.order(a, c) < 0
        assert US_LABEL_ALGEBRA.order(a, d) < 0

    def test_collides_empty_existing_is_false(self) -> None:
        assert not US_LABEL_ALGEBRA.collides(US_LABEL_ALGEBRA.parse("106"), [])

    def test_parsed_label_is_frozen_hashable(self) -> None:
        p = US_LABEL_ALGEBRA.parse("106A")
        assert isinstance(p, ParsedLabel)
        assert {p, p} == {p}
        assert p == US_LABEL_ALGEBRA.parse("106A")

    def test_construction_fails_loud_on_empty_jurisdiction(self) -> None:
        with pytest.raises(ValueError):
            LabelAlgebra(
                jurisdiction="",
                parse_fn=us_parse_label,
                successor_fn=lambda existing, anchor: us_parse_label("1"),
            )
