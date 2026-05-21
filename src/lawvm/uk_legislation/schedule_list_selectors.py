from __future__ import annotations

import re
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind


UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID = "uk_effect_schedule_list_entry_insert"


def _strip_schedule_entry_phrase(raw: str) -> str:
    text = " ".join(str(raw or "").split()).strip(" ,;.")
    text = text.strip("“”\"'‘’")
    text = " ".join(text.split()).strip(" ,;.")
    return text


def _strip_schedule_entry_payload(raw: str) -> str:
    text = _strip_schedule_entry_phrase(raw)
    text = re.sub(
        r"^(?:the\s+following\s+entry\s*)[—–-]?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s*;\s*(?:and)?\s*$", "", text, flags=re.I)
    return _strip_schedule_entry_phrase(text)


def _schedule_list_entry_selector_from_parts(
    *,
    direction: str,
    anchor_text: str,
    inserted_text: str,
    target_ref: str,
    target: LegalAddress,
) -> dict[str, Any] | None:
    direction = str(direction or "").lower()
    anchor_text = _strip_schedule_entry_phrase(anchor_text)
    inserted_text = _strip_schedule_entry_payload(inserted_text)
    if direction not in {"before", "after", "alphabetical"} or not inserted_text:
        return None
    if direction != "alphabetical" and not anchor_text:
        return None
    return {
        "rule_id": UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
        "direction": direction,
        "anchor_text": anchor_text,
        "inserted_text": inserted_text,
        "target_ref": target_ref,
        "target": str(target),
    }


def _uk_schedule_list_entry_insert_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract a deterministic schedule-list-entry sibling insertion selector."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if "table" in target_surface or "column" in text.lower():
        return None
    if _addr_container(target) != "schedule" or _addr_leaf_kind(target) not in {
        "schedule",
        "part",
        "chapter",
        "division",
    }:
        return None
    target_leaf_kind = _addr_leaf_kind(target)

    match = re.search(
        r"\b(?P<direction>before|after)\s+(?:the\s+)?entry\s+"
        r"(?:relating\s+to|relation\s+to|for)\s+(?P<anchor>.+?)"
        r"(?:,?\s+there\s+is\s+inserted|\s+insert\b)\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if match is None:
        match = re.search(
            r"\binsertion,\s*(?P<direction>before|after)\s+(?:the\s+)?entry\s+"
            r"(?:relating\s+to|for)\s+(?P<anchor>.+),?\s+of\s+(?P<payload>.+)$",
            text,
            re.I,
        )
    if match is None and target_leaf_kind == "schedule":
        match = re.search(
            r"\binsert\s+(?P<direction>before|after)\s+[“\"'](?P<anchor>.+?)[”\"']\s*[—–-]\s*(?P<payload>.+)$",
            text,
            re.I,
        )
    if match is None and target_leaf_kind == "schedule":
        match = re.search(
            r"\b(?P<direction>before|after)\s+[“\"'](?P<anchor>.+?)[”\"']\s+"
            r"(?:,?\s+)?(?:there\s+is\s+)?insert(?:ed)?\s*[—–-]?\s*(?P<payload>.+)$",
            text,
            re.I,
        )
    if match is not None:
        selector = _schedule_list_entry_selector_from_parts(
            direction=str(match.group("direction") or "").lower(),
            anchor_text=match.group("anchor"),
            inserted_text=match.group("payload"),
            target_ref=target_ref,
            target=target,
        )
        if selector is not None and re.search(r"\bentry\s+relation\s+to\b", text, re.I):
            selector["source_anchor_form"] = "entry_relation_to_typo"
        return selector

    match = re.search(
        r"\bat\s+(?:an?|the)\s+appropriate\s+place,?\s+in\s+alphabetical\s+order,?\s+"
        r"insert\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if match is None:
        return None
    return _schedule_list_entry_selector_from_parts(
        direction="alphabetical",
        anchor_text="",
        inserted_text=match.group("payload"),
        target_ref=target_ref,
        target=target,
    )
