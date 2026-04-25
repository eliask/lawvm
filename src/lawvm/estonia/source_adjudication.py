"""Typed Estonia oracle/source adjudication helpers."""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Sequence

from lawvm.replay_adjudication import SourceAdjudication


EE_CORE_COMPARISON_CLASSES = frozenset({"base_is_oracle", "commensurable_delta"})


class EESourceBasis(str, Enum):
    """Explicit source footing for an EE replay/oracle pair."""

    ALGTEKST_SOURCE = "algtekst_source"
    EARLIEST_AVAILABLE_TERVIKTEKST = "earliest_available_terviktekst"
    PAIRWISE_TERVIKTEKST_DELTA = "pairwise_terviktekst_delta"
    BASE_IS_ORACLE = "base_is_oracle"
    NONCOMMENSURABLE = "noncommensurable"


def classify_ee_source_basis(
    *,
    has_oracle: bool,
    base_is_consolidated: bool,
    oracle_matches_base: bool,
    effective_new_amendments: Sequence[str],
    future_new_amendments: Sequence[str],
    same_chain: bool,
) -> EESourceBasis:
    """Classify the factual source footing of an EE replay."""
    if not base_is_consolidated:
        return EESourceBasis.ALGTEKST_SOURCE
    if oracle_matches_base:
        return EESourceBasis.BASE_IS_ORACLE
    if effective_new_amendments:
        return EESourceBasis.PAIRWISE_TERVIKTEKST_DELTA
    if same_chain:
        return EESourceBasis.EARLIEST_AVAILABLE_TERVIKTEKST
    if future_new_amendments or not has_oracle:
        return EESourceBasis.NONCOMMENSURABLE
    return EESourceBasis.EARLIEST_AVAILABLE_TERVIKTEKST


def classify_ee_oracle_pair(
    *,
    has_oracle: bool,
    base_is_consolidated: bool,
    oracle_matches_base: bool,
    effective_new_amendments: Sequence[str],
    future_new_amendments: Sequence[str],
    same_chain: bool,
) -> str:
    """Classify whether an EE base/oracle pair is commensurable for replay.

    The Estonia bench is currently a pairwise terviktekst benchmark, so the
    important question is not only "did replay differ?" but also "was the
    oracle/base pair actually commensurable for replay at the chosen as-of
    date?".  This helper keeps that classification explicit and typed.
    """
    if not has_oracle:
        return "no_oracle"
    if oracle_matches_base:
        return "base_is_oracle"
    if future_new_amendments:
        return "forward_looking_oracle"
    if effective_new_amendments:
        return "commensurable_delta"
    if base_is_consolidated and same_chain:
        return "same_chain_editorial_drift"
    return "unclassified_oracle_delta"


def is_core_ee_comparison(comparison_class: str) -> bool:
    """Return True when the EE comparison is core-benchmark commensurable."""
    return comparison_class in EE_CORE_COMPARISON_CLASSES


def build_ee_source_adjudication(
    *,
    statute_id: str,
    base_id: str,
    oracle_id: str,
    as_of: str,
    comparison_class: str,
    lineage: Iterable[dict[str, Any]] = (),
) -> SourceAdjudication:
    """Build typed EE source adjudication from an oracle comparison class."""
    return SourceAdjudication(
        statute_id=statute_id,
        replay_mode="ee_pit",
        cutoff_date=as_of,
        oracle_version_amendment_id=oracle_id,
        oracle_suspect="" if is_core_ee_comparison(comparison_class) else comparison_class,
        lineage=(
            {"kind": "base", "aktViide": base_id},
            {"kind": "oracle", "aktViide": oracle_id},
            *tuple(lineage),
        ),
    )


__all__ = [
    "EE_CORE_COMPARISON_CLASSES",
    "EESourceBasis",
    "build_ee_source_adjudication",
    "classify_ee_oracle_pair",
    "classify_ee_source_basis",
    "is_core_ee_comparison",
]
