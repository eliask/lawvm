"""Conformance + unit test for the ``LabelAlgebra`` seam (#186, §4.2 item 4).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4.

WHAT THIS GUARDS. ``estonia/label_algebra.EE_LABEL_ALGEBRA`` is a DECLARED SPEC
of Estonia's real label calculus (superscript ``§10¹`` / lettered ``14a``
stem families). This test makes the algebra *first-class* WITHOUT a grafter
control-flow refactor: for each of the four §4.2 operations it asserts the
declared algebra reproduces EE's ACTUAL label decisions — bound to the SAME
code the grafter uses (``default_label_sort_key`` for order,
``normalized_label_key`` for collision, ``_normalize_num`` for the superscript
parse, ``_predecessor_rank``-style superscript ranking for the successor). If
EE's label logic later drifts from the declaration (the sort key changes, the
normalization changes), THIS test FAILS — which is what makes the parallel-first
algebra a faithful mirror rather than dead documentation.

It also pins the neutral-type invariants (``core/label_algebra``): parse
round-trip, order-relation laws (irreflexivity / antisymmetry / transitivity /
totality), successor correctness, collision detection, and the construction
fail-loud (an algebra with no ``jurisdiction`` raises).

PARALLEL-FIRST. The grafter is NOT yet routed through the algebra; the
load-bearing routing is the deferred follow-up. This test is the guardrail that
keeps the declared algebra honest until that routing lands.
"""

from __future__ import annotations

import pytest

from lawvm.core.label_algebra import LabelAlgebra, ParsedLabel
from lawvm.core.tree_ops import default_label_sort_key, normalized_label_key
from lawvm.estonia.label_algebra import EE_LABEL_ALGEBRA, ee_parse_label
from lawvm.estonia.peg import _normalize_num


# ---------------------------------------------------------------------------
# EE conformance: the declared algebra mirrors EE's ACTUAL label code
# ---------------------------------------------------------------------------


class TestEEParseConformance:
    """parse() decomposes exactly as EE's real normalization + sort key do."""

    @pytest.mark.parametrize(
        "raw, expect_stem, expect_components",
        [
            ("71", "71", ()),
            ("71¹", "71", (("super", 1),)),
            ("71 1", "71", (("super", 1),)),  # HTML-stripped superscript surface
            ("§71¹", "71", (("super", 1),)),  # section-symbol prefix stripped
            ("10¹", "10", (("super", 1),)),
            ("10²", "10", (("super", 2),)),
            ("14", "14", ()),
            ("14a", "14", (("letter", "a"),)),
            ("14b", "14", (("letter", "b"),)),
            ("26_1", "26", (("super", 1),)),  # already-normalized compound label
        ],
    )
    def test_parse_stem_and_components(
        self, raw: str, expect_stem: str, expect_components: tuple
    ) -> None:
        parsed = EE_LABEL_ALGEBRA.parse(raw)
        assert parsed.stem == expect_stem
        assert parsed.components == expect_components

    @pytest.mark.parametrize(
        "raw",
        ["71", "71¹", "71 1", "§71¹", "10¹", "14", "14a", "26_1"],
    )
    def test_sort_key_is_ee_default_label_sort_key(self, raw: str) -> None:
        """The parsed sort key IS EE's ``default_label_sort_key`` of the
        normalized label — the algebra threads EE's real order primitive, not a
        re-implementation."""
        normalized = _normalize_num(raw).strip()
        if normalized.startswith("§"):
            normalized = normalized[1:].strip()
        assert EE_LABEL_ALGEBRA.parse(raw).sort_key == default_label_sort_key(
            normalized
        )

    @pytest.mark.parametrize(
        "raw",
        ["71", "71¹", "71 1", "§71¹", "10¹", "14", "14a", "26_1"],
    )
    def test_collision_key_is_ee_normalized_label_key(self, raw: str) -> None:
        """The collision key IS EE's ``normalized_label_key`` (the SlotIdentity
        label component) of the normalized label."""
        normalized = _normalize_num(raw).strip()
        if normalized.startswith("§"):
            normalized = normalized[1:].strip()
        assert EE_LABEL_ALGEBRA.parse(raw).collision_key == normalized_label_key(
            normalized
        )


class TestEEOrderConformance:
    """order() reproduces EE's authoritative interleaved sibling order."""

    def test_superscript_sorts_after_stem_before_next(self) -> None:
        # §1: superscript / lettered inserts sort immediately after their stem,
        # before the next stem — 71 < 71¹ < 71² < 72.
        labels = ["72", "71²", "71", "71¹"]
        parsed = [EE_LABEL_ALGEBRA.parse(x) for x in labels]
        import functools

        ordered = sorted(
            parsed, key=functools.cmp_to_key(EE_LABEL_ALGEBRA.order)
        )
        assert [p.raw for p in ordered] == ["71", "71¹", "71²", "72"]

    def test_letter_family_order(self) -> None:
        labels = ["14b", "14", "14a"]
        parsed = [EE_LABEL_ALGEBRA.parse(x) for x in labels]
        import functools

        ordered = sorted(
            parsed, key=functools.cmp_to_key(EE_LABEL_ALGEBRA.order)
        )
        assert [p.raw for p in ordered] == ["14", "14a", "14b"]

    def test_order_agrees_with_ee_default_sort_key(self) -> None:
        """order() is EXACTLY the compare EE's ``default_label_sort_key``
        induces on any pair (the tree's authoritative insert ordering)."""
        labels = ["71", "71¹", "71²", "72", "14", "14a", "10", "10¹"]
        parsed = [(x, EE_LABEL_ALGEBRA.parse(x)) for x in labels]
        for _, a in parsed:
            for _, b in parsed:
                expected = (default_label_sort_key(a.collision_key) > default_label_sort_key(b.collision_key)) - (
                    default_label_sort_key(a.collision_key) < default_label_sort_key(b.collision_key)
                )
                assert EE_LABEL_ALGEBRA.order(a, b) == expected


class TestEESuccessorConformance:
    """successor_set() reproduces EE's fresh superscript-sibling calculus."""

    def test_first_superscript_after_bare_stem(self) -> None:
        # Insert a superscript sibling under §10 (no superscript siblings yet):
        # the admissible fresh label is §10¹ (10_1).
        existing = [EE_LABEL_ALGEBRA.parse("10")]
        fresh = EE_LABEL_ALGEBRA.successor_set(existing, anchor="10")
        assert fresh.collision_key == "10_1"
        assert fresh.stem == "10"
        assert fresh.components == (("super", 1),)

    def test_next_superscript_past_greatest(self) -> None:
        # §10 + §10¹ present → fresh is §10² (10_2), one past the greatest.
        existing = [EE_LABEL_ALGEBRA.parse("10"), EE_LABEL_ALGEBRA.parse("10¹")]
        fresh = EE_LABEL_ALGEBRA.successor_set(existing, anchor="10")
        assert fresh.collision_key == "10_2"
        assert fresh.components == (("super", 2),)

    def test_successor_ignores_other_stems(self) -> None:
        # Siblings from another stem do not affect the fresh superscript.
        existing = [
            EE_LABEL_ALGEBRA.parse("10"),
            EE_LABEL_ALGEBRA.parse("10¹"),
            EE_LABEL_ALGEBRA.parse("11"),
            EE_LABEL_ALGEBRA.parse("11¹"),
        ]
        fresh = EE_LABEL_ALGEBRA.successor_set(existing, anchor="10")
        assert fresh.collision_key == "10_2"

    def test_successor_infers_single_stem_without_anchor(self) -> None:
        existing = [EE_LABEL_ALGEBRA.parse("10"), EE_LABEL_ALGEBRA.parse("10¹")]
        fresh = EE_LABEL_ALGEBRA.successor_set(existing)
        assert fresh.collision_key == "10_2"

    def test_successor_fails_loud_on_ambiguous_stem(self) -> None:
        existing = [EE_LABEL_ALGEBRA.parse("10"), EE_LABEL_ALGEBRA.parse("11")]
        with pytest.raises(ValueError):
            EE_LABEL_ALGEBRA.successor_set(existing)

    def test_fresh_successor_does_not_collide(self) -> None:
        existing = [EE_LABEL_ALGEBRA.parse("10"), EE_LABEL_ALGEBRA.parse("10¹")]
        fresh = EE_LABEL_ALGEBRA.successor_set(existing, anchor="10")
        assert not EE_LABEL_ALGEBRA.collides(fresh, existing)


class TestEECollisionConformance:
    """collides() is EE's normalized-label (SlotIdentity) identity equality."""

    def test_superscript_surface_variants_collide(self) -> None:
        # §71¹, "71 1", and the normalized "71_1" are the SAME slot — they
        # collide (SlotIdentity normalized-label equality).
        existing = [EE_LABEL_ALGEBRA.parse("71¹")]
        assert EE_LABEL_ALGEBRA.collides(EE_LABEL_ALGEBRA.parse("71 1"), existing)
        assert EE_LABEL_ALGEBRA.collides(EE_LABEL_ALGEBRA.parse("§71¹"), existing)

    def test_distinct_labels_do_not_collide(self) -> None:
        existing = [EE_LABEL_ALGEBRA.parse("71"), EE_LABEL_ALGEBRA.parse("71¹")]
        assert not EE_LABEL_ALGEBRA.collides(EE_LABEL_ALGEBRA.parse("72"), existing)
        assert not EE_LABEL_ALGEBRA.collides(EE_LABEL_ALGEBRA.parse("71²"), existing)

    def test_stem_and_superscript_are_distinct_slots(self) -> None:
        # 71 and 71¹ are DIFFERENT slots (stem family, not identity) — the §1
        # "stem family (14, 14a)" distinction.
        assert not EE_LABEL_ALGEBRA.collides(
            EE_LABEL_ALGEBRA.parse("71"), [EE_LABEL_ALGEBRA.parse("71¹")]
        )


# ---------------------------------------------------------------------------
# Neutral-type invariants (core/label_algebra), jurisdiction-agnostic
# ---------------------------------------------------------------------------


class TestNeutralTypeInvariants:
    def test_parse_round_trip_raw_preserved(self) -> None:
        for raw in ["71", "71¹", "14a"]:
            assert EE_LABEL_ALGEBRA.parse(raw).raw == raw

    def test_order_irreflexive_and_antisymmetric(self) -> None:
        labels = [EE_LABEL_ALGEBRA.parse(x) for x in ["71", "71¹", "72", "14a"]]
        for a in labels:
            assert EE_LABEL_ALGEBRA.order(a, a) == 0
        for a in labels:
            for b in labels:
                assert EE_LABEL_ALGEBRA.order(a, b) == -EE_LABEL_ALGEBRA.order(b, a)

    def test_order_transitive_and_total(self) -> None:
        labels = [EE_LABEL_ALGEBRA.parse(x) for x in ["71", "71¹", "71²", "72"]]
        for a in labels:
            for b in labels:
                # totality: every pair is comparable to -1/0/1.
                assert EE_LABEL_ALGEBRA.order(a, b) in (-1, 0, 1)
        # transitivity on the known chain 71 < 71¹ < 71² < 72.
        a, b, c, d = labels
        assert EE_LABEL_ALGEBRA.order(a, b) < 0
        assert EE_LABEL_ALGEBRA.order(b, c) < 0
        assert EE_LABEL_ALGEBRA.order(a, c) < 0
        assert EE_LABEL_ALGEBRA.order(a, d) < 0

    def test_collides_empty_existing_is_false(self) -> None:
        assert not EE_LABEL_ALGEBRA.collides(EE_LABEL_ALGEBRA.parse("71"), [])

    def test_parsed_label_is_frozen_hashable(self) -> None:
        p = EE_LABEL_ALGEBRA.parse("71¹")
        assert isinstance(p, ParsedLabel)
        # ``ParsedLabel`` is a frozen dataclass — hashable (usable as a set/dict
        # key, which collision detection relies on) and identity-stable.
        assert {p, p} == {p}
        assert p == EE_LABEL_ALGEBRA.parse("71¹")

    def test_construction_fails_loud_on_empty_jurisdiction(self) -> None:
        with pytest.raises(ValueError):
            LabelAlgebra(
                jurisdiction="",
                parse_fn=ee_parse_label,
                successor_fn=lambda existing, anchor: ee_parse_label("1"),
            )
