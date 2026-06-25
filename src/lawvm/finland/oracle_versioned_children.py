"""Finlex consolidated XML versioned-child deduplication.

Finlex PIT XML can carry multiple sibling snapshots that share an eId slot.
Some are editorial prior-wording shadows; others are distinct live provisions
encoded with a reused positional eId. This module owns the comparison-only
dedup policy shared by whole-oracle text extraction and section comparison.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from lawvm.core.regex_safety import compile_classifier_regex
from typing import Optional

from lxml import etree

from lawvm.finland.helpers import _norm_num_token

# lawvm-regex: diagnostic Finlex eId version suffix parser for oracle dedup
_ORACLE_VERSION_SUFFIX_RE = re.compile(r"v(?P<version>\d{8})$")
# lawvm-regex: diagnostic Finlex editorial prior-wording note classifier
_PRIOR_WORDING_RE = compile_classifier_regex(r"\bAiempi sanamuoto kuuluu\b", re.IGNORECASE, classifier_id="fi.oracle_versioned_children.prior_wording_re")


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


def _oracle_eid_component_version(el: etree._Element) -> int:
    eid = el.get("eId", "")
    if not eid:
        return -1
    # lawvm-regex: diagnostic Finlex eId version suffix parser for oracle dedup
    match = _ORACLE_VERSION_SUFFIX_RE.search(eid.split("__")[-1])
    if match is None:
        return -1
    return int(match.group("version"))


def _has_finlex_original_version(el: etree._Element) -> bool:
    return bool(el.get("{http://data.finlex.fi/schema/finlex}originalVersion"))


def _nearest_section_has_original_version(el: etree._Element) -> bool:
    current = el.getparent()
    while current is not None:
        if _tag(current) == "section":
            return _has_finlex_original_version(current)
        current = current.getparent()
    return False


def _eid_slot_number(eid_base: str) -> tuple[str, int] | None:
    prefix, sep, tail = eid_base.rpartition("_")
    if not sep or not tail.isdigit():
        return None
    return f"{prefix}_", int(tail)


def _has_following_next_slot_child(
    child: etree._Element,
    child_tag: str,
    eid_base: str,
) -> bool:
    slot = _eid_slot_number(eid_base)
    if slot is None:
        return False
    prefix, number = slot
    sibling = child.getnext()
    while sibling is not None:
        if _tag(sibling) == child_tag:
            sibling_base = _oracle_eid_base(sibling)
            sibling_slot = _eid_slot_number(sibling_base or "")
            if sibling_slot == (prefix, number + 1):
                return True
            return False
        sibling = sibling.getnext()
    return False


def _element_clean_text(el: etree._Element) -> str:
    """Return cleaned alphanumeric-only text content of an element."""
    raw = etree.tostring(el, method="text", encoding="unicode")
    allowed = frozenset("abcdefghijklmnopqrstuvwxyz0123456789äöå")
    return "".join(ch for ch in raw.lower() if ch in allowed)


def _sequence_ratio_at_least(left: str, right: str, threshold: float) -> bool:
    matcher = SequenceMatcher(None, left, right)
    if matcher.quick_ratio() < threshold:
        return False
    return matcher.ratio() >= threshold


def strip_prior_wording_sibling(note: etree._Element) -> bool:
    """Remove the same-slot sibling introduced as Finlex prior wording.

    Finlex consolidated XML can encode a changed provision as:

    current versioned child, editorial note containing ``Aiempi sanamuoto
    kuuluu:``, prior versioned child. The note is editorial comparison metadata;
    once stripped, the following prior child must be stripped with it or the
    oracle projection counts stale text as current law.
    """
    note_text = etree.tostring(note, method="text", encoding="unicode")
    # lawvm-regex: diagnostic Finlex editorial prior-wording note classifier
    if not _PRIOR_WORDING_RE.search(note_text):
        return False
    previous = note.getprevious()
    candidate = note.getnext()
    if previous is None or candidate is None:
        return False
    if _tag(previous) != _tag(candidate):
        return False
    previous_base = _oracle_eid_base(previous)
    candidate_base = _oracle_eid_base(candidate)
    if previous_base is None or previous_base != candidate_base:
        return False
    parent = candidate.getparent()
    if parent is None:
        return False
    parent.remove(candidate)
    return True


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
            existing_has_orig = _has_finlex_original_version(existing)
            candidate_has_orig = _has_finlex_original_version(child)
            existing_text = _element_clean_text(existing)
            candidate_text = _element_clean_text(child)
            if existing_text and candidate_text:
                if existing_has_orig != candidate_has_orig:
                    candidate_is_unversioned_slot = _oracle_eid_component_version(child) < 0
                    adjacent_plain_shadow = (
                        existing_has_orig
                        and candidate_is_unversioned_slot
                        and existing.getnext() is child
                        and _has_following_next_slot_child(child, child_tag, eid_base)
                    )
                    if adjacent_plain_shadow:
                        parent.remove(child)
                        continue
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
                    if existing_has_orig and candidate_has_orig:
                        if _nearest_section_has_original_version(child):
                            continue
                    else:
                        continue
            parent.remove(child)
            continue
        seen[key] = child
