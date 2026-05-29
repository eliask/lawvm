"""Shared XML helpers for the UK legislation frontend."""
from __future__ import annotations

from lxml import etree as ET
from functools import lru_cache
from typing import Sequence

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.uk_grafter import _LEG_NS


@lru_cache(maxsize=4096)
def _local_tag_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _tag(el: ET._Element) -> str:
    return _local_tag_name(el.tag)


def _text_content(el: ET._Element) -> str:
    """Recursively collect normalised text."""
    parts: list[str] = []
    for node in el.iter():
        if node.text:
            parts.append(node.text)
        if node.tail and node is not el:
            parts.append(node.tail)
    return " ".join(" ".join(parts).split())


def _direct_structural_num(el: ET._Element) -> str:
    """Return the node's own structural number, not a descendant's number."""
    num_el = el.find(f"./{{{_LEG_NS}}}Pnumber")
    if num_el is None:
        num_el = el.find(f"./{{{_LEG_NS}}}Number")
    if num_el is None and _tag(el) == "Schedule":
        num_el = el.find(f".//{{{_LEG_NS}}}Number")
    if num_el is None:
        return ""
    return _text_content(num_el)


def _structural_children(el: ET._Element) -> tuple[ET._Element, ...]:
    structural_tags = {
        "Part",
        "Chapter",
        "EUChapter",
        "Pblock",
        "P1group",
        "Section",
        "P1",
        "Article",
        "Rule",
        "Subsection",
        "P2",
        "P3",
        "P4",
        "Schedule",
    }
    return tuple(child for child in list(el) if _tag(child) in structural_tags)


def _clone_element(el: ET._Element) -> ET._Element:
    return ET.fromstring(ET.tostring(el, encoding="unicode"))


def get_all_eids(nodes: Sequence[IRNode]) -> list[str]:
    """Recursively gather all eIds from an IR tree fragment."""
    eids = []
    for n in nodes:
        eid = n.attrs.get("id") or n.attrs.get("eId")
        if eid:
            eids.append(eid)
        if n.children:
            eids.extend(get_all_eids(n.children))
    return eids
