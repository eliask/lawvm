"""Trailing commencement-date qualifiers on UK effect Types.

legislation.gov.uk sometimes writes a commencement date into the effect ``Type``
cell (e.g. ``"added (1.7.1999)"``) rather than into ``InForceDates``. The trailing
date defeats the exact-string structural classification, so the effect is dropped
and its insert never replays. The parser splits the date off, recovering both the
base verb and the missing effective date.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.uk_legislation.effects import (
    _apply_uk_effect_type_date_qualifier,
    normalize_uk_effect_type_trailing_date,
)

_ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"


class TestNormalizeTrailingDate:
    def test_single_date_is_split_off(self) -> None:
        base, dates = normalize_uk_effect_type_trailing_date("added (1.7.1999)")
        assert base == "added"
        assert dates == ["1999-07-01"]

    def test_words_added_with_date(self) -> None:
        base, dates = normalize_uk_effect_type_trailing_date("words added (1.1.1997)")
        assert base == "words added"
        assert dates == ["1997-01-01"]

    def test_multiple_dates_in_one_qualifier(self) -> None:
        base, dates = normalize_uk_effect_type_trailing_date(
            "inserted (15.10.1995 1.3.1996)"
        )
        assert base == "inserted"
        assert dates == ["1995-10-15", "1996-03-01"]

    def test_non_date_qualifiers_are_left_intact(self) -> None:
        # Extent / status / numeric qualifiers carry meaning and must not be stripped.
        for raw in ("words added (EW)", "applied (temp.)", "applied (1)", "omitted (d)"):
            base, dates = normalize_uk_effect_type_trailing_date(raw)
            assert base == raw
            assert dates == []

    def test_plain_type_is_unchanged(self) -> None:
        base, dates = normalize_uk_effect_type_trailing_date("inserted")
        assert base == "inserted"
        assert dates == []

    def test_internal_parenthetical_not_stripped(self) -> None:
        # Only a *trailing* pure-date parenthetical is a misplaced commencement date.
        base, dates = normalize_uk_effect_type_trailing_date("repealed by 2010 c. 15")
        assert base == "repealed by 2010 c. 15"
        assert dates == []


class TestApplyDateQualifier:
    def test_recovered_date_appended_to_in_force_dates(self) -> None:
        in_force: list[dict[str, object]] = []
        base = _apply_uk_effect_type_date_qualifier("added (1.7.1999)", in_force)
        assert base == "added"
        assert in_force == [
            {
                "date": "1999-07-01",
                "applied": "true",
                "prospective": "false",
                "source": "type_date_qualifier",
            }
        ]

    def test_existing_date_not_duplicated(self) -> None:
        in_force: list[dict[str, object]] = [{"date": "1999-07-01", "applied": "true"}]
        base = _apply_uk_effect_type_date_qualifier("added (1.7.1999)", in_force)
        assert base == "added"
        assert len(in_force) == 1

    def test_no_qualifier_leaves_dates_untouched(self) -> None:
        in_force: list[dict[str, object]] = []
        base = _apply_uk_effect_type_date_qualifier("inserted", in_force)
        assert base == "inserted"
        assert in_force == []


@pytest.mark.skipif(not _ARCHIVE.exists(), reason="UK farchive not present")
class TestDateQualifierRecoversStructuralInsert:
    def test_section_23a_effect_is_structural_after_normalization(self) -> None:
        # ukpga/1978/30 s. 23A is "added (1.7.1999)" by ukpga/1998/46. Before the
        # split it classified non-structural and dropped; after, it must be a
        # structural-for-replay insert with a recovered effective date.
        from farchive import Farchive
        from lawvm.uk_legislation.effects import (
            load_effects_for_statute_from_archive,
            uk_effect_requires_affecting_source_for_replay,
        )

        with Farchive(_ARCHIVE) as archive:
            effects = load_effects_for_statute_from_archive("ukpga/1978/30", archive)

        s23a = [
            e
            for e in effects
            if e.affected_provisions.strip() == "s. 23A"
            and e.affecting_act_id == "ukpga/1998/46"
        ]
        assert s23a, "expected the s. 23A added-by-1998/46 effect row"
        effect = s23a[0]
        assert effect.effect_type == "added"
        assert effect.effective_date == "1999-07-01"
        # "added" is recovered as a nonstructural-but-replayable insert family; the
        # date split is what flips this gate from False to True.
        assert uk_effect_requires_affecting_source_for_replay(effect)
