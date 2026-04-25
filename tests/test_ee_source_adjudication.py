from __future__ import annotations

from lawvm.estonia.source_adjudication import (
    classify_ee_oracle_pair,
    is_core_ee_comparison,
)


def test_classify_ee_commensurable_delta_as_core() -> None:
    comparison = classify_ee_oracle_pair(
        has_oracle=True,
        base_is_consolidated=True,
        oracle_matches_base=False,
        effective_new_amendments=["117032026001"],
        future_new_amendments=[],
        same_chain=False,
    )

    assert comparison == "commensurable_delta"
    assert is_core_ee_comparison(comparison) is True


def test_classify_ee_forward_looking_oracle_as_non_core() -> None:
    comparison = classify_ee_oracle_pair(
        has_oracle=True,
        base_is_consolidated=True,
        oracle_matches_base=False,
        effective_new_amendments=[],
        future_new_amendments=["118032026002"],
        same_chain=False,
    )

    assert comparison == "forward_looking_oracle"
    assert is_core_ee_comparison(comparison) is False


def test_classify_ee_mixed_effective_and_future_oracle_as_non_core() -> None:
    comparison = classify_ee_oracle_pair(
        has_oracle=True,
        base_is_consolidated=True,
        oracle_matches_base=False,
        effective_new_amendments=["117032026001"],
        future_new_amendments=["118032026002"],
        same_chain=False,
    )

    assert comparison == "forward_looking_oracle"
    assert is_core_ee_comparison(comparison) is False


def test_classify_ee_same_chain_editorial_drift_as_non_core() -> None:
    comparison = classify_ee_oracle_pair(
        has_oracle=True,
        base_is_consolidated=True,
        oracle_matches_base=False,
        effective_new_amendments=[],
        future_new_amendments=[],
        same_chain=True,
    )

    assert comparison == "same_chain_editorial_drift"
    assert is_core_ee_comparison(comparison) is False
