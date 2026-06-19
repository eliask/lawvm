"""Finlex consolidated XML versioned-child deduplication.

Finlex PIT XML can carry multiple sibling snapshots that share an eId slot.
Some are editorial prior-wording shadows; others are distinct live provisions
encoded with a reused positional eId. This module owns the comparison-only
dedup policy shared by whole-oracle text extraction and section comparison.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Optional

from lxml import etree

from lawvm.finland.helpers import _norm_num_token

_ORACLE_VERSION_SUFFIX_RE = re.compile(r"v\d{8}$")


def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def norm_versioned_child_label(s: str) -> str:
    return _norm_num_token(s.replace("*", ""))


def _oracle_eid_base(el: etree._Element) -> Optional[str]:
    eid = el.get("eId", "")
    if not eid:
        return None
    return _ORACLE_VERSION_SUFFIX_RE.sub("", eid.split("__")[-1])


def _element_clean_text(el: etree._Element) -> str:
    """Return cleaned alphanumeric-only text content of an element."""
    raw = etree.tostring(el, method="text", encoding="unicode")
    return re.sub(r"[^a-z0-9äöå]", "", raw.lower())


def _sequence_ratio_at_least(left: str, right: str, threshold: float) -> bool:
    matcher = SequenceMatcher(None, left, right)
    if matcher.quick_ratio() < threshold:
        return False
    return matcher.ratio() >= threshold


def dedup_versioned_children(parent: etree._Element, child_tag: str) -> None:
    """Remove duplicate versioned children with the same eId base.

    Finlex consolidated XML sometimes embeds multiple versioned snapshots of the
    same provision, such as ``para_3v20140649`` and ``para_3v20230499`` both
    representing item 3. Those are prior-wording display shadows and should not
    appear as separate live provisions in comparison text.

    Finlex can also assign the same positional slot to genuinely distinct live
    provisions. For example, ``2012/316`` has an existing unnumbered subsection
    and a later inserted unnumbered fee subsection that share the same eId base.
    Dissimilar same-slot siblings are preserved unless the originalVersion shape
    identifies the candidate as a prior-wording shadow.
    """
    seen: dict[str, etree._Element] = {}
    finlex_orig_attr = "{http://data.finlex.fi/schema/finlex}originalVersion"
    for child in list(parent):
        if _tag(child) != child_tag:
            continue
        eid_base = _oracle_eid_base(child)
        if not eid_base:
            continue
        num_text = _num_text(child)
        key = f"{eid_base}\x00{norm_versioned_child_label(num_text)}"
        if key in seen:
            existing = seen[key]
            existing_has_orig = bool(existing.get(finlex_orig_attr))
            candidate_has_orig = bool(child.get(finlex_orig_attr))
            existing_text = _element_clean_text(existing)
            candidate_text = _element_clean_text(child)
            if existing_text and candidate_text:
                if existing_has_orig != candidate_has_orig:
                    overlaps_as_prior_wording = (
                        existing_text in candidate_text
                        or candidate_text in existing_text
                        or _sequence_ratio_at_least(existing_text, candidate_text, 0.55)
                    )
                    if overlaps_as_prior_wording:
                        if existing_has_orig:
                            parent.remove(child)
                            continue
                        existing_parent = existing.getparent()
                        if existing_parent is not None:
                            existing_parent.remove(existing)
                        seen[key] = child
                        continue
                shorter = min(len(existing_text), len(candidate_text))
                longer = max(len(existing_text), len(candidate_text))
                if shorter / longer < 0.5 or not _sequence_ratio_at_least(
                    existing_text, candidate_text, 0.75
                ):
                    if not (existing.get(finlex_orig_attr) and child.get(finlex_orig_attr)):
                        continue
            parent.remove(child)
            continue
        seen[key] = child
