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
    structural_tags = (
        "Part",
        "Chapter",
        "EUChapter",
        "P1group",
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
    num = _extract_num(el.find(f".//{{{_LEG_NS}}}Number"))
    if _clean_num(num) == "schedule":
        num = ""
    title_el = el.find(f".//{{{_LEG_NS}}}Title")
    if title_el is None:
        title_el = el.find(f".//{{{_LEG_NS}}}TitleBlock/{{{_LEG_NS}}}Title")
    title = _text_content(title_el)
    node = UKMutableNode(kind=IRNodeKind.SCHEDULE, label=num, text=title)
    _add_attrs(node, el)
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

    return IRStatute(
        statute_id=sid,
        title=title,
        body=root_body.to_irnode(),
        supplements=[schedule.to_irnode() for schedule in schedule_nodes],
        metadata={"source_path": source_path, "is_eur": is_eur, "version_label": vlabel},
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
):
    if _is_zombie(el, False, pit_date):
        return
    tag = _tag(el)
    # Skip editorial annotations entirely — they are absent from the enacted XML
    # and must not contribute eIds to the oracle scoring set.
    if tag in _EDITORIAL_TAGS:
        return
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
        if not _m:
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

    if eid:
        key = this_node_path.lower()
        if key not in eid_map:
            eid_map[key] = eid
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
            if ceid and ord_path not in eid_map:
                eid_map[ord_path] = ceid
        _visit_eid(child, next_parent_path, new_context, is_eur, pit_date, eid_map, text_map)


def _extract_eid_map_from_root(root: Any, pit_date: Optional[str] = None) -> Dict[str, Any]:
    eid_map = {}
    text_map = {}
    is_eur = any(_tag(el) == "EURetained" for el in root.iter() if isinstance(el.tag, str))
    body = root.find(f".//{{{_LEG_NS}}}Body")
    if body is None:
        body = root.find(f".//{{{_LEG_NS}}}EURetained")
    if body is not None:
        _visit_eid(body, "body", "body", is_eur, pit_date, eid_map, text_map)
    schedules = root.find(f".//{{{_LEG_NS}}}Schedules")
    if schedules is not None:
        _visit_eid(schedules, "", "schedule", is_eur, pit_date, eid_map, text_map)
    return {"eid_map": eid_map, "text_map": text_map}


def extract_eid_map(xml_path: Path, pit_date: Optional[str] = None) -> Dict[str, Any]:
    from lxml import etree as LET

    tree = LET.parse(str(xml_path))
    return _extract_eid_map_from_root(tree.getroot(), pit_date=pit_date)


def extract_eid_map_bytes(xml_bytes: bytes, pit_date: Optional[str] = None) -> Dict[str, Any]:
    from lxml import etree as LET

    root = LET.fromstring(xml_bytes)
    return _extract_eid_map_from_root(root, pit_date=pit_date)
