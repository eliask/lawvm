from __future__ import annotations

import re
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind
from lawvm.uk_legislation.heading_facets import _is_heading_only_ref
from lawvm.uk_legislation.replay_text import _compact_normalized_text
from lawvm.uk_legislation.uk_grafter import _clean_num


UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID = "uk_effect_schedule_list_entry_insert"
UK_NON_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID = "uk_effect_non_schedule_list_entry_insert"
UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID = (
    "uk_effect_connector_preceding_child_list_entry_substitution"
)
UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID = "uk_effect_schedule_list_entry_repeal"
UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID = "uk_effect_schedule_list_entry_replace"

_BEGINNING_LIST_ENTRY_INSERT_RE = re.compile(
    r"\bat\s+the\s+beginning\s+insert\s*[—–-]?\s*(?P<payload>.+)$",
    re.I,
)
_TYPE_LABEL_LIST_ENTRY_INSERT_RE = re.compile(
    r"\b(?P<direction>before|after)\s+"
    r"Type\s+(?P<anchor>[0-9A-Z]{1,4})\s+"
    r"insert\s*[—–-]?\s*(?P<payload>.+)$",
    re.I,
)
_TYPE_LABEL_LIST_ENTRY_REPEAL_RE = re.compile(
    r"\bomit(?:ted)?\s+Types?\s+"
    r"(?P<anchors>[0-9A-Z]{1,4}(?:\s*(?:,|;|\band\b)\s*[0-9A-Z]{1,4})*)\b",
    re.I,
)
_QUOTED_SCHEDULE_ENTRIES_FOR_REPEAL_RE = re.compile(
    r"\bomit(?:ted)?\s+(?:the\s+)?entries\s+for\s*[—–-]?\s*(?P<anchors>.+)$",
    re.I,
)
_PLURAL_RELATING_ENTRIES_REPLACE_RE = re.compile(
    r"\bfor\s+(?:the\s+)?entries\s+(?:relating\s+to|for)\s+"
    r"(?P<anchors>.+?)\s+substitute\s*[—–-]?\s*(?P<payload>.+)$",
    re.I,
)
_INDEX_ENTRY_SECTION_REF_RE = re.compile(
    r"\bsection\s+[0-9A-Za-z]+(?:\([^)]{1,50}\))*\b",
    re.I,
)
_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RE = re.compile(
    r"\bunder\s+the\s+heading\s+[“\"'‘](?P<heading>[^”\"'’]{1,160})[”\"'’],?\s+"
    r"for\s+[“\"'‘](?P<connector>[^”\"'’]{1,40})[”\"'’]\s+"
    r"preceding\s+(?P<child_kind>paragraph|sub-paragraph|subparagraph|subsection)\s+"
    r"\((?P<child_label>[0-9A-Za-z]+)\)\s+"
    r"substitute\s*[—–-]?\s*(?P<payload>.+)$",
    re.I,
)

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
    raw_text = str(raw or "").lstrip()
    text = _strip_schedule_entry_phrase(raw)
    if re.match(r"^[;,:]\s*(?:and|or)\s+", raw_text, flags=re.I):
        text = re.sub(r"^(?:and|or)\s+", "", text, flags=re.I)
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
    section_parts = tuple(
        _strip_schedule_entry_phrase(part)
        for part in re.split(r"\s*,\s+(?=(?:and\s+)?sections?\s+[0-9A-Za-z])", payload, flags=re.I)
    )
    section_parts = tuple(
        re.sub(r"^(?:and\s+)", "", part, flags=re.I).strip(" ,;.")
        for part in section_parts
        if part
    )
    if len(section_parts) > 1 and all(re.match(r"^sections?\s+[0-9A-Za-z]", part, re.I) for part in section_parts):
        return section_parts
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


def split_schedule_entry_replace_payload(raw: str) -> tuple[str, ...]:
    """Split a source-owned replacement payload into schedule-list entries.

    Replacement splitting is intentionally narrower than insertion splitting:
    it only admits a visible run of section-entry sentences.  Otherwise the
    replacement remains a single schedule entry so ordinary prose is not
    fragmented by punctuation.
    """
    payload = _strip_schedule_entry_payload(raw)
    if not payload:
        return ()
    text = " ".join(str(raw or "").split()).strip(" ,;")
    text = text.strip("“”\"'‘’")
    text = re.sub(
        r"^(?:the\s+following\s+entries?\s*)[—–-]?\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    if not re.search(r"\.\s+Sections?\s+[0-9A-Za-z]", text):
        return (payload,)
    parts = tuple(
        part.strip(" ,;")
        for part in re.findall(
            r"\bSections?\s+.*?(?:\.|$)(?=\s+Sections?\s+[0-9A-Za-z]|$)",
            text,
            flags=re.I,
        )
    )
    parts = tuple(part for part in parts if part)
    if len(parts) < 2:
        return (payload,)
    return parts


def _split_index_entry_replace_payload(raw: str, *, expected_count: int) -> tuple[str, ...]:
    payload = _strip_schedule_entry_payload(raw)
    if expected_count < 2 or not payload:
        return ()
    matches = tuple(_INDEX_ENTRY_SECTION_REF_RE.finditer(payload))
    if len(matches) != expected_count:
        return ()
    parts: list[str] = []
    start = 0
    for match in matches:
        part = _strip_schedule_entry_payload(payload[start : match.end()])
        if not part:
            return ()
        parts.append(part)
        start = match.end()
    trailing = payload[start:].strip(" ,;.")
    if trailing:
        return ()
    return tuple(parts)


def _schedule_list_entry_selector_from_parts(
    *,
    direction: str,
    anchor_text: str,
    inserted_text: str,
    target_ref: str,
    target: LegalAddress,
    rule_id: str = UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID,
) -> dict[str, Any] | None:
    direction = str(direction or "").lower()
    anchor_text = _strip_schedule_entry_phrase(anchor_text)
    inserted_text = _strip_schedule_entry_payload(inserted_text)
    if direction not in {"before", "after", "alphabetical", "beginning"} or not inserted_text:
        return None
    if direction not in {"alphabetical", "beginning"} and not anchor_text:
        return None
    return {
        "rule_id": rule_id,
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
    allow_local_paragraph_carrier: bool = False,
) -> dict[str, Any] | None:
    """Extract a deterministic schedule-list-entry sibling insertion selector."""
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_surface = f"{target_ref} {target}".lower()
    if "table" in target_surface or "column" in text.lower():
        return None
    target_container = _addr_container(target)
    target_leaf_kind = _addr_leaf_kind(target)
    schedule_carrier_target = target_container == "schedule" and target_leaf_kind in {
        "schedule",
        "part",
        "chapter",
        "division",
        "paragraph",
        "subparagraph",
    }
    local_list_leaf_kinds = {"section", "subsection"}
    if allow_local_paragraph_carrier:
        local_list_leaf_kinds = {*local_list_leaf_kinds, "paragraph", "subparagraph"}
    local_list_carrier_target = (
        target_container != "schedule" and target_leaf_kind in local_list_leaf_kinds
    )
    if not schedule_carrier_target and not local_list_carrier_target:
        return None
    rule_id = (
        UK_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID
        if schedule_carrier_target
        else UK_NON_SCHEDULE_LIST_ENTRY_INSERT_RULE_ID
    )
    entry_carrier_family = "schedule_list" if schedule_carrier_target else "non_schedule_local_list"

    match = re.search(
        r"\b(?P<direction>before|after)\s+(?:the\s+)?"
        r"(?:(?P<ordinal>first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th)\s+)?entry\s+"
        r"(?:\([^)]{1,120}\)\s+)?"
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
    exception_label_anchor = False
    if match is None and local_list_carrier_target:
        match = re.search(
            r"\b(?P<direction>before|after)\s+(?:the\s+)?"
            r"exception\s+(?P<anchor>[0-9A-Z]{1,4})\s+"
            r"(?:,?\s*there\s+is\s+inserted|insert\b)\s*[—–-]?\s*(?P<payload>.+)$",
            text,
            re.I,
        )
        exception_label_anchor = match is not None
    if match is not None:
        anchor = match.group("anchor")
        if exception_label_anchor:
            anchor = f"Exception {anchor}"
        selector = _schedule_list_entry_selector_from_parts(
            direction=str(match.group("direction") or "").lower(),
            anchor_text=anchor,
            inserted_text=match.group("payload"),
            target_ref=target_ref,
            target=target,
            rule_id=rule_id,
        )
        if selector is not None:
            selector["entry_carrier_family"] = entry_carrier_family
        if selector is not None and re.search(r"\bentry\s+relation\s+to\b", text, re.I):
            selector["source_anchor_form"] = "entry_relation_to_typo"
        if selector is not None and re.search(r"\bentry\s+\([^)]{1,120}\)\s+(?:relating\s+to|for)\b", text, re.I):
            selector["source_anchor_form"] = "entry_parenthetical_qualifier"
        if selector is not None and exception_label_anchor:
            selector["source_anchor_form"] = "exception_label"
        ordinal = match.groupdict().get("ordinal")
        if selector is not None and ordinal:
            selector["anchor_ordinal"] = _ENTRY_ORDINALS[ordinal.lower()]
        return selector

    if local_list_carrier_target:
        match = _TYPE_LABEL_LIST_ENTRY_INSERT_RE.search(text)
        if match is not None:
            selector = _schedule_list_entry_selector_from_parts(
                direction=str(match.group("direction") or "").lower(),
                anchor_text=f"Type {match.group('anchor')}",
                inserted_text=match.group("payload"),
                target_ref=target_ref,
                target=target,
                rule_id=rule_id,
            )
            if selector is not None:
                selector["entry_carrier_family"] = entry_carrier_family
                selector["source_anchor_form"] = "type_label"
            return selector

    if local_list_carrier_target:
        match = _BEGINNING_LIST_ENTRY_INSERT_RE.search(text)
        if match is not None:
            selector = _schedule_list_entry_selector_from_parts(
                direction="beginning",
                anchor_text="",
                inserted_text=match.group("payload"),
                target_ref=target_ref,
                target=target,
                rule_id=rule_id,
            )
            if selector is not None:
                selector["entry_carrier_family"] = entry_carrier_family
                selector["placement_family"] = "list_beginning_from_explicit_source"
            return selector

    match = re.search(
        r"\bat\s+(?:an?|the)\s+appropriate\s+place,?\s+in\s+alphabetical\s+order,?\s+"
        r"insert\s*[—–-]?\s*(?P<payload>.+)$",
        text,
        re.I,
    )
    if match is None:
        return None
    selector = _schedule_list_entry_selector_from_parts(
        direction="alphabetical",
        anchor_text="",
        inserted_text=match.group("payload"),
        target_ref=target_ref,
        target=target,
        rule_id=rule_id,
    )
    if selector is not None:
        selector["entry_carrier_family"] = entry_carrier_family
    return selector


def _uk_connector_preceding_child_list_entry_substitution_selector(
    *,
    target_ref: str,
    target: LegalAddress,
    extracted_text: Optional[str],
) -> dict[str, Any] | None:
    """Extract a connector-before-child substitution into a bounded insert selector.

    UK source sometimes expresses insertion of a new labelled list paragraph by
    substituting the connector immediately before the next child.  This selector
    only records the source-owned placement claim; lowering/replay must still
    prove both the contextual connector deletion and the child boundary.
    """
    text = " ".join((extracted_text or "").split())
    if not text:
        return None
    target_leaf_kind = _addr_leaf_kind(target)
    if _addr_container(target) == "schedule" or target_leaf_kind not in {"section", "subsection"}:
        return None
    match = _CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RE.search(text)
    if match is None:
        return None
    child_kind = str(match.group("child_kind") or "").lower().replace("-", "")
    inserted_text = _strip_schedule_entry_phrase(match.group("payload"))
    label_match = re.match(r"^\(?\s*(?P<label>[0-9A-Za-z]+)\s*\)?\s+(?P<body>.+)$", inserted_text)
    if label_match is None:
        return None
    inserted_body = _strip_schedule_entry_phrase(label_match.group("body"))
    return {
        "rule_id": UK_CONNECTOR_PRECEDING_CHILD_LIST_ENTRY_SUBSTITUTION_RULE_ID,
        "direction": "before",
        "anchor_text": "",
        "inserted_text": inserted_body,
        "inserted_label": _clean_num(label_match.group("label")),
        "target_ref": target_ref,
        "target": str(target),
        "entry_carrier_family": "non_schedule_local_list",
        "source_anchor_form": "connector_preceding_child",
        "heading_text": _strip_schedule_entry_phrase(match.group("heading")),
        "connector_text": _strip_schedule_entry_phrase(match.group("connector")),
        "anchor_child_kind": child_kind,
        "anchor_child_label": _clean_num(match.group("child_label")),
        "source_inserted_text": inserted_text,
    }


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


def _split_exception_repeal_anchors(raw: str) -> tuple[str, ...]:
    anchors: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"\s*(?:,|;|\band\b)\s*", str(raw or ""), flags=re.I):
        label = part.strip().upper()
        if not re.fullmatch(r"[0-9A-Z]{1,4}", label):
            continue
        anchor = f"Exception {label}"
        key = _compact_normalized_text(anchor)
        if key in seen:
            continue
        seen.add(key)
        anchors.append(anchor)
    return tuple(anchors)


def _split_type_label_repeal_anchors(raw: str) -> tuple[str, ...]:
    anchors: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"\s*(?:,|;|\band\b)\s*", str(raw or ""), flags=re.I):
        label = part.strip().upper()
        if not re.fullmatch(r"[0-9A-Z]{1,4}", label):
            continue
        anchor = f"Type {label}"
        key = _compact_normalized_text(anchor)
        if key in seen:
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
    # Inner "omit"/"insert" text inside a "words before the table substitute"
    # formula modifies the moved table; it is not a structural schedule-list-entry
    # repeal. The whole formula is owned by
    # uk_effect_schedule_words_before_table_substitution_lowered.
    if "for the words before the table substitute" in text.lower():
        return None
    target_surface = f"{target_ref} {target}".lower()
    target_leaf_kind = _addr_leaf_kind(target)
    local_list_carrier_target = _addr_container(target) != "schedule" and target_leaf_kind in {
        "section",
        "subsection",
    }
    if (
        "table" in target_surface
        or _is_heading_only_ref(target_ref)
        or (
            not local_list_carrier_target
            and target_leaf_kind not in {
                "schedule",
                "part",
                "chapter",
                "division",
                "paragraph",
                "subparagraph",
            }
        )
    ):
        return None
    if target_leaf_kind in {"paragraph", "subparagraph"} and not re.search(
        r"\bentr(?:y|ies)\b",
        text,
        re.I,
    ):
        return None
    if local_list_carrier_target:
        match = _TYPE_LABEL_LIST_ENTRY_REPEAL_RE.search(text)
        if match is not None:
            anchors = _split_type_label_repeal_anchors(match.group("anchors"))
            # A single `omit Type X` is a unique-literal text-patch repeal,
            # owned by uk_effect_unquoted_type_label_repeal_text_patch. Only
            # the plural/multi-anchor form (`omit Types X and Y …`) is a
            # structural local schedule-entry repeal.
            if len(anchors) >= 2:
                return {
                    "rule_id": UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
                    "anchors": list(anchors),
                    "target_ref": target_ref,
                    "target": str(target),
                    "entry_carrier_family": "non_schedule_local_list",
                    "source_anchor_form": "type_label",
                }
        # A QUOTED anchor (`omit the entry relating to "working day"`) names a
        # definition term and is owned by the definition-entry text-patch rule
        # (uk_effect_definition_entry_repeal_text_patch). Only an unquoted
        # structural-reference anchor (`… relating to section 134(5)(a)`) is a
        # structural local schedule-entry repeal.
        quoted_entry_relating_to = re.search(
            r"\bomit(?:ted)?\s+(?:the\s+)?entry\s+(?:relating\s+to|for)\s+"
            r"[“\"'‘]",
            text,
            re.I,
        )
        match = (
            None
            if quoted_entry_relating_to is not None
            else re.search(
                r"\bomit(?:ted)?\s+(?:the\s+)?entry\s+(?:relating\s+to|for)\s+"
                r"(?P<anchor>.+?)(?:,?\s+and\b|[.;,]|$)",
                text,
                re.I,
            )
        )
        if match is not None:
            anchor = _strip_schedule_entry_repeal_anchor(match.group("anchor"))
            if anchor:
                return {
                    "rule_id": UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
                    "anchors": [anchor],
                    "target_ref": target_ref,
                    "target": str(target),
                    "entry_carrier_family": "non_schedule_local_list",
                    "source_anchor_form": "local_entry_relating_to",
                }
        match = re.search(
            r"\bomit(?:ted)?\s+exceptions?\s+(?P<anchors>[0-9A-Z]{1,4}(?:\s*(?:,|;|\band\b)\s*[0-9A-Z]{1,4})*)\b",
            text,
            re.I,
        )
        if match is None:
            return None
        anchors = _split_exception_repeal_anchors(match.group("anchors"))
        if not anchors:
            return None
        return {
            "rule_id": UK_SCHEDULE_LIST_ENTRY_REPEAL_RULE_ID,
            "anchors": list(anchors),
            "target_ref": target_ref,
            "target": str(target),
            "entry_carrier_family": "non_schedule_local_list",
            "source_anchor_form": "exception_label",
        }

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
    if not anchors and target_leaf_kind == "schedule":
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
    if not anchors and target_leaf_kind == "schedule":
        match = _QUOTED_SCHEDULE_ENTRIES_FOR_REPEAL_RE.search(text)
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
    if match is not None and match.re is _QUOTED_SCHEDULE_ENTRIES_FOR_REPEAL_RE:
        selector["source_anchor_form"] = "quoted_entries_for"
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
    target_container = _addr_container(target)
    target_leaf_kind = _addr_leaf_kind(target)
    schedule_carrier_target = target_container == "schedule" and target_leaf_kind in {
        "schedule",
        "part",
        "chapter",
        "division",
        "paragraph",
        "subparagraph",
    }
    local_list_carrier_target = target_container != "schedule" and target_leaf_kind in {
        "section",
        "subsection",
        "paragraph",
        "subparagraph",
    }
    if "table" in target_surface or (
        not schedule_carrier_target and not local_list_carrier_target
    ):
        return None
    match = _PLURAL_RELATING_ENTRIES_REPLACE_RE.search(text)
    if match is not None:
        anchors = _quoted_schedule_entry_repeal_anchors(match.group("anchors"))
        raw_payload = str(match.group("payload") or "")
        replacement_texts = _split_index_entry_replace_payload(
            raw_payload,
            expected_count=len(anchors),
        )
        if anchors and replacement_texts and len(anchors) == len(replacement_texts):
            return {
                "rule_id": UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
                "anchor": anchors[0],
                "anchors": list(anchors),
                "replacement_text": replacement_texts[0],
                "replacement_texts": replacement_texts,
                "target_ref": target_ref,
                "target": str(target),
                "source_anchor_form": "plural_quoted_entries_relating_to",
                "replacement_payload_form": "index_entry_section_ref_sequence",
            }
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
    raw_payload = str(match.group("payload") or "")
    replacement_texts = split_schedule_entry_replace_payload(raw_payload)
    replacement = replacement_texts[0] if replacement_texts else ""
    if (
        len(replacement_texts) == 1
        and replacement
        and not replacement.endswith(",")
        and re.search(r"[“\"]\s*[^”\"]+,\s*[”\"]", raw_payload)
    ):
        replacement = f"{replacement},"
        replacement_texts = (replacement,)
    if not anchor or not replacement:
        return None
    return {
        "rule_id": UK_SCHEDULE_LIST_ENTRY_REPLACE_RULE_ID,
        "anchor": anchor,
        "replacement_text": replacement,
        "replacement_texts": replacement_texts,
        "target_ref": target_ref,
        "target": str(target),
    }
