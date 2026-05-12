"""Parser for Open Law ``codify:*`` operation XML."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Tuple

from lawvm.open_law.models import OpenLawAction, OpenLawFinding, OpenLawOperation
from lawvm.open_law.xml import OPEN_LAW_CODIFY_NS, convert_open_law_element

_LIBRARY_STRUCTURAL_PAYLOAD_TAGS = frozenset({"container", "section", "para", "text", "heading", "annotations"})


def parse_open_law_codify_ops(xml_text: str, *, source_id: str = "") -> Tuple[OpenLawOperation, ...]:
    """Extract typed Open Law codification operations from one XML document."""

    root = ET.fromstring(xml_text)
    effective = _first_descendant_text(root, "effective")
    out: list[OpenLawOperation] = []
    sequence = 1
    for element in root.iter():
        namespace, local = _split_tag(element.tag)
        if namespace != OPEN_LAW_CODIFY_NS:
            continue
        action = _action_from_local(local)
        path = _parse_path(element.attrib.get("path", ""))
        op_source_id = source_id or root.attrib.get("id", "")
        op_id = f"{op_source_id or 'open-law'}:{sequence}"
        payload_elements, diagnostics = _payload_elements_and_diagnostics(element, op_id=op_id, path=path)
        if len(payload_elements) > 1:
            diagnostics = diagnostics + (
                OpenLawFinding(
                    kind="open_law_codify_multiple_payload_children",
                    message="Open Law codify operation has multiple structural payload children; replay refuses to drop unclaimed siblings.",
                    op_id=op_id,
                    path=path,
                    blocking=True,
                ),
            )
        payload_element = payload_elements[0] if len(payload_elements) == 1 and not diagnostics else None
        payload = convert_open_law_element(payload_element) if payload_element is not None else None
        out.append(
            OpenLawOperation(
                op_id=op_id,
                sequence=sequence,
                action=action,
                doc=element.attrib.get("doc", ""),
                path=path,
                source_id=op_source_id,
                effective=effective,
                expire_date=element.attrib.get("date", ""),
                history=_parse_bool(element.attrib.get("history", "true")),
                applicability=element.attrib.get("applicability", ""),
                payload=payload,
                raw_action=local,
                diagnostics=diagnostics,
            )
        )
        sequence += 1
    return tuple(out)


def _action_from_local(local: str) -> OpenLawAction:
    if local == "replace":
        return OpenLawAction.REPLACE
    if local == "replace-or-insert":
        return OpenLawAction.REPLACE_OR_INSERT
    if local == "expire":
        return OpenLawAction.EXPIRE
    return OpenLawAction.UNSUPPORTED


def _parse_path(value: str) -> Tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _parse_bool(value: str) -> bool:
    return value.strip().lower() not in {"false", "0", "no"}


def _payload_element(element: ET.Element) -> ET.Element | None:
    payload_elements = _payload_elements(element)
    return payload_elements[0] if payload_elements else None


def _payload_elements(element: ET.Element) -> tuple[ET.Element, ...]:
    payload_elements, _diagnostics = _payload_elements_and_diagnostics(element, op_id="", path=())
    return payload_elements


def _payload_elements_and_diagnostics(
    element: ET.Element,
    *,
    op_id: str,
    path: Tuple[str, ...],
) -> tuple[tuple[ET.Element, ...], tuple[OpenLawFinding, ...]]:
    out: list[ET.Element] = []
    unsupported: list[str] = []
    for child in list(element):
        namespace, local = _split_tag(child.tag)
        if namespace == OPEN_LAW_CODIFY_NS:
            continue
        if local in _LIBRARY_STRUCTURAL_PAYLOAD_TAGS:
            out.append(child)
            continue
        unsupported.append(local)
    diagnostics: tuple[OpenLawFinding, ...] = ()
    if unsupported:
        diagnostics = (
            OpenLawFinding(
                kind="open_law_codify_unsupported_payload_child",
                message=(
                    "Open Law codify operation contains unsupported payload child elements "
                    f"({_format_payload_child_tags(unsupported)}); replay refuses to drop unclaimed siblings."
                ),
                op_id=op_id,
                path=path,
                blocking=True,
            ),
        )
    return tuple(out), diagnostics


def _format_payload_child_tags(tags: list[str]) -> str:
    return ", ".join(sorted(set(tags)))


def _first_descendant_text(element: ET.Element, local_name: str) -> str:
    for child in element.iter():
        if _split_tag(child.tag)[1] == local_name:
            text = "".join(chunk for chunk in child.itertext() if chunk).strip()
            if text:
                return " ".join(text.split())
    return ""


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag
