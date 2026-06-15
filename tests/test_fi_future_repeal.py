from __future__ import annotations

from lawvm.finland.future_repeal import RepealTargetRef, build_future_repeal_suffix


def test_build_future_repeal_suffix_indexes_later_repeals_only() -> None:
    repeal_1 = RepealTargetRef.section("1")
    repeal_2 = RepealTargetRef.section("2")
    repeal_3 = RepealTargetRef.chapter("3")

    suffix = build_future_repeal_suffix([
        {repeal_1},
        {repeal_2},
        set(),
        {repeal_3},
    ])

    assert suffix == [
        {repeal_2, repeal_3},
        {repeal_3},
        {repeal_3},
        set(),
    ]
