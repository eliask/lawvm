"""Shared EE reporting helpers for policy summaries and publication strata."""
from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any


class EEBenchmarkReportingStratum(str, Enum):
    CORE_COMMENSURABLE = "EE_CORE_COMMENSURABLE"
    BASE_IS_ORACLE = "EE_BASE_IS_ORACLE"
    FORWARD_LOOKING_ORACLE = "EE_FORWARD_LOOKING_ORACLE"
    SAME_CHAIN_EDITORIAL_DRIFT = "EE_SAME_CHAIN_EDITORIAL_DRIFT"
    NONCORE_SOURCE_GAP = "EE_NONCORE_SOURCE_GAP"


_CORE_SOURCE_BASES = {
    "algtekst_source",
    "earliest_available_terviktekst",
    "pairwise_terviktekst_delta",
}
_NONCORE_COMPARISON_CLASSES = {
    "no_oracle",
    "unclassified_oracle_delta",
}


def build_ee_comparison_policy_summary() -> dict:
    """Summarize the bounded non-silent EE comparison policy surface."""
    from lawvm.estonia.compare import (
        get_ee_comparison_non_silent_normalization_rule_classes,
        get_ee_comparison_non_silent_normalization_rules,
    )

    rule_classes = get_ee_comparison_non_silent_normalization_rule_classes()
    rules = get_ee_comparison_non_silent_normalization_rules()
    counts_by_class = {rule_class.value: 0 for rule_class in rule_classes}
    for rule in rules:
        counts_by_class[rule.rule_class.value] = counts_by_class.get(rule.rule_class.value, 0) + 1
    return {
        "non_silent_rule_class_count": len(rule_classes),
        "non_silent_rule_classes": [rule_class.value for rule_class in rule_classes],
        "non_silent_rule_count": len(rules),
        "non_silent_rule_names": [rule.name for rule in rules],
        "non_silent_rule_counts_by_class": counts_by_class,
    }


def classify_ee_benchmark_reporting_stratum(
    source_basis: str | None,
    comparison_class: str | None,
) -> EEBenchmarkReportingStratum:
    basis = (source_basis or "").strip()
    comparison = (comparison_class or "").strip()

    if basis == "base_is_oracle" or comparison == "base_is_oracle":
        return EEBenchmarkReportingStratum.BASE_IS_ORACLE
    if comparison == "forward_looking_oracle":
        return EEBenchmarkReportingStratum.FORWARD_LOOKING_ORACLE
    if comparison == "same_chain_editorial_drift":
        return EEBenchmarkReportingStratum.SAME_CHAIN_EDITORIAL_DRIFT
    if basis in _CORE_SOURCE_BASES and comparison not in _NONCORE_COMPARISON_CLASSES:
        return EEBenchmarkReportingStratum.CORE_COMMENSURABLE
    return EEBenchmarkReportingStratum.NONCORE_SOURCE_GAP


def build_ee_benchmark_reporting_summary(
    source_basis: str | None,
    comparison_class: str | None,
) -> dict[str, Any]:
    stratum = classify_ee_benchmark_reporting_stratum(source_basis, comparison_class)
    return {
        "benchmark_reporting_stratum": stratum.value,
        "benchmark_reporting_headline_eligible": stratum is EEBenchmarkReportingStratum.CORE_COMMENSURABLE,
    }


def count_ee_benchmark_reporting_strata(
    rows: Iterable[tuple[str | None, str | None]],
) -> dict[str, int]:
    counts = {stratum.value: 0 for stratum in EEBenchmarkReportingStratum}
    for source_basis, comparison_class in rows:
        stratum = classify_ee_benchmark_reporting_stratum(source_basis, comparison_class)
        counts[stratum.value] += 1
    return counts


__all__ = [
    "EEBenchmarkReportingStratum",
    "build_ee_benchmark_reporting_summary",
    "build_ee_comparison_policy_summary",
    "classify_ee_benchmark_reporting_stratum",
    "count_ee_benchmark_reporting_strata",
]
