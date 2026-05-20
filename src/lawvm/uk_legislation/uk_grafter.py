import xml.etree.ElementTree as ET
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, cast

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.mutable_ir import UKMutableNode

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"
_USER_AGENT = "LawVM-Replayer/1.0"
_LEG_BASE = "http://www.legislation.gov.uk"

# Editorial element types added by legislation.gov.uk editors — NOT part of the
# enacted statute text.  Excluded from EID scoring so that their presence in the
# consolidated oracle does not inflate the apparent gap vs the enacted version.
#   Commentary  — editorial notes attached to provisions (live in <Commentaries>
#                 top-level section, but may also appear inline via CommentaryRef)
#   Citation    — inline bibliographic references to other legislation
#   CitationSubRef — sub-references within Citations (nested inside Commentaries)
#   Term        — markup for defined terms; carries eId="term-<name>" inline
_EDITORIAL_TAGS: frozenset[str] = frozenset({"Commentary", "Citation", "CitationSubRef", "Term"})
_VISIBLE_INLINE_TEXT_TAGS: frozenset[str] = frozenset({"Citation", "CitationSubRef", "Term"})
_NON_LEGAL_UNIT_EID_TAGS: frozenset[str] = frozenset({"Text"})

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _tag(el: ET.Element) -> str:
    if el is None:
        return ""
    tag = el.tag
    if not isinstance(tag, str):
        return ""  # PI/Comment nodes have callable .tag
    return tag.split("}", 1)[1] if "}" in tag else tag


def _text_content(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return "".join(str(_t) for _t in el.itertext()).strip()


def _local_structural_text(el: ET.Element) -> str:
    """Collect local provision text without absorbing child provisions."""
    structural = {
        "part",
        "chapter",
        "euchapter",
        "p1group",
        "p2group",
        "p3group",
        "p4group",
        "section",
        "p1",
        "article",
        "eusection",
        "conventionrights",
        "pblock",
        "p2",
        "p3",
        "p4",
        "subsection",
        "paragraph",
        "schedule",
        "table",
    }
    transparent_skip = {"pnumber", "number", "title", "commentaryref"}
    structural_text_skip = {tag.lower() for tag in _EDITORIAL_TAGS - _VISIBLE_INLINE_TEXT_TAGS}

    def _collect(node: ET.Element) -> list[str]:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = _tag(child).lower()
            if (
                tag in structural
                or tag in transparent_skip
                or tag in structural_text_skip
                or _definition_ordered_list_term(node, child)
            ):
                pass
            else:
                parts.extend(_collect(child))
            if child.tail:
                parts.append(child.tail)
        return parts

    return " ".join(" ".join(_collect(el)).split())


def _extract_num(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _add_attrs(node: UKMutableNode, el: ET.Element):
    for attr in ["eId", "id", "Status", "RestrictStartDate", "RestrictEndDate"]:
        val = el.get(attr)
        if val:
            node.attrs[attr] = val


def _roman_to_int(s: str) -> str:
    """Return the Arabic-string form of ``s`` if it is a canonical Roman
    numeral, otherwise return ``s`` unchanged.

    Delegates to ``lawvm.roman``; rejects non-canonical spellings via
    round-trip canonicalization.  The previous implementation only
    handled I..X.
    """
    value = _shared_roman_to_arabic(s)
    return s if value is None else str(value)


def _clean_num(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r"^(Part|Section|Schedule|Chapter|Paragraph|Article|Rule|Regulation)\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[().]+$", "", s).strip()
    if re.match(r"^[IVXLCDM]+$", s, re.IGNORECASE):
        s = _roman_to_int(s)
    return s.lower().strip(".")


def _infer_container_number_from_source_uri(el: ET.Element, *, prefix: str) -> str:
    """Infer a missing/generic UK container number from an unambiguous source id/eId."""
    for attr_name in ("eId", "id"):
        raw = str(el.get(attr_name) or "").strip()
        if not raw:
            continue
        tail = raw.rsplit("/", 1)[-1].lower()
        if tail == prefix:
            return "1"
        match = re.search(rf"(?:^|-){re.escape(prefix)}-(?:n)?(?P<label>[0-9]+[a-z]?)\b", tail)
        if match is not None:
            return match.group("label")
    return ""


def _maybe_infer_container_number(
    node: UKMutableNode,
    el: ET.Element,
    *,
    prefix: str,
    original_label: str,
) -> None:
    if _clean_num(original_label) not in {"", prefix}:
        return
    inferred = _infer_container_number_from_source_uri(el, prefix=prefix)
    if not inferred:
        return
    node.label = inferred
    node.attrs["source_rule_id"] = _UK_CONTAINER_NUMBER_INFERRED_RULE_ID
    node.attrs["source_original_label"] = original_label
    node.attrs["source_inferred_label"] = inferred
    node.attrs["source_identifier"] = str(el.get("eId") or el.get("id") or "")


_UK_TABLE_ROW_TAGS = frozenset({"row", "tr"})
_UK_TABLE_CELL_TAGS = frozenset({"entry", "td", "th"})
_UK_TABLE_HEADER_CONTAINERS = frozenset({"thead"})
_UK_TABLE_TRANSPARENT_CONTAINERS = frozenset({"tgroup", "tbody", "tfoot"})
_UK_SCHEDULE_LIST_ENTRY_RULE_ID = "uk_schedule_list_entry_preserved"
_UK_CONTAINER_NUMBER_INFERRED_RULE_ID = "uk_container_number_inferred_from_source_uri"
_UK_SCHEDULE_ENTRY_TRANSPARENT_TAGS = frozenset(
    {
        "addition",
        "commentaryref",
        "emphasis",
        "repeal",
        "substitution",
        "text",
    }
)
_UK_SCHEDULE_ENTRY_BLOCKING_TAGS = frozenset(
    {
        "chapter",
        "part",
        "p1",
        "p1group",
        "p2",
        "p2group",
        "p3",
        "p3group",
        "p4",
        "p4group",
        "pblock",
        "section",
        "table",
    }
)


def _definition_ordered_list_term(parent_el: ET.Element, list_el: ET.Element) -> str:
    """Return the defined term for a definition-local ordered list, if any."""
    if _tag(list_el) != "OrderedList" or list_el.get("Type", "").lower() != "alpha":
        return ""
    before_parts: list[str] = []
    for child in parent_el:
        if child is list_el:
            break
        before_parts.append(_text_content(child))
        if child.tail:
            before_parts.append(child.tail)
    before_text = " ".join(" ".join(before_parts).split())
    if not before_text:
        return ""
    quoted_match = re.search(
        r"[“\"'\u2018]\s*(?P<term>[^”\"'\u2019;]{1,160}?)\s*[”\"'\u2019]\s*"
        r"(?:\([^)]{1,200}\)\s*)?"
        r"(?:,\s*[^;]{1,240}?)?"
        r"(?:means|includes|has\s+the\s+same\s+meaning\s+as|has\s+the\s+meaning|is\s+to\s+be\s+construed)\b",
        before_text,
        flags=re.I,
    )
    if quoted_match is not None:
        return " ".join(quoted_match.group("term").split())
    match = re.search(
        r"[“\"'\u2018]?\s*(?P<term>[^”\"'\u2019;]{1,160}?)\s*[”\"'\u2019]?\s+"
        r"(?:means|includes|has\s+the\s+same\s+meaning\s+as|has\s+the\s+meaning|is\s+to\s+be\s+construed)\b",
        before_text,
        flags=re.I,
    )
    return " ".join(match.group("term").split()) if match is not None else ""


def _alpha_label(index: int) -> str:
    if index < 0:
        return ""
    chars: list[str] = []
    value = index
    while True:
        value, rem = divmod(value, 26)
        chars.append(chr(ord("a") + rem))
        if value == 0:
            break
        value -= 1
    return "".join(reversed(chars))


def _parse_definition_ordered_list(el: ET.Element, parent_el: ET.Element) -> list[UKMutableNode]:
    term = _definition_ordered_list_term(parent_el, el)
    if not term:
        return []
    nodes: list[UKMutableNode] = []
    item_index = 0
    for child in el:
        if _tag(child) != "ListItem":
            continue
        label = (child.get("NumberOverride") or "").strip() or _alpha_label(item_index)
        item_index += 1
        text = _text_content(child)
        if not label or not text:
            continue
        nodes.append(
            UKMutableNode(
                kind=IRNodeKind.ITEM,
                label=None,
                text=text,
                attrs={
                    "source_rule_id": "uk_definition_ordered_list_child_preserved",
                    "definition_term": term,
                    "definition_child_label": label,
                    "source_tag": _tag(el),
                    "source_list_type": el.get("Type", ""),
                },
            )
        )
    return nodes


def _schedule_list_entry_node(
    el: ET.Element,
    *,
    source_ordinal: int,
    source_tag: str,
    source_list_type: str = "",
    source_decoration: str = "",
) -> UKMutableNode | None:
    text = _text_content(el)
    if not text:
        return None
    attrs: dict[str, Any] = {
        "source_rule_id": _UK_SCHEDULE_LIST_ENTRY_RULE_ID,
        "source_tag": source_tag,
        "source_ordinal": str(source_ordinal),
        "source_context": "schedule_body",
    }
    if source_list_type:
        attrs["source_list_type"] = source_list_type
    if source_decoration:
        attrs["source_decoration"] = source_decoration
    return UKMutableNode(
        kind=IRNodeKind.SCHEDULE_ENTRY,
        label=None,
        text=text,
        attrs=attrs,
    )


def _parse_schedule_body_list_entries(el: ET.Element, *, start_ordinal: int) -> list[UKMutableNode]:
    tag = _tag(el)
    if tag != "UnorderedList":
        return []
    nodes: list[UKMutableNode] = []
    for child in el:
        if _tag(child) != "ListItem":
            continue
        node = _schedule_list_entry_node(
            child,
            source_ordinal=start_ordinal + len(nodes),
            source_tag="ListItem",
            source_list_type=el.get("Type", ""),
            source_decoration=el.get("Decoration", ""),
        )
        if node is not None:
            nodes.append(node)
    return nodes


def _parse_schedule_body_p_entries(el: ET.Element, *, start_ordinal: int) -> list[UKMutableNode]:
    if _tag(el) != "P":
        return []
    nodes: list[UKMutableNode] = []
    for child in el:
        if _tag(child) == "UnorderedList":
            nodes.extend(_parse_schedule_body_list_entries(child, start_ordinal=start_ordinal + len(nodes)))
    if nodes:
        return nodes
    child_tags = {_tag(child).lower() for child in el}
    if child_tags & _UK_SCHEDULE_ENTRY_BLOCKING_TAGS:
        return []
    if child_tags and not child_tags <= _UK_SCHEDULE_ENTRY_TRANSPARENT_TAGS:
        return []
    node = _schedule_list_entry_node(el, source_ordinal=start_ordinal, source_tag="P")
    return [node] if node is not None else []


def _table_attrs(el: ET.Element, names: tuple[str, ...]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for name in names:
        value = el.get(name)
        if value:
            attrs[name] = value
    return attrs


def _parse_table_row(el: ET.Element, *, header_context: bool) -> UKMutableNode | None:
    cells: list[UKMutableNode] = []
    for child in el:
        tag = _tag(child).lower()
        if tag not in _UK_TABLE_CELL_TAGS:
            continue
        cell_kind = IRNodeKind.HEADER_CELL if header_context or tag == "th" else IRNodeKind.CELL
        attrs = _table_attrs(
            child,
            ("eId", "id", "rowspan", "colspan", "morerows", "namest", "nameend"),
        )
        cells.append(
            UKMutableNode(
                kind=cell_kind,
                text=_text_content(child),
                attrs=attrs,
            )
        )
    if not cells:
        return None
    return UKMutableNode(
        kind=IRNodeKind.ROW,
        attrs=_table_attrs(el, ("eId", "id")),
        children=cells,
    )


def _parse_table_rows(el: ET.Element, *, header_context: bool = False) -> list[UKMutableNode]:
    rows: list[UKMutableNode] = []
    for child in el:
        tag = _tag(child).lower()
        if tag in _UK_TABLE_ROW_TAGS:
            row = _parse_table_row(child, header_context=header_context)
            if row is not None:
                rows.append(row)
            continue
        if tag in _UK_TABLE_HEADER_CONTAINERS:
            rows.extend(_parse_table_rows(child, header_context=True))
            continue
        if tag in _UK_TABLE_TRANSPARENT_CONTAINERS:
            rows.extend(_parse_table_rows(child, header_context=header_context))
    return rows


def _local_table_text(el: ET.Element) -> str:
    """Collect table-local caption/text without duplicating row cell content."""
    skipped = _UK_TABLE_ROW_TAGS | _UK_TABLE_TRANSPARENT_CONTAINERS | _UK_TABLE_HEADER_CONTAINERS

    def _collect(node: ET.Element) -> list[str]:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            if _tag(child).lower() not in skipped:
                parts.extend(_collect(child))
            if child.tail:
                parts.append(child.tail)
        return parts

    return " ".join(" ".join(_collect(el)).split())


def _parse_table(el: ET.Element, context, force_active=False, pit_date=None, is_eur=False) -> UKMutableNode | None:
    del context, is_eur
    if _is_zombie(el, force_active, pit_date):
        return None
    return UKMutableNode(
        kind=IRNodeKind.TABLE,
        text=_local_table_text(el),
        attrs=_table_attrs(el, ("eId", "id")),
        children=_parse_table_rows(el),
    )


def _get_kind(tag: str, context: str = "body", is_eur: bool = False) -> str:
    t = tag.lower()
    if is_eur and t in ("p1", "section", "article", "eusection"):
        return "article"
    if context.startswith("schedule"):
        if t in ("p1", "paragraph"):
            return "paragraph"
        if t in ("p2", "subparagraph"):
            return "subparagraph"
        if t in ("p3", "p4"):
            return "item"
    if t in ("p1", "section", "article", "rule", "conventionrights"):
        return "section"
    if t in ("p2", "subsection", "paragraph"):
        return "subsection"
    if t in ("p3", "paragraph", "point"):
        return "paragraph"
    if t in ("p4", "subparagraph", "subpoint"):
        return "subparagraph"
    if t == "p1group":
        return "p1group"
    if t in ("p2group", "p3group", "p4group"):
        return "pgroup"
    if t in ("pblock", "eusection"):
        return "crossheading"
    if t in ("chapter", "euchapter"):
        return "chapter"
    if t in ("part", "eupart"):
        return "part"
    if t == "schedule":
        return "schedule"
    if t in ("body", "euretained"):
        return "body"
    if t == "division":
        return "recital"
    return t


_PHYSICAL_EID_BODY_KINDS = frozenset(
    {
        "section",
        "article",
        "rule",
        "regulation",
        "subsection",
        "paragraph",
        "subparagraph",
        "item",
        "point",
    }
)
_PHYSICAL_EID_SCHEDULE_KINDS = frozenset(
    {
        "schedule",
        "annex",
        "part",
        "chapter",
        "paragraph",
        "subsection",
        "subparagraph",
        "item",
        "point",
    }
)


def _physical_eid_from_semantic_path(path_key: str) -> str:
    """Derive the EID implied by physical XML ancestry, without trusting attrs."""
    parts = [part for part in str(path_key or "").split(":") if part and part != "body"]
    if not parts:
        return ""
    physical: list[str] = []
    in_schedule = False
    for part in parts:
        if "-" not in part:
            continue
        kind, raw_label = part.split("-", 1)
        kind = kind.lower()
        label = _clean_num(raw_label)
        if not label:
            continue
        if kind in {"schedule", "annex"}:
            physical.extend([kind, label])
            in_schedule = True
            continue
        if in_schedule:
            if kind not in _PHYSICAL_EID_SCHEDULE_KINDS:
                continue
            if kind in {"part", "chapter"}:
                physical.extend([kind, label])
            elif kind == "paragraph" and "paragraph" not in physical:
                physical.extend(["paragraph", label])
            else:
                physical.append(label)
            continue
        if kind not in _PHYSICAL_EID_BODY_KINDS:
            continue
        if kind in {"section", "article", "rule", "regulation"}:
            physical.extend([kind, label])
        else:
            physical.append(label)
    return "-".join(physical)


def _eid_leaf_label(eid: str) -> str:
    parts = [part for part in re.split(r"[-_]+", str(eid or "").lower()) if part]
    return parts[-1] if parts else ""


def _eid_with_leaf_label(eid: str, label: str) -> str:
    parts = [part for part in re.split(r"[-_]+", str(eid or "").lower()) if part]
    if not parts or not label:
        return ""
    return "-".join([*parts[:-1], label.lower()])


def _leading_digits(label: str) -> str:
    match = re.match(r"([0-9]+)", str(label or "").lower())
    return match.group(1) if match is not None else ""


def _section_or_article_root(eid: str) -> str:
    match = re.match(r"^(section|article|rule|regulation)-([^-]+)", str(eid or "").lower())
    if match is None:
        return ""
    return f"{match.group(1)}-{match.group(2)}"


def _record_physical_eid_drift(
    *,
    eid: str,
    physical_eid: str,
    tag: str,
    path_key: str,
    aliases: dict[str, str],
    observations: list[dict[str, Any]],
) -> None:
    if not eid or not physical_eid or eid == physical_eid:
        return
    if eid.lower() == physical_eid.lower():
        return
    # Narrow comparison-only repair: same root provision and same leaf label,
    # but the official EID's parent path contradicts XML physical ancestry.
    root = _section_or_article_root(eid)
    if not root or root != _section_or_article_root(physical_eid):
        return
    if _eid_leaf_label(eid) != _eid_leaf_label(physical_eid):
        return
    aliases.setdefault(eid, physical_eid)
    observations.append(
        {
            "rule_id": "uk_oracle_physical_parent_eid_drift_aligned",
            "phase": "oracle_alignment",
            "family": "oracle_identity_drift",
            "original_eid": eid,
            "physical_eid": physical_eid,
            "xml_tag": tag,
            "physical_path_key": path_key,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    )


def _record_visible_number_eid_alias(
    *,
    eid: str,
    kind: str,
    clean_num: str,
    tag: str,
    path_key: str,
    aliases: dict[str, str],
    observations: list[dict[str, Any]],
) -> None:
    if not eid or kind != "paragraph" or not clean_num:
        return
    eid_norm = str(eid or "").lower()
    if not eid_norm.startswith("schedule-"):
        return
    leaf = _eid_leaf_label(eid_norm)
    clean_leaf = _clean_num(clean_num)
    if not leaf or not clean_leaf or leaf == clean_leaf:
        return
    if "n" not in leaf:
        return
    if _leading_digits(leaf) != _leading_digits(clean_leaf):
        return
    visible_eid = _eid_with_leaf_label(eid_norm, clean_leaf)
    if not visible_eid or visible_eid == eid_norm:
        return
    aliases.setdefault(eid, visible_eid)
    observations.append(
        {
            "rule_id": "uk_oracle_visible_number_eid_alias_aligned",
            "phase": "oracle_alignment",
            "family": "oracle_identity_drift",
            "original_eid": eid,
            "visible_number_eid": visible_eid,
            "xml_tag": tag,
            "visible_number": clean_leaf,
            "physical_path_key": path_key,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    )


def _is_zombie(el: ET.Element, force_active: bool = False, pit_date: Optional[str] = None) -> bool:
    if force_active:
        return False
    status = el.get("Status")
    if status == "Repealed":
        return True
    if status == "Prospective" and not pit_date:
        return True

    if pit_date:
        start = el.get("RestrictStartDate")
        end = el.get("RestrictEndDate")
        if start and start > pit_date:
            return True
        if end and end <= pit_date:
            return True
    elif el.get("RestrictEndDate"):
        restrict_end = el.get("RestrictEndDate") or ""
        if restrict_end <= "2026-03-20":
            return True

    structural = (
        "part",
        "chapter",
        "euchapter",
        "p1group",
        "section",
        "p1",
        "article",
        "eusection",
        "pblock",
        "p2",
        "p3",
        "p4",
        "subsection",
        "paragraph",
        "schedule",
    )

    def _collect_local(node):
        txt = []
        if node.text:
            txt.append(node.text)
        for c in node:
            ct = _tag(c).lower()
            if ct not in structural and ct not in ("pnumber", "number", "title", "commentaryref"):
                txt.extend(_collect_local(c))
            if c.tail:
                txt.append(c.tail)
        return txt

    content_str = "".join(_collect_local(el)).strip()
    if content_str and re.match(r"^[.\s]+$", content_str):
        has_active = False
        for child in el:
            if _tag(child).lower() in structural:
                if not _is_zombie(child, False, pit_date):
                    has_active = True
                    break
        if not has_active:
            return True
    return False


def _parse_children(parent_el, context, force_active=False, pit_date=None, is_eur=False):
    children = []
    schedule_entry_ordinal = 1
    structural_tags = (
        "Part",
        "Chapter",
        "EUChapter",
        "P1group",
        "P2group",
        "P3group",
        "P4group",
        "P1",
        "Section",
        "Article",
        "Rule",
        "EUSection",
        "ConventionRights",
        "Pblock",
        "P2",
        "P3",
        "P4",
        "Schedule",
        "Table",
        "table",
    )

    for child in parent_el:
        ct = _tag(child)
        node = None
        if ct == "Part":
            node = _parse_part(child, context, force_active, pit_date, is_eur)
        elif ct in ("Chapter", "EUChapter"):
            node = _parse_chapter(child, context, force_active, pit_date, is_eur)
        elif ct == "P1group":
            node = _parse_p1group(child, context, force_active, pit_date, is_eur)
        elif ct in ("P2group", "P3group", "P4group"):
            node = _parse_pgroup(child, context, force_active, pit_date, is_eur)
        elif ct in ("P1", "Section", "Article", "Rule", "EUSection", "ConventionRights"):
            node = _parse_section(child, context, force_active, pit_date, is_eur)
        elif ct == "Pblock":
            node = _parse_pblock(child, context, force_active, pit_date, is_eur)
        elif ct == "P2":
            node = _parse_p2(child, context, force_active, pit_date, is_eur)
        elif ct == "P3":
            node = _parse_p3(child, context, force_active, pit_date, is_eur)
        elif ct == "P4":
            node = _parse_p4(child, context, force_active, pit_date, is_eur)
        elif ct == "Schedule":
            node = _parse_schedule_single(child, context, force_active, pit_date, is_eur)
        elif ct in ("Table", "table"):
            node = _parse_table(child, context, force_active, pit_date, is_eur)
        elif ct == "OrderedList":
            definition_children = _parse_definition_ordered_list(child, parent_el)
            if definition_children:
                children.extend(definition_children)
                continue
        elif context == "schedule" and ct == "UnorderedList":
            schedule_entries = _parse_schedule_body_list_entries(child, start_ordinal=schedule_entry_ordinal)
            if schedule_entries:
                schedule_entry_ordinal += len(schedule_entries)
                children.extend(schedule_entries)
                continue
        elif context == "schedule" and ct == "P":
            schedule_entries = _parse_schedule_body_p_entries(child, start_ordinal=schedule_entry_ordinal)
            if schedule_entries:
                schedule_entry_ordinal += len(schedule_entries)
                children.extend(schedule_entries)
                continue

        if ct in structural_tags:
            # If it's structural, we either add the node or skip it (if it's a zombie)
            # We do NOT extend its children into the parent unless it's a transparent wrapper
            if node:
                children.append(node)
        else:
            # Recurse for transparent containers
            if ct not in ("Pnumber", "Number", "Title", "CommentaryRef", "BlockAmendment"):
                children.extend(_parse_children(child, context, force_active, pit_date, is_eur))
    return children


def _parse_part(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num_el = el.find(f"./{{{_LEG_NS}}}Number")
    num = _extract_num(num_el) or _text_content(num_el)
    title = _text_content(el.find(f"./{{{_LEG_NS}}}Title"))
    node = UKMutableNode(kind=IRNodeKind.PART, label=num, text=title)
    _add_attrs(node, el)
    if not force_active:
        _maybe_infer_container_number(node, el, prefix="part", original_label=num)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    return node


def _parse_chapter(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Number"))
    title = _text_content(el.find(f"./{{{_LEG_NS}}}Title"))
    node = UKMutableNode(kind=IRNodeKind.CHAPTER, label=num, text=title)
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    return node


def _parse_p1group(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    title_el = el.find(f"./{{{_LEG_NS}}}Title")
    title = _text_content(title_el)
    node = UKMutableNode(kind=IRNodeKind.P1GROUP, label=None, text=title)
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    return node


def _parse_pgroup(el, context, force_active=False, pit_date=None, is_eur=False):
    """Preserve subordinate UK PnGroup titles as explicit heading carriers."""
    if _is_zombie(el, force_active, pit_date):
        return None
    title_el = el.find(f"./{{{_LEG_NS}}}Title")
    title = _text_content(title_el)
    node = UKMutableNode(
        kind=IRNodeKind.PGROUP,
        label=None,
        text=title,
        attrs={
            "source_tag": _tag(el),
            "source_rule_id": "uk_parse_subordinate_pgroup_heading_carrier",
        },
    )
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    return node


def _parse_section(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber")) or _extract_num(el.find(f"./{{{_LEG_NS}}}Number"))
    kind = _get_kind(_tag(el), context, is_eur)
    node = UKMutableNode(kind=cast(IRNodeKind, kind), label=num, text="")
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    if not node.children:
        node.text = _text_content(el)
    else:
        node.text = _local_structural_text(el)
    return node


def _parse_p2(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber"))
    kind = _get_kind(_tag(el), context, is_eur)
    node = UKMutableNode(kind=cast(IRNodeKind, kind), label=num, text="")
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    if not node.children:
        node.text = _text_content(el)
    else:
        node.text = _local_structural_text(el)
    return node


def _parse_p3(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber"))
    kind = _get_kind(_tag(el), context, is_eur)
    node = UKMutableNode(kind=cast(IRNodeKind, kind), label=num, text="")
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    if not node.children:
        node.text = _text_content(el)
    else:
        node.text = _local_structural_text(el)
    return node


def _parse_p4(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber"))
    kind = _get_kind(_tag(el), context, is_eur)
    node = UKMutableNode(kind=cast(IRNodeKind, kind), label=num, text="")
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    if not node.children:
        node.text = _text_content(el)
    else:
        node.text = _local_structural_text(el)
    return node


def _parse_pblock(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    title = _text_content(el.find(f"./{{{_LEG_NS}}}Title"))
    node = UKMutableNode(kind=IRNodeKind.CROSSHEADING, label=None, text=title)
    _add_attrs(node, el)
    node.children = _parse_children(el, context, force_active, pit_date, is_eur)
    return node


def _parse_schedule_single(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    raw_num = _extract_num(el.find(f".//{{{_LEG_NS}}}Number"))
    num = raw_num
    if _clean_num(num) == "schedule":
        num = ""
    title_el = el.find(f".//{{{_LEG_NS}}}Title")
    if title_el is None:
        title_el = el.find(f".//{{{_LEG_NS}}}TitleBlock/{{{_LEG_NS}}}Title")
    title = _text_content(title_el)
    node = UKMutableNode(kind=IRNodeKind.SCHEDULE, label=num, text=title)
    _add_attrs(node, el)
    if not force_active:
        _maybe_infer_container_number(node, el, prefix="schedule", original_label=raw_num)
    body = el.find(f".//{{{_LEG_NS}}}ScheduleBody")
    if body is not None:
        node.children = _parse_children(body, "schedule", force_active, pit_date, is_eur)
    return node


def _parse_schedules(root_el, force_active=False, pit_date=None, is_eur=False):
    s_el = root_el.find(f".//{{{_LEG_NS}}}Schedules")
    if s_el is None:
        return []
    res = []
    for child in s_el:
        if _tag(child) == "Schedule":
            node = _parse_schedule_single(child, "schedule", force_active, pit_date, is_eur)
            if node:
                res.append(node)
    return res


_SOURCE_PARSE_OBSERVATION_RULE_IDS = frozenset(
    {
        "uk_definition_ordered_list_child_preserved",
        _UK_CONTAINER_NUMBER_INFERRED_RULE_ID,
        _UK_SCHEDULE_LIST_ENTRY_RULE_ID,
    }
)


def _visible_inline_text_preservation_observation(
    root: ET.Element,
    *,
    statute_id: str,
    version_label: str,
    source_path: str,
) -> dict[str, Any] | None:
    count = 0
    samples: list[dict[str, str]] = []
    for el in root.iter():
        tag = _tag(el)
        if tag not in _VISIBLE_INLINE_TEXT_TAGS:
            continue
        text = _text_content(el)
        if not text:
            continue
        count += 1
        if len(samples) < 5:
            samples.append({
                "tag": tag,
                "text": " ".join(text.split())[:160],
            })
    if not count:
        return None
    return {
        "rule_id": "uk_visible_inline_text_preserved",
        "family": "source_shape_preservation",
        "phase": "source_parse",
        "statute_id": statute_id,
        "side": version_label,
        "source_url": source_path,
        "count": count,
        "samples": tuple(samples),
        "reason": (
            "UK visible inline source tags such as Citation, CitationSubRef, and Term "
            "were preserved as host provision text while remaining non-addressable as "
            "standalone legal units."
        ),
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }


def _source_parse_observations(
    root_body: UKMutableNode,
    supplements: list[UKMutableNode],
    *,
    statute_id: str,
    version_label: str,
    source_path: str,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    samples: dict[str, list[dict[str, str]]] = {}

    def _walk(node: UKMutableNode) -> None:
        rule_id = str(node.attrs.get("source_rule_id") or "")
        if rule_id in _SOURCE_PARSE_OBSERVATION_RULE_IDS:
            counts[rule_id] = counts.get(rule_id, 0) + 1
            bucket = samples.setdefault(rule_id, [])
            if len(bucket) < 5:
                sample = {"kind": node.kind.value}
                if rule_id == "uk_definition_ordered_list_child_preserved":
                    sample.update(
                        {
                            "definition_term": str(node.attrs.get("definition_term") or ""),
                            "definition_child_label": str(node.attrs.get("definition_child_label") or ""),
                        }
                    )
                elif rule_id == _UK_SCHEDULE_LIST_ENTRY_RULE_ID:
                    sample.update(
                        {
                            "source_tag": str(node.attrs.get("source_tag") or ""),
                            "source_ordinal": str(node.attrs.get("source_ordinal") or ""),
                            "text": " ".join(node.text.split())[:160],
                        }
                    )
                elif rule_id == _UK_CONTAINER_NUMBER_INFERRED_RULE_ID:
                    sample.update(
                        {
                            "source_identifier": str(node.attrs.get("source_identifier") or ""),
                            "original_label": str(node.attrs.get("source_original_label") or ""),
                            "inferred_label": str(node.attrs.get("source_inferred_label") or ""),
                        }
                    )
                bucket.append(sample)
        for child in node.children:
            _walk(child)

    _walk(root_body)
    for supplement in supplements:
        _walk(supplement)

    return [
        {
            "rule_id": rule_id,
            "family": "source_shape_preservation",
            "phase": "source_parse",
            "statute_id": statute_id,
            "side": version_label,
            "source_url": source_path,
            "count": count,
            "samples": samples.get(rule_id, []),
            "reason": "UK source XML structure was preserved as replay-addressable IR rather than flattened into host text.",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        }
        for rule_id, count in sorted(counts.items())
    ]


@dataclass
class UKStatuteIR:
    statute_id: str
    version_label: str
    title: str
    source_path: str
    body: IRNode
    supplements: list[IRNode]
    metadata: dict[str, Any]

    @property
    def schedules(self) -> list[IRNode]:
        """Compatibility alias for older UK-facing callers.

        Prefer ``supplements`` in new code; this property remains only so the
        first-party UK adapter can keep older boundary code working during
        migration.
        """
        warnings.warn(
            "UKStatuteIR.schedules is a transitional compatibility alias; use supplements instead.",
            stacklevel=2,
        )
        return self.supplements

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "uk_statute_ir",
            "statute_id": self.statute_id,
            "version_label": self.version_label,
            "title": self.title,
            "source_path": self.source_path,
            "metadata": self.metadata,
            "body": self.body.to_jsonable_dict(),
            "supplements": [s.to_jsonable_dict() for s in self.supplements],
        }


def _infer_statute_id(path: Path) -> str:
    parts = list(path.parts)
    for i, p in enumerate(parts):
        if re.fullmatch(r"ukpga|uksi|ukla|asp|anaw|eur|nia|asc", p, re.I):
            return "/".join(parts[i : i + 3])
    return "unknown"


def _build_ir_from_root(
    root: ET.Element,
    *,
    statute_id: Optional[str],
    version_label: Optional[str],
    pit_date: Optional[str],
    source_path: str,
) -> IRStatute:
    sid = statute_id or _infer_statute_id(Path(source_path))
    vlabel = version_label or "archive"

    title = ""
    meta_el = root.find(f".//{{{_LEG_NS}}}Metadata")
    if meta_el is not None:
        dc_title = meta_el.find(".//{http://purl.org/dc/elements/1.1/}title")
        if dc_title is not None:
            title = (dc_title.text or "").strip()

    body_el = None
    is_eur = False
    for el in root.iter():
        tag = _tag(el)
        if tag == "Body":
            body_el = el
            break
        if tag in ("EUBody", "EURetained"):
            body_el = el
            is_eur = True
            break

    body_nodes = []
    if body_el is not None:
        body_nodes = _parse_children(body_el, "body", False, pit_date, is_eur)

    if is_eur:
        for div in root.findall(f".//{{{_LEG_NS}}}Division"):
            node = _parse_section(div, "preamble", False, pit_date, True)
            if node:
                body_nodes.insert(0, node)

    root_body = UKMutableNode(kind=IRNodeKind.BODY, label=None, text="", children=body_nodes)
    schedule_nodes = _parse_schedules(root, False, pit_date, is_eur)
    parse_observations = _source_parse_observations(
        root_body,
        schedule_nodes,
        statute_id=sid,
        version_label=vlabel,
        source_path=source_path,
    )
    visible_inline_observation = _visible_inline_text_preservation_observation(
        root,
        statute_id=sid,
        version_label=vlabel,
        source_path=source_path,
    )
    if visible_inline_observation is not None:
        parse_observations.append(visible_inline_observation)

    return IRStatute(
        statute_id=sid,
        title=title,
        body=root_body.to_irnode(),
        supplements=[schedule.to_irnode() for schedule in schedule_nodes],
        metadata={
            "source_path": source_path,
            "is_eur": is_eur,
            "version_label": vlabel,
            "source_parse_observations": parse_observations,
        },
    )


def parse_uk_statute_ir(
    xml_path: Path,
    statute_id: Optional[str] = None,
    version_label: Optional[str] = None,
    pit_date: Optional[str] = None,
) -> IRStatute:
    tree = ET.parse(xml_path)
    return _build_ir_from_root(
        tree.getroot(),
        statute_id=statute_id or _infer_statute_id(xml_path),
        version_label=version_label or xml_path.parent.name,
        pit_date=pit_date,
        source_path=str(xml_path),
    )


def parse_uk_statute_ir_bytes(
    xml_bytes: bytes,
    *,
    statute_id: Optional[str] = None,
    version_label: Optional[str] = None,
    pit_date: Optional[str] = None,
    source_path: str = "<archive>",
) -> IRStatute:
    return _build_ir_from_root(
        ET.fromstring(xml_bytes),
        statute_id=statute_id,
        version_label=version_label,
        pit_date=pit_date,
        source_path=source_path,
    )


def _slugify(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")


def _normalize_text_for_grounding(text: str) -> str:
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


def _semantic_hash(text: str) -> str:
    noise = r"\b(the|a|an|of|and|or|to|in|by|from|with|as|for|is|it|at|on|this|that|be|been|being)\b"
    s = _normalize_text_for_grounding(text)
    s = re.sub(noise, "", s)
    return "".join(s.split())


def _visit_eid(
    el,
    parent_path_key: str,
    context: str,
    is_eur: bool,
    pit_date: Optional[str],
    eid_map: Dict[str, str],
    text_map: Dict[str, str],
    physical_eid_aliases: dict[str, str],
    visible_number_eid_aliases: dict[str, str],
    oracle_identity_observations: list[dict[str, Any]],
):
    if _is_zombie(el, False, pit_date):
        return
    tag = _tag(el)
    # Skip editorial annotations entirely — they are absent from the enacted XML
    # and must not contribute eIds to the oracle scoring set.
    if tag in _EDITORIAL_TAGS:
        return
    skip_own_eid = tag in _NON_LEGAL_UNIT_EID_TAGS
    eid = el.get("eId") or el.get("id")
    _pnum = el.find(f"./{{{_LEG_NS}}}Pnumber")
    _nnum = el.find(f"./{{{_LEG_NS}}}Number")
    num_el = _pnum if _pnum is not None else _nnum
    kind = _get_kind(tag, context, is_eur)
    if num_el is None and kind in ("chapter", "part"):
        num_el = el.find(f".//{{{_LEG_NS}}}Number")
    num = _extract_num(num_el)
    clean_num = _clean_num(num)

    # If no Pnumber element, infer num from the id/eId attribute itself.
    # Use re.search with the element's own kind so composite IDs like
    # "schedule-1-part-I-chapter-1" → kind=chapter → captures "1", not "1-part-i-chapter-1".
    if not clean_num and eid and kind not in ("body", "crossheading", "p1group", "pblock"):
        _eid_lower = eid.lower()
        _m = re.search(r"(?:^|-)(?:" + re.escape(kind) + r")-([^-]+)$", _eid_lower)
        if _m:
            _inferred = _clean_num(_m.group(1))
            if _inferred:
                clean_num = _inferred
        elif kind in {"subsection", "paragraph", "subparagraph", "item", "point"}:
            # Descendant UK IDs are often full ancestor paths
            # (`section-5-1B-c-ii`).  The physical local label is the final
            # component, not the whole section-rooted suffix.
            parts = [part for part in re.split(r"[-_]+", _eid_lower) if part]
            if parts:
                _inferred = _clean_num(parts[-1])
                if _inferred:
                    clean_num = _inferred
        else:
            # Fallback: any recognized kind at start (simple ids like "section-2")
            _m = re.match(
                r"(?:section|article|paragraph|subsection|schedule|part|chapter|annex|rule)[-](.+)$", _eid_lower
            )
            if _m:
                _inferred = _clean_num(_m.group(1))
                if _inferred:
                    clean_num = _inferred

    new_context = context
    if kind == "schedule" and clean_num:
        new_context = f"schedule-{clean_num}"
    elif kind == "body":
        new_context = "body"

    title_el = el.find(f"./{{{_LEG_NS}}}Title")
    title = _text_content(title_el) if title_el is not None else ""
    slug = _slugify(title)
    transparent_tags = (
        "p1para",
        "p2para",
        "p3para",
        "p4para",
        "schedules",
        "schedulebody",
        "pnumber",
        "number",
        "title",
        "body",
        "eubody",
        "euretained",
    )
    node_key_part = f"{kind}-{clean_num}" if clean_num else (f"{kind}-{slug}" if slug else kind)

    if kind in transparent_tags:
        this_node_path = parent_path_key
    else:
        this_node_path = f"{parent_path_key}:{node_key_part}" if parent_path_key else node_key_part

    if eid and not skip_own_eid:
        key = this_node_path.lower()
        if key not in eid_map:
            eid_map[key] = eid
        _record_physical_eid_drift(
            eid=eid,
            physical_eid=_physical_eid_from_semantic_path(this_node_path),
            tag=tag,
            path_key=this_node_path,
            aliases=physical_eid_aliases,
            observations=oracle_identity_observations,
        )
        _record_visible_number_eid_alias(
            eid=eid,
            kind=kind,
            clean_num=clean_num,
            tag=tag,
            path_key=this_node_path,
            aliases=visible_number_eid_aliases,
            observations=oracle_identity_observations,
        )
        text = _text_content(el)
        if text and not re.match(r"^[.\s]+$", text):
            norm = _normalize_text_for_grounding(text)
            text_map[eid] = norm
            h = _semantic_hash(text)
            if f"hash:{h}" not in eid_map:
                eid_map[f"hash:{h}"] = eid
        if clean_num:
            eid_map[f"{new_context}:{kind}-{clean_num}".lower()] = eid
            if is_eur and kind == "schedule":
                eid_map[f"{new_context}:annex-{clean_num}".lower()] = eid
            eid_map[f"{new_context}:suffix:{kind}-{clean_num}".lower()] = eid
            # Also add title-slug alias so pblocks/crossheadings with matching headings
            # can find numbered nodes (e.g. Schedule 1 ECHR article chapters).
            if slug:
                eid_map[f"{new_context}:suffix:{kind}-{slug}".lower()] = eid
        elif slug:
            eid_map[f"{new_context}:suffix:{kind}-{slug}".lower()] = eid

    next_parent_path = parent_path_key if kind in ("p1group", "pblock", "crossheading") else this_node_path
    kind_counts = {}
    for child in el:
        ct = _tag(child)
        # Skip editorial children in ordinal registration as well as in recursion.
        if ct in _EDITORIAL_TAGS:
            continue
        if _is_zombie(child, False, pit_date):
            continue
        ck = _get_kind(ct, new_context, is_eur)
        if ck not in transparent_tags:
            kind_counts[ck] = kind_counts.get(ck, 0) + 1
            ord_path = f"{next_parent_path}:{ck}[{kind_counts[ck]}]".lower()
            ceid = child.get("eId") or child.get("id")
            if ceid and ct not in _NON_LEGAL_UNIT_EID_TAGS and ord_path not in eid_map:
                eid_map[ord_path] = ceid
        _visit_eid(
            child,
            next_parent_path,
            new_context,
            is_eur,
            pit_date,
            eid_map,
            text_map,
            physical_eid_aliases,
            visible_number_eid_aliases,
            oracle_identity_observations,
        )


def _extract_eid_map_from_root(root: Any, pit_date: Optional[str] = None) -> Dict[str, Any]:
    eid_map = {}
    text_map = {}
    physical_eid_aliases: dict[str, str] = {}
    visible_number_eid_aliases: dict[str, str] = {}
    oracle_identity_observations: list[dict[str, Any]] = []
    is_eur = any(_tag(el) == "EURetained" for el in root.iter() if isinstance(el.tag, str))
    body = root.find(f".//{{{_LEG_NS}}}Body")
    if body is None:
        body = root.find(f".//{{{_LEG_NS}}}EURetained")
    if body is not None:
        _visit_eid(
            body,
            "body",
            "body",
            is_eur,
            pit_date,
            eid_map,
            text_map,
            physical_eid_aliases,
            visible_number_eid_aliases,
            oracle_identity_observations,
        )
    schedules = root.find(f".//{{{_LEG_NS}}}Schedules")
    if schedules is not None:
        _visit_eid(
            schedules,
            "",
            "schedule",
            is_eur,
            pit_date,
            eid_map,
            text_map,
            physical_eid_aliases,
            visible_number_eid_aliases,
            oracle_identity_observations,
        )
    return {
        "eid_map": eid_map,
        "text_map": text_map,
        "physical_eid_aliases": physical_eid_aliases,
        "visible_number_eid_aliases": visible_number_eid_aliases,
        "oracle_identity_observations": oracle_identity_observations,
    }


def extract_eid_map(xml_path: Path, pit_date: Optional[str] = None) -> Dict[str, Any]:
    from lxml import etree as LET

    tree = LET.parse(str(xml_path))
    return _extract_eid_map_from_root(tree.getroot(), pit_date=pit_date)


def extract_eid_map_bytes(xml_bytes: bytes, pit_date: Optional[str] = None) -> Dict[str, Any]:
    from lxml import etree as LET

    root = LET.fromstring(xml_bytes)
    return _extract_eid_map_from_root(root, pit_date=pit_date)
