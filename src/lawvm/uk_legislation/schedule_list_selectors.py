from __future__ import annotations

import re
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind
from lawvm.uk_legislation.heading_facets import _is_heading_only_ref
from lawvm.uk_legislation.replay_text import _compact_normalized_text
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID = "uk_effect_schedule_list_entry_insert"
UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID = "uk_effect_schedule_list_entry_repeal"
UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID = "uk_effect_schedule_list_entry_replace"

_ENTRY_ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}


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
    text = re.sub(r"\s*,\s*(?:and|or)?\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*;\s*(?:and)?\s*$", "", text, flags=re.I)
    return _strip_schedule_entry_phrase(text)


def split_schedule_entry_insert_payload(raw: str) -> tuple[str, ...]:
    """Split source-owned schedule-entry payloads into sibling entries.

    The split is intentionally narrow: every semicolon-delimited part must look
    like a numbered paragraph entry. Otherwise the payload remains a single
    entry so lowering does not invent structure from ordinary prose.
    """
    payload = _strip_schedule_entry_payload(raw)
    if ";" not in payload:
        return (payload,) if payload else ()
    parts = tuple(
        _strip_schedule_entry_phrase(part)
        for part in re.split(r"\s*;\s*(?:and\s+)?", payload, flags=re.I)
    )
    parts = tuple(part for part in parts if part)
    if len(parts) < 2:
        return (payload,) if payload else ()
    if all(re.match(r"^paragraph\s+\d+[A-Za-z]?\b", part, flags=re.I) for part in parts):
        return parts
    return (payload,) if payload else ()


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
        "paragraph",
        "subparagraph",
    }:
        return None
    target_leaf_kind = _addr_leaf_kind(target)

    match = re.search(
        r"\b(?P<direction>before|after)\s+(?:the\s+)?"
        r"(?:(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+)?entry\s+"
        r"(?:relating\s+to|relation\s+to|for)\s+(?P<anchor>.+?)"
        r"(?:,?\s+there\s+is\s+inserted|\s+insert\b)\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if match is None:
        match = re.search(
            r"\binsertion,\s*(?P<direction>before|after)\s+(?:the\s+)?"
            r"(?:(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+)?entry\s+"
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
        ordinal = match.groupdict().get("ordinal")
        if selector is not None and ordinal:
            selector["anchor_ordinal"] = _ENTRY_ORDINALS[ordinal.lower()]
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


def _strip_schedule_entry_repeal_anchor(raw: str) -> str:
    text = _strip_schedule_entry_phrase(raw)
    text = re.sub(r"^(?:and\s+)?(?:\(?[ivxlcdm]+\)?|[a-z])\.?\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(?:is|are)\s+(?:repealed|omitted)\b.*$", "", text, flags=re.I)
    return _strip_schedule_entry_phrase(text)


def _split_schedule_entry_repeal_anchors(raw: str) -> tuple[str, ...]:
    text = _strip_schedule_entry_phrase(raw)
    if not text:
        return ()
    if ";" in text:
        coarse_parts = re.split(r"\s*;\s*(?:and\s+)?", text, flags=re.I)
    elif "," in text:
        coarse_parts = re.split(r"\s*,\s*(?:and\s+)?", text, flags=re.I)
    else:
        coarse_parts = [text]
    parts: list[str] = []
    for part in coarse_parts:
        parts.extend(re.split(r"\s+and\s+(?=the\s+)", part, flags=re.I))
    anchors: list[str] = []
    seen: set[str] = set()
    for part in parts:
        anchor = _strip_schedule_entry_repeal_anchor(part)
        key = _compact_normalized_text(anchor)
        if not anchor or key in seen:
            continue
        seen.add(key)
        anchors.append(anchor)
    return tuple(anchors)


def _quoted_schedule_entry_repeal_anchors(raw: str) -> tuple[str, ...]:
    anchors: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"(?:[\u201c\"](?P<double>.*?)[\u201d\"]|[\u2018'](?P<single>.*?)[\u2019'])",
        str(raw or ""),
    ):
        anchor = _strip_schedule_entry_repeal_anchor(
            match.group("double") if match.group("double") is not None else match.group("single")
        )
        key = _compact_normalized_text(anchor)
        if not anchor or key in seen:
            continue
        seen.add(key)
        anchors.append(anchor)
    return tuple(anchors)


def _uk_schedule_list_entry_repeal_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract explicit schedule-list-entry repeal anchors.

    The effect target remains the schedule carrier, but these anchors limit the
    executable mutation to direct schedule-entry children. Missing or ambiguous
    anchors block in replay rather than deleting the schedule root.
    """
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if (
        "table" in target_surface
        or _is_heading_only_ref(target_ref)
        or _addr_leaf_kind(target) not in {
            "schedule",
            "part",
            "chapter",
            "division",
            "paragraph",
            "subparagraph",
        }
    ):
        return None
    if _addr_leaf_kind(target) in {"paragraph", "subparagraph"} and not re.search(
        r"\bentr(?:y|ies)\b",
        text,
        re.I,
    ):
        return None

    match = re.search(
        r"\b(?:the\s+)?(?:entry|entries)\s+(?:relating\s+to|for)\s*[—–-]?\s+"
        r"(?P<anchors>.+?)\s+(?:is|are)\s+(?:repealed|omitted)\b",
        text,
        re.I,
    )
    anchors: tuple[str, ...] = ()
    if match is not None:
        anchors = _split_schedule_entry_repeal_anchors(match.group("anchors"))
    else:
        match = re.search(
            r"\bomit(?:ted)?\s+(?:the\s+)?entry\s+(?:relating\s+to|for)\s+"
            r"[“\"']?(?P<anchor>.+?)[”\"']?(?:,?\s+and\b|[.;,]|$)",
            text,
            re.I,
        )
        if match is not None:
            anchors = (_strip_schedule_entry_repeal_anchor(match.group("anchor")),)
        else:
            match = re.search(
                r"\bomit(?:ted)?\s+(?:the\s+)?entry\s+[“\"'](?P<anchor>.+?)[”\"']"
                r"(?:\s+in\s+each\s+schedule)?(?:,?\s+and\b|[.;,]|$)",
                text,
                re.I,
            )
            if match is None:
                match = re.search(r"\bomit(?:ted)?\s+[“\"'](?P<anchor>.+?)[”\"']", text, re.I)
            if match is not None:
                anchors = (_strip_schedule_entry_repeal_anchor(match.group("anchor")),)
    if not anchors and _addr_leaf_kind(target) == "schedule":
        label = target.path[-1][1] if target.path else ""
        label_pattern = re.escape(str(label or ""))
        match = re.search(
            rf"\bin\s+schedule\s+{label_pattern}\b[^.;]*?,\s+"
            r"(?:the\s+)?entries\s+(?P<anchors>.+?)(?:\.|$)",
            text,
            re.I,
        )
        if match is not None:
            anchors = _quoted_schedule_entry_repeal_anchors(match.group("anchors"))
    if not anchors:
        return None
    selector = {
        "rule_id": UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
        "anchors": list(anchors),
        "target_ref": target_ref,
        "target": str(target),
    }
    if match is not None and "in schedule" in match.group(0).lower():
        selector["source_anchor_form"] = "repeal_table_schedule_entries"
    return selector


def _uk_numbered_schedule_entry_repeal_target(
    *,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> LegalAddress | None:
    """Refine a partition-carrier target to an explicitly numbered entry child."""
    if _addr_container(target) != "schedule":
        return None
    if _addr_leaf_kind(target) not in {"part", "chapter", "division"}:
        return None
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    match = re.search(
        r"\bomitt(?:ing|ed)?\s+(?:the\s+)?entry\b.+?\bnumbered\s+(?P<label>[0-9]+[A-Za-z]?)\b",
        text,
        re.I,
    )
    if match is None:
        match = re.search(
            r"\b(?:the\s+)?entry\b.+?\bnumbered\s+(?P<label>[0-9]+[A-Za-z]?)\b.+?"
            r"\b(?:is|are)\s+(?:repealed|omitted)\b",
            text,
            re.I,
        )
    if match is None:
        return None
    label = _clean_num(match.group("label"))
    if not label:
        return None
    return LegalAddress(path=(*target.path, ("paragraph", label)), special=None)


def _uk_schedule_list_entry_replace_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract explicit schedule-list-entry replacement anchors."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if "table" in target_surface or _addr_leaf_kind(target) != "schedule":
        return None
    match = re.search(
        r"\bfor\s+(?:the\s+)?entry\s+(?:relating\s+to|for)\s+"
        r"(?P<anchor>.+?)\s+substitute\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if match is None:
        match = re.search(
            r"\bfor\s+(?:the\s+)?entry\s+(?P<anchor>[“\"'‘].+?[”\"'’])"
            r"(?:\s+in\s+each\s+schedule,?)?\s+"
            r"substitute\s*[—–-]?\s*(?P<payload>.+)$",
            text,
            re.I,
        )
    if match is None:
        return None
    anchor = _strip_schedule_entry_phrase(match.group("anchor"))
    replacement = _strip_schedule_entry_payload(match.group("payload"))
    if not anchor or not replacement:
        return None
    return {
        "rule_id": UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
        "anchor": anchor,
        "replacement_text": replacement,
        "target_ref": target_ref,
        "target": str(target),
    }
