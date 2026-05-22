from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import (
    _addr_container,
    _addr_field,
    _addr_leaf_kind,
    _canonicalize_eid_tail_label,
    _canonicalize_schedule_paragraph_eid_label,
    _schedule_target_levels,
)
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address, uk_kind_matches
from lawvm.uk_legislation.uk_grafter import _clean_num


def _source_after_insertion_anchor(
    text: str,
    target: Optional[LegalAddress] = None,
) -> tuple[Optional[str], Optional[str]]:
    lead = " ".join(str(text or "").split())
    if not lead:
        return None, None
    after_target = (
        r"after\s+(?P<kind>sub-?paragraph|paragraph|subsection|section|ss\.|s\.)\s*"
        r"\(?(?P<label>[0-9a-zA-Z]+)\)?\b"
    )
    after_target_start = r"\bafter\s+(?:sub-?paragraph|paragraph|subsection|section|ss\.|s\.)\s*\(?[0-9a-zA-Z]+\)?\b"
    scoped_matches = tuple(
        re.finditer(
            rf"\b{after_target}(?:(?!{after_target_start}).)*?\binsert(?:ed)?\b",
            lead,
            flags=re.I,
        )
    )
    if scoped_matches:
        match = scoped_matches[-1]
        anchor_source = "extracted_source_insert_scoped_after_clause"
    else:
        match = re.search(rf"\b{after_target}", lead, flags=re.I)
        anchor_source = "extracted_source_after_clause"
    if match is None:
        return None, None
    kind = str(match.group("kind") or "").lower()
    label = str(match.group("label") or "")
    if not label:
        return None, None
    if target is not None and len(target.path) > 1:
        parent = target.parent()
        sibling_kind = _addr_leaf_kind(target)
        if parent is not None and sibling_kind:
            sibling = LegalAddress(path=(*parent.path, (sibling_kind, label)))
            return _fallback_target_eid(sibling), anchor_source
    prefix = "p1" if kind == "paragraph" else "section"
    return f"{prefix}-{label}", anchor_source


def _fallback_target_eid(addr: LegalAddress) -> str:
    """Return the UK local fallback eId shape for an address without oracle data."""
    addr = canonicalize_uk_address(addr)
    container = _addr_container(addr)
    section = _addr_field(addr, "schedule") or _addr_field(addr, "section")
    part = _addr_field(addr, "part")
    chapter = _addr_field(addr, "chapter")
    parts: list[str] = []
    if container == "schedule":
        parts.append(f"schedule-{_clean_num(section)}" if section else "schedule")
        if part:
            parts.append(f"part-{_clean_num(part)}")
        if chapter:
            parts.append(f"chapter-{_clean_num(chapter)}")
        paragraph, subsection, item_labels = _schedule_target_levels(addr)
        if paragraph:
            parts.append(f"paragraph-{_canonicalize_schedule_paragraph_eid_label(paragraph)}")
        if subsection:
            parts.append(_clean_num(subsection))
        for item_label in item_labels:
            parts.append(_canonicalize_eid_tail_label(item_label))
        return "-".join(part for part in parts if part)

    if section:
        parts.append(f"section-{_clean_num(section)}")
    for suffix_label in _body_target_eid_suffixes(addr):
        parts.append(_canonicalize_eid_tail_label(suffix_label))
    return "-".join(part for part in parts if part)


def _body_target_eid_suffixes(addr: LegalAddress) -> list[str]:
    """Return body descendant labels in UK eId order after the section root."""
    suffixes: list[str] = []
    seen_section_root = False
    for kind, label in addr.path:
        if kind in {"section", "article", "rule", "regulation"}:
            seen_section_root = True
            continue
        if not seen_section_root:
            continue
        if kind in {"subsection", "paragraph", "subparagraph", "item", "point"} and label:
            suffixes.append(label)
    return suffixes


def _source_before_insertion_anchor(
    text: str,
    target: LegalAddress,
) -> tuple[Optional[str], Optional[str]]:
    lead = " ".join(str(text or "").split())
    if not lead:
        return None, None
    match = re.search(
        r"\bbefore\s+(?P<kind>sub-?paragraph|paragraph|subsection|item)\s*"
        r"\(?(?P<label>[0-9a-zA-Z]+)\)?\s+insert\b",
        lead,
        flags=re.I,
    )
    if match is None:
        return None, None
    if len(target.path) < 2:
        return None, None
    label = str(match.group("label") or "")
    if not label:
        return None, None
    parent = target.parent()
    if parent is None:
        return None, None
    sibling_kind = _addr_leaf_kind(target)
    if not sibling_kind:
        return None, None
    sibling = LegalAddress(path=(*parent.path, (sibling_kind, label)))
    return _fallback_target_eid(sibling), "extracted_source_before_clause"


def _target_anchor_eid(target: LegalAddress) -> Optional[str]:
    if not target.path:
        return None
    if len(target.path) != 1:
        return _fallback_target_eid(target)
    kind, label = target.path[0]
    clean_label = _clean_num(label)
    if not clean_label:
        return None
    clean_kind = str(kind or "").lower()
    if clean_kind == "section":
        return f"section-{clean_label}"
    if clean_kind == "paragraph":
        return f"p1-{clean_label}"
    return None


@lru_cache(maxsize=131072)
def _uk_match_kind_label_cached(
    node_kind_raw: str,
    node_label_raw: str,
    target_kind_raw: str,
    target_label_raw: str,
) -> bool:
    nk = str(node_kind_raw)
    tk = target_kind_raw.lower()
    node_label = _clean_num(node_label_raw)
    want_label = _clean_num(target_label_raw) if target_label_raw else ""

    if not uk_kind_matches(
        node_kind=nk,
        target_kind=tk,
        node_label=node_label,
        target_label=want_label,
    ):
        return False

    if not target_label_raw:
        return True
    if tk == "schedule" and want_label:
        schedule_labels = {want_label}
        if want_label.startswith("schedule "):
            schedule_labels.add(want_label.removeprefix("schedule ").strip())
        else:
            schedule_labels.add(f"schedule {want_label}")
        return node_label in schedule_labels
    return node_label == want_label


def uk_match_kind_label(node: Any, kind: str, label: Optional[str]) -> bool:
    """Return whether a UK IR-like node matches a target kind/label pair."""
    return _uk_match_kind_label_cached(
        str(node.kind),
        str(node.label or ""),
        str(kind or ""),
        str(label or ""),
    )
