"""Build Finland source witnesses for consolidated, corrigendum, and adjudication surfaces."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.finland.consolidated_artifacts import artifact_record
from lawvm.finland.proof_surface_row_helpers import (
    bounded_bytes_preview,
    mapping_sequence,
    preview_digest_witness,
    string_sequence,
)


def consolidated_artifact_source_witness(
    *,
    locator: str,
    xml_bytes: bytes,
    source_role: str = "finlex_consolidated_oracle",
) -> SourceWitness:
    """Build a shared source witness for a cached Finland consolidated XML artifact."""

    record = artifact_record(locator, xml_bytes)
    preview = bounded_bytes_preview(xml_bytes)
    return SourceWitness(
        source_role=source_role,
        artifact_id=record.sid,
        locator=locator,
        version_id=record.embedded_version_tag or record.path_version,
        source_path=locator,
        digest=DigestWitness(
            digest_algorithm="sha256",
            digest=hashlib.sha256(xml_bytes).hexdigest(),
        ),
        bounded_preview=preview,
        preview_digest=preview_digest_witness(preview),
        source_lane=record.namespace or "finlex_consolidated",
        metadata={
            "namespace": record.namespace,
            "sid": record.sid,
            "lang": record.lang,
            "locator": locator,
            "path_version": record.path_version,
            "embedded_version_tag": record.embedded_version_tag,
            "date_consolidated": (record.date_consolidated.isoformat() if record.date_consolidated is not None else ""),
            "xml_size_bytes": len(xml_bytes),
        },
    )


def corrigendum_source_witness(
    row: Mapping[str, Any],
    *,
    source_role: str = "finland_corrigendum_pdf",
) -> SourceWitness:
    """Build a shared source witness for a Finland corrigendum PDF record."""

    source_pdf = str(row.get("source_pdf") or row.get("pdf_name") or "")
    amendment_id = str(row.get("amendment_id") or "")
    preview_parts = [
        part
        for part in (
            str(row.get("pdf_name") or ""),
            amendment_id,
            str(row.get("date_published") or ""),
            str(row.get("correction_item_count") or ""),
        )
        if part
    ]
    preview = " | ".join(preview_parts)
    digest_text = str(row.get("sha256") or "")
    return SourceWitness(
        source_role=source_role,
        artifact_id=source_pdf,
        source_unit_id=amendment_id,
        locator=source_pdf,
        version_id=str(row.get("date_published") or ""),
        source_path=source_pdf,
        digest=(DigestWitness(digest_algorithm="sha256", digest=digest_text) if digest_text else None),
        bounded_preview=preview,
        preview_digest=preview_digest_witness(preview),
        source_lane="corrigendum_pdf",
        metadata={
            "statute_id": str(row.get("statute_id") or ""),
            "amendment_id": amendment_id,
            "pdf_name": str(row.get("pdf_name") or ""),
            "source_pdf": source_pdf,
            "lang": str(row.get("lang") or ""),
            "date_published": str(row.get("date_published") or ""),
            "date_status": str(row.get("date_status") or ""),
            "correction_item_count": int(row.get("correction_item_count") or 0),
            "size_bytes": int(row.get("size_bytes") or 0),
        },
    )


def finlex_html_topology_source_witness(
    row: Mapping[str, Any],
    *,
    statute_id: str,
    source_role: str = "finlex_html_topology_audit",
) -> SourceWitness:
    """Build a shared source witness for a Finland HTML/XML topology audit."""

    html_url = str(row.get("html_url") or "")
    missing_from_xml = string_sequence(row.get("missing_from_xml"))
    extra_in_xml = string_sequence(row.get("extra_in_xml"))
    noncommensurable_reason = str(row.get("noncommensurable_reason") or "")
    html_error = str(row.get("html_error") or "")
    preview = " | ".join(
        part
        for part in (
            statute_id,
            html_url,
            f"missing_from_xml={','.join(missing_from_xml[:10])}" if missing_from_xml else "",
            f"extra_in_xml={','.join(extra_in_xml[:10])}" if extra_in_xml else "",
            f"noncommensurable_reason={noncommensurable_reason}" if noncommensurable_reason else "",
            f"html_error={html_error}" if html_error else "",
        )
        if part
    )
    return SourceWitness(
        source_role=source_role,
        artifact_id=statute_id,
        source_unit_id=statute_id,
        locator=html_url,
        version_id="live",
        source_path=html_url,
        bounded_preview=preview,
        preview_digest=preview_digest_witness(preview),
        source_lane="finlex_html_live_audit",
        metadata={
            "statute_id": statute_id,
            "html_url": html_url,
            "mismatch": bool(row.get("mismatch")),
            "missing_from_xml": missing_from_xml,
            "extra_in_xml": extra_in_xml,
            "missing_from_xml_count": len(missing_from_xml),
            "extra_in_xml_count": len(extra_in_xml),
            "html_error": html_error,
            "noncommensurable_reason": noncommensurable_reason,
        },
    )


def source_adjudication_lineage_source_witness_rows(
    source_adjudication: Any,
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Project Finland source-adjudication lineage rows into source witnesses."""

    if source_adjudication is None:
        return []
    adjudication_statute = str(_field(source_adjudication, "statute_id", "") or statute_id or "")
    lineage = mapping_sequence(_field(source_adjudication, "lineage", ()))
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(lineage, start=1):
        amendment_id = str(row.get("statute_id") or "")
        if not amendment_id:
            continue
        rows.append(
            source_adjudication_lineage_source_witness(
                row,
                statute_id=adjudication_statute,
                index=index,
            ).to_dict()
        )
    return rows


def source_adjudication_lineage_source_witness(
    row: Mapping[str, Any],
    *,
    statute_id: str,
    index: int,
    source_role: str = "finland_source_lineage_amendment",
) -> SourceWitness:
    """Build source footing for one Finland replay source-chain lineage row."""

    amendment_id = str(row.get("statute_id") or "")
    effective_date = str(row.get("effective_date") or "")
    issue_date = str(row.get("issue_date") or "")
    title = str(row.get("title") or "")
    included = bool(row.get("included"))
    selection_basis = str(row.get("selection_basis") or "")
    preview = " | ".join(
        part
        for part in (
            f"parent={statute_id}",
            f"sequence={index}",
            amendment_id,
            effective_date,
            issue_date,
            "included" if included else "not_included",
            selection_basis,
            title,
        )
        if part
    )
    return SourceWitness(
        source_role=source_role,
        artifact_id=amendment_id,
        source_unit_id=amendment_id,
        locator=amendment_id,
        version_id=effective_date or issue_date,
        source_path=amendment_id,
        bounded_preview=preview,
        preview_digest=preview_digest_witness(preview),
        source_lane="finland_source_adjudication_lineage",
        metadata={
            "statute_id": statute_id,
            "sequence": index,
            "amendment_id": amendment_id,
            "title": title,
            "effective_date": effective_date,
            "issue_date": issue_date,
            "sort_mode": str(row.get("sort_mode") or ""),
            "included": included,
            "selection_basis": selection_basis,
        },
    )


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


__all__ = [
    "consolidated_artifact_source_witness",
    "corrigendum_source_witness",
    "finlex_html_topology_source_witness",
    "source_adjudication_lineage_source_witness",
    "source_adjudication_lineage_source_witness_rows",
]
