"""Conformance + unit test for Finland's ``LabelAlgebra`` (#186, §4.2 item 4).

Design reference: ``notes_internal/FABLE_UNIVERSAL_ALGEBRA.md`` §4.2 item 4 +
§7 delta #4. Mirrors ``tests/test_label_algebra.py`` (the EE conformance test).

WHAT THIS GUARDS. ``finland/label_algebra.FI_LABEL_ALGEBRA`` is a DECLARED SPEC
of Finland's real section-label calculus (Arabic + lettered ``14 a §`` stem
families). This test makes the algebra *first-class* WITHOUT a grafter
control-flow refactor: for each of the four §4.2 operations it asserts the
declared algebra reproduces FI's ACTUAL label decisions — bound to the SAME code
FI uses (``helpers._section_sort_key`` for order, ``helpers._norm_num_token`` for
collision, ``uncovered_recovery_support.next_letter_label`` for the successor).
If FI's label logic later drifts from the declaration (the sort key changes, the
normalization changes, the letter-succession changes), THIS test FAILS — which is
what makes the parallel-first algebra a faithful mirror rather than dead
documentation.

It also pins the neutral-type invariants (``core/label_algebra``): parse
round-trip, order-relation laws (irreflexivity / antisymmetry / transitivity /
totality), successor correctness, collision detection, and the construction
fail-loud.

PARALLEL-FIRST. The grafter is NOT yet routed through the algebra (FI's grafter
positions inserts via core ``insert_sorted`` / ``default_label_sort_key``); the
load-bearing routing is the deferred follow-up. This test is the guardrail that
keeps the declared algebra honest until that routing lands.
"""

from __future__ import annotations

import functools

import pytest

from lawvm.core.label_algebra import LabelAlgebra, ParsedLabel
from lawvm.finland.helpers import _norm_num_token, _section_sort_key
from lawvm.finland.label_algebra import FI_LABEL_ALGEBRA, fi_parse_label
from lawvm.finland.uncovered_recovery_support import next_letter_label


# ---------------------------------------------------------------------------
# FI conformance: the declared algebra mirrors FI's ACTUAL label code
# ---------------------------------------------------------------------------


class TestFIParseConformance:
    """parse() decomposes exactly as FI's real section primitives do."""

    @pytest.mark.parametrize(
        "raw, expect_stem, expect_components",
        [
            ("14 §", "14", ()),
            ("14", "14", ()),
            ("14 a §", "14", (("letter", "a"),)),
            ("14a", "14", (("letter", "a"),)),
            ("14 a §.", "14", (("letter", "a"),)),  # old-format trailing dot
            ("3 a §.", "3", (("letter", "a"),)),
            ("14b", "14", (("letter", "b"),)),
            ("14z", "14", (("letter", "z"),)),
            ("§ 1.", "1", ()),  # sign-first old surface
            ("15", "15", ()),
        ],
    )
    def test_parse_stem_and_components(
        self, raw: str, expect_stem: str, expect_components: tuple
    ) -> None:
        parsed = FI_LABEL_ALGEBRA.parse(raw)
        assert parsed.stem == expect_stem
        assert parsed.components == expect_components

    @pytest.mark.parametrize(
        "raw",
        ["14 §", "14 a §", "14a", "14 a §.", "3 a §.", "14z", "§ 1.", "15", "10 luku"],
    )
    def test_sort_key_is_fi_section_sort_key(self, raw: str) -> None:
        """The parsed sort key IS FI's ``_section_sort_key`` — the algebra threads
        FI's real order primitive, not a re-implementation."""
        assert FI_LABEL_ALGEBRA.parse(raw).sort_key == _section_sort_key(raw)

    @pytest.mark.parametrize(
        "raw",
        ["14 §", "14 a §", "14a", "14 a §.", "3 a §.", "14z", "§ 1.", "15", "10 luku"],
    )
    def test_collision_key_is_fi_norm_num_token(self, raw: str) -> None:
        """The collision key IS FI's ``_norm_num_token`` (the section identity
        token) of the label."""
        assert FI_LABEL_ALGEBRA.parse(raw).collision_key == _norm_num_token(raw)


class TestFIOrderConformance:
    """order() reproduces FI's authoritative interleaved sibling order."""

    def test_letter_suffix_sorts_after_stem_before_next(self) -> None:
        # §1.6: a letter-suffixed insert sorts immediately after its stem, before
        # the next stem — 14 < 14a < 14b < 15.
        labels = ["15", "14b", "14", "14a"]
        parsed = [FI_LABEL_ALGEBRA.parse(x) for x in labels]
        ordered = sorted(parsed, key=functools.cmp_to_key(FI_LABEL_ALGEBRA.order))
        assert [p.raw for p in ordered] == ["14", "14a", "14b", "15"]

    def test_order_agrees_with_fi_section_sort_key(self) -> None:
        """order() is EXACTLY the compare FI's ``_section_sort_key`` induces on
        any pair (the FI section insert ordering)."""
        labels = ["14", "14a", "14b", "15", "3", "3a", "10", "10a"]
        parsed = [FI_LABEL_ALGEBRA.parse(x) for x in labels]
        for a in parsed:
            for b in parsed:
                ka, kb = _section_sort_key(a.raw), _section_sort_key(b.raw)
                expected = (ka > kb) - (ka < kb)
                assert FI_LABEL_ALGEBRA.order(a, b) == expected


class TestFISuccessorConformance:
    """successor_set() reproduces FI's fresh letter-sibling calculus."""

    def test_first_letter_after_bare_stem(self) -> None:
        # Insert a letter sibling after §14 (no suffix): fresh label is 14a.
        fresh = FI_LABEL_ALGEBRA.successor_set([], anchor="14 §")
        assert fresh.collision_key == "14a"
        assert fresh.stem == "14"
        assert fresh.components == (("letter", "a"),)

    def test_next_letter_past_greatest(self) -> None:
        # §14a present → fresh is 14b, one letter past.
        fresh = FI_LABEL_ALGEBRA.successor_set([], anchor="14a")
        assert fresh.collision_key == "14b"
        assert fresh.components == (("letter", "b"),)

    def test_successor_is_fi_next_letter_label(self) -> None:
        """The fresh label IS FI's ``next_letter_label`` of the anchor."""
        for anchor in ["14 §", "14a", "18", "3b"]:
            fresh = FI_LABEL_ALGEBRA.successor_set([], anchor=anchor)
            assert fresh.collision_key == _norm_num_token(next_letter_label(anchor))

    def test_successor_infers_greatest_sibling_without_anchor(self) -> None:
        existing = [FI_LABEL_ALGEBRA.parse("14"), FI_LABEL_ALGEBRA.parse("14a")]
        fresh = FI_LABEL_ALGEBRA.successor_set(existing)
        assert fresh.collision_key == "14b"

    def test_successor_fails_loud_on_exhausted_series(self) -> None:
        # FI has no admissible fresh label past 'z' (next_letter_label → None).
        with pytest.raises(ValueError):
            FI_LABEL_ALGEBRA.successor_set([], anchor="14z")

    def test_successor_fails_loud_without_anchor_or_siblings(self) -> None:
        with pytest.raises(ValueError):
            FI_LABEL_ALGEBRA.successor_set([])

    def test_fresh_successor_does_not_collide(self) -> None:
        existing = [FI_LABEL_ALGEBRA.parse("14"), FI_LABEL_ALGEBRA.parse("14a")]
        fresh = FI_LABEL_ALGEBRA.successor_set(existing, anchor="14a")
        assert not FI_LABEL_ALGEBRA.collides(fresh, existing)


class TestFICollisionConformance:
    """collides() is FI's normalized-token (``_norm_num_token``) identity."""

    def test_surface_variants_collide(self) -> None:
        # "14 a §.", "14 a §", and the compact "14a" are the SAME slot — they
        # collide (normalized-token equality).
        existing = [FI_LABEL_ALGEBRA.parse("14a")]
        assert FI_LABEL_ALGEBRA.collides(FI_LABEL_ALGEBRA.parse("14 a §."), existing)
        assert FI_LABEL_ALGEBRA.collides(FI_LABEL_ALGEBRA.parse("14 a §"), existing)

    def test_distinct_labels_do_not_collide(self) -> None:
        existing = [FI_LABEL_ALGEBRA.parse("14"), FI_LABEL_ALGEBRA.parse("14a")]
        assert not FI_LABEL_ALGEBRA.collides(FI_LABEL_ALGEBRA.parse("15"), existing)
        assert not FI_LABEL_ALGEBRA.collides(FI_LABEL_ALGEBRA.parse("14b"), existing)

    def test_stem_and_letter_are_distinct_slots(self) -> None:
        # 14 and 14a are DIFFERENT slots (stem family, not identity) — the §1.6
        # "stem family (14, 14 a)" distinction.
        assert not FI_LABEL_ALGEBRA.collides(
            FI_LABEL_ALGEBRA.parse("14"), [FI_LABEL_ALGEBRA.parse("14a")]
        )


# ---------------------------------------------------------------------------
# Neutral-type invariants (core/label_algebra), jurisdiction-agnostic
# ---------------------------------------------------------------------------


class TestNeutralTypeInvariants:
    def test_parse_round_trip_raw_preserved(self) -> None:
        for raw in ["14 §", "14 a §", "14a"]:
            assert FI_LABEL_ALGEBRA.parse(raw).raw == raw

    def test_order_irreflexive_and_antisymmetric(self) -> None:
        labels = [FI_LABEL_ALGEBRA.parse(x) for x in ["14", "14a", "15", "3a"]]
        for a in labels:
            assert FI_LABEL_ALGEBRA.order(a, a) == 0
        for a in labels:
            for b in labels:
                assert FI_LABEL_ALGEBRA.order(a, b) == -FI_LABEL_ALGEBRA.order(b, a)

    def test_order_transitive_and_total(self) -> None:
        labels = [FI_LABEL_ALGEBRA.parse(x) for x in ["14", "14a", "14b", "15"]]
        for a in labels:
            for b in labels:
                assert FI_LABEL_ALGEBRA.order(a, b) in (-1, 0, 1)
        a, b, c, d = labels
        assert FI_LABEL_ALGEBRA.order(a, b) < 0
        assert FI_LABEL_ALGEBRA.order(b, c) < 0
        assert FI_LABEL_ALGEBRA.order(a, c) < 0
        assert FI_LABEL_ALGEBRA.order(a, d) < 0

    def test_collides_empty_existing_is_false(self) -> None:
        assert not FI_LABEL_ALGEBRA.collides(FI_LABEL_ALGEBRA.parse("14"), [])

    def test_parsed_label_is_frozen_hashable(self) -> None:
        p = FI_LABEL_ALGEBRA.parse("14 a §")
        assert isinstance(p, ParsedLabel)
        assert {p, p} == {p}
        assert p == FI_LABEL_ALGEBRA.parse("14 a §")

    def test_construction_fails_loud_on_empty_jurisdiction(self) -> None:
        with pytest.raises(ValueError):
            LabelAlgebra(
                jurisdiction="",
                parse_fn=fi_parse_label,
                successor_fn=lambda existing, anchor: fi_parse_label("1"),
            )
