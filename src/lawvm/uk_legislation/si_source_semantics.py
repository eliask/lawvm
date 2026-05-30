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
    r"\b(?:applies?\s+(?:only\s+)?(?:in\s+relation\s+)?to|do(?:es)?\s+not\s+apply|"
    r"application\s+of\s+(?:these|this|the)\s+"
    r"(?:regulations?|order|rules?|article)|(?:these|this|the)\s+"
    r"(?:regulations?|order|rules?|article)\s+appl(?:y|ies))\b",
    re.I,
)
_REVOCATION_RE = re.compile(r"\b(?:revokes?|revoked|revocation|ceases?\s+to\s+have\s+effect|lapses?)\b", re.I)
_CORRECTION_RE = re.compile(r"\b(?:correction\s+slips?|reprints?|reprinted)\b", re.I)
_AMENDMENT_PAYLOAD_ANCESTORS = frozenset({"BlockAmendment", "InlineAmendment"})
_VIRES_MARKERS = (
    ("exercise_of_powers", ("in exercise of",)),
    ("powers_conferred", ("power conferred", "powers conferred")),
    ("consultation", ("having consulted", "after consultation", "consulted with")),
    ("designation", ("designated for the purposes",)),
    ("approval", ("with the approval", "approved by")),
)
_REVOCATION_LAPSE_MARKERS = (
    ("revocation", ("revoke", "revoked", "revokes", "revocation")),
    ("cessation", ("cease to have effect", "ceases to have effect")),
    ("lapse", ("lapse", "lapses")),
)
_CORRECTION_MARKERS = (
    ("correction_slip", ("correction slip", "correction slips")),
    ("reprint", ("reprint", "reprinted", "reprints")),
)
_GEOGRAPHIC_TERM_MARKERS = (
    ("northern_ireland", "northern ireland"),
    ("england", "england"),
    ("wales", "wales"),
    ("scotland", "scotland"),
    ("great_britain", "great britain"),
    ("united_kingdom", "united kingdom"),
    ("channel_islands", "channel islands"),
    ("isle_of_man", "isle of man"),
)


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
            "citation_texts": _bounded_child_texts(el, "Citation"),
            "has_vires_phrase": bool(_VIRES_RE.search(text)),
            "vires_markers": _vires_markers(text),
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
        source_role = _body_clause_source_role(p1)
        status = "payload_carried" if source_role == "amendment_payload_provision" else "record"
        family_names = frozenset(family for family, _rule_id in families)
        geographic_terms = _geographic_terms(text)
        relation = _extent_application_relation(family_names)
        revocation_lapse_kinds = _revocation_lapse_kinds(text)
        for family, rule_id in families:
            records.append(
                UKSISourceSemanticsRecord(
                    family=family,
                    statute_id=statute_id,
                    rule_id=rule_id,
                    source_path=source_path,
                    status=status,
                    text_preview=_preview(text),
                    detail={
                        "provision_label": label,
                        "provision_title": title,
                        "source_role": source_role,
                        "geographic_terms": geographic_terms,
                        "extent_application_relation": relation,
                        "revocation_lapse_kinds": revocation_lapse_kinds,
                    },
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
    matches = _correction_matches(root)
    if not matches:
        return None
    marker_kinds: list[str] = []
    for match in matches:
        for kind in match["marker_kinds"]:
            if kind not in marker_kinds:
                marker_kinds.append(kind)
    return UKSISourceSemanticsRecord(
        family="si_correction_slip_surface",
        statute_id=statute_id,
        rule_id="uk_si_correction_slip_surface_recorded",
        source_path=source_path,
        text_preview=matches[0]["text_preview"],
        detail={
            "correction_marker_kinds": tuple(marker_kinds),
            "correction_match_count": len(matches),
            "correction_contexts": tuple(matches[:12]),
        },
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


def _body_clause_source_role(el: ET._Element) -> str:
    for ancestor in el.iterancestors():
        if _local_name(ancestor) in _AMENDMENT_PAYLOAD_ANCESTORS:
            return "amendment_payload_provision"
    return "instrument_body_provision"


def _geographic_terms(text: str) -> tuple[str, ...]:
    normalized = str(text or "").lower()
    return tuple(label for label, marker in _GEOGRAPHIC_TERM_MARKERS if marker in normalized)


def _extent_application_relation(family_names: frozenset[str]) -> str:
    has_extent = "si_extent_clause_surface" in family_names
    has_application = "si_application_clause_surface" in family_names
    if has_extent and has_application:
        return "combined_extent_and_application"
    if has_extent:
        return "extent_only"
    if has_application:
        return "application_only"
    return "not_extent_or_application"


def _revocation_lapse_kinds(text: str) -> tuple[str, ...]:
    normalized = str(text or "").lower()
    return tuple(
        label
        for label, markers in _REVOCATION_LAPSE_MARKERS
        if any(marker in normalized for marker in markers)
    )


def _vires_markers(text: str) -> tuple[str, ...]:
    normalized = str(text or "").lower()
    return tuple(
        label
        for label, markers in _VIRES_MARKERS
        if any(marker in normalized for marker in markers)
    )


def _correction_matches(root: ET._Element) -> tuple[dict[str, Any], ...]:
    matches: list[dict[str, Any]] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        fields: list[tuple[str, str]] = []
        if el.text and _CORRECTION_RE.search(el.text):
            fields.append(("text", el.text))
        if el.tail and _CORRECTION_RE.search(el.tail):
            fields.append(("tail", el.tail))
        for key, value in el.attrib.items():
            if _CORRECTION_RE.search(value):
                fields.append((f"attr:{ET.QName(key).localname}", value))
        for source_field, text in fields:
            matches.append(
                {
                    "source_tag": _local_name(el),
                    "source_field": source_field,
                    "source_path_hint": _element_path_hint(el),
                    "marker_kinds": _correction_marker_kinds(text),
                    "text_preview": _preview(text),
                }
            )
    return tuple(matches)


def _correction_marker_kinds(text: str) -> tuple[str, ...]:
    normalized = str(text or "").lower()
    return tuple(
        label
        for label, markers in _CORRECTION_MARKERS
        if any(marker in normalized for marker in markers)
    )


def _element_path_hint(el: ET._Element, *, limit: int = 8) -> str:
    names: list[str] = []
    current = el
    while current is not None and len(names) < limit:
        if isinstance(current.tag, str):
            names.append(_local_name(current))
        current = current.getparent()
    return ">".join(reversed(names))


def _bounded_child_texts(el: ET._Element, local_name: str, *, limit: int = 12) -> tuple[str, ...]:
    texts: list[str] = []
    for child in el.iter():
        if _local_name(child) != local_name:
            continue
        text = _preview(_text(child), limit=160)
        if text and text not in texts:
            texts.append(text)
        if len(texts) >= limit:
            break
    return tuple(texts)


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
