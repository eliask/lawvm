"""Per-unit materialization-totality lens — proven on the 1929/234 witness.

The 1929/234 (rikoslaki) part-replace orphan-retirement bug (``cae79014``, fixed
in ``apply_runtime_support`` ``48e20106``) silently dropped sections 110-113 via
a ``content=None`` chapter snapshot that masked them — while the aggregate bench
score did not move. These tests prove the per-unit lens
(:mod:`lawvm.finland.materialization_totality`):

* FIRES a ``SILENTLY_DROPPED_UNIT`` violation NAMING the missing sections on a
  synthetic reproduction of the masking condition (sections in the declared
  universe, absent from the materialized tree, with no tombstone / typed reason);
* is SILENT (verdict ``TOTAL`` / no violation) on the corrected materialization;
* on the REAL 1929/234 replay, classifies sections 110-113 as ``PRESENT`` (the
  oracle-grounded witness fact — they survive the fix), never as violations.
"""

from __future__ import annotations

from typing import cast

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.materialization_totality import (
    MaterializationTotalityCode,
    MaterializationTotalityError,
    MaterializationTotalityVerdict,
    TypedAbsenceReason,
    UniverseSpec,
    UnitDisposition,
    check_materialization_totality,
    universe_from_tree,
)


# --------------------------------------------------------------------------- #
# Synthetic tree builders mirroring the 1929/234 part_5 shape.                 #
# --------------------------------------------------------------------------- #


def _section(label: str, *, tombstone: bool = False) -> IRNode:
    attrs = {"lawvm_repeal_placeholder": "1"} if tombstone else {}
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=f"{label} § body", attrs=attrs)


def _chapter(label: str, *sections: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, children=tuple(sections))


def _part(label: str, *chapters: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.PART, label=label, children=tuple(chapters))


def _root(*parts: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(parts))


def _base_tree_with_part5() -> IRNode:
    """Base/source tree carrying part_5 chapter with sections 110-113 (+ neighbours)."""
    return _root(
        _part(
            "5",
            _chapter("1", _section("108"), _section("109")),
            _chapter("2", _section("110"), _section("111"), _section("112"), _section("113")),
        )
    )


# --------------------------------------------------------------------------- #
# UniverseSpec — the root-committed declared universe.                         #
# --------------------------------------------------------------------------- #


def test_universe_from_tree_enumerates_section_units() -> None:
    base = _base_tree_with_part5()
    uni = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    assert len(uni) == 6
    assert set(uni.expected_units) == {
        "sec_108",
        "sec_109",
        "sec_110",
        "sec_111",
        "sec_112",
        "sec_113",
    }
    assert uni.expected_units["sec_110"] == "110 §"


def test_universe_root_is_deterministic_and_commits_membership() -> None:
    base = _base_tree_with_part5()
    uni_a = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    uni_b = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    # Deterministic: same members -> same root.
    assert uni_a.universe_root == uni_b.universe_root
    # Dropping a member changes the root (omission is detectable).
    dropped = {k: v for k, v in uni_a.expected_units.items() if k != "sec_110"}
    uni_dropped = UniverseSpec(
        work_id="1929/234", pit_date="2026-06-23", expected_units=dropped
    )
    assert uni_dropped.universe_root != uni_a.universe_root


def test_universe_from_tree_excludes_already_tombstoned_base_sections() -> None:
    base = _root(_chapter("1", _section("1"), _section("2", tombstone=True)))
    uni = universe_from_tree(base, work_id="w", pit_date="t")
    assert set(uni.expected_units) == {"sec_1"}


# --------------------------------------------------------------------------- #
# THE WITNESS — fires on the 1929/234 silent-drop, silent on the fixed tree.   #
# --------------------------------------------------------------------------- #


def test_fires_silent_drop_violation_naming_1929_234_masked_sections() -> None:
    """Reproduce the masking: part_5 ch.2 (sections 110-113) vanishes silently.

    Mirrors the pre-fix ``content=None`` chapter snapshot that masked sections
    110-113: the materialized tree simply lacks them, with NO tombstone and NO
    typed absence reason. The per-unit lens MUST fire a ``SILENTLY_DROPPED_UNIT``
    violation NAMING each missing section (the aggregate would not see it).
    """
    base = _base_tree_with_part5()
    uni = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")

    # Masked materialization: chapter 2 (110-113) gone entirely; 108/109 survive.
    masked = _root(_part("5", _chapter("1", _section("108"), _section("109"))))

    res = check_materialization_totality(uni, masked)

    assert res.verdict is MaterializationTotalityVerdict.INCOMPLETE
    dropped = {s.address_key for s in res.shortfalls}
    assert dropped == {"sec_110", "sec_111", "sec_112", "sec_113"}
    assert all(
        s.code is MaterializationTotalityCode.SILENTLY_DROPPED_UNIT for s in res.shortfalls
    )
    # Self-evidencing: the finding NAMES the address text in its detail.
    detail_110 = next(s.detail for s in res.shortfalls if s.address_key == "sec_110")
    assert "110 §" in detail_110
    assert "SILENT DROP" in detail_110
    # The universe root is carried so the claimed set is checkable.
    assert res.universe_root == uni.universe_root
    assert res.violation_count == 4
    assert res.present_count == 2


def test_silent_on_corrected_materialization() -> None:
    """The fixed materialization carries every expected section live -> TOTAL."""
    base = _base_tree_with_part5()
    uni = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    # Corrected: the full part_5 survives (the post-fix behaviour).
    corrected = _base_tree_with_part5()
    res = check_materialization_totality(uni, corrected)
    assert res.verdict is MaterializationTotalityVerdict.TOTAL
    assert res.shortfalls == ()
    assert res.violation_count == 0
    assert res.present_count == 6


# --------------------------------------------------------------------------- #
# Ownership paths — tombstone / typed-absence / typed-residual are NOT silent. #
# --------------------------------------------------------------------------- #


def test_repeal_tombstone_is_benign_absent_not_violation() -> None:
    base = _base_tree_with_part5()
    uni = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    # 110 survives only as an in-tree repeal tombstone -> owned, never silent.
    materialized = _root(
        _part(
            "5",
            _chapter("1", _section("108"), _section("109")),
            _chapter(
                "2",
                _section("110", tombstone=True),
                _section("111"),
                _section("112"),
                _section("113"),
            ),
        )
    )
    res = check_materialization_totality(uni, materialized)
    assert res.dispositions["sec_110"] == UnitDisposition.BENIGN_ABSENT.value
    assert res.verdict is MaterializationTotalityVerdict.TOTAL_WITH_RESIDUALS
    assert res.violation_count == 0


def test_caller_typed_absence_makes_drop_benign() -> None:
    base = _base_tree_with_part5()
    uni = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    masked = _root(_part("5", _chapter("1", _section("108"), _section("109"))))
    res = check_materialization_totality(
        uni,
        masked,
        typed_absences=[
            TypedAbsenceReason(address_key=f"sec_{n}", kind="repealed", detail=f"repealed {n}")
            for n in (110, 111, 112, 113)
        ],
    )
    assert res.verdict is MaterializationTotalityVerdict.TOTAL_WITH_RESIDUALS
    assert res.violation_count == 0
    assert res.benign_absent_count == 4


def test_caller_typed_residual_makes_drop_owned() -> None:
    base = _base_tree_with_part5()
    uni = universe_from_tree(base, work_id="1929/234", pit_date="2026-06-23")
    masked = _root(_part("5", _chapter("1", _section("108"), _section("109"))))
    res = check_materialization_totality(
        uni,
        masked,
        typed_residual_keys=["sec_110", "sec_111", "sec_112", "sec_113"],
    )
    assert res.verdict is MaterializationTotalityVerdict.TOTAL_WITH_RESIDUALS
    assert res.violation_count == 0
    assert res.typed_residual_count == 4


def test_typed_absence_reason_requires_a_kind() -> None:
    with pytest.raises(MaterializationTotalityError):
        TypedAbsenceReason(address_key="sec_110", kind="")


def test_empty_universe_is_not_computed() -> None:
    uni = UniverseSpec(work_id="w", pit_date="t", expected_units={})
    res = check_materialization_totality(uni, _root())
    assert res.verdict is MaterializationTotalityVerdict.NOT_COMPUTED
    assert res.shortfalls == ()


# --------------------------------------------------------------------------- #
# Real 1929/234 replay — the oracle-grounded witness fact.                     #
# --------------------------------------------------------------------------- #


def test_real_1929_234_sections_110_113_present_post_fix() -> None:
    """Oracle-grounded: on the real 1929/234 replay, sections 110-113 are PRESENT.

    The fix ``48e20106`` restored these oracle-present sections. The per-unit
    lens MUST classify them as ``PRESENT`` (not ``VIOLATION``) when run over the
    real base universe + materialized tree — the live witness the synthetic
    reproduction abstracts.
    """
    from tests.corpus_pin_helpers import pinned_replay

    replay = pinned_replay(
        "1929/234", mode="official_consolidation", quiet=True, build_full_products=False
    )
    base = cast(IRNode, replay.ctx.base_ir)
    materialized = cast(IRNode, replay.materialized_state.ir)

    uni = universe_from_tree(base, work_id="1929/234", pit_date="current")
    res = check_materialization_totality(uni, materialized)

    for sec in ("sec_110", "sec_111", "sec_112", "sec_113"):
        assert res.dispositions[sec] == UnitDisposition.PRESENT.value, (
            f"{sec} must be PRESENT post-fix (oracle-present); a regression of the "
            f"48e20106 fix would re-drop it -> the lens would flag SILENTLY_DROPPED_UNIT"
        )
    # And none of 110-113 is among the silent-drop violations.
    dropped = {s.address_key for s in res.shortfalls}
    assert dropped.isdisjoint({"sec_110", "sec_111", "sec_112", "sec_113"})
