from __future__ import annotations

import re
from typing import Any, Mapping, Optional

import Levenshtein
from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.xml_ingest import xml_element_to_text
from lawvm.tools.section_keys import norm_section_label, normalize_address_filter


def clean_text(text: str) -> str:
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


def render_node_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, IRNode):
        return irnode_to_text(node)
    if isinstance(node, etree._Element):
        return xml_element_to_text(node)
    return str(node)


def summarize_node(node: Any, max_chars: int = 240) -> Optional[dict]:
    if node is None:
        return None
    text = " ".join(render_node_text(node).split())
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    if isinstance(node, IRNode):
        return {
            "kind": node.kind,
            "label": node.label or "",
            "children": len(node.children),
            "text": text,
        }
    return {
        "kind": getattr(node, "tag", "xml"),
        "label": "",
        "children": len(list(node)),
        "text": text,
    }


def resolve_section_key(sections: Mapping[str, Any], section_filter: str) -> str:
    if not sections:
        raise ValueError("no sections available")
    if ":" in section_filter:
        wanted = normalize_address_filter(section_filter)
        if wanted in sections:
            return wanted
        matches = [key for key in sections if key.endswith(f"/{wanted}") or key == wanted]
    else:
        wanted = norm_section_label(section_filter)
        exact = [key for key in sections if key == wanted or key == f"section:{wanted}"]
        if len(exact) == 1:
            return exact[0]
        matches = [key for key in sections if key.endswith(f"/section:{wanted}")]
        if not matches:
            matches = exact
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"section not found: {section_filter}")
    raise ValueError(
        f"ambiguous section filter {section_filter!r}: {', '.join(sorted(matches))}"
    )


def score_text_pair(left: str, right: str) -> float:
    c_left = clean_text(left)
    c_right = clean_text(right)
    if not c_left and not c_right:
        return 1.0
    if not c_left or not c_right:
        return 0.0
    return Levenshtein.ratio(c_left, c_right)
