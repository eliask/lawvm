"""Helpers for surfacing adjudicated EE residual inventories in reports."""
from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from lawvm.estonia.residual_inventory import (
    EEPairResidualInventory,
    EEResidualRecord,
    get_ee_residual_inventory,
)


_PRESENTATION_PUNCTUATION_WHITESPACE_BUCKET = "presentation_punctuation_whitespace"
_PRESENTATION_PUNCTUATION_WHITESPACE_EVIDENCE = (
    "Replay and Riigi Teataja text differ only by Unicode punctuation "
    "and whitespace after a publication-only projection. The row is "
    "kept for auditability but hidden from the default open candidate queue."
)


@dataclass(frozen=True)
class EEResidualSummary:
    """Known EE residual inventory plus current divergence matching summary."""

    base_id: str
    oracle_id: str
    statute_title: str
    comparison_class: str
    residual_count: int
    bucket_counts: dict[str, int]
    matched_current_divergence_count: int
    matched_current_bucket_counts: dict[str, int]
    unknown_current_divergence_count: int
    unknown_current_divergence_addresses: tuple[str, ...]
    record_by_address: dict[str, EEResidualRecord]


def strip_ee_punctuation_and_whitespace(text: str) -> str:
    """Projection used only for EE residual triage, not replay comparison."""
    return "".join(
        char
        for char in text
        if not char.isspace()
        and not unicodedata.category(char).startswith(("P", "Z"))
    )


def is_ee_punctuation_whitespace_only_difference(
    replay_text: str | None,
    oracle_text: str | None,
) -> bool:
    """Return true when EE surfaces differ only by punctuation/whitespace.

    This is a reporting/publication classifier. It deliberately does not
    normalize letters, digits, symbols, or words, and it never mutates replay.
    """
    if replay_text is None or oracle_text is None:
        return False
    if replay_text == oracle_text:
        return False
    return (
        strip_ee_punctuation_and_whitespace(replay_text)
        == strip_ee_punctuation_and_whitespace(oracle_text)
    )


def build_ee_punctuation_whitespace_record(address: str) -> EEResidualRecord:
    """Build a typed presentation-only residual record for one EE address."""
    return EEResidualRecord(
        address=address,
        bucket=_PRESENTATION_PUNCTUATION_WHITESPACE_BUCKET,
        evidence=_PRESENTATION_PUNCTUATION_WHITESPACE_EVIDENCE,
    )


def _derive_ancestor_record(
    address: str,
    descendant_records: tuple[EEResidualRecord, ...],
) -> EEResidualRecord | None:
    buckets = {record.bucket for record in descendant_records}
    if not buckets:
        return None
    if len(buckets) == 1:
        bucket = next(iter(buckets))
    else:
        bucket = "descendant_residual_mix"
    evidences = {record.evidence for record in descendant_records}
    if len(evidences) == 1:
        evidence = next(iter(evidences))
    elif bucket == "descendant_residual_mix":
        bucket_counts = Counter(record.bucket for record in descendant_records)
        bucket_summary = ", ".join(
            f"{name}={count}" for name, count in sorted(bucket_counts.items())
        )
        evidence = (
            f"All current descendant divergences under {address} are already adjudicated, "
            f"but they span multiple residual buckets ({bucket_summary})."
        )
    else:
        evidence = (
            f"All current descendant divergences under {address} are adjudicated as "
            f"{bucket}."
        )
    return EEResidualRecord(address=address, bucket=bucket, evidence=evidence)


def _with_inherited_ancestor_records(
    exact_records: dict[str, EEResidualRecord],
    ordered_addresses: tuple[str, ...],
) -> dict[str, EEResidualRecord]:
    records = dict(exact_records)
    for address in sorted(ordered_addresses, key=lambda value: value.count("/"), reverse=True):
        if address in records:
            continue
        descendant_addresses = tuple(
            candidate
            for candidate in ordered_addresses
            if candidate.startswith(f"{address}/")
        )
        if not descendant_addresses:
            continue
        descendant_records = tuple(records[candidate] for candidate in descendant_addresses if candidate in records)
        if len(descendant_records) != len(descendant_addresses):
            continue
        derived = _derive_ancestor_record(address, descendant_records)
        if derived is not None:
            records[address] = derived
    return records


def _build_summary(
    inventory: EEPairResidualInventory,
    divergence_addresses: Iterable[str],
) -> EEResidualSummary:
    exact_records = {record.address: record for record in inventory.residuals}
    ordered_addresses = tuple(divergence_addresses)
    records = _with_inherited_ancestor_records(exact_records, ordered_addresses)
    matched_records = [records[address] for address in ordered_addresses if address in records]
    unknown_addresses = tuple(address for address in ordered_addresses if address not in records)
    return EEResidualSummary(
        base_id=inventory.base_id,
        oracle_id=inventory.oracle_id,
        statute_title=inventory.statute_title,
        comparison_class=inventory.comparison_class,
        residual_count=len(inventory.residuals),
        bucket_counts=dict(Counter(record.bucket for record in inventory.residuals)),
        matched_current_divergence_count=len(matched_records),
        matched_current_bucket_counts=dict(Counter(record.bucket for record in matched_records)),
        unknown_current_divergence_count=len(unknown_addresses),
        unknown_current_divergence_addresses=unknown_addresses,
        record_by_address=records,
    )


def build_ee_residual_summary(
    base_id: str | None,
    oracle_id: str | None,
    divergence_addresses: Iterable[str] = (),
) -> EEResidualSummary | None:
    """Return the known residual summary for one EE pair, if available."""
    if not base_id or not oracle_id:
        return None
    inventory = get_ee_residual_inventory(base_id, oracle_id)
    if inventory is None:
        return None
    return _build_summary(inventory, divergence_addresses)


__all__ = [
    "EEResidualSummary",
    "build_ee_punctuation_whitespace_record",
    "build_ee_residual_summary",
    "is_ee_punctuation_whitespace_only_difference",
    "strip_ee_punctuation_and_whitespace",
]
