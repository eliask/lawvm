"""Generated evidence helpers for repeated Estonia residual families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class GeneratedEEResidualEvidence:
    """A lightweight generated residual record before inventory lowering."""

    address: str
    bucket: str
    evidence: str


def build_inserted_section_omission_family(
    *,
    section_address: str,
    section_symbol: str,
    source_act_id: str,
    later_source_act_id: str | None = None,
    oracle_id: str,
    subsection_labels: Sequence[str],
    bucket: str = "source_pathology",
) -> tuple[GeneratedEEResidualEvidence, ...]:
    """Build a repeated source-backed omission family for one inserted section cluster."""
    later_clause = (
        f"and source act {later_source_act_id} later amends it, "
        if later_source_act_id
        else ""
    )
    section_evidence = (
        f"Source act {source_act_id} inserts {section_symbol} cleanly {later_clause}"
        f"but oracle {oracle_id} still omits the inserted {section_symbol} section cluster."
    )
    subsection_evidence = (
        f"Source act {source_act_id} inserts {section_symbol} cleanly {later_clause}"
        f"but oracle {oracle_id} still omits the inserted {section_symbol} subsection cluster."
    )

    records = [
        GeneratedEEResidualEvidence(
            address=section_address,
            bucket=bucket,
            evidence=section_evidence,
        )
    ]
    for label in subsection_labels:
        records.append(
            GeneratedEEResidualEvidence(
                address=f"{section_address}/subsection:{label}",
                bucket=bucket,
                evidence=subsection_evidence,
            )
        )
    return tuple(records)


def build_inserted_note_omission_family(
    *,
    note_address: str,
    note_symbol: str,
    source_act_id: str,
    oracle_id: str,
    note_label: str = "normitehniline märkus",
    bucket: str = "source_oracle_drift",
) -> tuple[GeneratedEEResidualEvidence, ...]:
    """Build a repeated source-backed omission family for one inserted note cluster."""
    evidence = (
        f"Source act {source_act_id} cleanly inserts the {note_symbol} {note_label}, "
        f"but oracle {oracle_id} omits the inserted {note_symbol} note."
    )
    return (
        GeneratedEEResidualEvidence(
            address=note_address,
            bucket=bucket,
            evidence=evidence,
        ),
    )


def build_inserted_item_omission_family(
    *,
    item_address: str,
    source_act_id: str,
    oracle_id: str,
    item_labels: Sequence[str],
    bucket: str = "source_pathology",
) -> tuple[GeneratedEEResidualEvidence, ...]:
    """Build a repeated source-backed omission family for one inserted item cluster."""
    records = []
    for label in item_labels:
        address_label = label.replace("^", "_")
        records.append(
            GeneratedEEResidualEvidence(
                address=f"{item_address}/item:{address_label}",
                bucket=bucket,
                evidence=(
                    f"Source act {source_act_id} emits item {label} cleanly; "
                    f"oracle {oracle_id} omits it entirely."
                ),
            )
        )
    return tuple(records)


def build_shortened_section_family(
    *,
    records: Sequence[tuple[str, str]],
    bucket: str = "source_oracle_drift",
) -> tuple[GeneratedEEResidualEvidence, ...]:
    """Build a repeated source-backed shortening family for related section nodes."""
    return tuple(
        GeneratedEEResidualEvidence(address=address, bucket=bucket, evidence=evidence)
        for address, evidence in records
    )


def build_address_list_family(
    *,
    addresses: Sequence[str],
    evidence: str,
    bucket: str = "source_oracle_drift",
) -> tuple[GeneratedEEResidualEvidence, ...]:
    """Build a repeated residual family for arbitrary addresses sharing one evidence thesis."""
    return tuple(
        GeneratedEEResidualEvidence(address=address, bucket=bucket, evidence=evidence)
        for address in addresses
    )


__all__ = [
    "GeneratedEEResidualEvidence",
    "build_address_list_family",
    "build_inserted_section_omission_family",
    "build_inserted_note_omission_family",
    "build_inserted_item_omission_family",
    "build_shortened_section_family",
]
