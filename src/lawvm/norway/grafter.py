"""Norway (Lovdata) frontend for LawVM.

The Norway path is structurally different from Finland and Estonia:

- consolidated base acts come from Lovdata public bulk downloads as HTML-in-XML
- amending acts also come from public bulk downloads
- amendment targeting is encoded directly in attributes such as
  ``data-change-part`` / ``data-add-new-part`` / ``data-remove-part``

That means Norway should be compiler-first but not NLP-first. The main task is
to normalize Lovdata structure into IR trees and LegalOperation objects.
"""

from __future__ import annotations

import copy
import itertools
import re
import tarfile
from dataclasses import dataclass, replace as dc_replace
from typing import Generator, List, Optional, Sequence, Tuple, cast

from lxml import etree

from lawvm.core import tree_ops
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.roman import roman_to_arabic as _shared_roman_to_int
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum

NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER = "no_parse_replace_promoted_to_insert_for_same_target_renumber"


def _no_action_value(action: StructuralAction | str) -> str:
    """Normalize action to string value for comparisons and serialization."""
    return action.value if isinstance(action, StructuralAction) else action


def _no_kind_value(kind: IRNodeKind | str) -> str:
    """Normalize IR node kinds to string values for comparisons."""
    return kind.value if isinstance(kind, IRNodeKind) else kind


_FILENAME_RE = re.compile(r"^(?:nl/)?nl-(\d{4})(\d{2})(\d{2})-(\d+)(?:-(nn))?\.xml$")
_AMENDMENT_FILENAME_RE = re.compile(r"^(?:lti/\d{4}/)?nl-(\d{4})(\d{2})(\d{2})-(\d+)(?:-(nn))?\.xml$")
_REFID_RE = re.compile(r"lov/\d{4}-\d{2}-\d{2}(?:-\d+)?")
_SPACE_RE = re.compile(r"\s+")
_SECTION_LABEL_RE = re.compile(r"^\s*§\s*")
_NUMBERED_SUBSECTION_RE = re.compile(r"^\(\s*(\d+)\s*\)\s*")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_SENTENCE_ABBREVIATIONS = {"jf.", "nr.", "pkt.", "mv.", "osv."}
_CONTINUATION_PUNKTUM_RE = re.compile(
    r"^(?:Første|Fyrste|Andre|Annet|Tredje|Fjerde|Femte|Sjette|Sjuende|Syvende|Åttende|Niende|Tiende)\s+punktum\b",
    re.IGNORECASE,
)
_NORWEGIAN_ORDINALS = {
    "første": "1",
    "fyrste": "1",
    "andre": "2",
    "annet": "2",
    "tredje": "3",
    "fjerde": "4",
    "femte": "5",
    "sjette": "6",
    "sjuende": "7",
    "syvende": "7",
    "åttende": "8",
    "niende": "9",
    "tiende": "10",
}
_NORWEGIAN_MONTHS = {
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
}
_NORWEGIAN_MONTH_NUMBERS = {
    "januar": "01",
    "februar": "02",
    "mars": "03",
    "april": "04",
    "mai": "05",
    "juni": "06",
    "juli": "07",
    "august": "08",
    "september": "09",
    "oktober": "10",
    "november": "11",
    "desember": "12",
}


def _repair_no_mojibake(text: str) -> str:
    if not text or not any(marker in text for marker in ("Ã", "Â", "â")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    if repaired == text:
        return text
    original_markers = sum(text.count(marker) for marker in ("Ã", "Â", "â"))
    repaired_markers = sum(repaired.count(marker) for marker in ("Ã", "Â", "â"))
    if repaired_markers > original_markers:
        return text
    return repaired


_FUTURE_HEADING_RANGE_RE = re.compile(
    r"Ny deloverskrift til (?:ny |nye )?§{1,2}\s*([0-9A-Za-z-]+)(?:\s+til\s+([0-9A-Za-z-]+))?",
    re.IGNORECASE,
)
_SECTION_HEADING_ONLY_RE = re.compile(r"^Overskrift(?:en|a) til §", re.IGNORECASE)
_QUOTED_NO_TEXT_REPLACE_RE = re.compile(
    r"[«\"]([^»\"]+)[»\"](?:\s+erstattes)?\s+med\s+[«\"]([^»\"]+)[»\"]", re.IGNORECASE
)
_TEXT_BLOCK_CLASSES = {"legalP", "defaultP", "legalArticleHeader"}
_ITEM_CONTAINER_TAGS = {"ol", "ul"}


@dataclass(frozen=True)
class NOHeadingGroup:
    start_label: str
    end_label: str
    title: str
    sequence: int


def lovdata_filename_to_id(filename: str) -> Optional[str]:
    """Convert ``nl/nl-18840614-003.xml`` to ``no/lov/1884-06-14-3``.

    Returns ``None`` for Nynorsk duplicates (``-nn.xml``).
    """
    basename = filename.rsplit("/", 1)[-1]
    match = _FILENAME_RE.match(filename) or _FILENAME_RE.match(basename)
    if not match:
        return None
    year, month, day, number, nynorsk = match.groups()
    if nynorsk:
        return None
    return f"no/lov/{year}-{month}-{day}-{int(number)}"


def lovdata_amendment_filename_to_id(filename: str) -> Optional[str]:
    """Convert Lovtidend archive filenames to canonical amendment statute IDs."""
    basename = filename.rsplit("/", 1)[-1]
    match = _AMENDMENT_FILENAME_RE.match(filename) or _AMENDMENT_FILENAME_RE.match(basename)
    if not match:
        return None
    year, month, day, number, nynorsk = match.groups()
    if nynorsk:
        return None
    return f"no/lovtid/{year}-{month}-{day}-{int(number)}"


def normalize_lovdata_refid(raw: str) -> Optional[str]:
    """Normalize noisy Lovdata act references to canonical ``no/lov/...`` IDs."""
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("no/lov/"):
        raw = raw.removeprefix("no/")
    match = _REFID_RE.search(raw)
    if not match:
        return None
    return f"no/{match.group(0)}"


def _parse_document(html_bytes: bytes) -> etree._Element:
    """Parse Lovdata HTML/XML bytes into a tolerant element tree."""
    xml_parser = etree.XMLParser(recover=True)
    try:
        root = etree.fromstring(html_bytes, parser=xml_parser)
        if root is not None:
            return root
    except etree.XMLSyntaxError:
        pass

    html_parser = etree.HTMLParser(recover=True)
    root = etree.fromstring(html_bytes, parser=html_parser)
    if root is None:
        raise ValueError("unable to parse Lovdata document")
    return root


def _local_name(el: etree._Element) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _classes(el: etree._Element) -> set[str]:
    raw = el.get("class", "")
    return {part for part in raw.split() if part}


def _has_class(el: etree._Element, cls: str) -> bool:
    return cls in _classes(el)


def _normalize_space(text: str) -> str:
    return _SPACE_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _normalize_label(label: str) -> str:
    label = _normalize_space(label)
    label = _SECTION_LABEL_RE.sub("", label)
    label = label.rstrip(".:;,)")
    return label.strip()


def _normalize_no_section_label(label: str) -> str:
    return _normalize_label(label).replace(" ", "")


def _first_heading_text(el: etree._Element) -> str:
    for child in el:
        if _local_name(child) in {"h1", "h2", "h3", "h4"}:
            text = _normalize_space("".join(str(_t) for _t in child.itertext()))
            if text:
                return text
    return ""


def _node_text_without_structural_children(
    el: etree._Element,
    *,
    skip_direct_classes: frozenset[str] = frozenset(),
) -> str:
    """Extract text while excluding nested structural blocks/lists."""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        classes = _classes(child)
        if skip_direct_classes and skip_direct_classes & classes:
            if child.tail:
                parts.append(child.tail)
            continue
        lname = _local_name(child)
        if lname not in {"article", "section", "li", "ol", "ul"}:
            child_text = _normalize_space("".join(str(_t) for _t in child.itertext()))
            if child_text:
                parts.append(child_text)
        if child.tail:
            parts.append(child.tail)
    return _normalize_space(" ".join(parts))


def _direct_children(el: etree._Element, tag: Optional[str] = None) -> list[etree._Element]:
    out: list[etree._Element] = []
    for child in el:
        if not isinstance(child.tag, str):
            continue
        if tag is None or _local_name(child) == tag:
            out.append(child)
    return out


def _find_direct_children_with_class(el: etree._Element, cls: str) -> list[etree._Element]:
    return [child for child in _direct_children(el) if _has_class(child, cls)]


def _iter_change_descendants(el: etree._Element) -> list[etree._Element]:
    """Return change blocks under a document-change container without nested duplicates."""
    change_nodes: list[etree._Element] = []
    stack = list(reversed(_direct_children(el)))
    while stack:
        node = stack.pop()
        if "change" in _classes(node):
            change_nodes.append(node)
            continue
        stack.extend(reversed(_direct_children(node)))
    return change_nodes


def _extract_items(container: etree._Element) -> list[IRNode]:
    items: list[IRNode] = []
    used_labels: set[str] = set()
    next_index = 1
    for child in _direct_children(container):
        lname = _local_name(child)
        if lname == "article":
            for item in _extract_items(child):
                relabeled = _with_no_node_label(
                    item,
                    _dedupe_no_sibling_label(item.label or str(next_index), next_index, used_labels),
                )
                items.append(relabeled)
                next_index += 1
        elif lname in _ITEM_CONTAINER_TAGS:
            for grandchild in _direct_children(child, "li"):
                item = _parse_item(grandchild, next_index, used_labels)
                if item is not None:
                    items.append(item)
                    next_index += 1
        elif lname == "li":
            item = _parse_item(child, next_index, used_labels)
            if item is not None:
                items.append(item)
                next_index += 1
    return items


def _index_no_item_candidates(
    candidates: dict[tuple[str, str], IRNode],
    item: IRNode,
) -> None:
    if item.label:
        candidates[("item", item.label)] = item
    for child in item.children:
        if _no_kind_value(child.kind) == "item":
            _index_no_item_candidates(candidates, child)


def _dedupe_no_sibling_label(
    preferred: str,
    sequence_index: int,
    used_labels: set[str],
) -> str:
    label = _normalize_label(preferred) or str(sequence_index)
    if label not in used_labels:
        used_labels.add(label)
        return label
    fallback = sequence_index
    while str(fallback) in used_labels:
        fallback += 1
    label = str(fallback)
    used_labels.add(label)
    return label


def _parse_item(
    li_el: etree._Element,
    sequence_index: int,
    used_labels: set[str],
) -> Optional[IRNode]:
    label = li_el.get("data-name") or li_el.get("data-li-identifier") or li_el.get("id") or ""
    if _normalize_label(label) in {"", "-"}:
        label = str(sequence_index)
    label = _dedupe_no_sibling_label(label, sequence_index, used_labels)
    text = _node_text_without_structural_children(li_el)
    if not text:
        for child in _direct_children(li_el, "article"):
            child_text = _node_text_without_structural_children(child)
            if not child_text:
                for grandchild in _direct_children(child, "article"):
                    child_text = _node_text_without_structural_children(grandchild)
                    if child_text:
                        break
            if child_text:
                text = child_text
                break
    children = _extract_items(li_el)
    if not label and not text and not children:
        return None
    return IRNode(kind=IRNodeKind.ITEM, label=label, text=text, children=tuple(children))


def _parse_subsection(article_el: etree._Element, index: int, used_labels: set[str]) -> Optional[IRNode]:
    raw_label = article_el.get("data-numerator", "").strip() or str(index)
    label = _dedupe_no_sibling_label(raw_label, index, used_labels)
    text = _node_text_without_structural_children(
        article_el,
        skip_direct_classes=frozenset({"leddfortsettelse"}),
    )
    if article_el.get("data-numerator"):
        text = _NUMBERED_SUBSECTION_RE.sub("", text, count=1)
    items = _extract_items(article_el)
    continuation_texts = [
        _normalize_space("".join(str(_t) for _t in child.itertext()))
        for child in _direct_children(article_el)
        if "leddfortsettelse" in _classes(child)
    ]
    continuation_texts = [part for part in continuation_texts if part]
    sentence_children = [
        IRNode(kind=IRNodeKind.SENTENCE, label=str(index), text=part)
        for index, part in enumerate(continuation_texts, start=1)
    ]
    children = tuple(items + sentence_children)
    if not text and not children:
        return None
    return IRNode(
        kind=IRNodeKind.SUBSECTION,
        label=label,
        text=text,
        children=children,
    )


def _merge_no_unlabeled_subsection_continuation(
    children: list[IRNode],
    article_el: etree._Element,
    subsection: IRNode,
    used_labels: set[str],
) -> bool:
    if article_el.get("data-numerator"):
        return False
    if subsection.children:
        return False
    text = _normalize_space(subsection.text or "")
    if not _CONTINUATION_PUNKTUM_RE.match(text):
        return False
    if not children:
        return False
    prev = children[-1]
    if _no_kind_value(prev.kind) != "subsection":
        return False
    prev_text = _normalize_space(prev.text or "")
    merged_text = " ".join(part for part in [prev_text, text] if part).strip()
    children[-1] = IRNode(
        kind=prev.kind,
        label=prev.label,
        text=merged_text,
        attrs=dict(prev.attrs),
        children=prev.children,
    )
    used_labels.discard(subsection.label or "")
    return True


def _merge_no_leddfortsettelse_paragraph(
    children: list[IRNode],
    paragraph_el: etree._Element,
) -> bool:
    classes = _classes(paragraph_el)
    if "leddfortsettelse" not in classes:
        return False
    if not children:
        return False
    prev = children[-1]
    if _no_kind_value(prev.kind) != "subsection":
        return False
    text = _normalize_space(" ".join(str(_t) for _t in paragraph_el.itertext()))
    if not text:
        return False
    if any(_no_kind_value(child.kind) in {"item", "sentence"} for child in prev.children):
        sentence_labels = [
            int(child.label)
            for child in prev.children
            if _no_kind_value(child.kind) == "sentence" and child.label and re.fullmatch(r"\d+", child.label)
        ]
        next_label = str(max(sentence_labels) + 1) if sentence_labels else "1"
        children[-1] = IRNode(
            kind=prev.kind,
            label=prev.label,
            text=prev.text,
            attrs=dict(prev.attrs),
            children=tuple(
                [child for child in prev.children] + [IRNode(kind=IRNodeKind.SENTENCE, label=next_label, text=text)]
            ),
        )
        return True
    prev_text = _normalize_space(prev.text or "")
    merged_text = " ".join(part for part in [prev_text, text] if part).strip()
    children[-1] = IRNode(
        kind=prev.kind,
        label=prev.label,
        text=merged_text,
        attrs=dict(prev.attrs),
        children=prev.children,
    )
    return True


def _section_label_from_element(section_el: etree._Element) -> str:
    label = section_el.get("data-name", "")
    if not label:
        url = section_el.get("data-lovdata-url") or section_el.get("data-lovdata-URL") or ""
        label = url.rsplit("/", 1)[-1]
    return _normalize_label(label)


def _parse_section(section_el: etree._Element) -> Optional[IRNode]:
    label = _section_label_from_element(section_el)
    heading_text = _first_heading_text(section_el)
    children: list[IRNode] = []
    if heading_text:
        title = heading_text
        if label:
            title = re.sub(rf"^\s*§\s*{re.escape(label)}\s*", "", title).strip(" .:-")
        if title:
            children.append(IRNode(kind=IRNodeKind.HEADING, text=title))

    subsection_index = 1
    used_subsection_labels: set[str] = set()
    for child in _direct_children(section_el):
        lname = _local_name(child)
        if lname == "p":
            _merge_no_leddfortsettelse_paragraph(children, child)
            continue
        if lname != "article":
            continue
        classes = _classes(child)
        if "changesToParent" in classes:
            continue
        if not ({"legalP", "defaultP", "numberedLegalP"} & classes):
            continue
        subsection = _parse_subsection(child, subsection_index, used_subsection_labels)
        if subsection is not None:
            if _merge_no_unlabeled_subsection_continuation(children, child, subsection, used_subsection_labels):
                continue
            children.append(subsection)
            subsection_index += 1

    if not children:
        text = _node_text_without_structural_children(section_el)
        if not text:
            return None
        return IRNode(kind=IRNodeKind.SECTION, label=label or None, text=text)

    return IRNode(kind=IRNodeKind.SECTION, label=label or None, children=tuple(children))


def _parse_future_section(section_el: etree._Element) -> Optional[IRNode]:
    label = _normalize_label(section_el.get("data-name", "") or "")
    children: list[IRNode] = []

    for child in _direct_children(section_el):
        if "futureLegalArticleHeader" not in _classes(child):
            continue
        title = _normalize_space("".join(str(_t) for _t in child.itertext()))
        if label:
            title = re.sub(rf"^\s*§\s*{re.escape(label)}\s*", "", title).strip(" .:-")
        if title:
            children.append(IRNode(kind=IRNodeKind.HEADING, text=title))

    subsection_index = 1
    used_subsection_labels: set[str] = set()
    for child in _direct_children(section_el, "article"):
        classes = _classes(child)
        if not ({"legalP", "defaultP", "numberedLegalP"} & classes):
            continue
        subsection = _parse_subsection(child, subsection_index, used_subsection_labels)
        if subsection is not None:
            if _merge_no_unlabeled_subsection_continuation(children, child, subsection, used_subsection_labels):
                continue
            children.append(subsection)
            subsection_index += 1
    for child in _direct_children(section_el, "p"):
        _merge_no_leddfortsettelse_paragraph(children, child)

    if not children:
        return IRNode(kind=IRNodeKind.SECTION, label=label or None, text="")
    return IRNode(kind=IRNodeKind.SECTION, label=label or None, children=tuple(children))


def _label_from_container_url(section_el: etree._Element) -> Optional[str]:
    url = section_el.get("data-lovdata-url") or section_el.get("data-lovdata-URL") or ""
    tail = (url or "").rsplit("/", 1)[-1]
    match = re.match(r"KAPITTEL_(.+)$", tail)
    if match:
        return _normalize_label(match.group(1).replace("_", "-")) or None
    return _normalize_label(tail) or None


def _container_kind_and_label(section_el: etree._Element) -> tuple[str, Optional[str]]:
    data_name = section_el.get("data-name", "") or ""
    if data_name.startswith("del"):
        label = _normalize_label(data_name.removeprefix("del")) or None
        if label and re.search(r"\d", label):
            return "part", label
        return "part", _label_from_container_url(section_el)
    if data_name.startswith("kap"):
        label = _normalize_label(data_name.removeprefix("kap")) or None
        if label and (re.search(r"\d", label) or re.fullmatch(r"[ivxlcdm]+", label, re.IGNORECASE)):
            return "chapter", label
        return "chapter", _label_from_container_url(section_el)

    return "chapter", _label_from_container_url(section_el)


def _parse_container(section_el: etree._Element) -> Optional[IRNode]:
    kind, label = _container_kind_and_label(section_el)
    heading_text = _first_heading_text(section_el)
    children: list[IRNode] = []
    if heading_text:
        children.append(IRNode(kind=IRNodeKind.HEADING, text=heading_text))

    for child in _direct_children(section_el):
        lname = _local_name(child)
        if lname == "section" and _has_class(child, "section"):
            parsed = _parse_container(child)
            if parsed is not None:
                children.append(parsed)
        elif lname == "article" and _has_class(child, "legalArticle"):
            parsed = _parse_section(child)
            if parsed is not None:
                children.append(parsed)

    if not any(_no_kind_value(child.kind) != "heading" for child in children):
        return None
    return IRNode(kind=IRNodeKind(kind), label=label, children=tuple(children))


def parse_no_statute(html_bytes: bytes, statute_id: str) -> IRStatute:
    """Parse a Lovdata consolidated document into canonical IR."""
    root = _parse_document(html_bytes)
    title = _normalize_space(str(root.xpath("string(//title[1])")))
    main_nodes = cast(
        list[etree._Element], root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' documentBody ')]")
    )
    main = main_nodes[0] if main_nodes else root

    body_children: list[IRNode] = []
    chapter_nodes = [child for child in _direct_children(main, "section") if _has_class(child, "section")]
    for chapter_el in chapter_nodes:
        chapter = _parse_container(chapter_el)
        if chapter is not None:
            body_children.append(chapter)

    if not body_children:
        for article in _direct_children(main, "article"):
            if not _has_class(article, "legalArticle"):
                continue
            section = _parse_section(article)
            if section is not None:
                body_children.append(section)

    return IRStatute(
        statute_id=statute_id,
        title=title,
        body=IRNode(kind=IRNodeKind.BODY, children=tuple(body_children)),
        metadata={"source_format": "lovdata_html"},
    )


def _eli_kind_and_step(parts: Sequence[str], idx: int) -> tuple[Optional[tuple[str, str]], int]:
    token = parts[idx]
    if token.startswith("KAPITTEL_"):
        return ("chapter", _normalize_label(token.split("_", 1)[1].replace("_", "-"))), idx
    if token.startswith("§"):
        return ("section", _normalize_label(token)), idx
    if token in {"ledd", "nummer", "bokstav", "setning"} and idx + 1 < len(parts):
        label = _normalize_label(parts[idx + 1])
        kind = {
            "ledd": "subsection",
            "nummer": "item",
            "bokstav": "item",
            "setning": "sentence",
        }[token]
        return (kind, label), idx + 1
    return None, idx


def lovdata_path_to_address(path: str) -> Optional[LegalAddress]:
    """Convert a Lovdata ELI-like path to a LegalAddress."""
    if not path:
        return None
    parts = [part for part in path.strip().split("/") if part]
    steps: list[tuple[str, str]] = []
    idx = 0
    while idx < len(parts):
        step, idx = _eli_kind_and_step(parts, idx)
        if step is not None:
            steps.append(step)
        idx += 1
    if not steps:
        return None
    return LegalAddress(path=tuple(steps))


def _split_change_attr(value: str, default_action: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for token in value.split():
        token = token.strip()
        if not token:
            continue
        if ";;" in token:
            out.append(("renumber", token))
            continue
        if token.startswith("tilføyer="):
            out.append(("insert", token.split("=", 1)[1]))
        else:
            out.append((default_action, token))
    return out


def _split_move_attr(value: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    tokens = [token.strip() for token in value.split() if token.strip()]
    for token in reversed(tokens):
        if ";;" not in token:
            continue
        src, dst = token.split(";;", 1)
        if src and dst:
            out.append((src, dst))
    return out


def _payload_from_direct_text_article(
    article_el: etree._Element,
    target: LegalAddress,
) -> Optional[IRNode]:
    kind = target.leaf_kind()
    label = target.leaf_label() or None
    if kind == "subsection":
        payload = _parse_subsection(article_el, 1, set())
        if payload is None:
            return None
        return _with_no_node_label(payload, label)
    if kind == "sentence":
        text = _node_text_without_structural_children(article_el)
        if not text:
            return None
        return IRNode(kind=IRNodeKind.SENTENCE, label=label, text=text)
    return None


def _split_no_sentences(text: str) -> list[str]:
    raw_parts = [
        _normalize_space(part) for part in _SENTENCE_SPLIT_RE.split(_normalize_space(text)) if _normalize_space(part)
    ]
    parts: list[str] = []
    for part in raw_parts:
        first_token = part.split()[0].lower() if part.split() else ""
        if (
            parts
            and parts[-1].split()
            and (
                parts[-1].split()[-1].lower() in _SENTENCE_ABBREVIATIONS
                or (re.fullmatch(r"\d+\.", parts[-1].split()[-1]) is not None and first_token in _NORWEGIAN_MONTHS)
            )
        ):
            parts[-1] = _normalize_space(f"{parts[-1]} {part}")
        else:
            parts.append(part)
    return parts


def _extract_payload_candidates(
    change_el: etree._Element,
    targets: Sequence[LegalAddress],
) -> dict[tuple[str, str], IRNode]:
    """Build leaf-kind/label payload candidates from a Lovdata change block."""
    candidates: dict[tuple[str, str], IRNode] = {}

    used_item_labels: set[str] = set()
    next_item_index = 1

    for li_el in _direct_children(change_el, "li"):
        item = _parse_item(li_el, next_item_index, used_item_labels)
        if item is not None and item.label:
            _index_no_item_candidates(candidates, item)
            next_item_index += 1

    for container in _direct_children(change_el):
        if _local_name(container) in _ITEM_CONTAINER_TAGS:
            for li_el in _direct_children(container, "li"):
                item = _parse_item(li_el, next_item_index, used_item_labels)
                if item is not None and item.label:
                    _index_no_item_candidates(candidates, item)
                    next_item_index += 1

    for article in _direct_children(change_el, "article"):
        classes = _classes(article)
        if "legalArticle" in classes:
            section = _parse_section(article)
            if section is not None and section.label:
                candidates[("section", section.label)] = section
        elif "futureLegalArticle" in classes:
            section = _parse_future_section(article)
            if section is not None and section.label:
                candidates[("section", section.label)] = section

    direct_text_articles = [
        article
        for article in _direct_children(change_el, "article")
        if {"legalP", "numberedLegalP"} & _classes(article)
    ]
    direct_targets = [
        target for target in targets if target.leaf_kind() in {"subsection", "sentence"} and target.leaf_label()
    ]
    if direct_text_articles and direct_targets:
        leaf_kinds = {target.leaf_kind() for target in direct_targets}
        if len(direct_text_articles) >= len(direct_targets) and len(leaf_kinds) == 1:
            for article, target in zip(direct_text_articles, direct_targets):
                payload = _payload_from_direct_text_article(article, target)
                if payload is not None and target.leaf_label():
                    candidates[(target.leaf_kind(), target.leaf_label())] = payload
        elif len(direct_text_articles) == 1 and leaf_kinds == {"sentence"} and len(direct_targets) > 1:
            text = _node_text_without_structural_children(direct_text_articles[0])
            sentences = _split_no_sentences(text)
            if len(sentences) == len(direct_targets):
                for sentence_text, target in zip(sentences, direct_targets):
                    if not target.leaf_label():
                        continue
                        candidates[("sentence", target.leaf_label())] = IRNode(
                            kind=IRNodeKind.SENTENCE,
                            label=target.leaf_label(),
                            text=sentence_text,
                        )
    if direct_targets and {target.leaf_kind() for target in direct_targets} == {"sentence"} and len(direct_targets) > 1:
        raw_text = _normalize_space(" ".join(str(_t) for _t in change_el.itertext()))
        if ":" in raw_text:
            tail = _normalize_space(raw_text.split(":", 1)[1])
            sentences = _split_no_sentences(tail)
            if len(sentences) == len(direct_targets):
                for sentence_text, target in zip(sentences, direct_targets):
                    if not target.leaf_label():
                        continue
                    candidates[("sentence", target.leaf_label())] = IRNode(
                        kind=IRNodeKind.SENTENCE,
                        label=target.leaf_label(),
                        text=sentence_text,
                    )

    return candidates


def _extract_payload_candidates_from_nodes(
    nodes: Sequence[etree._Element],
    targets: Sequence[LegalAddress],
) -> dict[tuple[str, str], IRNode]:
    container = etree.Element("payload")
    for node in nodes:
        # XML elements are mutable and get re-parented when appended, so keep a
        # detached clone at the boundary before building payload candidates.
        cloned = copy.deepcopy(node)
        container.append(cloned)
        if _local_name(cloned) == "article":
            for child in _direct_children(cloned):
                if _local_name(child) in _ITEM_CONTAINER_TAGS or _local_name(child) == "li":
                    container.append(child)
    return _extract_payload_candidates(container, targets)


def _infer_same_base_subsection_targets_from_lead(lead: str) -> list[LegalAddress]:
    return [target for _action, target in _infer_same_base_subsection_target_specs_from_lead(lead)]


def _infer_same_base_subsection_target_specs_from_lead(
    lead: str,
) -> list[tuple[StructuralAction, LegalAddress]]:
    lead = _normalize_space(lead).rstrip(":")
    match = re.search(
        r"§\s*([0-9A-Za-z-]+)\s+(.+?)\s+ledd\s+(?:skal\s+)?lyde$",
        lead,
        re.IGNORECASE,
    )
    if not match:
        return []
    section_label = _normalize_no_section_label(match.group(1))
    ordinal_phrase = _normalize_space(match.group(2)).lower()
    tokens = re.split(r"\s*(?:,| og )\s*", ordinal_phrase)
    specs: list[tuple[StructuralAction, LegalAddress]] = []
    current_action: StructuralAction = StructuralAction.REPLACE
    for token in tokens:
        token = token.strip()
        if re.match(r"^(?:nytt|nye)\s+", token, re.IGNORECASE):
            current_action = StructuralAction.INSERT
        elif re.match(r"^nåværende\s+", token, re.IGNORECASE):
            current_action = StructuralAction.REPLACE
        token = re.sub(r"^(?:nytt|nye|nåværende)\s+", "", token, flags=re.IGNORECASE)
        label = _NORWEGIAN_ORDINALS.get(token.strip())
        if not label:
            return []
        specs.append((current_action, LegalAddress(path=(("section", section_label), ("subsection", label)))))
    return specs


def _infer_same_base_sentence_targets_from_lead(lead: str) -> list[LegalAddress]:
    return [target for _action, target in _infer_same_base_sentence_target_specs_from_lead(lead)]


def _infer_same_base_sentence_target_specs_from_lead(
    lead: str,
) -> list[tuple[StructuralAction, LegalAddress]]:
    lead = _normalize_space(lead).rstrip(":")
    match = re.match(
        r"^§\s*([0-9A-Za-z-]+)\s+(.+?)\s+ledd\s+(.+?)\s+punktum\s+(?:skal\s+lyde|oppheves)$",
        lead,
        re.IGNORECASE,
    )
    if match:
        section_label = _normalize_no_section_label(match.group(1))
        subsection_label = _NORWEGIAN_ORDINALS.get(_normalize_space(match.group(2)).lower())
        if not subsection_label:
            return []
        ordinal_phrase = _normalize_space(match.group(3)).lower()
        tokens = re.split(r"\s*(?:,| og )\s*", ordinal_phrase)
        specs: list[tuple[StructuralAction, LegalAddress]] = []
        current_action: StructuralAction = StructuralAction.REPLACE
        for token in tokens:
            token = token.strip()
            if re.match(r"^(?:nytt|nye)\s+", token, re.IGNORECASE):
                current_action = StructuralAction.INSERT
            elif re.match(r"^nåværende\s+", token, re.IGNORECASE):
                current_action = StructuralAction.REPLACE
            token = re.sub(r"^(?:nåværende|nytt|nye)\s+", "", token, flags=re.IGNORECASE)
            if token == "siste":
                label = "last"
            else:
                label = _NORWEGIAN_ORDINALS.get(token)
            if not label:
                return []
            specs.append(
                (
                    current_action,
                    LegalAddress(
                        path=(("section", section_label), ("subsection", subsection_label), ("sentence", label))
                    ),
                )
            )
        return specs
    match = re.match(
        r"^§\s*([0-9A-Za-z-]+)\s+(.+?)\s+punktum\s+(?:skal\s+lyde|oppheves)$",
        lead,
        re.IGNORECASE,
    )
    if not match:
        return []
    section_label = _normalize_no_section_label(match.group(1))
    ordinal_phrase = _normalize_space(match.group(2)).lower()
    tokens = re.split(r"\s*(?:,| og )\s*", ordinal_phrase)
    specs: list[tuple[StructuralAction, LegalAddress]] = []
    current_action: StructuralAction = StructuralAction.REPLACE
    for token in tokens:
        token = token.strip()
        if re.match(r"^(?:nytt|nye)\s+", token, re.IGNORECASE):
            current_action = StructuralAction.INSERT
        elif re.match(r"^nåværende\s+", token, re.IGNORECASE):
            current_action = StructuralAction.REPLACE
        token = re.sub(r"^(?:nåværende|nytt|nye)\s+", "", token, flags=re.IGNORECASE)
        if token == "siste":
            label = "last"
        else:
            label = _NORWEGIAN_ORDINALS.get(token)
        if not label:
            return []
        specs.append((current_action, LegalAddress(path=(("section", section_label), ("sentence", label)))))
    return specs


def _infer_same_base_item_targets_from_lead(lead: str) -> list[LegalAddress]:
    lead = _normalize_space(lead).rstrip(":")
    match = re.search(
        r"§\s*([0-9A-Za-z-]+)\s+(.+?)\s+ledd\s+bokstav\s+([A-Za-z])(?:\s+(?:nr\.|nummer)\s+([0-9A-Za-z-]+))?\s+(?:skal\s+)?lyde\b",
        lead,
        re.IGNORECASE,
    )
    if match:
        section_label = _normalize_no_section_label(match.group(1))
        subsection_label = _NORWEGIAN_ORDINALS.get(_normalize_space(match.group(2)).lower())
        if not subsection_label:
            return []
        item_label = _normalize_label(match.group(3)).lower()
        if not item_label:
            return []
        nested_label = _normalize_label(match.group(4) or "").lower()
        path = [
            ("section", section_label),
            ("subsection", subsection_label),
            ("item", item_label),
        ]
        if nested_label:
            path.append(("item", nested_label))
        return [LegalAddress(path=tuple(path))]
    match = re.search(
        r"§\s*([0-9A-Za-z-]+)\s+(.+?)\s+ledd\s+nytt\s+siste\s+strekpunkt\s+(?:skal\s+)?lyde\b",
        lead,
        re.IGNORECASE,
    )
    if not match:
        return []
    section_label = _normalize_no_section_label(match.group(1))
    subsection_label = _NORWEGIAN_ORDINALS.get(_normalize_space(match.group(2)).lower())
    if not subsection_label:
        return []
    return [
        LegalAddress(
            path=(
                ("section", section_label),
                ("subsection", subsection_label),
                ("item", "last"),
            )
        )
    ]


def _infer_same_base_subsection_targets(
    change_el: etree._Element,
) -> list[LegalAddress]:
    """Recover malformed same-section subsection targets from amendment lead text.

    Some Lovdata amendment blocks carry one correct same-base target and one bogus
    cross-act target even though the lead text is unambiguous, e.g.
    ``§ 11 andre og tredje ledd skal lyde:``. In that narrow case we recover the
    intended same-base subsection targets from the prose instead of silently
    dropping the extra payload.
    """
    lead_articles = [article for article in _direct_children(change_el, "article") if "defaultP" in _classes(article)]
    if not lead_articles:
        return []
    return _infer_same_base_subsection_targets_from_lead(
        _normalize_space(" ".join(str(_t) for _t in lead_articles[0].itertext()))
    )


def _infer_same_base_sentence_targets(
    change_el: etree._Element,
) -> list[LegalAddress]:
    lead_articles = [article for article in _direct_children(change_el, "article") if "defaultP" in _classes(article)]
    if not lead_articles:
        return []
    return _infer_same_base_sentence_targets_from_lead(
        _normalize_space(" ".join(str(_t) for _t in lead_articles[0].itertext()))
    )


def _heading_only_section_payload(
    change_el: etree._Element,
    action: StructuralAction | str,
    target: LegalAddress,
) -> Optional[IRNode]:
    if _no_action_value(action) != "replace" or target.leaf_kind() != "section":
        return None
    text_articles = [article for article in _direct_children(change_el, "article") if "defaultP" in _classes(article)]
    if not text_articles:
        return None
    lead = _normalize_space(" ".join(str(_t) for _t in text_articles[0].itertext()))
    if not _SECTION_HEADING_ONLY_RE.match(lead):
        return None
    title = ""
    for article in text_articles[1:]:
        candidate = _normalize_space(" ".join(str(_t) for _t in article.itertext()))
        if candidate:
            title = candidate
            break
    if not title:
        return None
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=target.leaf_label() or None,
        children=(IRNode(kind=IRNodeKind.HEADING, text=title),),
    )


def _heading_only_unstructured_section_payload(
    target_label: str,
    payload_nodes: Sequence[etree._Element],
) -> Optional[IRNode]:
    title = ""
    for node in payload_nodes:
        if _local_name(node) != "article" or not ({"defaultP", "legalP"} & _classes(node)):
            continue
        candidate = _normalize_space(" ".join(str(_t) for _t in node.itertext()))
        if candidate:
            title = candidate
            break
    if not title:
        return None
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=target_label,
        children=(IRNode(kind=IRNodeKind.HEADING, text=title),),
    )


def _expand_no_section_range_labels(start_label: str, end_label: str) -> list[str]:
    start = _normalize_no_section_label(start_label)
    end = _normalize_no_section_label(end_label)
    if start.isdigit() and end.isdigit():
        start_int = int(start)
        end_int = int(end)
        if start_int <= end_int:
            return [str(value) for value in range(start_int, end_int + 1)]
    return [start, end]


def _fallback_payload(
    change_el: etree._Element, action: StructuralAction | str, target: LegalAddress
) -> Optional[IRNode]:
    if _no_action_value(action) == "repeal":
        return None
    text_blocks = [
        _node_text_without_structural_children(article)
        for article in _direct_children(change_el, "article")
        if _classes(article) & _TEXT_BLOCK_CLASSES
    ]
    text = _normalize_space(" ".join(block for block in text_blocks if block))
    if not text:
        text = _node_text_without_structural_children(change_el)
    if not text:
        return None
    text = re.sub(
        r"^(?:nye?\s+)?§{1,2}\s*[^:]+?\bskal lyde:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = _normalize_space(text)
    return IRNode(kind=cast(IRNodeKind, target.leaf_kind() or "content"), label=target.leaf_label() or None, text=text)


def parse_no_heading_groups(html_bytes: bytes, base_id: str) -> list[NOHeadingGroup]:
    """Parse Norway section-range heading groups such as 'Ny deloverskrift til §§ 2-1 til 2-5'."""
    root = _parse_document(html_bytes)
    raw_base = base_id.removeprefix("no/")
    groups: list[NOHeadingGroup] = []
    sequence = 1

    for doc_change in cast(
        list[etree._Element],
        root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' document-change ')]"),
    ):
        source_doc = (doc_change.get("data-document") or "").strip()
        if source_doc != raw_base:
            continue
        children = _direct_children(doc_change)
        for idx, child in enumerate(children):
            if "defaultP" not in _classes(child):
                continue
            text = _normalize_space(" ".join(str(_t) for _t in child.itertext()))
            match = _FUTURE_HEADING_RANGE_RE.search(text)
            if not match:
                continue
            title_el = children[idx + 1] if idx + 1 < len(children) else None
            if title_el is None or "futuretitle" not in _classes(title_el):
                continue
            title = _normalize_space(" ".join(str(_t) for _t in title_el.itertext()))
            if not title:
                continue
            start_label = _normalize_label(match.group(1))
            end_label = _normalize_label(match.group(2) or match.group(1))
            groups.append(
                NOHeadingGroup(
                    start_label=start_label,
                    end_label=end_label,
                    title=title,
                    sequence=sequence,
                )
            )
            sequence += 1

    return groups


def parse_no_amendment_ops(html_bytes: bytes, source_id: str) -> List[LegalOperation]:
    """Parse Lovdata amendment blocks into LegalOperation objects."""
    ops: list[LegalOperation] = []
    for _base_id, doc_ops in iter_no_document_change_ops(html_bytes, source_id):
        ops.extend(doc_ops)
    return ops


def _iter_unstructured_no_change_groups(
    root: etree._Element,
    source_id: str,
) -> list[tuple[str, list[LegalOperation]]]:
    """Parse older Lovtidend amendment acts without ``document-change`` wrappers."""
    changed_docs: list[str] = []
    for dd in cast(
        list[etree._Element],
        root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' changesToDocuments ')]"),
    ):
        changed_docs.extend(
            ref
            for ref in (
                normalize_lovdata_refid(_normalize_space("".join(str(_t) for _t in li.itertext())))
                for li in cast(list[etree._Element], dd.xpath(".//li"))
            )
            if ref is not None
        )
    changed_docs = list(dict.fromkeys(changed_docs))
    default_base_id = changed_docs[0] if len(changed_docs) == 1 else None

    sequence = 1
    doc_ops_by_base: dict[str, list[LegalOperation]] = {}
    all_sections = cast(list[etree._Element], root.xpath("//main/section"))
    kapi_sections = [section for section in all_sections if (section.get("data-name") or "").lower() == "kapi"]
    seen_sections = {id(section) for section in kapi_sections}
    sections = kapi_sections + [section for section in all_sections if id(section) not in seen_sections]
    if sections:
        children = []
        section_base_ids: list[str | None] = []
        for section in sections:
            section_children: list[etree._Element] = []
            section_child_base_ids: list[str | None] = []
            direct_children = _direct_children(section)
            section_base_id = _infer_no_unstructured_section_base_id(direct_children)
            for direct_child in direct_children:
                if _local_name(direct_child) == "article" and "legalArticle" in _classes(direct_child):
                    article_children = _direct_children(direct_child)
                    section_children.extend(article_children)
                    section_child_base_ids.extend([None] * len(article_children))
                    continue
                section_children.append(direct_child)
                section_child_base_ids.append(section_base_id)
            children.extend(section_children)
            section_base_ids.extend(section_child_base_ids)
    else:
        mains = cast(list[etree._Element], root.xpath("//main"))
        if not mains:
            return []
        children = []
        section_base_ids = []
        for container in _direct_children(mains[0]):
            if _local_name(container) != "article":
                continue
            if "legalArticle" in _classes(container):
                direct_children = _direct_children(container)
                children.extend(direct_children)
                section_base_ids.extend([None] * len(direct_children))
            else:
                children.append(container)
                section_base_ids.append(None)

    idx = 0
    active_base_id: str | None = None
    while idx < len(children):
        child = children[idx]
        section_base_id = section_base_ids[idx] if idx < len(section_base_ids) else None
        child_classes = _classes(child)
        if _local_name(child) != "article" or not ({"defaultP", "legalP"} & child_classes):
            idx += 1
            continue
        lead = _repair_no_mojibake(_normalize_space(" ".join(str(_t) for _t in child.itertext())))
        explicit_section_base_id = _extract_no_section_base_id_from_lead(lead)
        if explicit_section_base_id is not None:
            active_base_id = explicit_section_base_id
        lead_base_id = default_base_id or explicit_section_base_id or active_base_id or section_base_id
        embedded = _extract_no_embedded_multi_act_lead(lead)
        if embedded is not None:
            lead_base_id, lead = embedded
            active_base_id = lead_base_id
        payload_nodes: list[etree._Element] = []
        cursor = idx + 1
        while cursor < len(children):
            nxt = children[cursor]
            if _local_name(nxt) == "article" and "defaultP" in _classes(nxt):
                break
            payload_nodes.append(nxt)
            cursor += 1

        future_articles = [
            node for node in payload_nodes if _local_name(node) == "article" and "futureLegalArticle" in _classes(node)
        ]
        text_articles = [
            node
            for node in payload_nodes
            if _local_name(node) == "article" and {"legalP", "numberedLegalP"} & _classes(node)
        ]

        text_replace_pairs = _extract_no_global_text_replace_pairs(lead)
        if text_replace_pairs:
            cited_base_ids = _extract_no_law_citation_base_ids(lead)
            for node in payload_nodes:
                cited_text = _normalize_space(" ".join(str(_t) for _t in node.itertext()))
                cited_base_ids.extend(_extract_no_law_citation_base_ids(cited_text))
            cited_base_ids = list(dict.fromkeys(cited_base_ids))
            if cited_base_ids:
                for cited_base_id in cited_base_ids:
                    cited_doc_ops = doc_ops_by_base.setdefault(cited_base_id, [])
                    for old_text, new_text in text_replace_pairs:
                        cited_doc_ops.append(
                            LegalOperation(
                                op_id=f"{source_id}:{sequence}",
                                sequence=sequence,
                                action=StructuralAction.TEXT_REPLACE,
                                target=LegalAddress(path=()),
                                text_patch=TextPatchSpec(
                                    kind=TextPatchKindEnum.REPLACE,
                                    selector=TextSelector(
                                        match_text=old_text,
                                        occurrence=0,
                                    ),
                                    replacement=new_text,
                                ),
                                source=OperationSource(
                                    statute_id=source_id,
                                    raw_text=lead,
                                    title=cited_base_id,
                                ),
                                provenance_tags=(f"base_act:{cited_base_id}", "fallback:unstructured", "scope:global"),
                                group_id=f"{source_id}:{cited_base_id}:{sequence}",
                            )
                        )
                        sequence += 1
                idx = cursor
                continue

        if lead_base_id is None:
            idx += 1
            continue
        doc_ops = doc_ops_by_base.setdefault(lead_base_id, [])

        heading_only_match = re.match(
            r"^§\s*([0-9A-Za-z-]+)\s+overskriften\s+skal\s+lyde:?$",
            lead,
            re.IGNORECASE,
        )
        if heading_only_match:
            target_label = _normalize_no_section_label(heading_only_match.group(1))
            payload = _heading_only_unstructured_section_payload(target_label, payload_nodes)
            if payload is not None:
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.REPLACE,
                        target=LegalAddress(path=(("section", target_label),)),
                        payload=payload,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
                idx = cursor
                continue

        repeal_renumber_match = re.match(
            r"^§\s*([0-9A-Za-z-]+)\s+(.+?)\s+ledd\s+oppheves\.\s*Nåværende\s+(.+?)\s+ledd\s+blir\s+(.+?)\s+ledd\.?$",
            lead,
            re.IGNORECASE,
        )
        if repeal_renumber_match:
            section_label = _normalize_no_section_label(repeal_renumber_match.group(1))
            repeal_targets = _infer_same_base_subsection_targets_from_lead(
                f"§ {section_label} {repeal_renumber_match.group(2)} ledd skal lyde"
            )
            source_targets = _infer_same_base_subsection_targets_from_lead(
                f"§ {section_label} {repeal_renumber_match.group(3)} ledd skal lyde"
            )
            dest_targets = _infer_same_base_subsection_targets_from_lead(
                f"§ {section_label} {repeal_renumber_match.group(4)} ledd skal lyde"
            )
            for target in repeal_targets:
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.REPEAL,
                        target=target,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            for src_target, dst_target in zip(source_targets, dest_targets):
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.RENUMBER,
                        target=src_target,
                        destination=dst_target,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        section_match = re.match(
            r"^(?:Ny\s+)?§\s*([0-9A-Za-z-]+(?:\s+[A-Za-z])?)\s+skal\s+lyde:?$",
            lead,
            re.IGNORECASE,
        )
        if section_match and future_articles:
            target = LegalAddress(path=(("section", _normalize_no_section_label(section_match.group(1))),))
            payload = _parse_future_section(future_articles[0])
            if payload is not None:
                action = StructuralAction.INSERT if lead.lower().startswith("ny §") else StructuralAction.REPLACE
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=action,
                        target=target,
                        payload=payload,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
                idx = cursor
                continue

        sentence_specs = _infer_same_base_sentence_target_specs_from_lead(lead)
        if sentence_specs:
            sentence_targets = [target for _action, target in sentence_specs]
            sentence_payloads: list[IRNode] = []
            if len(text_articles) >= len(sentence_targets):
                for target, article in zip(sentence_targets, text_articles):
                    payload = _payload_from_direct_text_article(article, target)
                    if payload is None:
                        sentence_payloads = []
                        break
                    sentence_payloads.append(payload)
            elif len(text_articles) == 1:
                text = _node_text_without_structural_children(text_articles[0])
                sentences = _split_no_sentences(text)
                if len(sentences) == len(sentence_targets):
                    sentence_payloads = [
                        IRNode(kind=IRNodeKind.SENTENCE, label=target.leaf_label() or None, text=sentence_text)
                        for target, sentence_text in zip(sentence_targets, sentences)
                    ]
            if sentence_payloads and len(sentence_payloads) == len(sentence_targets):
                for (action, target), payload in zip(sentence_specs, sentence_payloads):
                    doc_ops.append(
                        LegalOperation(
                            op_id=f"{source_id}:{sequence}",
                            sequence=sequence,
                            action=action,
                            target=target,
                            payload=payload,
                            source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                            provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                            group_id=f"{source_id}:{lead_base_id}:{sequence}",
                        )
                    )
                    sequence += 1
                idx = cursor
                continue

        subsection_specs = _infer_same_base_subsection_target_specs_from_lead(lead)
        if subsection_specs and len(text_articles) >= len(subsection_specs):
            for (action, target), article in zip(subsection_specs, text_articles):
                payload = _payload_from_direct_text_article(article, target)
                if payload is None:
                    continue
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=action,
                        target=target,
                        payload=payload,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        item_targets = _infer_same_base_item_targets_from_lead(lead)
        if item_targets:
            payload_candidates = _extract_payload_candidates_from_nodes([child, *payload_nodes], item_targets)
            for target in item_targets:
                payload = payload_candidates.get((target.leaf_kind(), target.leaf_label()))
                if payload is None and target.leaf_kind() == "item" and target.leaf_label() == "last":
                    item_payloads = [
                        candidate for (kind, _label), candidate in payload_candidates.items() if kind == "item"
                    ]
                    if len(item_payloads) == 1:
                        payload = _with_no_node_label(item_payloads[0], "last")
                if payload is None:
                    continue
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.REPLACE,
                        target=target,
                        payload=payload,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        repeal_match = re.match(r"^§\s*([0-9A-Za-z-]+)\s+(.+?)\s+ledd\s+oppheves\.?$", lead, re.IGNORECASE)
        if repeal_match:
            for target in _infer_same_base_subsection_targets_from_lead(
                f"§ {repeal_match.group(1)} {repeal_match.group(2)} ledd skal lyde"
            ):
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.REPEAL,
                        target=target,
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        plural_section_repeal_match = re.match(
            r"^§§\s*([0-9A-Za-z-]+)\s+og\s+([0-9A-Za-z-]+)\s+oppheves\.?$",
            lead,
            re.IGNORECASE,
        )
        if plural_section_repeal_match:
            for label in (
                _normalize_no_section_label(plural_section_repeal_match.group(1)),
                _normalize_no_section_label(plural_section_repeal_match.group(2)),
            ):
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.REPEAL,
                        target=LegalAddress(path=(("section", label),)),
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        section_repeal_match = re.match(r"^§\s*([0-9A-Za-z-]+)\s+oppheves\.?$", lead, re.IGNORECASE)
        if section_repeal_match:
            label = _normalize_no_section_label(section_repeal_match.group(1))
            doc_ops.append(
                LegalOperation(
                    op_id=f"{source_id}:{sequence}",
                    sequence=sequence,
                    action=StructuralAction.REPEAL,
                    target=LegalAddress(path=(("section", label),)),
                    source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                    provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                    group_id=f"{source_id}:{lead_base_id}:{sequence}",
                )
            )
            sequence += 1
            idx = cursor
            continue

        range_section_repeal_match = re.match(
            r"^§§\s*([0-9A-Za-z-]+)\s+til\s+([0-9A-Za-z-]+)\s+oppheves\.?$",
            lead,
            re.IGNORECASE,
        )
        if range_section_repeal_match:
            for label in _expand_no_section_range_labels(
                range_section_repeal_match.group(1),
                range_section_repeal_match.group(2),
            ):
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.REPEAL,
                        target=LegalAddress(path=(("section", label),)),
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        renumber_match = re.match(
            r"^Nåværende §\s*([0-9A-Za-z-]+)\s+blir ny §\s*([0-9A-Za-z-]+)\.?$",
            lead,
            re.IGNORECASE,
        )
        if renumber_match:
            src_label = _normalize_label(renumber_match.group(1))
            dst_label = _normalize_label(renumber_match.group(2))
            doc_ops.append(
                LegalOperation(
                    op_id=f"{source_id}:{sequence}",
                    sequence=sequence,
                    action=StructuralAction.RENUMBER,
                    target=LegalAddress(path=(("section", src_label),)),
                    destination=LegalAddress(path=(("section", dst_label),)),
                    source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                    provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                    group_id=f"{source_id}:{lead_base_id}:{sequence}",
                )
            )
            sequence += 1
            idx = cursor
            continue

        plural_renumber_match = re.match(
            r"^Nåværende §§\s*([0-9A-Za-z-]+)\s+og\s+([0-9A-Za-z-]+)\s+blir §§\s*([0-9A-Za-z-]+)\s+og\s+([0-9A-Za-z-]+)\.?$",
            lead,
            re.IGNORECASE,
        )
        if plural_renumber_match:
            pairs = [
                (_normalize_label(plural_renumber_match.group(1)), _normalize_label(plural_renumber_match.group(3))),
                (_normalize_label(plural_renumber_match.group(2)), _normalize_label(plural_renumber_match.group(4))),
            ]
            for src_label, dst_label in pairs:
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.RENUMBER,
                        target=LegalAddress(path=(("section", src_label),)),
                        destination=LegalAddress(path=(("section", dst_label),)),
                        source=OperationSource(statute_id=source_id, raw_text=lead, title=lead_base_id),
                        provenance_tags=(f"base_act:{lead_base_id}", "fallback:unstructured"),
                        group_id=f"{source_id}:{lead_base_id}:{sequence}",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        idx += 1

    return [
        (base_id, _promote_no_replace_with_following_renumber_insert(doc_ops))
        for base_id, doc_ops in doc_ops_by_base.items()
        if doc_ops
    ]


def _infer_no_unstructured_section_base_id(children: list[etree._Element]) -> str | None:
    for child in children:
        if _local_name(child) != "article" or not ({"defaultP", "legalP"} & _classes(child)):
            continue
        lead = _repair_no_mojibake(_normalize_space(" ".join(str(_t) for _t in child.itertext())))
        embedded = _extract_no_embedded_multi_act_lead(lead)
        if embedded is not None:
            return embedded[0]
        section_base_id = _extract_no_section_base_id_from_lead(lead)
        if section_base_id is not None:
            return section_base_id
    return None


def _promote_no_replace_with_following_renumber_insert(
    ops: list[LegalOperation],
) -> list[LegalOperation]:
    """Treat replace+same-target-renumber as insertion of new content.

    If an amendment says content at address X "skal lyde" and separately says the
    current content at X becomes the new X+1, the semantic effect is insertion of
    new content at X plus renumbering of the old X.
    """
    renumber_targets = {
        op.target.path for op in ops if op.action is StructuralAction.RENUMBER and op.destination is not None
    }
    promoted: list[LegalOperation] = []
    for op in ops:
        if (
            op.action is StructuralAction.REPLACE
            and op.payload is not None
            and op.target.path in renumber_targets
            and _no_kind_value(op.payload.kind) == op.target.leaf_kind()
        ):
            promoted.append(
                dc_replace(
                    op,
                    action=StructuralAction.INSERT,
                    provenance_tags=(
                        *op.provenance_tags,
                        NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER,
                    ),
                )
            )
            continue
        promoted.append(op)
    return promoted


def _extract_no_embedded_multi_act_lead(lead: str) -> tuple[str, str] | None:
    lead = _repair_no_mojibake(lead)
    patterns = (
        r"^\d+\.\s+I lov\s+(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)\s+.+?\s+skal\s+(§.+)$",
        r"^I\s+(?:lov\s+)?(?:.+?\s+av\s+)?(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)\s+.+?\s+skal\s+(§.+)$",
    )
    match = None
    for pattern in patterns:
        match = re.match(pattern, lead, re.IGNORECASE)
        if match is not None:
            break
    if match is None:
        return None
    day = int(match.group(1))
    month = _NORWEGIAN_MONTH_NUMBERS.get(match.group(2).lower())
    year = match.group(3)
    number = int(match.group(4))
    embedded_lead = match.group(5).strip()
    if " skal " not in embedded_lead.lower():
        embedded_lead = re.sub(r"\s+lyd([ea]):?$", r" skal lyd\1:", embedded_lead, flags=re.IGNORECASE)
    if month is None:
        return None
    return (f"no/lov/{year}-{month}-{day:02d}-{number}", embedded_lead)


def _extract_no_section_base_id_from_lead(lead: str) -> str | None:
    lead = _repair_no_mojibake(lead)
    lowered = lead.lower()
    lowered = re.sub(r"^\d+\.\s*", "", lowered)
    if not lowered.startswith("i "):
        return None
    section_intro_markers = (
        "gjøres følgende endring",
        "gjøres følgende endringer",
        "gjøres disse endringene",
        "gjer følgjande endring",
        "gjer følgjande endringar",
        "gjerast følgjande endring",
        "gjerast følgjande endringar",
        "blir gjort følgende endring",
        "blir gjort følgende endringer",
        "blir gjort følgjande endring",
        "blir gjort følgjande endringar",
    )
    if not any(marker in lowered for marker in section_intro_markers):
        return None
    return _extract_no_law_citation_base_id(lead)


def _extract_no_law_citation_base_id(text: str) -> str | None:
    text = _repair_no_mojibake(text)
    match = re.search(
        r"(?:^|\b)(?:Midlertidig\s+)?lov\s+(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)",
        text,
        re.IGNORECASE,
    )
    if match is None:
        fallback = re.search(
            r"av\s+(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)",
            text,
            re.IGNORECASE,
        )
        if fallback is None:
            return None
        prefix = text[max(0, fallback.start() - 80) : fallback.start()].lower()
        if "lov" not in prefix:
            return None
        match = fallback
    day = int(match.group(1))
    month = _NORWEGIAN_MONTH_NUMBERS.get(match.group(2).lower())
    year = match.group(3)
    number = int(match.group(4))
    if month is None:
        return None
    return f"no/lov/{year}-{month}-{day:02d}-{number}"


def _extract_no_law_citation_base_ids(text: str) -> list[str]:
    base_ids: list[str] = []
    for match in re.finditer(
        r"(?:^|\b)(?:Midlertidig\s+)?lov\s+(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)",
        text,
        re.IGNORECASE,
    ):
        day = int(match.group(1))
        month = _NORWEGIAN_MONTH_NUMBERS.get(match.group(2).lower())
        year = match.group(3)
        number = int(match.group(4))
        if month is None:
            continue
        base_ids.append(f"no/lov/{year}-{month}-{day:02d}-{number}")
    return list(dict.fromkeys(base_ids))


def _extract_no_global_text_replace_pairs(lead: str) -> list[tuple[str, str]]:
    return [
        (_normalize_space(old), _normalize_space(new))
        for old, new in _QUOTED_NO_TEXT_REPLACE_RE.findall(lead)
        if _normalize_space(old) and _normalize_space(new)
    ]


def iter_no_document_change_ops(
    html_bytes: bytes,
    source_id: str,
) -> list[tuple[str, list[LegalOperation]]]:
    """Group compiled amendment ops by base act for one Lovtidend document.

    Architectural note:
    this is still a direct lowering seam from source-local change markup into
    `LegalOperation`. The long-term Norway shape should insert explicit
    change-surface and payload-surface waists above this function so replay no
    longer depends on frontend-local recovery decisions.
    """
    root = _parse_document(html_bytes)
    grouped: list[tuple[str, list[LegalOperation]]] = []
    sequence = 1

    change_nodes = cast(
        list[etree._Element],
        root.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' document-change ')]"),
    )
    if not change_nodes:
        return _iter_unstructured_no_change_groups(root, source_id)
    for doc_change in change_nodes:
        source_doc = doc_change.get("data-document", "").strip()
        base_id = normalize_lovdata_refid(source_doc)
        if not source_doc or base_id is None:
            continue
        doc_ops: list[LegalOperation] = []
        for change_el in _iter_change_descendants(doc_change):
            raw_text = _normalize_space(" ".join(str(_t) for _t in change_el.itertext()))
            lead_articles = [
                article for article in _direct_children(change_el, "article") if "defaultP" in _classes(article)
            ]
            lead_text = (
                _normalize_space(" ".join(str(_t) for _t in lead_articles[0].itertext())) if lead_articles else raw_text
            )
            specs: list[tuple[str, str]] = []
            renumber_specs = _split_move_attr(change_el.get("data-move-part", ""))
            specs.extend(_split_change_attr(change_el.get("data-change-part", ""), "replace"))
            specs.extend(_split_change_attr(change_el.get("data-add-new-part", ""), "insert"))
            specs.extend(_split_change_attr(change_el.get("data-remove-part", ""), "repeal"))
            specs.extend(_split_change_attr(change_el.get("data-repeal-part", ""), "repeal"))

            parsed_specs: list[tuple[StructuralAction, LegalAddress]] = []
            skipped_cross_base_specs: list[tuple[str, str]] = []
            for action, raw_target in specs:
                if action == "renumber":
                    if ";;" not in raw_target:
                        continue
                    src, dst = raw_target.split(";;", 1)
                    renumber_specs.append((src, dst))
                    continue
                target_base = normalize_lovdata_refid(raw_target)
                if target_base is not None and target_base != base_id:
                    skipped_cross_base_specs.append((action, raw_target))
                    continue
                target = lovdata_path_to_address(raw_target)
                if target is not None:
                    parsed_specs.append((StructuralAction(action), target))

            if skipped_cross_base_specs and parsed_specs:
                non_skipped_actions = {_no_action_value(action) for action, _target in parsed_specs}
                skipped_actions = {action for action, _raw_target in skipped_cross_base_specs}
                inferred_targets = _infer_same_base_subsection_targets(change_el)
                if (
                    len(non_skipped_actions | skipped_actions) == 1
                    and len(inferred_targets) == len(parsed_specs) + len(skipped_cross_base_specs)
                    and all(target.leaf_kind() == "subsection" for target in inferred_targets)
                ):
                    inferred_map = {target.path: target for target in inferred_targets}
                    existing_paths = {target.path for _action, target in parsed_specs}
                    if existing_paths.issubset(inferred_map):
                        action = next(iter(non_skipped_actions | skipped_actions))
                        parsed_specs = [(StructuralAction(action), target) for target in inferred_targets]

            inferred_sentence_specs = _infer_same_base_sentence_target_specs_from_lead(lead_text)
            if inferred_sentence_specs:
                parsed_specs = list(inferred_sentence_specs)

            inferred_targets = _infer_same_base_subsection_targets(change_el)
            if (
                inferred_targets
                and len(parsed_specs) == 1
                and parsed_specs[0][1].leaf_kind() == "section"
                and parsed_specs[0][0] in {StructuralAction.INSERT, StructuralAction.REPLACE}
            ):
                parsed_specs = [(parsed_specs[0][0], target) for target in inferred_targets]

            if (
                not inferred_sentence_specs
                and " nytt " in f" {lead_text.lower()} "
                and all(target.leaf_kind() == "subsection" for _action, target in parsed_specs)
            ):
                parsed_specs = [(StructuralAction.INSERT, target) for _action, target in parsed_specs]

            inferred_sentence_targets = _infer_same_base_sentence_targets(change_el)
            if (
                inferred_sentence_targets
                and parsed_specs
                and all(target.leaf_kind() in {"section", "subsection"} for _action, target in parsed_specs)
                and len({target.path[0] for _action, target in parsed_specs}) == 1
            ):
                parsed_specs = [(StructuralAction.REPLACE, target) for target in inferred_sentence_targets]

            payload_candidates = _extract_payload_candidates(
                change_el,
                [target for _action, target in parsed_specs],
            )

            for raw_target, raw_destination in renumber_specs:
                target_base = normalize_lovdata_refid(raw_target)
                dest_base = normalize_lovdata_refid(raw_destination)
                if (target_base is not None and target_base != base_id) or (
                    dest_base is not None and dest_base != base_id
                ):
                    continue
                target = lovdata_path_to_address(raw_target)
                destination = lovdata_path_to_address(raw_destination)
                if target is None or destination is None:
                    continue
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=StructuralAction.RENUMBER,
                        target=target,
                        destination=destination,
                        source=OperationSource(
                            statute_id=source_id,
                            raw_text=raw_text,
                            title=source_doc,
                        ),
                        provenance_tags=(f"base_act:{base_id}",),
                        group_id=f"{source_id}:{source_doc}:{sequence}",
                    )
                )
                sequence += 1

            for action, target in parsed_specs:
                payload = payload_candidates.get((target.leaf_kind(), target.leaf_label()))
                if payload is None:
                    payload = _heading_only_section_payload(change_el, action, target)
                if payload is None:
                    payload = _fallback_payload(change_el, action, target)
                doc_ops.append(
                    LegalOperation(
                        op_id=f"{source_id}:{sequence}",
                        sequence=sequence,
                        action=action,
                        target=target,
                        payload=payload if payload is not None else None,
                        source=OperationSource(
                            statute_id=source_id,
                            raw_text=raw_text,
                            title=source_doc,
                        ),
                        provenance_tags=(f"base_act:{base_id}",),
                        group_id=f"{source_id}:{source_doc}:{sequence}",
                    )
                )
                sequence += 1
        if doc_ops:
            grouped.append((base_id, _promote_no_replace_with_following_renumber_insert(doc_ops)))

    return grouped


def _no_sort_key(label: Optional[str]) -> tuple[int, str, int]:
    if not label:
        return (-1, "", 0)
    normalized = _normalize_label(label).lower()
    hyphen_match = re.match(r"^(\d+)-(\d+)([a-z]*)$", normalized)
    if hyphen_match:
        major, minor, suffix = hyphen_match.groups()
        return (int(major) * 10000 + int(minor), suffix, 0)
    letter_match = re.match(r"^(\d+)([a-z]*)$", normalized)
    if letter_match:
        number, suffix = letter_match.groups()
        return (int(number), suffix, 0)
    roman = _roman_to_int(normalized)
    if roman is not None:
        return (roman, "", 0)
    return tree_ops._default_sort_key(normalized)


def _resolve_no_path(body: IRNode, target: LegalAddress) -> Optional[tree_ops.Path]:
    """Resolve a possibly shallow Lovdata target against the current tree."""
    full_path: Optional[tree_ops.Path] = None
    for idx, (kind, label) in enumerate(target.path):
        if idx == 0:
            full_path = tree_ops.find(body, kind, label)
        elif full_path is not None:
            parent_node = tree_ops.resolve(body, full_path)
            if parent_node is None:
                return None
            if kind == "sentence" and label == "last":
                parent_path = full_path
                body = _materialize_no_sentence_children(body, parent_path)
                full_path = _find_last_direct_child_path(body, parent_path, "sentence")
                if full_path is None:
                    return None
                continue
            inner_path = tree_ops.find(parent_node, kind, label)
            if inner_path is None:
                return None
            full_path = full_path + inner_path
        if full_path is None:
            return None
    return full_path


def _find_insert_parent(scope_node: IRNode, content_kind: str) -> Optional[tree_ops.Path]:
    """Find a unique descendant container whose direct children match content kind."""
    matches: list[tree_ops.Path] = []

    def _walk(node: IRNode, prefix: tree_ops.Path) -> None:
        if any(_no_kind_value(child.kind) == content_kind for child in node.children):
            matches.append(prefix)
        for child in node.children:
            step = (str(child.kind), child.label or "")
            _walk(child, prefix + (step,))

    _walk(scope_node, ())
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_no_last_child_path(
    body: IRNode,
    parent_path: tree_ops.Path,
    child_kind: str,
) -> Optional[tree_ops.Path]:
    parent_node = tree_ops.resolve(body, parent_path) if parent_path else body
    if parent_node is None:
        return None
    matches = [
        parent_path + ((str(child.kind), child.label or ""),)
        for child in parent_node.children
        if child.kind == child_kind and child.label
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: _no_sort_key(path[-1][1]))


def _next_no_child_label(parent_node: IRNode, child_kind: str) -> str:
    labels = [child.label or "" for child in parent_node.children if child.kind == child_kind and child.label]
    numeric = [int(label) for label in labels if label.isdigit()]
    if labels and len(numeric) == len(labels):
        return str(max(numeric) + 1)
    alpha = [label for label in labels if len(label) == 1 and label.isalpha()]
    if labels and len(alpha) == len(labels):
        return chr(max(ord(label.lower()) for label in alpha) + 1)
    if numeric:
        return str(max(numeric) + 1)
    return "1"


def _section_family_prefix(label: str) -> Optional[str]:
    normalized = _normalize_label(label)
    hyphen_match = re.match(r"^(\d+)-", normalized)
    if hyphen_match:
        return f"{hyphen_match.group(1)}-"
    number_match = re.match(r"^(\d+)", normalized)
    if number_match:
        return number_match.group(1)
    return None


def _iter_paths(node: IRNode, prefix: Optional[tree_ops.Path] = None) -> Generator[tree_ops.Path, None, None]:
    prefix = prefix or ()
    for child in node.children:
        path = prefix + ((str(child.kind), child.label or ""),)
        yield path
        yield from _iter_paths(child, path)


def _infer_section_parent_path(body: IRNode, section_label: str) -> Optional[tree_ops.Path]:
    """Infer chapter/container for a new Norway section from nearby existing section labels."""
    family = _section_family_prefix(section_label)
    if not family:
        return None
    parent_paths: set[tuple[tuple[str, str], ...]] = set()
    for path in _iter_paths(body):
        if not path:
            continue
        kind, label = path[-1]
        if kind != "section":
            continue
        normalized = _normalize_label(label)
        if family.endswith("-"):
            matches = normalized.startswith(family)
        else:
            matches = normalized == family or normalized.startswith(f"{family}-") or normalized.startswith(f"{family}a")
        if matches and path[:-1]:
            parent_paths.add(tuple(path[:-1]))
    if len(parent_paths) == 1:
        return next(iter(parent_paths))
    return None


def _resolve_existing_prefix(
    body: IRNode,
    target: LegalAddress,
) -> tuple[Optional[tree_ops.Path], int]:
    """Resolve the longest existing prefix of a Norway target address."""
    if not target.path:
        return None, 0
    full_path: Optional[tree_ops.Path] = None
    matched = 0
    for idx, (kind, label) in enumerate(target.path):
        if idx == 0:
            candidate = tree_ops.find(body, kind, label)
        elif full_path is not None:
            scope_kind, scope_label = full_path[-1]
            candidate = tree_ops.find(
                body,
                kind,
                label,
                scope_kind=scope_kind,
                scope_label=scope_label,
            )
        else:
            candidate = None
        if candidate is None:
            break
        full_path = candidate
        matched = idx + 1
    return full_path, matched


def _ensure_no_container_chain(
    body: IRNode,
    base_path: tree_ops.Path,
    missing_steps: Sequence[tuple[str, str]],
) -> tuple[IRNode, tree_ops.Path]:
    """Create missing address containers before inserting leaf content."""
    current_path = list(base_path)
    for kind, label in missing_steps:
        body = tree_ops.insert_sorted(
            body,
            current_path,
            IRNode(kind=IRNodeKind(kind), label=label),
            sort_key_fn=_no_sort_key,
        )
        current_path = current_path + [(kind, label)]
    return body, tuple(current_path)


def _materialize_no_sentence_children(body: IRNode, parent_path: tree_ops.Path) -> IRNode:
    """Split raw subsection/item text into sentence children on demand."""
    parent = tree_ops.resolve(body, parent_path)
    if parent is None:
        return body
    if _no_kind_value(parent.kind) not in {"subsection", "item"}:
        return body
    if not parent.text or any(_no_kind_value(child.kind) == "sentence" for child in parent.children):
        return body
    sentences = _split_no_sentences(parent.text)
    if not sentences:
        return body
    replacement = IRNode(
        kind=parent.kind,
        label=parent.label,
        text="",
        attrs=dict(parent.attrs),
        children=tuple(
            [
                IRNode(kind=IRNodeKind.SENTENCE, label=str(index), text=sentence_text)
                for index, sentence_text in enumerate(sentences, start=1)
            ]
            + [child for child in parent.children]
        ),
    )
    return tree_ops.replace_at(body, parent_path, replacement)


def _resolve_shallow_no_sentence_path(
    body: IRNode,
    target: LegalAddress,
) -> tuple[IRNode, Optional[tree_ops.Path]]:
    """Resolve section-level sentence targets via a unique direct text container."""
    if len(target.path) != 2 or target.path[0][0] != "section" or target.path[1][0] != "sentence":
        return body, None
    section_path = _resolve_no_path(body, LegalAddress(path=(target.path[0],)))
    if section_path is None:
        return body, None
    section_node = tree_ops.resolve(body, section_path)
    if section_node is None:
        return body, None
    hosts = [child for child in section_node.children if _no_kind_value(child.kind) in {"subsection", "item"}]
    if len(hosts) != 1:
        return body, None
    host = hosts[0]
    host_path = section_path + ((str(host.kind), host.label or ""),)
    body = _materialize_no_sentence_children(body, host_path)
    if target.path[1][1] == "last":
        resolved = _find_last_direct_child_path(body, host_path, "sentence")
    else:
        resolved = _find_direct_child_path(body, host_path, "sentence", target.path[1][1])
    return body, resolved


def _resolve_shallow_no_sentence_host_path(
    body: IRNode,
    target: LegalAddress,
) -> tuple[IRNode, Optional[tree_ops.Path]]:
    """Resolve the unique host path for section-level sentence targets."""
    if len(target.path) != 2 or target.path[0][0] != "section" or target.path[1][0] != "sentence":
        return body, None
    section_path = _resolve_no_path(body, LegalAddress(path=(target.path[0],)))
    if section_path is None:
        return body, None
    section_node = tree_ops.resolve(body, section_path)
    if section_node is None:
        return body, None
    hosts = [child for child in section_node.children if _no_kind_value(child.kind) in {"subsection", "item"}]
    if len(hosts) != 1:
        return body, None
    host = hosts[0]
    host_path = section_path + ((str(host.kind), host.label or ""),)
    body = _materialize_no_sentence_children(body, host_path)
    return body, host_path


def _roman_to_int(label: str) -> Optional[int]:
    """Norway-side wrapper that normalises the label first then delegates.

    The shared ``lawvm.roman`` parser rejects non-canonical spellings via
    round-trip canonicalization, fixing a latent bug in the previous
    inline implementation where the ``prev`` tracker only updated in the
    additive branch.
    """
    return _shared_roman_to_int(_normalize_label(label))


def _numeric_chapter_label(label: str) -> str:
    roman = _roman_to_int(label)
    if roman is not None:
        return str(roman)
    return _normalize_label(label)


def _label_in_range(label: str, start_label: str, end_label: str) -> bool:
    key = _no_sort_key(label)
    return _no_sort_key(start_label) <= key <= _no_sort_key(end_label)


def _replace_node_at_path(tree: IRNode, path: tree_ops.Path, replacement: IRNode) -> IRNode:
    if not path:
        return replacement
    head_kind, head_label = path[0]
    new_children: list[IRNode] = []
    for child in tree.children:
        if _no_kind_value(child.kind) == head_kind and (child.label or "") == head_label:
            new_children.append(_replace_node_at_path(child, path[1:], replacement))
        else:
            new_children.append(child)
    return IRNode(
        kind=tree.kind,
        label=tree.label,
        text=tree.text,
        attrs=dict(tree.attrs),
        children=tuple(new_children),
    )


def _with_no_node_label(node: IRNode, label: str | None) -> IRNode:
    return IRNode(
        kind=node.kind,
        label=label,
        text=node.text,
        attrs=dict(node.attrs),
        children=node.children,
    )


def _find_direct_child_path(
    body: IRNode,
    parent_path: tree_ops.Path,
    kind: str,
    label: Optional[str],
) -> Optional[tree_ops.Path]:
    parent = tree_ops.resolve(body, parent_path) if parent_path else body
    if parent is None:
        return None
    normalized_label = label or ""
    for child in parent.children:
        if _no_kind_value(child.kind) == kind and (child.label or "") == normalized_label:
            return parent_path + ((str(child.kind), child.label or ""),)
    return None


def _find_last_direct_child_path(
    body: IRNode,
    parent_path: tree_ops.Path,
    kind: str,
) -> Optional[tree_ops.Path]:
    parent = tree_ops.resolve(body, parent_path) if parent_path else body
    if parent is None:
        return None
    numeric_children = [
        child for child in parent.children if _no_kind_value(child.kind) == kind and child.label and re.fullmatch(r"\d+", child.label)
    ]
    if not numeric_children:
        return None
    last_label = str(max(int(child.label) for child in numeric_children if child.label))
    return _find_direct_child_path(body, parent_path, kind, last_label)


def _appendable_no_sentence_target(
    body: IRNode,
    parent_path: tree_ops.Path,
    target_label: Optional[str],
) -> bool:
    if not target_label or re.fullmatch(r"\d+", target_label) is None:
        return False
    parent = tree_ops.resolve(body, parent_path)
    if parent is None:
        return False
    sentence_labels = [
        int(child.label)
        for child in parent.children
        if _no_kind_value(child.kind) == "sentence" and child.label and re.fullmatch(r"\d+", child.label)
    ]
    if not sentence_labels:
        return False
    return int(target_label) == max(sentence_labels) + 1


def _appendable_no_item_payload(
    body: IRNode,
    parent_path: tree_ops.Path,
    payload: IRNode,
) -> IRNode:
    if _no_kind_value(payload.kind) != "item" or payload.label != "last":
        return payload
    parent = tree_ops.resolve(body, parent_path)
    if parent is None:
        return payload
    item_labels = [
        int(child.label)
        for child in parent.children
        if _no_kind_value(child.kind) == "item" and child.label and re.fullmatch(r"\d+", child.label)
    ]
    next_label = str(max(item_labels) + 1) if item_labels else "1"
    return IRNode(
        kind=payload.kind,
        label=next_label,
        text=payload.text,
        attrs=dict(payload.attrs),
        children=payload.children,
    )


def _apply_no_text_replace(node: IRNode, match: str, replacement: str) -> IRNode:
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text.replace(match, replacement) if node.text else node.text,
        attrs=dict(node.attrs),
        children=tuple(_apply_no_text_replace(child, match, replacement) for child in node.children),
    )


def _apply_heading_group(body: IRNode, group: NOHeadingGroup) -> IRNode:
    start_path = tree_ops.find(body, "section", group.start_label)
    if start_path is None or not start_path:
        return body
    parent_path = start_path[:-1]
    parent_node = tree_ops.resolve(body, parent_path)
    if parent_node is None:
        return body

    matched_sections = [
        child
        for child in parent_node.children
        if _no_kind_value(child.kind) == "section" and child.label and _label_in_range(child.label, group.start_label, group.end_label)
    ]
    if not matched_sections:
        return body

    chapter_labels = [_numeric_chapter_label(label) for kind, label in parent_path if kind == "chapter" and label]
    if not chapter_labels:
        return body
    group_label = "-".join(chapter_labels + [str(group.sequence)])

    if any(_no_kind_value(child.kind) == "chapter" and child.label == group_label for child in parent_node.children):
        return body

    section_labels = {child.label for child in matched_sections}
    grouped_children = (IRNode(kind=IRNodeKind.HEADING, text=group.title), *matched_sections)
    grouped_node = IRNode(kind=IRNodeKind.CHAPTER, label=group_label, children=grouped_children)

    new_children: list[IRNode] = []
    inserted = False
    for child in parent_node.children:
        if _no_kind_value(child.kind) == "section" and child.label in section_labels:
            if not inserted:
                new_children.append(grouped_node)
                inserted = True
            continue
        new_children.append(child)
    if not inserted:
        new_children.append(grouped_node)

    replacement = IRNode(
        kind=parent_node.kind,
        label=parent_node.label,
        text=parent_node.text,
        attrs=dict(parent_node.attrs),
        children=tuple(new_children),
    )
    return _replace_node_at_path(body, parent_path, replacement)


def apply_no_heading_groups(statute: IRStatute, heading_groups: Sequence[NOHeadingGroup]) -> IRStatute:
    """Regroup flat Norway section ranges under synthetic subchapter containers."""
    body = statute.body
    for group in heading_groups:
        body = _apply_heading_group(body, group)
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=body,
        supplements=statute.supplements,
        metadata=dict(statute.metadata),
    )


def _append_no_replay_adjudication(
    adjudications_out: Optional[List[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: Optional[dict[str, str]] = None,
) -> None:
    """Append a Norway replay adjudication when a sink list is available."""
    if adjudications_out is None:
        return
    adjudications_out.append(
        CompileAdjudication(
            kind=kind,
            message=message,
            source_statute=op.source.statute_id if op.source else "",
            op_id=op.op_id,
            detail=detail or {},
        )
    )


def _no_path_label(path: tree_ops.Path) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _no_replay_payload_detail(payload: Optional[IRNode]) -> dict[str, str]:
    if payload is None:
        return {}
    return {
        "payload_kind": _no_kind_value(payload.kind),
        "payload_label": payload.label or "",
    }


def apply_no_ops(
    statute: IRStatute,
    ops: List[LegalOperation],
    adjudications_out: Optional[List[CompileAdjudication]] = None,
    strict_invariants: bool = True,
    strict_action_family: bool = False,
) -> IRStatute:
    """Apply a minimal structural Norway operation set to a statute tree.

    Architectural note:
    this function still carries some target completion / structural recovery
    debt that should move upward into elaboration. Replay should converge on an
    execution-only contract over fully resolved canonical operations.
    """
    body = statute.body

    def _group_sort_key(op: LegalOperation) -> tuple[str, str, str, int]:
        effective = op.source.effective if op.source and op.source.effective else ""
        enacted = op.source.enacted if op.source and op.source.enacted else ""
        source_id = op.source.statute_id if op.source and op.source.statute_id else ""
        return (effective, enacted, source_id, op.sequence)

    def _group_identity(op: LegalOperation) -> tuple[str, str, str]:
        effective = op.source.effective if op.source and op.source.effective else ""
        enacted = op.source.enacted if op.source and op.source.enacted else ""
        source_id = op.source.statute_id if op.source and op.source.statute_id else ""
        return (effective, enacted, source_id)

    def _renumber_sort_key(op: LegalOperation) -> tuple[int, tuple[tuple[int, str, int], ...], int]:
        return (
            len(op.target.path),
            tuple(_no_sort_key(label) for _kind, label in op.target.path),
            op.sequence,
        )

    def _ordered_renumber_group(group: list[LegalOperation]) -> list[LegalOperation]:
        """Order renumber chains so occupied destinations are vacated first."""
        renumbers = [op for op in group if op.action is StructuralAction.RENUMBER and op.destination is not None]
        by_target = {op.target.path: op for op in renumbers}
        ordered: list[LegalOperation] = []
        visiting: set[tuple[tuple[str, str], ...]] = set()
        visited: set[tuple[tuple[str, str], ...]] = set()

        def _visit(op: LegalOperation) -> None:
            key = op.target.path
            if key in visited:
                return
            if key in visiting:
                return
            visiting.add(key)
            dep = by_target.get(op.destination.path if op.destination is not None else ())
            if dep is not None:
                _visit(dep)
            visiting.remove(key)
            visited.add(key)
            ordered.append(op)

        for op in sorted(renumbers, key=_renumber_sort_key, reverse=True):
            _visit(op)
        return ordered

    ordered_ops: list[tuple[LegalOperation, set[tuple[tuple[str, str], ...]]]] = []
    for _group_key, group_iter in itertools.groupby(sorted(ops, key=_group_sort_key), key=_group_identity):
        group = list(group_iter)
        renumber_sources = {
            op.target.path for op in group if op.action is StructuralAction.RENUMBER and op.destination is not None
        }
        ordered_ops.extend(
            (op, renumber_sources)
            for op in sorted((op for op in group if op.action is StructuralAction.REPEAL), key=lambda op: op.sequence)
        )
        ordered_ops.extend((op, renumber_sources) for op in _ordered_renumber_group(group))
        ordered_ops.extend(
            (op, renumber_sources)
            for op in sorted(
                (op for op in group if op.action not in {StructuralAction.REPEAL, StructuralAction.RENUMBER}),
                key=lambda op: op.sequence,
            )
        )

    def _assert_no_invariant_violations(op: LegalOperation) -> None:
        violations = [
            violation
            for violation in tree_ops.check_invariants(body, sort_key=_no_sort_key)
            if "duplicate " in violation or " out of order:" in violation
        ]
        if not violations:
            return
        joined = "; ".join(violations)
        _append_no_replay_adjudication(
            adjudications_out,
            kind="replay_tree_invariant_violation",
            message="Norway replay violated order/duplication invariant.",
            op=op,
            detail={
                "action": _no_action_value(op.action),
                "target": str(op.target),
                "violations": joined,
            },
        )
        if not strict_invariants:
            return
        source_id = op.source.statute_id if op.source else ""
        raise ValueError(
            f"Norway replay invariant violation after {op.action} {op.target.path!r} "
            f"from {source_id or '<unknown>'}: {joined}"
        )

    def _record_action_family_recovery(
        *,
        kind: str,
        message: str,
        op: LegalOperation,
        detail: dict[str, str],
    ) -> None:
        _append_no_replay_adjudication(
            adjudications_out,
            kind=kind,
            message=message,
            op=op,
            detail=detail,
        )
        if not strict_action_family:
            return
        source_id = op.source.statute_id if op.source else ""
        raise ValueError(
            f"Norway replay action-family recovery {kind} after {op.action} "
            f"{op.target.path!r} from {source_id or '<unknown>'}"
        )

    for op, renumber_sources in ordered_ops:
        if _no_action_value(op.action) == "text_replace":
            patch = op.text_patch
            if patch is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unsupported_action",
                    message="Norway replay skipped text_replace without structured text_patch.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            text_match = patch.selector.match_text
            text_replacement = patch.replacement if patch.replacement is not None else ""
            if not text_match or text_replacement is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unsupported_action",
                    message="Norway replay skipped text_replace without match/replacement.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            if not op.target.path:
                body = _apply_no_text_replace(body, text_match, text_replacement)
                _assert_no_invariant_violations(op)
                continue
            resolved_path = _resolve_no_path(body, op.target)
            if resolved_path is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unresolved_target",
                    message="Norway replay skipped text_replace: target not found.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            node = tree_ops.resolve(body, resolved_path)
            if node is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unresolved_target",
                    message="Norway replay skipped text_replace: target not found.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            body = tree_ops.replace_at(
                body,
                resolved_path,
                _apply_no_text_replace(node, text_match, text_replacement),
            )
            _assert_no_invariant_violations(op)
            continue
        if not op.target.path:
            _append_no_replay_adjudication(
                adjudications_out,
                kind="replay_noop",
                message="Norway replay skipped operation with missing target path.",
                op=op,
                detail={"action": _no_action_value(op.action)},
            )
            _assert_no_invariant_violations(op)
            continue
        if op.action not in {
            StructuralAction.REPLACE,
            StructuralAction.REPEAL,
            StructuralAction.INSERT,
            StructuralAction.RENUMBER,
        }:
            _append_no_replay_adjudication(
                adjudications_out,
                kind="replay_unsupported_action",
                message="Norway replay skipped unsupported action.",
                op=op,
                detail={"action": _no_action_value(op.action), "target": str(op.target)},
            )
            _assert_no_invariant_violations(op)
            continue
        if op.target.leaf_kind() == "sentence" and op.target.parent() is not None:
            parent_path = _resolve_no_path(body, cast(LegalAddress, op.target.parent()))
            if parent_path is not None:
                body = _materialize_no_sentence_children(body, parent_path)
        resolved_path = _resolve_no_path(body, op.target)
        if (
            resolved_path is None
            and op.target.leaf_kind() == "sentence"
            and op.target.leaf_label() == "last"
            and op.target.parent() is not None
        ):
            parent_path = _resolve_no_path(body, cast(LegalAddress, op.target.parent()))
            if parent_path is not None:
                resolved_path = _resolve_no_last_child_path(body, parent_path, "sentence")
        if resolved_path is None and op.target.leaf_kind() == "sentence":
            body, resolved_path = _resolve_shallow_no_sentence_path(body, op.target)

        if op.action is StructuralAction.REPLACE and op.payload is not None:
            payload = op.payload
            if resolved_path is not None and _no_kind_value(payload.kind) == "sentence" and payload.label == "last":
                resolved_node = tree_ops.resolve(body, resolved_path)
                if resolved_node is not None and resolved_node.label:
                    payload = _with_no_node_label(payload, resolved_node.label)
            if resolved_path is None:
                if (
                    op.target.leaf_kind() == "sentence"
                    and _no_kind_value(payload.kind) == "sentence"
                    and op.target.parent() is not None
                ):
                    target_parent = cast(LegalAddress, op.target.parent())
                    resolved_parent = _resolve_no_path(body, target_parent)
                    if (
                        resolved_parent is not None
                        and _find_direct_child_path(
                            body,
                            resolved_parent,
                            "sentence",
                            op.payload.label,
                        )
                        is None
                        and _appendable_no_sentence_target(
                            body,
                            resolved_parent,
                            op.target.leaf_label(),
                        )
                    ):
                        _record_action_family_recovery(
                            kind="no_replay_replace_recovered_by_insert",
                            message="Norway replay recovered missing-target replace by inserting a sentence.",
                            op=op,
                            detail={
                                "rule_id": "no_replace_missing_sentence_append_to_resolved_parent",
                                "original_action": "replace",
                                "executed_action": "insert",
                                "target": str(op.target),
                                "insert_parent_path": _no_path_label(resolved_parent),
                                **_no_replay_payload_detail(payload),
                            },
                        )
                        body = tree_ops.insert_sorted(
                            body,
                            resolved_parent,
                            payload,
                            sort_key_fn=_no_sort_key,
                        )
                        _assert_no_invariant_violations(op)
                        continue
                if op.target.leaf_kind() == "sentence" and _no_kind_value(payload.kind) == "sentence":
                    body, shallow_host_path = _resolve_shallow_no_sentence_host_path(body, op.target)
                    if (
                        shallow_host_path is not None
                        and _find_direct_child_path(
                            body,
                            shallow_host_path,
                            "sentence",
                            op.payload.label,
                        )
                        is None
                        and _appendable_no_sentence_target(
                            body,
                            shallow_host_path,
                            op.target.leaf_label(),
                        )
                    ):
                        _record_action_family_recovery(
                            kind="no_replay_replace_recovered_by_insert",
                            message="Norway replay recovered missing-target replace by inserting a sentence.",
                            op=op,
                            detail={
                                "rule_id": "no_replace_missing_sentence_append_to_shallow_host",
                                "original_action": "replace",
                                "executed_action": "insert",
                                "target": str(op.target),
                                "insert_parent_path": _no_path_label(shallow_host_path),
                                **_no_replay_payload_detail(payload),
                            },
                        )
                        body = tree_ops.insert_sorted(
                            body,
                            shallow_host_path,
                            payload,
                            sort_key_fn=_no_sort_key,
                        )
                        _assert_no_invariant_violations(op)
                        continue
                if (
                    op.target.leaf_kind() == "item"
                    and _no_kind_value(payload.kind) == "item"
                    and payload.label == "last"
                    and op.target.parent() is not None
                ):
                    target_parent = cast(LegalAddress, op.target.parent())
                    resolved_parent = _resolve_no_path(body, target_parent)
                    if resolved_parent is not None:
                        append_payload = _appendable_no_item_payload(body, resolved_parent, payload)
                        if (
                            _find_direct_child_path(
                                body,
                                resolved_parent,
                                "item",
                                append_payload.label,
                            )
                            is None
                        ):
                            _record_action_family_recovery(
                                kind="no_replay_replace_recovered_by_insert",
                                message="Norway replay recovered missing-target replace by inserting an item.",
                                op=op,
                                detail={
                                    "rule_id": "no_replace_missing_last_item_append_to_parent",
                                    "original_action": "replace",
                                    "executed_action": "insert",
                                    "target": str(op.target),
                                    "insert_parent_path": _no_path_label(resolved_parent),
                                    **_no_replay_payload_detail(append_payload),
                                },
                            )
                            body = tree_ops.insert_sorted(
                                body,
                                resolved_parent,
                                append_payload,
                                sort_key_fn=_no_sort_key,
                            )
                            _assert_no_invariant_violations(op)
                            continue
                if _no_kind_value(payload.kind) == "section" and op.target.leaf_kind() == "section":
                    parent_path: tree_ops.Path = ()
                    if op.target.parent() is not None:
                        target_parent = cast(LegalAddress, op.target.parent())
                        resolved_parent = _resolve_no_path(body, target_parent)
                        if resolved_parent is not None:
                            parent_path = resolved_parent
                        else:
                            prefix_path, matched = _resolve_existing_prefix(body, target_parent)
                            if prefix_path is None and matched == 0:
                                _append_no_replay_adjudication(
                                    adjudications_out,
                                    kind="replay_unresolved_target",
                                    message="Norway replay skipped operation: parent not found.",
                                    op=op,
                                    detail={
                                        "action": _no_action_value(op.action),
                                        "target": str(op.target),
                                        "target_parent": str(target_parent),
                                    },
                                )
                                _assert_no_invariant_violations(op)
                                continue
                            body, parent_path = _ensure_no_container_chain(
                                body,
                                prefix_path or (),
                                target_parent.path[matched:],
                            )
                    elif payload.label:
                        inferred_section_parent = _infer_section_parent_path(
                            body,
                            payload.label,
                        )
                        if inferred_section_parent is not None:
                            parent_path = inferred_section_parent
                    _record_action_family_recovery(
                        kind="no_replay_replace_recovered_by_insert",
                        message="Norway replay recovered missing-target replace by inserting a section.",
                        op=op,
                        detail={
                            "rule_id": "no_replace_missing_section_insert",
                            "original_action": "replace",
                            "executed_action": "insert",
                            "target": str(op.target),
                            "insert_parent_path": _no_path_label(parent_path),
                            **_no_replay_payload_detail(payload),
                        },
                    )
                    body = tree_ops.insert_sorted(
                        body,
                        parent_path,
                        payload,
                        sort_key_fn=_no_sort_key,
                    )
                    _assert_no_invariant_violations(op)
                    continue
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unresolved_target",
                    message="Norway replay skipped operation: target not found.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue

            existing = tree_ops.resolve(body, resolved_path)
            if (
                existing is not None
                and _no_kind_value(existing.kind) == "section"
                and _no_kind_value(op.payload.kind) == "section"
                and not op.payload.text
                and len(op.payload.children) == 1
                and _no_kind_value(op.payload.children[0].kind) == "heading"
            ):
                merged_children = [op.payload.children[0]]
                merged_children.extend(child for child in existing.children if _no_kind_value(child.kind) != "heading")
                body = tree_ops.replace_at(
                    body,
                    resolved_path,
                    IRNode(
                        kind=existing.kind,
                        label=existing.label,
                        text=existing.text,
                        attrs=dict(existing.attrs),
                        children=tuple(merged_children),
                    ),
                )
                _assert_no_invariant_violations(op)
                continue
            body = tree_ops.replace_at(body, resolved_path, payload)

        elif op.action is StructuralAction.REPEAL:
            if resolved_path is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unresolved_target",
                    message="Norway replay skipped operation: target not found.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            body = tree_ops.remove_at(body, resolved_path)

        elif op.action is StructuralAction.INSERT and op.payload is not None:
            payload = op.payload
            if resolved_path is not None:
                _record_action_family_recovery(
                    kind="no_replay_insert_occupied_target_replaced",
                    message="Norway replay recovered insert into an occupied target by replacing that target.",
                    op=op,
                    detail={
                        "rule_id": "no_insert_occupied_target_replace",
                        "original_action": "insert",
                        "executed_action": "replace",
                        "target": str(op.target),
                        "resolved_path": _no_path_label(resolved_path),
                        **_no_replay_payload_detail(payload),
                    },
                )
                body = tree_ops.replace_at(body, resolved_path, payload)
                _assert_no_invariant_violations(op)
                continue
            parent_path: tree_ops.Path = ()
            if op.target.parent() is not None:
                target_parent = cast(LegalAddress, op.target.parent())
                resolved_parent = _resolve_no_path(body, target_parent)
                if resolved_parent is not None:
                    parent_path = resolved_parent
                else:
                    prefix_path, matched = _resolve_existing_prefix(body, target_parent)
                    if prefix_path is None and matched == 0:
                        _append_no_replay_adjudication(
                            adjudications_out,
                            kind="replay_unresolved_target",
                            message="Norway replay skipped operation: parent not found.",
                            op=op,
                            detail={
                                "action": _no_action_value(op.action),
                                "target": str(op.target),
                                "target_parent": str(target_parent),
                            },
                        )
                        _assert_no_invariant_violations(op)
                        continue
                    body, parent_path = _ensure_no_container_chain(
                        body,
                        prefix_path or (),
                        target_parent.path[matched:],
                    )
            elif _no_kind_value(payload.kind) == "section" and payload.label:
                inferred_section_parent = _infer_section_parent_path(body, payload.label)
                if inferred_section_parent is not None:
                    parent_path = inferred_section_parent
            parent_node = tree_ops.resolve(body, parent_path) if parent_path else body
            if parent_node is not None and _no_kind_value(payload.kind) == "item" and payload.label == "last":
                payload = _with_no_node_label(payload, _next_no_child_label(parent_node, "item"))
            if parent_node is not None:
                inferred = _find_insert_parent(parent_node, str(payload.kind))
                if inferred is not None:
                    parent_path = parent_path + inferred
            direct_existing_path = _find_direct_child_path(
                body,
                parent_path,
                str(payload.kind),
                payload.label,
            )
            if direct_existing_path is not None:
                _record_action_family_recovery(
                    kind="no_replay_insert_occupied_direct_child_replaced",
                    message="Norway replay recovered insert into an occupied direct child by replacing that child.",
                    op=op,
                    detail={
                        "rule_id": "no_insert_occupied_direct_child_replace",
                        "original_action": "insert",
                        "executed_action": "replace",
                        "target": str(op.target),
                        "parent_path": _no_path_label(parent_path),
                        "occupied_child_path": _no_path_label(direct_existing_path),
                        **_no_replay_payload_detail(payload),
                    },
                )
                body = tree_ops.replace_at(
                    body,
                    direct_existing_path,
                    payload,
                )
                _assert_no_invariant_violations(op)
                continue
            body = tree_ops.insert_sorted(
                body,
                parent_path,
                payload,
                sort_key_fn=_no_sort_key,
            )

        elif op.action is StructuralAction.RENUMBER and op.destination is not None:
            if resolved_path is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unresolved_target",
                    message="Norway replay skipped operation: target not found.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            node = tree_ops.resolve(body, resolved_path)
            if node is None:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_unresolved_target",
                    message="Norway replay skipped operation: target not found.",
                    op=op,
                    detail={"action": _no_action_value(op.action), "target": str(op.target)},
                )
                _assert_no_invariant_violations(op)
                continue
            moved = node
            if op.destination.leaf_label():
                moved = _with_no_node_label(moved, op.destination.leaf_label())
            source_parent_path = resolved_path[:-1]
            destination_path = _resolve_no_path(body, op.destination)
            if (
                destination_path is not None
                and destination_path != resolved_path
                and op.destination.path not in renumber_sources
            ):
                body = tree_ops.remove_at(body, destination_path)
            body = tree_ops.remove_at(body, resolved_path)
            destination_parent = op.destination.parent()
            if destination_parent is not None:
                parent_path = _resolve_no_path(body, destination_parent) or ()
            else:
                parent_path = source_parent_path
            body = tree_ops.insert_sorted(
                body,
                parent_path,
                moved,
                sort_key_fn=_no_sort_key,
            )
        _assert_no_invariant_violations(op)

    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=body,
        supplements=statute.supplements,
        metadata=dict(statute.metadata),
    )


def open_lovdata_archive(tar_bz2_path: str) -> Generator[Tuple[str, bytes], None, None]:
    """Yield ``(statute_id, bytes)`` pairs from a Lovdata public tarball."""
    with tarfile.open(tar_bz2_path, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".xml"):
                continue
            statute_id = lovdata_filename_to_id(member.name)
            if statute_id is None:
                continue
            file_obj = tf.extractfile(member)
            if file_obj is None:
                continue
            yield statute_id, file_obj.read()


def open_lovdata_amendment_archive(tar_bz2_path: str) -> Generator[Tuple[str, bytes], None, None]:
    """Yield ``(source_id, bytes)`` pairs from a Lovtidend tarball."""
    with tarfile.open(tar_bz2_path, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".xml"):
                continue
            source_id = lovdata_amendment_filename_to_id(member.name)
            if source_id is None:
                continue
            file_obj = tf.extractfile(member)
            if file_obj is None:
                continue
            yield source_id, file_obj.read()
