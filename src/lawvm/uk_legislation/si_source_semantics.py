"""Diagnostic inventory for UK statutory-instrument source semantics.

The records here are evidence surfaces for SI-specific semantics. They do not
lower effects or mutate replay state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from typing import Any

from lxml import etree as ET


_SI_CLASSES = frozenset({"uksi", "ssi", "wsi", "nisr", "ukci", "ukmo"})
_WS_RE = re.compile(r"\s+")
_VIRES_RE = re.compile(
    r"\b(?:in\s+exercise\s+of|powers?\s+conferred|having\s+consulted|designated\s+for\s+the\s+purposes)\b",
    re.I,
)
_BODY_COMMENCEMENT_RE = re.compile(
    r"\b(?:commencement|comes?\s+into\s+force|coming\s+into\s+force|shall\s+come\s+into\s+force)\b",
    re.I,
)
_EXTENT_RE = re.compile(r"\b(?:extends?\s+to|does\s+not\s+extend\s+to|extent)\b", re.I)
_APPLICATION_RE = re.compile(
    r"\b(?:applies?\s+to|do(?:es)?\s+not\s+apply|application\s+of\s+(?:these|this|the)\s+"
    r"(?:regulations?|order|rules?|article)|(?:these|this|the)\s+"
    r"(?:regulations?|order|rules?|article)\s+appl(?:y|ies))\b",
    re.I,
)
_REVOCATION_RE = re.compile(r"\b(?:revokes?|revoked|revocation|ceases?\s+to\s+have\s+effect|lapses?)\b", re.I)
_CORRECTION_RE = re.compile(r"\b(?:correction\s+slips?|reprints?)\b", re.I)


@dataclass(frozen=True)
class UKSISourceSemanticsRecord:
    """One diagnostic source-surface record for an SI-like document."""

    family: str
    statute_id: str
    rule_id: str
    source_path: str = ""
    status: str = "record"
    text_preview: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "family": self.family,
            "statute_id": self.statute_id,
            "rule_id": self.rule_id,
            "status": self.status,
        }
        if self.source_path:
            row["source_path"] = self.source_path
        if self.text_preview:
            row["text_preview"] = self.text_preview
        row.update(self.detail)
        return row


def is_uk_si_document_id(statute_id: str) -> bool:
    return str(statute_id or "").split("/", 1)[0] in _SI_CLASSES


def scan_si_source_semantics_bytes(
    statute_id: str,
    xml_bytes: bytes,
    *,
    source_path: str = "",
) -> tuple[UKSISourceSemanticsRecord, ...]:
    """Return diagnostic records for one SI XML source blob."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        return (
            UKSISourceSemanticsRecord(
                family="si_source_parse_error",
                statute_id=statute_id,
                rule_id="uk_si_source_xml_parse_error",
                source_path=source_path,
                status="blocking",
                detail={"exception_type": type(exc).__name__, "exception_message": str(exc)},
            ),
        )
    return scan_si_source_semantics_root(statute_id, root, source_path=source_path)


def scan_si_source_semantics_root(
    statute_id: str,
    root: ET._Element,
    *,
    source_path: str = "",
) -> tuple[UKSISourceSemanticsRecord, ...]:
    records: list[UKSISourceSemanticsRecord] = []
    records.append(_structure_record(statute_id, root, source_path=source_path))
    commencement = _commencement_record(statute_id, root, source_path=source_path)
    if commencement is not None:
        records.append(commencement)
    vires = _vires_record(statute_id, root, source_path=source_path)
    if vires is not None:
        records.append(vires)
    records.extend(_body_clause_records(statute_id, root, source_path=source_path))
    correction = _correction_record(statute_id, root, source_path=source_path)
    if correction is not None:
        records.append(correction)
    return tuple(records)


def _structure_record(
    statute_id: str,
    root: ET._Element,
    *,
    source_path: str,
) -> UKSISourceSemanticsRecord:
    tag_counts = Counter(_local_name(el) for el in root.iter() if isinstance(el.tag, str))
    main_type = _first_text(root, "DocumentMainType")
    minor_type = _first_text(root, "DocumentMinorType")
    category = _first_text(root, "DocumentCategory")
    number_of_provisions = str(root.get("NumberOfProvisions") or "")
    return UKSISourceSemanticsRecord(
        family="si_structure_vocabulary",
        statute_id=statute_id,
        rule_id="uk_si_structure_vocabulary_recorded",
        source_path=source_path,
        detail={
            "document_main_type": main_type,
            "document_minor_type": minor_type,
            "document_category": category,
            "number_of_provisions": number_of_provisions,
            "has_secondary_prelims": tag_counts["SecondaryPrelims"] > 0,
            "has_enacting_text": tag_counts["EnactingText"] > 0,
            "has_signed_section": tag_counts["SignedSection"] > 0,
            "has_body": tag_counts["Body"] > 0,
            "has_schedules": tag_counts["Schedules"] > 0 or tag_counts["Schedule"] > 0,
            "has_explanatory_notes": tag_counts["ExplanatoryNotes"] > 0,
            "body_p1_count": tag_counts["P1"],
            "schedule_count": tag_counts["Schedule"],
        },
    )


def _commencement_record(
    statute_id: str,
    root: ET._Element,
    *,
    source_path: str,
) -> UKSISourceSemanticsRecord | None:
    coming_into_force = _elements(root, "ComingIntoForce")
    if not coming_into_force:
        return None
    dates: list[str] = []
    text_values: list[str] = []
    for el in coming_into_force:
        for date_el in el.iter():
            if not isinstance(date_el.tag, str):
                continue
            date_value = str(date_el.get("Date") or "")
            if date_value and date_value not in dates:
                dates.append(date_value)
        text = _text(el)
        if text and text not in text_values:
            text_values.append(text)
    status = "single_date" if len(dates) == 1 else "multiple_or_textual"
    if not dates:
        status = "textual_or_missing_date"
    return UKSISourceSemanticsRecord(
        family="si_commencement_surface",
        statute_id=statute_id,
        rule_id="uk_si_commencement_surface_recorded",
        source_path=source_path,
        status=status,
        text_preview=_preview(" | ".join(text_values)),
        detail={
            "coming_into_force_element_count": len(coming_into_force),
            "coming_into_force_dates": tuple(dates),
            "coming_into_force_text_count": len(text_values),
        },
    )


def _vires_record(
    statute_id: str,
    root: ET._Element,
    *,
    source_path: str,
) -> UKSISourceSemanticsRecord | None:
    el = _first_element(root, "EnactingText")
    if el is None:
        return None
    text = _text(el)
    if not text:
        return None
    return UKSISourceSemanticsRecord(
        family="si_vires_recital_surface",
        statute_id=statute_id,
        rule_id="uk_si_vires_recital_surface_recorded",
        source_path=source_path,
        status="matched" if _VIRES_RE.search(text) else "present_unclassified",
        text_preview=_preview(text),
        detail={
            "citation_count": sum(1 for child in el.iter() if _local_name(child) == "Citation"),
            "has_vires_phrase": bool(_VIRES_RE.search(text)),
        },
    )


def _body_clause_records(
    statute_id: str,
    root: ET._Element,
    *,
    source_path: str,
) -> tuple[UKSISourceSemanticsRecord, ...]:
    body = _first_element(root, "Body")
    if body is None:
        return ()
    records: list[UKSISourceSemanticsRecord] = []
    for p1 in _elements(body, "P1"):
        text = _text(p1)
        if not text:
            continue
        families = _body_clause_families(text)
        if not families:
            continue
        label = _own_number(p1)
        title = _own_title(p1)
        for family, rule_id in families:
            records.append(
                UKSISourceSemanticsRecord(
                    family=family,
                    statute_id=statute_id,
                    rule_id=rule_id,
                    source_path=source_path,
                    text_preview=_preview(text),
                    detail={"provision_label": label, "provision_title": title},
                )
            )
    return tuple(records)


def _body_clause_families(text: str) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    if _BODY_COMMENCEMENT_RE.search(text):
        out.append(("si_body_commencement_clause_surface", "uk_si_body_commencement_clause_surface_recorded"))
    if _EXTENT_RE.search(text):
        out.append(("si_extent_clause_surface", "uk_si_extent_clause_surface_recorded"))
    if _APPLICATION_RE.search(text):
        out.append(("si_application_clause_surface", "uk_si_application_clause_surface_recorded"))
    if _REVOCATION_RE.search(text):
        out.append(("si_revocation_lapse_surface", "uk_si_revocation_lapse_surface_recorded"))
    return tuple(out)


def _correction_record(
    statute_id: str,
    root: ET._Element,
    *,
    source_path: str,
) -> UKSISourceSemanticsRecord | None:
    text = _text(root)
    if not _CORRECTION_RE.search(text):
        return None
    return UKSISourceSemanticsRecord(
        family="si_correction_slip_surface",
        statute_id=statute_id,
        rule_id="uk_si_correction_slip_surface_recorded",
        source_path=source_path,
        text_preview=_preview(text),
    )


def _elements(root: ET._Element, local_name: str) -> tuple[ET._Element, ...]:
    return tuple(el for el in root.iter() if _local_name(el) == local_name)


def _first_element(root: ET._Element, local_name: str) -> ET._Element | None:
    for el in root.iter():
        if _local_name(el) == local_name:
            return el
    return None


def _first_text(root: ET._Element, local_name: str) -> str:
    el = _first_element(root, local_name)
    if el is None:
        return ""
    text = _text(el)
    if text:
        return text
    return str(el.get("Value") or "")


def _own_number(el: ET._Element) -> str:
    for child in el:
        if _local_name(child) in {"Pnumber", "Number"}:
            return _text(child)
    return ""


def _own_title(el: ET._Element) -> str:
    for child in el:
        if _local_name(child) == "Title":
            return _text(child)
    return ""


def _text(el: ET._Element) -> str:
    return _WS_RE.sub(" ", " ".join(part for part in el.itertext() if part)).strip()


def _preview(text: str, *, limit: int = 240) -> str:
    normalized = _WS_RE.sub(" ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _local_name(el: ET._Element) -> str:
    if not isinstance(el.tag, str):
        return ""
    return ET.QName(el).localname
