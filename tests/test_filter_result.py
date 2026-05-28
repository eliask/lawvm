from __future__ import annotations

from lawvm.core.filter_result import FilterResult, RejectedItem, filter_result_from_parts


def test_filter_result_preserves_accepted_and_rejected_lanes() -> None:
    rejected = RejectedItem(item="drop-a", reason="unsupported", reason_code="UNSUPPORTED")

    result = filter_result_from_parts(
        accepted_items=["keep-a", "keep-b"],
        rejected_items=[rejected],
    )

    assert result == FilterResult(
        accepted_items=("keep-a", "keep-b"),
        rejected_items=(rejected,),
    )
    assert result.rejected_payloads == ("drop-a",)
    assert result.rejected_reason_counts() == {"unsupported": 1}


def test_filter_result_rejected_reason_counts_ignore_empty_reasons() -> None:
    result = FilterResult(
        rejected_items=(
            RejectedItem(item="a", reason="same"),
            RejectedItem(item="b", reason="same"),
            RejectedItem(item="c", reason=""),
        )
    )

    assert result.rejected_reason_counts() == {"same": 2}

