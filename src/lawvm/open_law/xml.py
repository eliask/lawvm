"""Open Law XML to LawVM IR conversion."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Iterable, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind

OPEN_LAW_LIBRARY_NS = "https://open.law/schemas/library"
OPEN_LAW_CODIFY_NS = "https://open.law/schemas/codify"

_STRUCTURAL_TAGS = frozenset({"document", "container", "section", "para", "text", "heading", "annotations", "annotation"})
_SKIPPED_INLINE_TAGS = frozenset({"prefix", "num"})
_WHITESPACE_RE = re.compile(r"\s+")


def parse_open_law_xml(xml_text: str) -> IRNode:
    """Parse Open Law XML into a LawVM ``IRNode`` tree.

    The result is intentionally a structural projection, not a full-fidelity XML
    round trip. Attributes preserve the Open Law tag and selected identity
    metadata needed for replay/audit diagnostics.
    """

    root = ET.fromstring(xml_text)
    node = _convert_element(root)
    if node.kind == IRNodeKind.BODY:
        return node
    return IRNode(kind=IRNodeKind.BODY, children=(node,))


def convert_open_law_element(element: ET.Element) -> IRNode:
    """Convert one already-parsed Open Law structural element."""

    return _convert_element(element)


def wrap_open_law_body_with_prefix(tree: IRNode, prefix: Tuple[str, ...]) -> IRNode:
    """Wrap a partial Open Law tree in explicit carried parent context.

    Public bulk files may store a chapter subtree while codify actions use the
    full COMAR locator. Callers may supply the missing parent labels explicitly;
    this helper never guesses them from the file path.
    """

    current = tree
    for label in reversed(prefix):
        children = current.children if current.kind is IRNodeKind.BODY else (current,)
        current = IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.HCONTAINER,
                    label=label,
                    attrs={"open_law_tag": "carried_context", "source_family": "open_law_explicit_path_prefix"},
                    children=children,
                ),
            ),
        )
    return current


def _convert_element(element: ET.Element) -> IRNode:
    local = _local_name(element.tag)
    if local == "document":
        return _convert_document(element)
    if local == "container":
        return _convert_container(element)
    if local == "section":
        return _convert_section(element)
    if local == "para":
        return _convert_para(element)
    if local == "text":
        return IRNode(kind=IRNodeKind.CONTENT, text=_collapse_itertext(element), attrs=_attrs(element))
    if local == "heading":
        return IRNode(kind=IRNodeKind.HEADING, text=_collapse_itertext(element), attrs=_attrs(element))
    if local == "annotations":
        return _convert_annotations(element)
    if local == "annotation":
        attrs = _attrs(element)
        attrs.update({key: value for key, value in element.attrib.items() if key in {"type", "subtype", "effective"}})
        return IRNode(kind=IRNodeKind.CONTENT, text=_collapse_itertext(element), attrs=attrs)
    return IRNode(kind=IRNodeKind.CONTENT, text=_collapse_itertext(element), attrs=_attrs(element))


def _convert_document(element: ET.Element) -> IRNode:
    children = _converted_children(element)
    attrs = _attrs(element)
    doc_id = element.attrib.get("id", "")
    if doc_id:
        attrs["open_law_id"] = doc_id
    return IRNode(kind=IRNodeKind.BODY, attrs=attrs, children=children)


def _convert_container(element: ET.Element) -> IRNode:
    label = _first_child_text(element, "num")
    attrs = _attrs(element)
    prefix = _first_child_text(element, "prefix")
    if prefix:
        attrs["prefix"] = prefix
    children = _identity_children(element) + _converted_children(element)
    return IRNode(kind=IRNodeKind.HCONTAINER, label=label or None, attrs=attrs, children=children)


def _convert_section(element: ET.Element) -> IRNode:
    label = _first_child_text(element, "num")
    attrs = _attrs(element)
    prefix = _first_child_text(element, "prefix")
    if prefix:
        attrs["prefix"] = prefix
    section_type = element.attrib.get("type", "")
    if section_type:
        attrs["open_law_section_type"] = section_type
    children = _identity_children(element) + _converted_children(element)
    return IRNode(kind=IRNodeKind.SECTION, label=label or None, attrs=attrs, children=children)


def _convert_para(element: ET.Element) -> IRNode:
    label = _first_child_text(element, "num")
    attrs = _attrs(element)
    children = _identity_children(element) + _converted_children(element)
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label or None, attrs=attrs, children=children)


def _convert_annotations(element: ET.Element) -> IRNode:
    return IRNode(
        kind=IRNodeKind.HCONTAINER,
        label="annos",
        attrs=_attrs(element),
        children=_converted_children(element),
    )


def _identity_children(element: ET.Element) -> Tuple[IRNode, ...]:
    out: list[IRNode] = []
    num = _first_child_text(element, "num")
    if num:
        out.append(IRNode(kind=IRNodeKind.NUM, text=num))
    return tuple(out)


def _converted_children(element: ET.Element) -> Tuple[IRNode, ...]:
    out: list[IRNode] = []
    for child in list(element):
        local = _local_name(child.tag)
        namespace = _namespace(child.tag)
        if namespace == OPEN_LAW_CODIFY_NS:
            continue
        if local in _SKIPPED_INLINE_TAGS:
            continue
        if local in _STRUCTURAL_TAGS:
            out.append(_convert_element(child))
    return tuple(out)


def _attrs(element: ET.Element) -> dict[str, str]:
    attrs = {
        "open_law_tag": _local_name(element.tag),
    }
    source_id = element.attrib.get("id", "")
    if source_id:
        attrs["open_law_id"] = source_id
    return attrs


def _first_child_text(element: ET.Element, local_name: str) -> str:
    for child in list(element):
        if _local_name(child.tag) == local_name:
            return _collapse_itertext(child)
    return ""


def _collapse_itertext(element: ET.Element) -> str:
    return _WHITESPACE_RE.sub(" ", "".join(_iter_text_chunks(element)).strip())


def _iter_text_chunks(element: ET.Element) -> Iterable[str]:
    for chunk in element.itertext():
        if chunk:
            yield chunk


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def _namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""
