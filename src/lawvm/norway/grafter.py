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

import contextvars
import copy
import re
import tarfile
from collections import Counter
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import Any, Generator, List, Mapping, Optional, Sequence, Tuple, cast

from lxml import etree

from lawvm.core import tree_ops
from lawvm.core.archive_safety import (
    ArchiveMemberTooLarge,
    log_archive_member_too_large,
    safe_tar_read,
)
from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.invariant_profiles import CORE_REPLAY_DELTA_MINIMAL_FAMILIES
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.op_ordering import OrderingProfile, order_ops
from lawvm.core.provenance import compute_source_anchor
from lawvm.core.apply_seam import (
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    OpAcceptance,
    apply_op,
)
from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.mutation_boundary import (
    TreePath,
    TreePaths,
    diff_ir_paths_identity_pruned,
)
from lawvm.core.phase_result import Finding
from lawvm.core.xml_parse import parse_corpus_xml
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
from lawvm.core.semantic_types import (
    IRNodeKind,
    StructuralAction,
    TextPatchKindEnum,
    structural_action_from_str,
    structural_action_value,
)
from lawvm.core.quirks_disposition import QuirksDisposition
from lawvm.core.write_receipt import WriteReceipt, receipt_address_string
from lawvm.norway.mutation_boundary_per_op_probe import (
    drain_seam_boundary_observations as _no_drain_seam_boundary_observations,
)
from lawvm.norway.scope_confidence import NOScopeConfidence
from lawvm.core.totalization import (
    FailureClass,
    NoopIdempotent,
    Recover,
    Reject,
)
from lawvm.norway.totalization_table import NO_TOTALIZATION_TABLE

NO_PARSE_REPLACE_PROMOTED_TO_INSERT_FOR_RENUMBER = "no_parse_replace_promoted_to_insert_for_same_target_renumber"
NO_PARSE_STRUCTURED_TARGET_REBOUND_FROM_LEAD = "no_parse_structured_target_rebound_from_lead"
NO_PARSE_ACTION_RECOVERED_FROM_STRUCTURED_LEAD = "no_parse_action_recovered_from_structured_lead"


def _no_action_value(action: StructuralAction | str) -> str:
    """Normalize action to string value for comparisons and serialization.

    Fail-loud on an unrecognized ``str``: the shared jurisdiction-neutral
    ``structural_action_value`` is intentionally non-validating (it is the
    inverse direction; a caller may feed an already-valid boundary string).
    This wrapper is the only Norway action boundary and is reached with raw
    parsed action strings -- so it routes through ``structural_action_from_str``
    (raise) to mirror EE/UK's ``_to_structural_action`` and close the
    producer-side hole where an unknown action would otherwise pass through the
    comparison/serialization boundary unlabelled.
    """
    validated = structural_action_from_str(action, on_unknown="raise")
    return structural_action_value(validated)


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
    try:
        root = parse_corpus_xml(html_bytes, recover=True)
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


NO_PARSE_MALFORMED_STRUCTURED_RENUMBER_ATTR_SKIPPED = "no_parse_malformed_structured_renumber_attr_skipped"


def _structured_move_attr_skip_reason(token: str) -> Optional[str]:
    separator_count = token.count(";;")
    if separator_count == 0:
        return "missing_separator"
    if separator_count > 1:
        return "multiple_separators"
    src, dst = token.split(";;", 1)
    if not src and not dst:
        return "missing_source_and_destination"
    if not src:
        return "missing_source"
    if not dst:
        return "missing_destination"
    return None


def _split_move_attr(
    value: str,
    *,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
    source_id: str = "",
    base_id: str = "",
    source_doc: str = "",
    raw_text: str = "",
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    tokens = [token.strip() for token in value.split() if token.strip()]
    for token in reversed(tokens):
        reason = _structured_move_attr_skip_reason(token)
        if reason is not None:
            _append_no_parse_adjudication(
                adjudications_out,
                kind=NO_PARSE_MALFORMED_STRUCTURED_RENUMBER_ATTR_SKIPPED,
                message="Norway parser skipped malformed structured renumber token.",
                source_id=source_id,
                detail=diagnostic_detail(
                    rule_id=NO_PARSE_MALFORMED_STRUCTURED_RENUMBER_ATTR_SKIPPED,
                    phase="parse",
                    family="source_pathology",
                    blocking=True,
                    reason=reason,
                    base_id=base_id,
                    source_doc=source_doc,
                    attr_name="data-move-part",
                    raw_token=token,
                    raw_text=raw_text,
                ),
            )
            continue
        src, dst = token.split(";;", 1)
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
            for article, target in zip(direct_text_articles, direct_targets, strict=False):
                payload = _payload_from_direct_text_article(article, target)
                if payload is not None and target.leaf_label():
                    candidates[(target.leaf_kind(), target.leaf_label())] = payload
        elif len(direct_text_articles) == 1 and leaf_kinds == {"sentence"} and len(direct_targets) > 1:
            text = _node_text_without_structural_children(direct_text_articles[0])
            sentences = _split_no_sentences(text)
            if len(sentences) == len(direct_targets):
                for sentence_text, target in zip(sentences, direct_targets, strict=True):
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
                for sentence_text, target in zip(sentences, direct_targets, strict=True):
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


_NO_WHOLE_SECTION_LEAD_RE = re.compile(
    r"^(?P<insert>Ny\s+)?§\s*(?P<label>[0-9]+(?:-[0-9]+)*(?:\s*[A-Za-z])?)\s+skal\s+lyde:\s*(?P<inline>.*)$",
    re.IGNORECASE | re.DOTALL,
)


# A chapter-scoped whole-section lead ("I kapittel III skal ny § 16-2 lyde:")
# carries the same operative content as the canonical "Ny § 16-2 skal lyde:"
# form, but the locative ``I kapittel <X>`` prefix and the ``skal ny § X lyde``
# verb order defeat ``_NO_WHOLE_SECTION_LEAD_RE``. Norwegian § numbering is
# act-global (the chapter is redundant for addressing), so we can safely drop
# the chapter scope and rewrite the lead into the canonical form the existing
# section lowering consumes.
_NO_CHAPTER_SCOPED_SECTION_LEAD_RE = re.compile(
    r"^I\s+kapit(?:tel|let|tlet)\s+\S+(?:\s+\S+?)?\s+skal\s+"
    r"(?P<insert>ny(?:tt|e)?\s+)?§\s*(?P<label>[0-9]+(?:-[0-9]+)*(?:\s*[A-Za-z])?)"
    r"\s+lyde:\s*(?P<inline>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_no_chapter_scoped_section_lead(lead: str) -> str:
    """Rewrite a chapter-scoped whole-section lead into the canonical form.

    "I kapittel III skal ny § 16-2 lyde: …" -> "Ny § 16-2 skal lyde: …" so the
    existing ``_NO_WHOLE_SECTION_LEAD_RE`` section lowering recognizes it. Leaves
    leads that do not match this exact shape untouched.
    """
    match = _NO_CHAPTER_SCOPED_SECTION_LEAD_RE.match(lead)
    if match is None:
        return lead
    prefix = "Ny " if match.group("insert") else ""
    inline = match.group("inline")
    return f"{prefix}§ {match.group('label')} skal lyde: {inline}".rstrip()


def _build_no_unstructured_section_payload(
    label: str,
    inline_text: str,
    payload_nodes: Sequence[etree._Element],
) -> Optional[IRNode]:
    """Build a whole-section payload from an unstructured ``§ X skal lyde:`` lead.

    Reuses the structured ``_parse_future_section`` lowering by wrapping the lead's
    inline tail text plus the following non-lead payload articles into a synthetic
    ``futureLegalArticle`` element. Returns ``None`` when no payload content can be
    recovered so the caller can honestly drop the lead.
    """
    synthetic = etree.Element("article")
    synthetic.set("class", "futureLegalArticle")
    synthetic.set("data-name", f"§{label}")

    inline_text = _normalize_space(inline_text)
    if inline_text:
        # An inline tail frequently re-states the section header (``§ 2-2. Title``)
        # before the body. Split it into a header span + body so the reused
        # ``_parse_future_section`` lowering treats the title as a heading, not text.
        header_match = re.match(
            rf"^§\s*{re.escape(label)}\s*\.\s*(?P<title>[^.]*?\S)?\s+(?P<body>[A-ZÆØÅ].*)$",
            inline_text,
            re.DOTALL,
        )
        if header_match:
            header_span = etree.SubElement(synthetic, "span")
            header_span.set("class", "futureLegalArticleHeader")
            header_span.text = f"§ {label}. {header_match.group('title') or ''}".strip()
            body = _normalize_space(header_match.group("body"))
            if body:
                body_article = etree.SubElement(synthetic, "article")
                body_article.set("class", "legalP")
                body_article.text = body
        else:
            inline_article = etree.SubElement(synthetic, "article")
            inline_article.set("class", "legalP")
            inline_article.text = inline_text

    for node in payload_nodes:
        if _local_name(node) != "article":
            continue
        if not ({"legalP", "numberedLegalP", "listArticle"} & _classes(node)):
            continue
        synthetic.append(copy.deepcopy(node))

    if len(synthetic) == 0:
        return None
    payload = _parse_future_section(synthetic)
    if payload is None or (not payload.children and not _normalize_space(payload.text or "")):
        return None
    return payload


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


# Raw-amendment-source context for the byte-span SourceAnchor program (task
# #92, mirroring the Estonia pilot at estonia/peg.py). The raw Lovdata HTML
# bytes are in scope only at the top entry ``parse_no_amendment_ops`` (every
# per-op clause below it has already been text-flattened via
# ``_normalize_space(" ".join(el.itertext()))`` — exactly the EE flattening
# shape — so the byte/char offset into the raw artifact is lost at the op
# emission sites). Rather than thread ``html_bytes`` through the many
# op-emission call sites and the ``iter_no_document_change_ops`` generator
# contract, the top entry publishes the raw artifact in this ContextVar for the
# duration of one amendment's parse; the uniform provenance post-pass
# (:func:`mint_no_source_anchors`) reads it and mints a TRUE SourceAnchor for
# every op whose recorded clause text survives flattening as a single verbatim,
# unique byte run of the raw artifact. When it does not (clause reconstructed
# across tag boundaries / whitespace-collapsed, or repeated/ambiguous),
# ``compute_source_anchor`` returns None and the anchor is honestly left absent
# — never fabricated.
_NO_RAW_SOURCE_CTX: "contextvars.ContextVar[tuple[str, bytes] | None]" = contextvars.ContextVar(
    "no_raw_source_ctx", default=None
)


def set_no_raw_source_context(
    source_artifact_id: str, raw_bytes: bytes
) -> "contextvars.Token[tuple[str, bytes] | None]":
    """Publish the raw amendment artifact for SourceAnchor minting in this parse.

    Returns a token the caller MUST pass to :func:`reset_no_raw_source_context`
    in a ``finally`` so the context never leaks across amendments.
    """
    return _NO_RAW_SOURCE_CTX.set((source_artifact_id, raw_bytes))


def reset_no_raw_source_context(
    token: "contextvars.Token[tuple[str, bytes] | None]",
) -> None:
    """Clear the raw-source context published by :func:`set_no_raw_source_context`."""
    _NO_RAW_SOURCE_CTX.reset(token)


def mint_no_source_anchors(ops: List[LegalOperation]) -> List[LegalOperation]:
    """Stamp a TRUE byte-span :class:`SourceAnchor` on every anchorable op.

    Final, uniform post-pass over the WHOLE emitted op stream (every mint path),
    run by :func:`parse_no_amendment_ops` once the raw amendment artifact has
    been published in the parse context (see
    :func:`set_no_raw_source_context`).

    For each op that already carries an ``OperationSource`` but no anchor, the
    op's recorded clause text (``source.raw_text`` — falling back to the op's
    ``raw_text``) is located in the raw artifact bytes via
    :func:`lawvm.core.provenance.compute_source_anchor`. The anchor is built on
    that EXACT recorded clause string, so a verifier re-slicing the raw bytes at
    the anchor span gets back precisely the clause text. When the clause is not
    a single verbatim, unique byte run of the artifact (flattened across HTML
    tags, whitespace-collapsed, or repeated/ambiguous), ``compute_source_anchor``
    returns ``None`` and the anchor is honestly left absent — never fabricated.

    Additive metadata only: it touches solely ``source.source_anchor`` and never
    an apply-authoritative field, so NO replay output is byte-identical
    (AGENTS.md §0 grounding-neutral). Idempotent: an op that already carries an
    anchor is left untouched. A no-op when no raw artifact is in context.
    """
    raw_ctx = _NO_RAW_SOURCE_CTX.get()
    if raw_ctx is None or not ops:
        return ops
    artifact_id, raw_bytes = raw_ctx
    anchored: List[LegalOperation] = []
    for op in ops:
        src = op.source
        if src is None or src.source_anchor is not None:
            anchored.append(op)
            continue
        clause = src.raw_text or op.raw_text or ""
        anchor = (
            compute_source_anchor(
                source_artifact_id=artifact_id,
                raw_bytes=raw_bytes,
                clause_text=clause,
            )
            if clause
            else None
        )
        if anchor is None:
            anchored.append(op)
            continue
        anchored.append(dc_replace(op, source=dc_replace(src, source_anchor=anchor)))
    return anchored


def parse_no_amendment_ops(
    html_bytes: bytes,
    source_id: str,
    *,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
) -> List[LegalOperation]:
    """Parse Lovdata amendment blocks into LegalOperation objects."""
    # Publish the raw amendment artifact so the final anchor pass
    # (:func:`mint_no_source_anchors`, applied to the assembled op stream below)
    # can mint a TRUE byte-span SourceAnchor for every op whose recorded clause
    # text survives text-flattening as a verbatim, unique byte run of these
    # bytes (task #92). The token is reset in the finally below so the context
    # never leaks across amendments or to other frontends.
    _raw_source_token = set_no_raw_source_context(source_id, html_bytes)
    try:
        ops: list[LegalOperation] = []
        for _base_id, doc_ops in iter_no_document_change_ops(
            html_bytes,
            source_id,
            adjudications_out=adjudications_out,
        ):
            ops.extend(doc_ops)
        # Final uniform byte-span anchor pass over the WHOLE op stream (every
        # mint path), while the raw artifact is still published in context.
        return mint_no_source_anchors(ops)
    finally:
        reset_no_raw_source_context(_raw_source_token)


def _iter_unstructured_no_change_groups(
    root: etree._Element,
    source_id: str,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
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
            if _no_unstructured_lead_looks_operative(lead):
                _append_no_unstructured_parse_adjudication(
                    adjudications_out,
                    kind="no_parse_unstructured_lead_base_unresolved",
                    message="Norway unstructured amendment lead looked operative, but no base act could be resolved.",
                    source_id=source_id,
                    lead=lead,
                    base_id="",
                    detail={},
                )
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
            _append_no_unstructured_parse_adjudication(
                adjudications_out,
                kind="no_parse_unstructured_payload_unresolved",
                message="Norway unstructured heading replacement lead resolved a target but no heading payload could be extracted.",
                source_id=source_id,
                lead=lead,
                base_id=lead_base_id,
                detail={"target": f"section:{target_label}", "payload_family": "heading_only"},
            )

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
            paired_renumber_count = min(len(source_targets), len(dest_targets))
            if len(source_targets) != len(dest_targets):
                _append_no_unstructured_parse_adjudication(
                    adjudications_out,
                    kind="no_parse_unstructured_renumber_arity_mismatch_skipped",
                    message=(
                        "Norway unstructured repeal/renumber lead resolved unequal source "
                        "and destination target counts; unmatched targets were not compiled."
                    ),
                    source_id=source_id,
                    lead=lead,
                    base_id=lead_base_id,
                    detail={
                        "section": section_label,
                        "source_count": len(source_targets),
                        "destination_count": len(dest_targets),
                        "paired_count": paired_renumber_count,
                        "source_targets": [_no_address_detail(target) for target in source_targets],
                        "destination_targets": [_no_address_detail(target) for target in dest_targets],
                        "unmatched_source_targets": [
                            _no_address_detail(target) for target in source_targets[paired_renumber_count:]
                        ],
                        "unmatched_destination_targets": [
                            _no_address_detail(target) for target in dest_targets[paired_renumber_count:]
                        ],
                    },
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
            for src_target, dst_target in zip(source_targets, dest_targets, strict=False):
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
                        witness_rule_id="no_section_renumber_relabel",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        section_lead = _normalize_no_chapter_scoped_section_lead(lead)
        section_match = _NO_WHOLE_SECTION_LEAD_RE.match(section_lead)
        if section_match:
            target = LegalAddress(path=(("section", _normalize_no_section_label(section_match.group("label"))),))
            action = StructuralAction.INSERT if section_match.group("insert") else StructuralAction.REPLACE
            if future_articles:
                payload = _parse_future_section(future_articles[0])
            else:
                payload = _build_no_unstructured_section_payload(
                    _normalize_no_section_label(section_match.group("label")),
                    section_match.group("inline"),
                    payload_nodes,
                )
            if payload is not None:
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
            _append_no_unstructured_parse_adjudication(
                adjudications_out,
                kind="no_parse_unstructured_payload_unresolved",
                message="Norway unstructured section lead resolved a target but no section payload could be extracted.",
                source_id=source_id,
                lead=lead,
                base_id=lead_base_id,
                detail={"target": _no_address_detail(target), "payload_family": "future_section"},
            )

        sentence_specs = _infer_same_base_sentence_target_specs_from_lead(lead)
        if sentence_specs:
            sentence_targets = [target for _action, target in sentence_specs]
            sentence_payloads: list[IRNode] = []
            if len(text_articles) >= len(sentence_targets):
                for target, article in zip(sentence_targets, text_articles, strict=False):
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
                        for target, sentence_text in zip(sentence_targets, sentences, strict=True)
                    ]
            if sentence_payloads and len(sentence_payloads) == len(sentence_targets):
                for (action, target), payload in zip(sentence_specs, sentence_payloads, strict=True):
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
            _append_no_unstructured_parse_adjudication(
                adjudications_out,
                kind="no_parse_unstructured_payload_unresolved",
                message="Norway unstructured sentence lead resolved targets but payload extraction did not cover them.",
                source_id=source_id,
                lead=lead,
                base_id=lead_base_id,
                detail={
                    "targets": tuple(_no_address_detail(target) for target in sentence_targets),
                    "payload_family": "sentence",
                },
            )

        subsection_specs = _infer_same_base_subsection_target_specs_from_lead(lead)
        if subsection_specs and len(text_articles) >= len(subsection_specs):
            unresolved_targets: list[LegalAddress] = []
            for (action, target), article in zip(subsection_specs, text_articles, strict=False):
                payload = _payload_from_direct_text_article(article, target)
                if payload is None:
                    unresolved_targets.append(target)
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
            for target in unresolved_targets:
                _append_no_unstructured_parse_adjudication(
                    adjudications_out,
                    kind="no_parse_unstructured_payload_unresolved",
                    message="Norway unstructured subsection lead resolved a target but no subsection payload could be extracted.",
                    source_id=source_id,
                    lead=lead,
                    base_id=lead_base_id,
                    detail={"target": _no_address_detail(target), "payload_family": "subsection"},
                )
            idx = cursor
            continue

        item_targets = _infer_same_base_item_targets_from_lead(lead)
        if item_targets:
            payload_candidates = _extract_payload_candidates_from_nodes([child, *payload_nodes], item_targets)
            unresolved_targets: list[LegalAddress] = []
            for target in item_targets:
                payload = payload_candidates.get((target.leaf_kind(), target.leaf_label()))
                if payload is None and target.leaf_kind() == "item" and target.leaf_label() == "last":
                    item_payloads = [
                        candidate for (kind, _label), candidate in payload_candidates.items() if kind == "item"
                    ]
                    if len(item_payloads) == 1:
                        payload = _with_no_node_label(item_payloads[0], "last")
                if payload is None:
                    unresolved_targets.append(target)
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
            for target in unresolved_targets:
                _append_no_unstructured_parse_adjudication(
                    adjudications_out,
                    kind="no_parse_unstructured_payload_unresolved",
                    message="Norway unstructured item lead resolved a target but no item payload could be extracted.",
                    source_id=source_id,
                    lead=lead,
                    base_id=lead_base_id,
                    detail={"target": _no_address_detail(target), "payload_family": "item"},
                )
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
                    witness_rule_id="no_section_renumber_relabel",
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
                        witness_rule_id="no_section_renumber_relabel",
                    )
                )
                sequence += 1
            idx = cursor
            continue

        if _no_unstructured_lead_looks_operative(lead):
            _append_no_unstructured_parse_adjudication(
                adjudications_out,
                kind="no_parse_unstructured_lead_unmatched",
                message="Norway unstructured amendment lead looked operative but matched no supported lowering family.",
                source_id=source_id,
                lead=lead,
                base_id=lead_base_id,
                detail={},
            )
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
    # ``skal\s+(?:\S+\s+)*?`` tolerates intervening qualifier words between the
    # ``skal`` verb and the ``§`` target ("skal ny § 12 a lyde", "skal nytt
    # § 4 a lyde"). The capture begins at ``§`` so the rebuilt embedded lead
    # stays a ``§ …`` form the section/subsection lowering families consume.
    patterns = (
        r"^\d+\.\s+I lov\s+(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)\s+.+?\s+skal\s+(?:\S+\s+)*?(§.+)$",
        r"^I\s+(?:lov\s+|midlertidig\s+lov\s+)?(?:.+?\s+av\s+)?(\d{1,2})\.\s+([A-Za-zæøåÆØÅ]+)\s+(\d{4})\s+nr\.\s+(\d+)\s+.+?\s+skal\s+(?:\S+\s+)*?(§.+)$",
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
    if month is None:
        return None
    # An intervening ``ny``/``nytt``/``nye`` qualifier immediately before the
    # ``§`` means the section is being inserted, not replaced; surface it as the
    # ``Ny § …`` prefix that the ``_NO_WHOLE_SECTION_LEAD_RE`` insert branch
    # recognizes.
    insert_qualifier = bool(
        re.search(r"\bskal\s+ny(?:tt|e)?\s+$", lead[: match.start(5)], re.IGNORECASE)
    )
    embedded_lead = match.group(5).strip()
    if " skal " not in embedded_lead.lower():
        embedded_lead = re.sub(r"\s+lyd([ea]):?$", r" skal lyd\1:", embedded_lead, flags=re.IGNORECASE)
    if insert_qualifier and not re.match(r"^ny(?:tt|e)?\b", embedded_lead, re.IGNORECASE):
        embedded_lead = f"Ny {embedded_lead}"
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


def _no_unstructured_lead_looks_operative(lead: str) -> bool:
    return bool(
        re.search(
            # ``skal(?:\s+\S+){1,5}?\s+lyde`` admits the intervening-qualifier
            # framing with one *or several* tokens between the ``skal`` verb and
            # ``lyde`` ("skal ny § X lyde", "skal ny § 4 a lyde", "skal nytt ledd
            # lyde"); the prior single-token bound silently dropped the multi-token
            # chapter-scoped insert leads. The trailing alternatives add the
            # nynorsk action verbs (gjer/vert gjort/gjerast/endrast/opphevast)
            # alongside the bokmål forms so genuinely operative leads are honestly
            # adjudicated rather than silently treated as inert prose.
            r"(\bskal\s+lyde\b|\bskal(?:\s+\S+){1,5}?\s+lyde\b|\boppheves\b|\bopphevast\b"
            r"|\bblir\b|\bendres\b|\bendrast\b|\btilf[øo]yes\b|\btilf[øo]yast\b"
            r"|\bf[øo]yes\b|\bf[øo]yast\b|\bflyttes\b|\bflyttast\b"
            r"|\bgjer\b|\bgjerast\b|\bvert\s+gjort\b)",
            lead,
            re.IGNORECASE,
        )
    )


def _no_address_detail(address: LegalAddress) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in address.path)


def _append_no_unstructured_parse_adjudication(
    adjudications_out: Optional[List[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    source_id: str,
    lead: str,
    base_id: str,
    detail: dict[str, object],
) -> None:
    _append_no_parse_adjudication(
        adjudications_out,
        kind=kind,
        message=message,
        source_id=source_id,
        detail=diagnostic_detail(
            rule_id=kind,
            family="unsupported_or_unresolved_action",
            phase="parse",
            blocking=True,
            source_excerpt=_normalize_space(lead)[:240],
            base_id=base_id,
            detail=detail,
        ),
    )


def _append_no_parse_adjudication(
    adjudications_out: Optional[List[CompileAdjudication]],
    *,
    kind: str,
    message: str,
    source_id: str,
    detail: dict[str, object],
) -> None:
    if adjudications_out is None:
        return
    adjudications_out.append(
        CompileAdjudication(
            kind=kind,
            message=message,
            source_statute=source_id,
            blocking=_no_adjudication_blocking(kind, detail),
            phase=_no_adjudication_phase(kind, detail),
            detail=detail,
        )
    )


def _no_adjudication_blocking(kind: str, detail: Mapping[str, object]) -> bool:
    blocking = detail.get("blocking")
    if not isinstance(blocking, bool):
        raise ValueError(
            f"Norway adjudication kind={kind!r} envelope is missing a typed "
            "'blocking'; build the detail via diagnostic_detail()."
        )
    return blocking


def _no_adjudication_phase(kind: str, detail: Mapping[str, object]) -> str:
    phase = detail.get("phase")
    if not isinstance(phase, str) or not phase:
        raise ValueError(
            f"Norway adjudication kind={kind!r} envelope is missing 'phase'; "
            "build the detail via diagnostic_detail()."
        )
    return phase


def _no_structured_spec_detail(
    specs: Sequence[tuple[StructuralAction, LegalAddress]],
) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "action": _no_action_value(action),
            "target": _no_address_detail(target),
        }
        for action, target in specs
    )


def _append_no_structured_parse_recovery_adjudications(
    adjudications_out: Optional[List[CompileAdjudication]],
    *,
    source_id: str,
    base_id: str,
    source_doc: str,
    raw_text: str,
    reason: str,
    scope_confidence: NOScopeConfidence,
    original_specs: Sequence[tuple[StructuralAction, LegalAddress]],
    recovered_specs: Sequence[tuple[StructuralAction, LegalAddress]],
) -> None:
    """Emit adjudications for a structured-target recovery, carrying a typed scope witness.

    ``scope_confidence`` is a typed ``NOScopeConfidence`` (inheriting the
    ``lawvm.core.scope_confidence.ScopeConfidence`` marker protocol). The §2.2
    ladder rung is projected onto the detail-map string surface via
    ``.rung_id`` so the existing ``core.compile_result``'s string-typed
    ``scope_confidence`` reader continues to work byte-identically, while the
    producer boundary stays typed (AGENTS.md §1.9): a bare string cannot cross
    this signature or the ``LegalOperation.scope_confidence`` waist.
    """
    if not original_specs or list(original_specs) == list(recovered_specs):
        return
    scope_confidence_rung = scope_confidence.rung_id
    _append_no_parse_adjudication(
        adjudications_out,
        kind=NO_PARSE_STRUCTURED_TARGET_REBOUND_FROM_LEAD,
        message=(
            "Norway parser replaced structured target attributes with narrower "
            "targets inferred from the operative lead or payload."
        ),
        source_id=source_id,
        detail=diagnostic_detail(
            rule_id=NO_PARSE_STRUCTURED_TARGET_REBOUND_FROM_LEAD,
            phase="parse",
            family="target_resolution_recovery",
            blocking=True,
            reason=reason,
            base_id=base_id,
            source_doc=source_doc,
            scope_confidence=scope_confidence_rung,
            original_specs=_no_structured_spec_detail(original_specs),
            recovered_specs=_no_structured_spec_detail(recovered_specs),
            raw_text=raw_text,
        ),
    )
    original_actions = tuple(_no_action_value(action) for action, _target in original_specs)
    recovered_actions = tuple(_no_action_value(action) for action, _target in recovered_specs)
    if original_actions == recovered_actions:
        return
    _append_no_parse_adjudication(
        adjudications_out,
        kind=NO_PARSE_ACTION_RECOVERED_FROM_STRUCTURED_LEAD,
        message="Norway parser recovered structured operation action family from the operative lead.",
        source_id=source_id,
        detail=diagnostic_detail(
            rule_id=NO_PARSE_ACTION_RECOVERED_FROM_STRUCTURED_LEAD,
            phase="parse",
            family="action_family_recovery",
            blocking=True,
            reason=reason,
            base_id=base_id,
            source_doc=source_doc,
            scope_confidence=scope_confidence_rung,
            original_actions=original_actions,
            recovered_actions=recovered_actions,
            original_specs=_no_structured_spec_detail(original_specs),
            recovered_specs=_no_structured_spec_detail(recovered_specs),
            raw_text=raw_text,
        ),
    )


def iter_no_document_change_ops(
    html_bytes: bytes,
    source_id: str,
    *,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
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
        return _iter_unstructured_no_change_groups(root, source_id, adjudications_out=adjudications_out)
    for doc_change in change_nodes:
        source_doc = doc_change.get("data-document", "").strip()
        base_id = normalize_lovdata_refid(source_doc)
        if not source_doc or base_id is None:
            _append_no_parse_adjudication(
                adjudications_out,
                kind="no_parse_document_change_base_unresolved",
                message="Norway parser skipped structured document-change with missing or unmappable base act.",
                source_id=source_id,
                detail=diagnostic_detail(
                    rule_id="no_parse_document_change_base_unresolved",
                    phase="parse",
                    family="source_pathology",
                    blocking=True,
                    reason="missing_data_document" if not source_doc else "unmappable_data_document",
                    source_doc=source_doc,
                ),
            )
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
            renumber_specs = _split_move_attr(
                change_el.get("data-move-part", ""),
                adjudications_out=adjudications_out,
                source_id=source_id,
                base_id=base_id,
                source_doc=source_doc,
                raw_text=raw_text,
            )
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
                    continue
                _append_no_parse_adjudication(
                    adjudications_out,
                    kind="no_parse_unresolved_structured_target_skipped",
                    message="Norway parser skipped structured target whose path could not be lowered.",
                    source_id=source_id,
                    detail=diagnostic_detail(
                        rule_id="no_parse_unresolved_structured_target_skipped",
                        phase="parse",
                        family="target_resolution_recovery",
                        blocking=True,
                        base_id=base_id,
                        source_doc=source_doc,
                        action=action,
                        raw_target=raw_target,
                        target_base=target_base or "",
                        raw_text=raw_text,
                    ),
                )

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
                        recovered_specs = [(StructuralAction(action), target) for target in inferred_targets]
                        _append_no_structured_parse_recovery_adjudications(
                            adjudications_out,
                            source_id=source_id,
                            base_id=base_id,
                            source_doc=source_doc,
                            raw_text=raw_text,
                            reason="cross_base_structured_target_recovered_from_lead",
                            scope_confidence=NOScopeConfidence(rung_id="inferred_from_payload"),
                            original_specs=parsed_specs,
                            recovered_specs=recovered_specs,
                        )
                        parsed_specs = recovered_specs
                        skipped_cross_base_specs = []

            for action, raw_target in skipped_cross_base_specs:
                _append_no_parse_adjudication(
                    adjudications_out,
                    kind="no_parse_cross_base_structured_target_skipped",
                    message="Norway parser skipped structured target for a different base act.",
                    source_id=source_id,
                    detail=diagnostic_detail(
                        rule_id="no_parse_cross_base_structured_target_skipped",
                        phase="parse",
                        family="source_pathology",
                        blocking=True,
                        base_id=base_id,
                        source_doc=source_doc,
                        action=action,
                        raw_target=raw_target,
                        target_base=normalize_lovdata_refid(raw_target) or "",
                        raw_text=raw_text,
                    ),
                )

            inferred_sentence_specs = _infer_same_base_sentence_target_specs_from_lead(lead_text)
            if inferred_sentence_specs:
                recovered_specs = list(inferred_sentence_specs)
                _append_no_structured_parse_recovery_adjudications(
                    adjudications_out,
                    source_id=source_id,
                    base_id=base_id,
                    source_doc=source_doc,
                    raw_text=raw_text,
                    reason="sentence_targets_inferred_from_lead",
                    scope_confidence=NOScopeConfidence(rung_id="explicit_source_with_context"),
                    original_specs=parsed_specs,
                    recovered_specs=recovered_specs,
                )
                parsed_specs = recovered_specs

            inferred_targets = _infer_same_base_subsection_targets(change_el)
            if (
                inferred_targets
                and len(parsed_specs) == 1
                and parsed_specs[0][1].leaf_kind() == "section"
                and parsed_specs[0][0] in {StructuralAction.INSERT, StructuralAction.REPLACE}
            ):
                recovered_specs = [(parsed_specs[0][0], target) for target in inferred_targets]
                _append_no_structured_parse_recovery_adjudications(
                    adjudications_out,
                    source_id=source_id,
                    base_id=base_id,
                    source_doc=source_doc,
                    raw_text=raw_text,
                    reason="section_target_expanded_to_subsections_from_payload",
                    scope_confidence=NOScopeConfidence(rung_id="inferred_from_payload"),
                    original_specs=parsed_specs,
                    recovered_specs=recovered_specs,
                )
                parsed_specs = recovered_specs

            if (
                not inferred_sentence_specs
                and " nytt " in f" {lead_text.lower()} "
                and all(target.leaf_kind() == "subsection" for _action, target in parsed_specs)
            ):
                recovered_specs = [(StructuralAction.INSERT, target) for _action, target in parsed_specs]
                _append_no_structured_parse_recovery_adjudications(
                    adjudications_out,
                    source_id=source_id,
                    base_id=base_id,
                    source_doc=source_doc,
                    raw_text=raw_text,
                    reason="new_subsection_lead_recovered_insert_action",
                    scope_confidence=NOScopeConfidence(rung_id="explicit_source_with_context"),
                    original_specs=parsed_specs,
                    recovered_specs=recovered_specs,
                )
                parsed_specs = recovered_specs

            inferred_sentence_targets = _infer_same_base_sentence_targets(change_el)
            if (
                inferred_sentence_targets
                and parsed_specs
                and all(target.leaf_kind() in {"section", "subsection"} for _action, target in parsed_specs)
                and len({target.path[0] for _action, target in parsed_specs}) == 1
            ):
                recovered_specs = [(StructuralAction.REPLACE, target) for target in inferred_sentence_targets]
                _append_no_structured_parse_recovery_adjudications(
                    adjudications_out,
                    source_id=source_id,
                    base_id=base_id,
                    source_doc=source_doc,
                    raw_text=raw_text,
                    reason="sentence_targets_inferred_from_payload",
                    scope_confidence=NOScopeConfidence(rung_id="inferred_from_payload"),
                    original_specs=parsed_specs,
                    recovered_specs=recovered_specs,
                )
                parsed_specs = recovered_specs

            payload_candidates = _extract_payload_candidates(
                change_el,
                [target for _action, target in parsed_specs],
            )

            for raw_target, raw_destination in renumber_specs:
                target_base = normalize_lovdata_refid(raw_target)
                dest_base = normalize_lovdata_refid(raw_destination)
                target_cross_base = target_base is not None and target_base != base_id
                destination_cross_base = dest_base is not None and dest_base != base_id
                if target_cross_base or destination_cross_base:
                    _append_no_parse_adjudication(
                        adjudications_out,
                        kind="no_parse_cross_base_structured_renumber_skipped",
                        message=(
                            "Norway parser skipped structured renumber whose source "
                            "or destination belongs to a different base act."
                        ),
                        source_id=source_id,
                        detail=diagnostic_detail(
                            rule_id="no_parse_cross_base_structured_renumber_skipped",
                            phase="parse",
                            family="source_pathology",
                            blocking=True,
                            base_id=base_id,
                            source_doc=source_doc,
                            raw_target=raw_target,
                            raw_destination=raw_destination,
                            target_base=target_base or "",
                            destination_base=dest_base or "",
                            target_cross_base=target_cross_base,
                            destination_cross_base=destination_cross_base,
                            raw_text=raw_text,
                        ),
                    )
                    continue
                target = lovdata_path_to_address(raw_target)
                destination = lovdata_path_to_address(raw_destination)
                if target is None or destination is None:
                    _append_no_parse_adjudication(
                        adjudications_out,
                        kind="no_parse_unresolved_structured_renumber_skipped",
                        message=(
                            "Norway parser skipped structured renumber whose source "
                            "or destination path could not be lowered."
                        ),
                        source_id=source_id,
                        detail=diagnostic_detail(
                            rule_id="no_parse_unresolved_structured_renumber_skipped",
                            phase="parse",
                            family="target_resolution_recovery",
                            blocking=True,
                            base_id=base_id,
                            source_doc=source_doc,
                            raw_target=raw_target,
                            raw_destination=raw_destination,
                            target_resolved=target is not None,
                            destination_resolved=destination is not None,
                            raw_text=raw_text,
                        ),
                    )
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
                        witness_rule_id="no_section_renumber_relabel",
                    )
                )
                sequence += 1

            for action, target in parsed_specs:
                payload = payload_candidates.get((target.leaf_kind(), target.leaf_label()))
                if payload is None:
                    payload = _heading_only_section_payload(change_el, action, target)
                if payload is None:
                    payload = _fallback_payload(change_el, action, target)
                if payload is not None and _no_action_value(action) in ("repeal", "text_repeal"):
                    # The payload-candidate map is consulted for every action with
                    # no filter, so a structured REPEAL/TEXT_REPEAL can pick up a
                    # synthesised structural payload.  (The ``_*_payload`` fallbacks
                    # are already repeal-gated to None.)  Repeals never carry
                    # content, so we coerce the payload to None here to keep the
                    # repeal-payload=None invariant structurally enforced and record
                    # the dropped payload kind/label so the closed hole stays
                    # auditable.
                    dropped_kind = _no_kind_value(payload.kind)
                    dropped_label = payload.label or ""
                    _append_no_parse_adjudication(
                        adjudications_out,
                        kind="no_repeal_payload_dropped",
                        message=(
                            "Norway repeal/text_repeal lowering carried a synthesised "
                            f"structural payload (kind={dropped_kind!r}, "
                            f"label={dropped_label!r}); repeals never carry content, "
                            "so lowering coerced the payload to None."
                        ),
                        source_id=source_id,
                        detail=diagnostic_detail(
                            rule_id="no_repeal_payload_dropped",
                            phase="parse",
                            family="payload_normalization",
                            blocking=False,
                            quirks_disposition=QuirksDisposition.APPLY,
                            base_id=base_id,
                            source_doc=source_doc,
                            action=_no_action_value(action),
                            target=str(target),
                            dropped_payload_kind=dropped_kind,
                            dropped_payload_label=dropped_label,
                            raw_text=raw_text,
                        ),
                    )
                    payload = None
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


def _no_sort_key(
    label: Optional[str],
    *,
    roman_single_letters: bool = False,
) -> tuple[int, str, int]:
    if not label:
        return (-1, "", 0)
    cased = _normalize_label(label)
    normalized = cased.lower()
    hyphen_match = re.match(r"^(\d+)-(\d+)([a-z]*)$", normalized)
    if hyphen_match:
        major, minor, suffix = hyphen_match.groups()
        return (int(major) * 10000 + int(minor), suffix, 0)
    letter_match = re.match(r"^(\d+)([a-z]*)$", normalized)
    if letter_match:
        number, suffix = letter_match.groups()
        return (int(number), suffix, 0)
    # Roman-numeral ordering is genuine for chapter/part labels (uppercase
    # ``I, II, ... IX``) and for multi-character lowercase roman sub-items
    # (``ii, iii, iv``). A *single* lowercase Latin letter is normally a
    # Norwegian litra (bokstav: ``a, b, c, d, e, ...``), NOT a roman numeral --
    # treating ``c`` as 100 or ``d`` as 500 breaks the alphabetic ordering of
    # litra lists and spuriously trips the replay order invariant on untouched
    # subtrees. The single exception is a sibling group that is itself a roman
    # sequence (``i, ii, ..., v, ..., ix``), where a lone ``v``/``x`` IS roman;
    # callers that know the sibling context pass ``roman_single_letters=True``.
    is_single_lowercase_letter = len(cased) == 1 and cased.islower() and cased.isalpha()
    if roman_single_letters or not is_single_lowercase_letter:
        roman = _roman_to_int(cased)
        if roman is not None:
            return (roman, "", 0)
    return tree_ops.default_label_sort_key(normalized)


def _no_sibling_group_uses_roman_single_letters(labels: Sequence[Optional[str]]) -> bool:
    """Decide whether an ordered same-kind sibling group is a roman sequence.

    Lone lowercase ``i``/``v``/``x``/``l``/``c``/``d``/``m`` are ambiguous: they
    are litra in an alphabetic bokstav list (``a, b, c, ...``) but roman in a
    roman list (``i, ii, iii, iv, v, ...``). The deciding signal is the sibling
    set: a roman list contains a multi-character lowercase roman label
    (``ii``/``iii``/...) and never contains a non-roman litra letter
    (``a``/``b``/``f``/``g``/``h``/``j``/``k``/...).
    """
    norm = [(_normalize_label(label).lower()) for label in labels if label]
    if not norm:
        return False
    has_multichar_roman = any(len(s) > 1 and re.fullmatch(r"[ivxlcdm]+", s) for s in norm)
    if not has_multichar_roman:
        return False
    # Any single letter outside the roman alphabet means this is a litra list.
    return all(re.fullmatch(r"[ivxlcdm]+", s) for s in norm)


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


def _materialize_no_sentence_children_with_count(body: IRNode, parent_path: tree_ops.Path) -> tuple[IRNode, int]:
    """Split raw subsection/item text into sentence children on demand."""
    parent = tree_ops.resolve(body, parent_path)
    if parent is None:
        return body, 0
    if _no_kind_value(parent.kind) not in {"subsection", "item"}:
        return body, 0
    if not parent.text or any(_no_kind_value(child.kind) == "sentence" for child in parent.children):
        return body, 0
    sentences = _split_no_sentences(parent.text)
    if not sentences:
        return body, 0
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
    return tree_ops.replace_at(body, parent_path, replacement), len(sentences)


def _materialize_no_sentence_children(body: IRNode, parent_path: tree_ops.Path) -> IRNode:
    materialized_body, _count = _materialize_no_sentence_children_with_count(body, parent_path)
    return materialized_body


def _resolve_shallow_no_sentence_path(
    body: IRNode,
    target: LegalAddress,
) -> tuple[IRNode, Optional[tree_ops.Path], Optional[tree_ops.Path], int]:
    """Resolve section-level sentence targets via a unique direct text container."""
    if len(target.path) != 2 or target.path[0][0] != "section" or target.path[1][0] != "sentence":
        return body, None, None, 0
    section_path = _resolve_no_path(body, LegalAddress(path=(target.path[0],)))
    if section_path is None:
        return body, None, None, 0
    section_node = tree_ops.resolve(body, section_path)
    if section_node is None:
        return body, None, None, 0
    hosts = [child for child in section_node.children if _no_kind_value(child.kind) in {"subsection", "item"}]
    if len(hosts) != 1:
        return body, None, None, 0
    host = hosts[0]
    host_path = section_path + ((str(host.kind), host.label or ""),)
    body, materialized_count = _materialize_no_sentence_children_with_count(body, host_path)
    if target.path[1][1] == "last":
        resolved = _find_last_direct_child_path(body, host_path, "sentence")
    else:
        resolved = _find_direct_child_path(body, host_path, "sentence", target.path[1][1])
    return body, resolved, host_path, materialized_count


def _resolve_shallow_no_sentence_host_path(
    body: IRNode,
    target: LegalAddress,
) -> tuple[IRNode, Optional[tree_ops.Path], int]:
    """Resolve the unique host path for section-level sentence targets."""
    if len(target.path) != 2 or target.path[0][0] != "section" or target.path[1][0] != "sentence":
        return body, None, 0
    section_path = _resolve_no_path(body, LegalAddress(path=(target.path[0],)))
    if section_path is None:
        return body, None, 0
    section_node = tree_ops.resolve(body, section_path)
    if section_node is None:
        return body, None, 0
    hosts = [child for child in section_node.children if _no_kind_value(child.kind) in {"subsection", "item"}]
    if len(hosts) != 1:
        return body, None, 0
    host = hosts[0]
    host_path = section_path + ((str(host.kind), host.label or ""),)
    body, materialized_count = _materialize_no_sentence_children_with_count(body, host_path)
    return body, host_path, materialized_count


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
    detail: Optional[dict[str, Any]] = None,
) -> None:
    """Append a Norway replay adjudication when a sink list is available."""
    if adjudications_out is None:
        return
    raw_detail = dict(detail or {})
    detail_rule_id = str(raw_detail.pop("rule_id", "") or "")
    detail_family = str(raw_detail.pop("family", "") or "")
    detail_reason = str(raw_detail.pop("reason", "") or "")
    detail_message = str(raw_detail.pop("message", "") or "")
    if kind in {"replay_unsupported_action", "replay_unresolved_target", "replay_noop"}:
        family = "unsupported_or_unresolved_action"
    elif kind in {
        "replay_tree_invariant_violation",
        "replay_tree_invariant_violation_downgraded",
    }:
        family = "tree_invariant_violation"
    elif kind.startswith("no_replay_"):
        family = "action_family_recovery"
    else:
        family = ""
    normalized_detail = diagnostic_detail(
        rule_id=detail_rule_id or kind,
        phase="replay",
        blocking=True,
        family=detail_family or family,
        reason=detail_reason,
        message=detail_message,
        detail=raw_detail,
    )
    adjudications_out.append(
        CompileAdjudication(
            kind=kind,
            message=message,
            source_statute=op.source.statute_id if op.source else "",
            op_id=op.op_id,
            blocking=True,
            phase="replay",
            detail=normalized_detail,
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


def _no_temporal_key(op: LegalOperation) -> tuple[str, str, str, int]:
    """NO's temporal/group sort key: ``(effective, enacted, source_id, sequence)``.

    The verbatim lift of ``apply_no_ops``'s old ``_group_sort_key``. The shared
    kernel's stage-1 temporal sort uses this; its first three components also
    serve as the structural-vacate group identity (see :func:`_no_group_key`).
    """
    effective = op.source.effective if op.source and op.source.effective else ""
    enacted = op.source.enacted if op.source and op.source.enacted else ""
    source_id = op.source.statute_id if op.source and op.source.statute_id else ""
    return (effective, enacted, source_id, op.sequence)


def _no_group_key(op: LegalOperation) -> tuple[str, str, str]:
    """NO's structural-vacate group identity: ``(effective, enacted, source_id)``.

    The verbatim lift of ``apply_no_ops``'s old ``_group_identity`` — the
    temporal key minus its ``sequence`` tail. The kernel partitions the
    temporally sorted ops by this so REPEAL-first / topological-RENUMBER /
    rest-by-sequence is applied per affecting-act moment.
    """
    effective = op.source.effective if op.source and op.source.effective else ""
    enacted = op.source.enacted if op.source and op.source.enacted else ""
    source_id = op.source.statute_id if op.source and op.source.statute_id else ""
    return (effective, enacted, source_id)


def _no_renumber_tiebreak_key(
    op: LegalOperation,
) -> tuple[int, tuple[tuple[int, str, int], ...], int]:
    """NO's independent-renumber tiebreak: the verbatim old ``_renumber_sort_key``.

    Orders genuinely independent RENUMBER ops (no vacate dependency between
    them) inside the topological sort by ``(path-depth, label-sort-keys,
    sequence)``; the kernel's DFS enforces vacate-before-occupy regardless.
    """
    return (
        len(op.target.path),
        tuple(_no_sort_key(label) for _kind, label in op.target.path),
        op.sequence,
    )


def no_ordering_profile() -> OrderingProfile:
    """The NO jurisdiction ordering profile fed to the unified kernel.

    Wave 0 (``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.2 / §4): NO's
    ordering is the temporal group sort ``(effective, enacted, source_id,
    sequence)`` followed by the within-group structural-vacate order
    (REPEAL-first, then topological RENUMBER vacating destinations before
    occupying them, then the rest by sequence), with same-moment cross-act
    detection delegated to the shared ``cross_act_same_moment`` detector. The
    profile encodes exactly that prior contract so ``order_ops`` reproduces the
    old group-sort + ``_ordered_renumber_group`` + direct-detector path
    byte-for-byte:

    - ``finder_kind_prefix="no"`` — the prefix the direct detector call used.
    - ``incompatible_payload_predicate=None`` — the detector's *default*
      conservative predicate (NO carries no jurisdiction-specific predicate).
    - ``temporal_key=_no_temporal_key`` — ``(effective, enacted, source_id,
      sequence)``, the old ``_group_sort_key``.
    - ``lex_posterior=False`` (implicit) — NO had no affecting-act lexical
      tiebreak; the within-group order is the structural-vacate stage.
    - no ``precedence_claims`` — NO has no validated precedence-rule registry.
    - ``renumber_vacate=True`` with ``renumber_group_key=_no_group_key`` and
      ``renumber_tiebreak_key=_no_renumber_tiebreak_key`` — the shared lift of
      NO's ``_ordered_renumber_group`` group fold (kernel §3.2 step 5).
    """
    return OrderingProfile(
        finder_kind_prefix="no",
        temporal_key=_no_temporal_key,
        renumber_vacate=True,
        renumber_group_key=_no_group_key,
        renumber_tiebreak_key=_no_renumber_tiebreak_key,
    )


# ── EV-05 execution-authorization: NO proof minting + resolver ────────────────
#
# The genuine authority for a NO state-mutating op is its AFFECTING ACT — the
# source document/act whose change-instructions (johtolause / endringslov lead)
# directed the change. NO lowers every op from a real amendment source and
# stamps that source's id onto ``op.source.statute_id`` (NO's ``source_id``: the
# act directing the change, distinct from the ``base_act:`` target it amends).
# ``_mint_no_execution_authorization`` projects that known authority into a typed
# :class:`ExecutionAuthorization` proof; the NO resolver
# (:func:`_no_execution_authorization`) prefers a proof already minted onto the
# op's carrier and otherwise mints one HERE from the op's source identity, so NO
# need not re-stamp every upstream op-construction site (byte-identity-safe). An
# op with NO affecting-act identity (``op.source`` is ``None`` / blank
# ``statute_id``) has UNKNOWN authority — no proof is fabricated, so the EV-05
# observe gate fires honestly on it (the real unauthorized residue).

#: The NO execution-authorization rule family stamped into a minted proof's
#: ``authorization_rule_id``. The actual rule_id appends the affecting act id, so
#: the proof points at the concrete authorizing act (``no_affecting_act:<statute>``).
_NO_EXECUTION_AUTHORIZATION_RULE = "no_affecting_act_authorizes_apply"


def _mint_no_execution_authorization(
    op: LegalOperation,
) -> Optional[ExecutionAuthorization]:
    """Mint a typed ``ExecutionAuthorization`` from a NO op's affecting-act identity.

    The authority a NO op carries is its source affecting act: the act whose
    change-instructions directed this change is what authorizes the apply. When
    the op carries a real ``op.source.statute_id`` (the affecting act id), that is
    a GENUINELY KNOWN authority, so we mint a replay-authorized proof whose
    ``authorization_rule_id`` names the concrete act
    (``no_affecting_act:<statute_id>``) and whose ``detail`` records the witness
    rule + scope-confidence rung (read-as-witness only — §2.10). When the op
    carries no affecting-act identity (no ``source`` / blank ``statute_id``), the
    authority is UNKNOWN: we return ``None`` and never fabricate a proof, so the
    EV-05 gate honestly witnesses that op as unauthorized.

    The proof is replay-authorized (``executable``/``replay_authorized`` both
    ``True``) because the affecting act IS the apply authority for NO's replay
    lane — NO's apply is the act executing its own directed changes. This is the
    honest NO footing, not a blanket pass: the gate still fires on every op whose
    authorizing act is not identified.
    """
    source = op.source
    statute_id = (source.statute_id if source is not None else "") or ""
    if not statute_id:
        return None
    rung = ""
    scope_confidence = op.scope_confidence
    if scope_confidence is not None:
        rung = getattr(scope_confidence, "rung_id", "") or ""
    return ExecutionAuthorization(
        executable=True,
        replay_authorized=True,
        authorization_status="replay_authorized",
        authorization_rule_id=f"no_affecting_act:{statute_id}",
        owner_phase="apply",
        strict_disposition="record",
        quirks_disposition=QuirksDisposition.RECORD,
        safe_default="execute_only_after_affecting_act_identity_is_known",
        required_proofs=(),
        forbidden_shortcuts=(
            "treat_op_existence_as_replay_authority_without_affecting_act",
        ),
        detail={
            "rule_family": _NO_EXECUTION_AUTHORIZATION_RULE,
            "affecting_act": statute_id,
            "witness_rule_id": op.witness_rule_id or "",
            "scope_confidence_rung": rung,
            "owner": "norway/grafter:_mint_no_execution_authorization",
        },
    )


def _no_execution_authorization(
    op: LegalOperation,
) -> Optional[ExecutionAuthorization]:
    """NO ``authorization_resolver``: read a minted proof, else mint from source.

    Prefers an ``ExecutionAuthorization`` already minted onto the op's
    ``execution_authorization`` carrier (the generic
    ``core/apply_seam.read_op_execution_authorization`` path); if the op carries
    none, mints one from its affecting-act identity via
    :func:`_mint_no_execution_authorization`. Returns ``None`` only when the op's
    authority is genuinely unknown (no affecting act) — the honest EV-05 residue.
    """
    if op.execution_authorization is not None:
        return op.execution_authorization
    return _mint_no_execution_authorization(op)


# ── AM-01 provenance-acceptance: NO Parsed-vs-Recovered verdict ───────────────
#
# NO marks a RECOVERED (recognizer/fallback-guessed) op by carrying a typed
# ``NOScopeConfidence`` (``op.scope_confidence``) whose ``rung_id`` is an
# inferred/fallback §2.2 ladder value (AGENTS.md §2.2). A grammar-recognized
# (``Parsed``) op carries an explicit-source rung or no scope_confidence at all.
# The FI reference (``finland/op_provenance.admits``) admits only ``Parsed``
# under strict; a ``Recovered`` op is refused. NO mirrors that here WITHOUT
# importing ``finland/``: it reads its OWN typed ``op.scope_confidence`` carrier
# and computes the core-neutral ``OpAcceptance`` verdict the seam records.

#: Scope-confidence rungs that mark a RECOVERED (guessed/inferred) op — the
#: AGENTS.md §2.2 inferred/fallback ladder values. A Parsed op carries an
#: explicit rung (``explicit_source`` / ``explicit_source_with_context``) or no
#: scope_confidence carrier at all.
_NO_RECOVERED_SCOPE_CONFIDENCE_RUNGS: frozenset[str] = frozenset(
    {
        "inferred_from_group",
        "inferred_from_payload",
        "inferred_from_live_unique",
        "inferred_singleton_path",
        "fallback",
    }
)


def _no_op_provenance_acceptance(op: LegalOperation) -> Optional[OpAcceptance]:
    """NO ``provenance_resolver``: the core-neutral AM-01 acceptance verdict.

    Reads NO's OWN derivation signal — the typed ``NOScopeConfidence`` carried on
    ``op.scope_confidence`` (its ``rung_id``) — to classify the op as ``Parsed``
    (admitted) or ``Recovered`` (refused under strict), mirroring the FI reference
    (``admits``/``mode_for``: STRICT admits only ``Parsed``) without importing
    ``finland/``. A recovered op (an inferred/fallback rung) yields a NOT-admitted
    verdict under NO's ``strict`` acceptance mode → the AM-01 observe gate
    witnesses it. A parsed op (explicit rung / no scope_confidence carrier) yields
    an admitted verdict → silent. The seam merely records this decision; NO does
    not block on it (observe-first — the AM-01 block promotion is a future
    measure-then-flip step).
    """
    rung = ""
    scope_confidence = op.scope_confidence
    if scope_confidence is not None:
        rung = getattr(scope_confidence, "rung_id", "") or ""
    recovered = rung in _NO_RECOVERED_SCOPE_CONFIDENCE_RUNGS
    if recovered:
        return OpAcceptance(
            admitted=False,
            acceptance_mode="strict",
            provenance_kind="recovered",
            detail={
                "scope_confidence_rung": rung,
                "witness_rule_id": op.witness_rule_id or "",
                "owner": "norway/grafter:_no_op_provenance_acceptance",
            },
        )
    return OpAcceptance(
        admitted=True,
        acceptance_mode="strict",
        provenance_kind="parsed",
        detail={
            "scope_confidence_rung": rung,
            "owner": "norway/grafter:_no_op_provenance_acceptance",
        },
    )


def apply_no_ops(
    statute: IRStatute,
    ops: List[LegalOperation],
    adjudications_out: Optional[List[CompileAdjudication]] = None,
    strict_invariants: bool = True,
    strict_action_family: bool = False,
    strict_recovery: bool = False,
    seam_observations_out: Optional[list[Finding]] = None,
) -> IRStatute:
    """Apply a minimal structural Norway operation set to a statute tree.

    Architectural note:
    this function still carries some target completion / structural recovery
    debt that should move upward into elaboration. Replay should converge on an
    execution-only contract over fully resolved canonical operations.
    """
    # §1.7 same-moment cross-act conflict pre-pass (AGENTS.md §1.7).
    #
    # Runs BEFORE the apply fold to emit a blocking finding for incompatible
    # whole-target payloads from distinct affecting acts at the same
    # (effective_date, target) moment. The finding is ADDITIVE: apply order is
    # unchanged so non-ambiguous cases are byte-identical to the pre-detection
    # path; the finding surfaces the silent group-order pick so strict mode can
    # reject. The cross-act finding carries an empty op_id so the
    # conserved-wrapper partition (which keys per-op skips by op_id) is
    # unaffected.
    #
    # Routed through the shared module exactly as EE/UK do (B1: NO/SE wiring of
    # the §1.7 silent-last-wins risk). NO uses the shared module's *default*
    # conservative compatibility predicate (no jurisdiction-specific
    # re-implementation): NO ops carry StructuralAction enum actions, which the
    # default predicate classifies directly. NO has no validated precedence-rule
    # registry yet, so every detected conflict emits
    # ``resolution: "sequence_order_unproven"``.
    # Unified ordering kernel (Wave 0). ``order_ops`` composes the temporal
    # group sort ``(effective, enacted, source_id, sequence)``, the same-moment
    # cross-act conflict pre-pass (DELEGATED verbatim to the shared
    # ``detect_cross_act_same_moment_conflicts`` — the §1.7 finding, ADDITIVE and
    # carrying an empty op_id so the conserved-wrapper partition is unaffected),
    # and the structural-vacate stage (REPEAL-first, then topological RENUMBER
    # vacating destinations before they are occupied, then the rest by
    # sequence). The ordered op list and the findings are byte-identical to the
    # old group-sort + ``_ordered_renumber_group`` + direct-detector path —
    # proven by ``tests/test_no_order_ops_parallel_run.py``.
    ordered_result = order_ops(ops, no_ordering_profile())
    if adjudications_out is not None:
        adjudications_out.extend(ordered_result.findings)

    body = statute.body

    # Reconstruct the per-op ``renumber_sources`` carrier the apply fold below
    # consumes: the set of RENUMBER source paths within the op's affecting-act
    # group ``(effective, enacted, source_id)``. The kernel returns a flat
    # ordered op list, so derive each group's renumber-source set once and pair
    # it with every op of that group (the old fold paired the per-group set with
    # each op of the group identically).
    _no_renumber_sources_by_group: dict[tuple[str, str, str], set[tuple[tuple[str, str], ...]]] = {}
    for op in ordered_result.ops:
        if op.action is StructuralAction.RENUMBER and op.destination is not None:
            _no_renumber_sources_by_group.setdefault(_no_group_key(op), set()).add(
                op.target.path
            )
    ordered_ops: list[tuple[LegalOperation, set[tuple[tuple[str, str], ...]]]] = [
        (op, _no_renumber_sources_by_group.get(_no_group_key(op), set()))
        for op in ordered_result.ops
    ]

    no_replay_tree_invariant_families = CORE_REPLAY_DELTA_MINIMAL_FAMILIES

    def _resolve_invariant_parent(path: tree_ops.InvariantPath) -> Optional[IRNode]:
        """Walk ``body`` along an invariant path (root step is the body itself)."""
        node: Optional[IRNode] = body
        for kind, label in path[1:]:
            if node is None:
                return None
            node = next(
                (
                    child
                    for child in node.children
                    if _no_kind_value(child.kind) == kind and (child.label or None) == label
                ),
                None,
            )
        return node

    def _sort_order_violation_is_spurious(violation: tree_ops.TreeInvariantViolation) -> bool:
        """True if a roman sibling group is actually ordered under roman semantics.

        The context-free ``_no_sort_key`` treats a lone lowercase ``i``/``v``/``x``
        as litra, which is correct for bokstav lists but wrong inside a roman
        sub-item sequence (``i, ii, ..., v, ..., ix``). Re-check the offending
        sibling group with sibling context before flagging it.
        """
        if violation.kind != "sort_order":
            return False
        parent = _resolve_invariant_parent(violation.path)
        if parent is None:
            return False
        labels = [
            child.label
            for child in parent.children
            if _no_kind_value(child.kind) == violation.child_kind and child.label
        ]
        if not _no_sibling_group_uses_roman_single_letters(labels):
            return False
        keys = [_no_sort_key(label, roman_single_letters=True) for label in labels]
        return keys == sorted(keys)

    def _assert_no_invariant_violations(op: LegalOperation) -> None:
        all_violations = tuple(
            tree_ops.iter_tree_invariant_violations(
                body,
                sort_key=_no_sort_key,
                families=no_replay_tree_invariant_families,
            )
        )
        typed_violations = tuple(
            violation
            for violation in all_violations
            if not _sort_order_violation_is_spurious(violation)
        )
        # Witness-required-for-downgrade: a sort_order violation dropped from the
        # blocking set as "spurious" must leave an attributable witness, never
        # vanish silently. Record the downgrade (the roman-semantics re-check is
        # its justification) so a future regression in the spurious predicate is
        # auditable rather than invisible.
        spurious_downgrades = tuple(
            violation
            for violation in all_violations
            if violation.kind == "sort_order"
            and _sort_order_violation_is_spurious(violation)
        )
        if spurious_downgrades:
            _append_no_replay_adjudication(
                adjudications_out,
                kind="replay_tree_invariant_violation_downgraded",
                message="Norway sort_order invariant violation downgraded as spurious.",
                op=op,
                detail={
                    "action": _no_action_value(op.action),
                    "target": str(op.target),
                    "nonblocking_reclassification_rule_id": (
                        "no_sort_order_spurious_roman_single_letter_recheck"
                    ),
                    "reclassification_reason": (
                        "The flagged sibling group is correctly ordered under "
                        "roman-numeral semantics (i, ii, ..., v, ...); the "
                        "context-free litra sort key mis-flagged it."
                    ),
                    "downgraded_violations": tuple(
                        violation.to_dict() for violation in spurious_downgrades
                    ),
                },
            )
        violations = tuple(violation.message for violation in typed_violations)
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
                "invariant_violations": tuple(violation.to_dict() for violation in typed_violations),
            },
        )
        if not strict_invariants:
            return
        source_id = op.source.statute_id if op.source else ""
        raise ValueError(
            f"Norway replay invariant violation after {op.action} {op.target.path!r} "
            f"from {source_id or '<unknown>'}: {joined}"
        )

    # §2.9 per-op carrier: when a recovery lane INTENTIONALLY retargets the write
    # to a node outside the op's nominal storage boundary (e.g. a missing-target
    # REPLACE recovered by INSERT at a resolved parent / body root), the recovered
    # write parent path is appended here so the per-op mutation-boundary probe can
    # declare it as an authorized ``declared_recovery`` boundary extension. Reset
    # per op below; stays empty (and is ignored) when the probe is off.
    _no_declared_recovery_paths: list[tree_ops.Path] = []

    def _record_action_family_recovery(
        *,
        kind: str,
        message: str,
        op: LegalOperation,
        detail: dict[str, str],
        recovered_path: Optional[tree_ops.Path] = None,
    ) -> None:
        _append_no_replay_adjudication(
            adjudications_out,
            kind=kind,
            message=message,
            op=op,
            detail=detail,
        )
        if recovered_path is not None:
            _no_declared_recovery_paths.append(tuple(recovered_path))
        if not strict_action_family:
            return
        source_id = op.source.statute_id if op.source else ""
        raise ValueError(
            f"Norway replay action-family recovery {kind} after {op.action} "
            f"{op.target.path!r} from {source_id or '<unknown>'}"
        )

    def _record_lineage_recovery(
        *,
        kind: str,
        message: str,
        op: LegalOperation,
        detail: dict[str, str | bool],
    ) -> None:
        _append_no_replay_adjudication(
            adjudications_out,
            kind=kind,
            message=message,
            op=op,
            detail=detail,
        )
        if not strict_recovery:
            return
        source_id = op.source.statute_id if op.source else ""
        raise ValueError(
            f"Norway replay recovery {kind} after {op.action} "
            f"{op.target.path!r} from {source_id or '<unknown>'}"
        )

    def _record_structural_recovery(
        *,
        kind: str,
        message: str,
        op: LegalOperation,
        detail: dict[str, Any],
    ) -> None:
        _append_no_replay_adjudication(
            adjudications_out,
            kind=kind,
            message=message,
            op=op,
            detail=detail,
        )
        if not strict_recovery:
            return
        source_id = op.source.statute_id if op.source else ""
        raise ValueError(
            f"Norway replay recovery {kind} after {op.action} "
            f"{op.target.path!r} from {source_id or '<unknown>'}"
        )

    # §2.9 per-op mutation-boundary observation: the seam (``core/apply_seam
    # .apply_op``) is the UNIVERSAL always-on LS-01 observer — it runs the core
    # ``audit_op_mutation_boundary`` on every landed write and routes the witness
    # to ``AppliedOp.observations`` (``boundary_mode="off"``). The retired in-fold
    # probe is gone; ``apply_no_ops`` now DRAINS that observation into the same
    # env-gated ``no_replay_mutation_boundary_per_op_violation_observed``
    # adjudication in the seam loop below (default-off → byte-stable bench output).
    # Per-op ``renumber_sources`` carrier the materializer reads. The seam loop
    # sets it before each ``apply_op`` call (the seam materializer signature is
    # ``(state, op)``; ``renumber_sources`` travels via this closure slot rather
    # than a second argument so the universal seam interface stays op-only).
    _no_active_renumber_sources: set[tuple[tuple[str, str], ...]] = set()

    # ── NO materializer (Wave 1, design §3.1/§3.5). ──────────────────────────
    # The per-op tree dispatch — NO's REPLACE/INSERT/REPEAL/RENUMBER/text_replace
    # apply with its inline sentence-materialization, container-chain and
    # occupied-target recovery transforms — IS the NO :class:`Materializer`. The
    # dispatch body below is the verbatim prior inline fold body (no re-indent):
    # it mutates the closure ``body`` seeded from the seam-supplied
    # ``before_body``, and every prior ``continue`` is now a bare ``return``
    # (control-flow only; the landed/skipped signal is derived by the caller
    # from ``body is not before_body``). The closures it captures (the recovery
    # recorders,
    # ``_assert_no_invariant_violations``, ``adjudications_out``) are unchanged,
    # so NO's three strict flags still raise IN PLACE exactly as before — the
    # "strictness = profile policy" mapping (design §2.1 #3) is realized by those
    # raises propagating through ``apply_op`` to the caller.
    def _no_materialize_one(
        before_body: IRNode, op: LegalOperation
    ) -> MaterializeResult[IRNode]:
        nonlocal body
        body = before_body
        renumber_sources = _no_active_renumber_sources
        # Reset the per-op declared-recovery carrier so a recovery retarget from a
        # prior op never leaks into this op's boundary.
        _no_declared_recovery_paths.clear()

        def _dispatch() -> None:
            """Run one op's tree dispatch (mutating the closure ``body``).

            Verbatim lift of the prior inline per-op fold body. Each prior
            ``continue`` (a skip / early-applied path) is now a bare ``return``,
            and the natural fall-through end (a REPLACE/REPEAL/INSERT/RENUMBER
            landed) also ``return``s. Whether the op landed a write is derived by
            the caller from ``body is not before_body`` (the persistent-CoW
            identity test) — so the dispatch itself needs no return value; the
            ``continue`` → ``return`` rewrite is purely control-flow, leaving the
            mutation semantics byte-identical.
            """
            nonlocal body
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
                    return
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
                    return
                if not op.target.path:
                    body = _apply_no_text_replace(body, text_match, text_replacement)
                    _assert_no_invariant_violations(op)
                    return
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
                    return
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
                    return
                body = tree_ops.replace_at(
                    body,
                    resolved_path,
                    _apply_no_text_replace(node, text_match, text_replacement),
                )
                _assert_no_invariant_violations(op)
                return
            if not op.target.path:
                _append_no_replay_adjudication(
                    adjudications_out,
                    kind="replay_noop",
                    message="Norway replay skipped operation with missing target path.",
                    op=op,
                    detail={"action": _no_action_value(op.action)},
                )
                _assert_no_invariant_violations(op)
                return
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
                return
            if op.target.leaf_kind() == "sentence" and op.target.parent() is not None:
                parent_path = _resolve_no_path(body, cast(LegalAddress, op.target.parent()))
                if parent_path is not None:
                    body, materialized_count = _materialize_no_sentence_children_with_count(body, parent_path)
                    if materialized_count:
                        _record_structural_recovery(
                            kind="no_replay_sentence_children_materialized",
                            message=(
                                "Norway replay materialized sentence children from parent text "
                                "before applying a sentence-level operation."
                            ),
                            op=op,
                            detail={
                                "rule_id": "no_sentence_text_materialized_for_sentence_target",
                                "family": "ontology_normalization",
                                "target": str(op.target),
                                "materialized_parent_path": _no_path_label(parent_path),
                                "materialized_sentence_count": materialized_count,
                            },
                        )
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
                body, resolved_path, shallow_host_path, materialized_count = _resolve_shallow_no_sentence_path(
                    body,
                    op.target,
                )
                if shallow_host_path is not None and materialized_count:
                    _record_structural_recovery(
                        kind="no_replay_sentence_children_materialized",
                        message=(
                            "Norway replay materialized sentence children from a unique shallow "
                            "sentence host before applying a sentence-level operation."
                        ),
                        op=op,
                        detail={
                            "rule_id": "no_sentence_text_materialized_for_shallow_sentence_target",
                            "family": "ontology_normalization",
                            "target": str(op.target),
                            "materialized_parent_path": _no_path_label(shallow_host_path),
                            "materialized_sentence_count": materialized_count,
                        },
                    )
                if resolved_path is not None and shallow_host_path is not None:
                    _record_structural_recovery(
                        kind="no_replay_shallow_sentence_target_rebound",
                        message=(
                            "Norway replay resolved a section-level sentence target through "
                            "the section's unique direct sentence host."
                        ),
                        op=op,
                        detail={
                            "rule_id": "no_shallow_sentence_target_rebound_to_unique_host",
                            "family": "target_resolution_recovery",
                            "target": str(op.target),
                            "resolved_path": _no_path_label(resolved_path),
                            "host_path": _no_path_label(shallow_host_path),
                        },
                    )

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
                                recovered_path=resolved_parent,
                            )
                            body = tree_ops.insert_sorted(
                                body,
                                resolved_parent,
                                payload,
                                sort_key_fn=_no_sort_key,
                            )
                            _assert_no_invariant_violations(op)
                            return
                    if op.target.leaf_kind() == "sentence" and _no_kind_value(payload.kind) == "sentence":
                        body, shallow_host_path, materialized_count = _resolve_shallow_no_sentence_host_path(body, op.target)
                        if shallow_host_path is not None and materialized_count:
                            _record_structural_recovery(
                                kind="no_replay_sentence_children_materialized",
                                message=(
                                    "Norway replay materialized sentence children from a unique shallow "
                                    "sentence host before recovering replace as insert."
                                ),
                                op=op,
                                detail={
                                    "rule_id": "no_sentence_text_materialized_for_shallow_sentence_target",
                                    "family": "ontology_normalization",
                                    "target": str(op.target),
                                    "materialized_parent_path": _no_path_label(shallow_host_path),
                                    "materialized_sentence_count": materialized_count,
                                },
                            )
                        if shallow_host_path is not None:
                            _record_structural_recovery(
                                kind="no_replay_shallow_sentence_target_rebound",
                                message=(
                                    "Norway replay resolved a section-level sentence target through "
                                    "the section's unique direct sentence host."
                                ),
                                op=op,
                                detail={
                                    "rule_id": "no_shallow_sentence_target_rebound_to_unique_host",
                                    "family": "target_resolution_recovery",
                                    "target": str(op.target),
                                    "host_path": _no_path_label(shallow_host_path),
                                },
                            )
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
                                recovered_path=shallow_host_path,
                            )
                            body = tree_ops.insert_sorted(
                                body,
                                shallow_host_path,
                                payload,
                                sort_key_fn=_no_sort_key,
                            )
                            _assert_no_invariant_violations(op)
                            return
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
                                    recovered_path=resolved_parent,
                                )
                                body = tree_ops.insert_sorted(
                                    body,
                                    resolved_parent,
                                    append_payload,
                                    sort_key_fn=_no_sort_key,
                                )
                                _assert_no_invariant_violations(op)
                                return
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
                                    return
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
                        # θ: (REPLACE, target_absent) — the table declares NO
                        # recovers a missing-target section REPLACE by rewriting
                        # to INSERT (§2.3). The recovery rule_id + the rewritten
                        # action come from the table cell.
                        disposition = NO_TOTALIZATION_TABLE.lookup(
                            StructuralAction.REPLACE, FailureClass.TARGET_ABSENT
                        )
                        assert isinstance(disposition, Recover)
                        _record_action_family_recovery(
                            kind="no_replay_replace_recovered_by_insert",
                            message="Norway replay recovered missing-target replace by inserting a section.",
                            op=op,
                            detail={
                                "rule_id": disposition.rule_id,
                                "original_action": _no_action_value(op.action),
                                "executed_action": _no_action_value(
                                    disposition.rewritten_action
                                ),
                                "target": str(op.target),
                                "insert_parent_path": _no_path_label(parent_path),
                                **_no_replay_payload_detail(payload),
                            },
                            recovered_path=parent_path,
                        )
                        body = tree_ops.insert_sorted(
                            body,
                            parent_path,
                            payload,
                            sort_key_fn=_no_sort_key,
                        )
                        _assert_no_invariant_violations(op)
                        return
                    _append_no_replay_adjudication(
                        adjudications_out,
                        kind="replay_unresolved_target",
                        message="Norway replay skipped operation: target not found.",
                        op=op,
                        detail={"action": _no_action_value(op.action), "target": str(op.target)},
                    )
                    _assert_no_invariant_violations(op)
                    return

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
                    return
                body = tree_ops.replace_at(body, resolved_path, payload)

            elif op.action is StructuralAction.REPEAL:
                if resolved_path is None:
                    # θ: (REPEAL, target_absent) — the table is the source of the
                    # off-domain disposition (§2.3). NO declares this a strict
                    # Reject; the grafter reads the code from the table cell.
                    disposition = NO_TOTALIZATION_TABLE.lookup(
                        StructuralAction.REPEAL, FailureClass.TARGET_ABSENT
                    )
                    assert isinstance(disposition, Reject)
                    _append_no_replay_adjudication(
                        adjudications_out,
                        kind=disposition.code,
                        message="Norway replay skipped operation: target not found.",
                        op=op,
                        detail={"action": _no_action_value(op.action), "target": str(op.target)},
                    )
                    _assert_no_invariant_violations(op)
                    return
                body = tree_ops.remove_at(body, resolved_path)

            elif op.action is StructuralAction.INSERT and op.payload is not None:
                payload = op.payload
                if resolved_path is not None:
                    # θ: (INSERT, target_occupied) — the table declares NO
                    # recovers by rewriting to REPLACE (§2.3). The rule_id the
                    # WriteReceipt/adjudication cites comes from the table cell.
                    disposition = NO_TOTALIZATION_TABLE.lookup(
                        StructuralAction.INSERT, FailureClass.TARGET_OCCUPIED
                    )
                    assert isinstance(disposition, Recover)
                    _record_action_family_recovery(
                        kind="no_replay_insert_occupied_target_replaced",
                        message="Norway replay recovered insert into an occupied target by replacing that target.",
                        op=op,
                        detail={
                            "rule_id": disposition.rule_id,
                            "original_action": "insert",
                            "executed_action": _no_action_value(
                                disposition.rewritten_action
                            ),
                            "target": str(op.target),
                            "resolved_path": _no_path_label(resolved_path),
                            **_no_replay_payload_detail(payload),
                        },
                    )
                    body = tree_ops.replace_at(body, resolved_path, payload)
                    _assert_no_invariant_violations(op)
                    return
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
                            return
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
                    return
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
                    return
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
                    return
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
                    occupied_destination = tree_ops.resolve(body, destination_path)
                    # θ: (RENUMBER, dest_occupied) — the table declares NO
                    # recovers by removing the occupant and proceeding with the
                    # RENUMBER (§2.3). The recovery rule_id comes from the table
                    # cell (the rewritten action is RENUMBER itself).
                    disposition = NO_TOTALIZATION_TABLE.lookup(
                        StructuralAction.RENUMBER, FailureClass.DEST_OCCUPIED
                    )
                    assert isinstance(disposition, Recover)
                    _record_lineage_recovery(
                        kind="no_replay_renumber_occupied_destination_removed",
                        message=(
                            "Norway replay cleared an occupied renumber destination "
                            "that was not itself moved by the same renumber group."
                        ),
                        op=op,
                        detail={
                            "rule_id": disposition.rule_id,
                            "family": "migration_or_lineage_recovery",
                            "source_path": _no_path_label(resolved_path),
                            "destination_path": _no_path_label(destination_path),
                            "destination_target": _no_address_detail(op.destination),
                            "removed_kind": _no_kind_value(occupied_destination.kind)
                            if occupied_destination is not None
                            else "",
                            "removed_label": occupied_destination.label
                            if occupied_destination is not None and occupied_destination.label is not None
                            else "",
                            "destination_was_renumber_source": False,
                        },
                    )
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
            # Natural fall-through: a REPLACE/REPEAL/INSERT/RENUMBER landed.
            return

        # §2.9 per-op mutation-boundary observation: the in-fold env-probe is
        # RETIRED. The seam's always-on observer (``core/apply_seam.apply_op``)
        # runs the IDENTICAL core ``audit_op_mutation_boundary`` on the landed
        # write and routes the witness to ``AppliedOp.observations``; the seam
        # loop below drains that observation into the same env-gated
        # ``no_replay_mutation_boundary_per_op_violation_observed`` adjudication
        # (carrying these ``declared_recovery_prefixes`` via the
        # ``MaterializeResult`` below, so the recovery-aware verdict is identical).
        _dispatch()
        applied = body is not before_body
        return MaterializeResult(
            new_state=body,
            applied=applied,
            declared_recovery_prefixes=tuple(_no_declared_recovery_paths),
        )

    # ── NO apply profile (Wave 1, design §3.1). ──────────────────────────────
    # ``boundary_mode="off"``: the seam's always-on observer is the SINGLE LS-01
    # producer; the in-fold probe is retired and the seam loop below drains the
    # observation into the env-gated ``no_replay_mutation_boundary_per_op_*``
    # adjudication, so the env-flag-ON output is byte-identical to the
    # pre-cutover fold. ``emit_receipts``/``emit_coverage`` are False in the bare
    # fold: the additive per-op receipt + coverage lanes are produced by the
    # dedicated ``no_replay_write_receipts`` / ``apply_no_ops_conserved`` callers,
    # so the bare ``apply_no_ops`` result stays byte-identical (no new artifacts)
    # — the equality gate is confined to the materialized IRStatute +
    # adjudications. ``renumber_migration_rule_ids`` names the migration that
    # explains a RENUMBER's bound→landed relabel divergence when receipts ARE
    # requested (the additive lane).
    # ── NO EV-05 authorization resolver + AM-01 provenance resolver (this task).
    # ``authorization_resolver`` mints/reads a real ``ExecutionAuthorization``
    # proof from each op's affecting-act identity (``_no_execution_authorization``)
    # so the EV-05 observe gate goes QUIET for every op whose authorizing act is
    # known and fires only on the genuinely unauthorized residue (the firewall
    # hole drops from ~100% to the real unauthorized fraction). ``provenance_
    # resolver`` hands the seam NO's core-neutral Parsed-vs-Recovered acceptance
    # verdict (``_no_op_provenance_acceptance``), read from NO's typed
    # ``op.scope_confidence`` carrier, so the AM-01 gate measures NO's
    # Recovered-vs-Parsed op population. BOTH are OBSERVE-only: their witnesses
    # route to ``AppliedOp.observations`` (never production ``findings``), so NO's
    # materialized statute + adjudications stay byte-identical. NO is NOT flipped
    # to block on either gate — that is a future measure-then-promote step.
    no_apply_profile: ApplyProfile[IRNode] = ApplyProfile(
        jurisdiction="no",
        materializer=_no_materialize_one,
        boundary_mode="off",
        emit_receipts=False,
        emit_coverage=False,
        renumber_migration_rule_ids=("no_section_renumber_relabel",),
        authorization_resolver=_no_execution_authorization,
        provenance_resolver=_no_op_provenance_acceptance,
    )

    # ── Seam loop (design §3.1): order_ops already ran; apply each op through
    # the unified per-op kernel. The NO materializer carries the substantive
    # dispatch; the seam owns the (here-disabled) receipt/coverage outputs and
    # the boundary gate. ``_no_active_renumber_sources`` is set per op so the
    # materializer reads the right renumber-source set (the seam interface is
    # op-only). ──────────────────────────────────────────────────────────────
    for op, renumber_sources in ordered_ops:
        _no_active_renumber_sources = renumber_sources
        pre_op_body = body
        applied_result: AppliedOp[IRNode] = apply_op(
            body,
            op,
            provenance=op.source,
            profile=no_apply_profile,
            source_statute=statute.statute_id,
        )
        body = applied_result.new_state

        # ── I1-strong conservation: derive the applied signal from the CONTENT
        # footprint, not object identity (#186, mirroring EE #185). ────────────
        # ``applied_result.applied`` is the seam's OBJECT-IDENTITY signal — the NO
        # materializer derives it from ``body is not before_body``. NO's tree_ops
        # (``replace_at`` / ``insert_sorted``) rebuild the targeted subtree on
        # every landed REPLACE / text_replace, so a REPLACE (or text_replace)
        # whose payload equals the live text returns a FRESH-but-content-equal
        # node: object identity reports ``applied=True`` for a write that landed
        # NOTHING. The op was then counted ACCEPTED-without-write (the conserved
        # partition keys on the enumerated skip kinds, and NO's ``replay_noop`` was
        # only emitted for the missing-target-path case, never for a content-equal
        # no-op) — the exact conservation leak AGENTS.md §1.8 / the
        # universal-algebra I1 strong form ("accepted ⟺ op landed a write")
        # forbids.
        #
        # The ground-truth footprint is the identity-pruned content diff — the
        # SAME signal NO's ``no_replay_write_receipts`` already uses to decide
        # whether a write receipt exists (empty diff ⇒ no receipt ⇒ no write).
        # Object identity is a NECESSARY precondition (no fresh object ⇒ definitely
        # no write); a fresh object counts as a write ONLY when the content
        # actually differs. A genuine landed write always has a non-empty diff, so
        # this is byte-identical for every op that truly mutated; it only
        # reclassifies the false-positive content-identical no-ops, which now emit
        # ``replay_noop`` and land REJECTED in the conserved partition.
        changed = applied_result.applied and bool(
            diff_ir_paths_identity_pruned(pre_op_body, body)
        )
        if applied_result.applied and not changed:
            # θ: content_identical — the op resolved and applied but landed no
            # content write. The table declares this the I1-strong NoopIdempotent
            # conservation cell (§2.3); the grafter detects content_identical (the
            # identity-pruned empty diff above) and reads the no-op code from the
            # table. NO's no-op disposition is uniform across the resolving
            # actions (REPLACE / text_replace), so the canonical REPLACE cell is
            # the source of the code.
            disposition = NO_TOTALIZATION_TABLE.lookup(
                StructuralAction.REPLACE, FailureClass.CONTENT_IDENTICAL
            )
            assert isinstance(disposition, NoopIdempotent)
            _append_no_replay_adjudication(
                adjudications_out,
                kind=disposition.code,
                message="Norway replay emitted a content-identical no-op for operation.",
                op=op,
                detail={"action": _no_action_value(op.action), "target": str(op.target)},
            )

        # ── B-enforcement (LS-01): drain the seam's OBSERVE lane. ─────────────
        # The universal apply seam runs the always-on per-op mutation-boundary
        # audit on every landed write (``boundary_mode="off"`` routes the
        # ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` escape witness to
        # ``AppliedOp.observations``, NEVER to ``findings`` — production output
        # stays byte-identical). When a caller asks for the observations
        # (``seam_observations_out`` provided — the corpus boundary-cleanliness
        # MEASUREMENT and the block-mode promotion decision read it), they are
        # appended verbatim. Default ``None`` is a pure no-op: production replay
        # never allocates or reads the lane, so byte-identity is unconditional.
        if seam_observations_out is not None and applied_result.observations:
            seam_observations_out.extend(applied_result.observations)

        # ── B-enforcement (LS-01 cleanup): drain the seam's boundary observation
        # into the env-gated NO adjudication (the retired in-fold probe's surface).
        # When ``LAWVM_NO_MUTATION_BOUNDARY_PER_OP=1`` and ``adjudications_out`` is
        # supplied, project the seam's ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP``
        # observation — produced by the IDENTICAL core ``audit_op_mutation_boundary``
        # the probe consumed, carrying the same ``declared_recovery_prefixes`` from
        # the ``MaterializeResult`` — into the ``no_replay_mutation_boundary_per_op_
        # violation_observed`` ``CompileAdjudication`` the in-fold probe used to emit.
        # Default (env-off or ``adjudications_out is None``) is a pure no-op →
        # byte-identical production.
        _no_drain_seam_boundary_observations(
            applied_result.observations,
            adjudications_out=adjudications_out,
            source_statute=statute.statute_id,
            op_id=op.op_id,
        )

    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=body,
        supplements=statute.supplements,
        metadata=dict(statute.metadata),
    )


# ---------------------------------------------------------------------------
# Typed apply-result carrier (AGENTS.md §1.8 — replay conservation contract).
#
# The classic ``apply_no_ops`` returns only the mutated :class:`IRStatute` and
# shuttles skipped-op evidence through an ``adjudications_out`` out-parameter.
# The AGENTS.md §1.8 contract requires the apply path to return accepted AND
# rejected carriers (``FilterResult`` shape) so a downstream consumer cannot
# silently lose track of filtered ops. ``apply_no_ops_conserved`` is the typed
# wrapper that mirrors ``apply_no_ops``'s behaviour and surfaces both lanes
# via the contract-shape FilterResult[LegalOperation]. Production routing to
# the conserved wrapper is a separate per-frontend decision (AGENTS.md §1.8);
# this wrapper is added WITHOUT touching callers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NOApplyResult:
    """Typed apply-result conservation carrier (AGENTS.md §1.8).

    Mirrors the FilterResult contract shape: every op in the input set is
    either in ``applied_ops`` (its binding landed in the output statute) or
    surfaces as a :class:`RejectedItem[LegalOperation]` witness in
    ``skipped_items`` with a ``reason`` / ``reason_code`` and ``blocking``
    disposition. The mutation footprint (the IRStatute returned by
    :func:`apply_no_ops`) is the ``statute`` field.

    The ``filter_result`` field is the canonical ``FilterResult`` projection
    of the same accepted/rejected partition, so callers that already consume
    the shared core type can reuse it without unpacking ``applied_ops`` /
    ``skipped_items`` separately.

    Recovery adjudications (the ``no_replay_*`` family — e.g.
    ``no_replay_replace_recovered_by_insert``) are emitted by the bare variant
    when an op is APPLIED via a named recovery transformation (REPLACE
    recovered to INSERT, INSERT into an occupied slot recovered to REPLACE,
    etc.), NOT when it is skipped. They are therefore intentionally NOT in
    :data:`_NO_SKIP_ADJUDICATION_KINDS`; only the genuine per-op skip kinds
    (``replay_unsupported_action`` / ``replay_unresolved_target`` /
    ``replay_noop``) mark an op as rejected. The post-apply
    ``replay_tree_invariant_violation*`` records are emitted AFTER an op was
    applied (or raised in strict mode before the conserved wrapper returns),
    so they are also NOT in the skip set.

    The optional ``write_receipts`` field carries per-op landed-write receipts
    (AGENTS.md §2.3 + notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md §4) when
    the conserved wrapper is invoked with ``emit_receipts=True``. Default
    ``()`` so receipt-free callers (existing tests + the cheaper apply fold)
    pay no per-op snapshot overhead. Production lanes (NO replay's
    ``replay_no_to_pit``) request receipts so the §4 mutation-boundary
    contract is auditable downstream — without this, a guard that exists but
    is unreachable from production is the §2.9 worst-case silent failure.
    Mirrors the SE precedent at ``sweden/grafter.py:3800``.
    """

    statute: IRStatute
    filter_result: "FilterResult[LegalOperation]"
    write_receipts: tuple["WriteReceipt", ...] = ()

    @property
    def applied_ops(self) -> tuple["LegalOperation", ...]:
        return self.filter_result.accepted_items

    @property
    def skipped_items(self) -> tuple["RejectedItem[LegalOperation]", ...]:
        return self.filter_result.rejected_items


# Per-op skip adjudication kinds emitted by :func:`apply_no_ops`. Each is
# emitted ONLY at a per-op skip path that emits an adjudication and then
# ``continue``s (the op is NOT applied). Recovery adjudications
# (``no_replay_*``) and post-apply violation records
# (``replay_tree_invariant_violation*``) are intentionally excluded: those are
# emitted when an op WAS applied (with a recovery transformation or a
# downstream-invariant finding) rather than skipped.
_NO_SKIP_ADJUDICATION_KINDS = frozenset(
    {
        "replay_unsupported_action",
        "replay_unresolved_target",
        "replay_noop",
    }
)


def apply_no_ops_conserved(
    statute: IRStatute,
    ops: List[LegalOperation] | Tuple["LegalOperation", ...],
    *,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
    strict_invariants: bool = True,
    strict_action_family: bool = False,
    strict_recovery: bool = False,
    emit_receipts: bool = False,
) -> NOApplyResult:
    """Apply a Norway op set with a typed conservation receipt (§1.8).

    Mirrors :func:`apply_no_ops` exactly (same replay semantics, same
    ``adjudications_out`` side channel — when the caller passes one, both the
    conserved typed result AND the existing descriptive adjudications are
    populated). Returns a :class:`NOApplyResult` whose ``filter_result``
    partitions every input op into accepted (its replay applied) or rejected
    (its replay skipped, with a witness adjudication carrying the reason).
    The contract is monotone: every input op ends up either accepted or
    rejected, never silently dropped.

    The partition keys on ``op_id`` (the NO bare variant emits one
    ``CompileAdjudication`` per skipped op carrying that op's ``op_id``). An
    op is rejected iff its ``op_id`` appears in a per-op SKIP adjudication
    (``replay_unsupported_action`` / ``replay_unresolved_target`` /
    ``replay_noop``). Recovery adjudications (``no_replay_*``) and post-apply
    invariant records (``replay_tree_invariant_violation*``) do NOT mark an op
    as rejected — those are emitted when the op WAS applied (with a recovery or
    downstream violation), not when it was skipped. Empty or duplicate
    ``op_id`` values would mis-partition and are rejected with a
    ``ValueError`` rather than silently dropping or mis-bucketing an op.

    When ``emit_receipts=True`` is passed, per-op landed-write receipts
    (§2.3 + notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md §4) are also
    produced via :func:`no_replay_write_receipts` and surfaced on
    :attr:`NOApplyResult.write_receipts`. Each receipt records the landed
    footprint (created/replaced/removed/renumbered paths) plus pre/post
    structural subtree hashes for the covering region. Production lanes
    (NO replay's ``replay_no_to_pit``) pass ``emit_receipts=True`` so the
    §4 mutation-boundary contract is auditable downstream — without this,
    a guard that exists but is unreachable from production is the §2.9
    worst-case silent failure (the bug that previously read: conserved
    wrapper bypassed by production ``apply_no_ops`` call site). Mirrors the
    SE precedent at ``sweden/grafter.py:3811``.
    """
    ops_list = list(ops)
    # Conservation requires a robust op IDENTITY for the accepted/rejected
    # partition. The op_id string is NOT a safe identity key: it defaults to
    # "" (a SKIPPED op with an empty op_id would be filtered out of the
    # skipped set and silently land in the accepted lane — a §1.8
    # "never silently dropped" violation) and it is not guaranteed unique
    # (a duplicate/shared op_id mis-partitions both ops). Fail loud on either
    # degenerate case so the op_id-keyed partition below is provably bijective.
    op_ids = [op.op_id for op in ops_list]
    if any(not op_id for op_id in op_ids):
        empty_positions = [i for i, op_id in enumerate(op_ids) if not op_id]
        raise ValueError(
            "apply_no_ops_conserved requires every op to carry a non-empty op_id "
            "(the conservation partition keys on op_id and an empty op_id would be "
            f"silently dropped from the skipped lane). Empty op_id at positions {empty_positions}."
        )
    if len(set(op_ids)) != len(op_ids):
        counts = Counter(op_ids)
        duplicates = sorted(op_id for op_id, n in counts.items() if n > 1)
        raise ValueError(
            "apply_no_ops_conserved requires op_ids to be unique (the conservation "
            "partition keys on op_id and duplicate op_ids would mis-partition). "
            f"Duplicate op_ids: {duplicates}."
        )
    # Trust the bare-apply contract: ``apply_no_ops`` appends each per-op
    # adjudication to ``adjudications_out`` in place. Routing the caller's
    # list directly through bare apply means a mid-apply raise (the §1.10
    # fail-loud path under ``strict_action_family=True`` for the
    # NO insert-occupied-target recovery collision) preserves the recovery
    # adjudication witnesses emitted BEFORE the raise on the caller's
    # accumulator — the caller can then diagnose via the partial
    # adjudications (AGENTS.md §1.0 evidence is not silently destroyed).
    # When the caller did not pass an ``adjudications_out``, use a throwaway
    # local buffer so bare-apply's mutations stay scoped and the partition
    # below still has a source to read from.
    adjudications: List[CompileAdjudication] = (
        adjudications_out if adjudications_out is not None else []
    )
    applied_statute = apply_no_ops(
        statute,
        ops_list,
        adjudications_out=adjudications,
        strict_invariants=strict_invariants,
        strict_action_family=strict_action_family,
        strict_recovery=strict_recovery,
    )
    # Partition: an op is REJECTED iff its op_id appears on a per-op SKIP
    # adjudication. Recovery adjudications (no_replay_*) record transformations
    # that WERE applied (e.g. REPLACE recovered to INSERT) and must NOT mark
    # their op as rejected. See ``_NO_SKIP_ADJUDICATION_KINDS`` above.
    skipped_op_ids = {
        a.op_id
        for a in adjudications
        if a.op_id and a.kind in _NO_SKIP_ADJUDICATION_KINDS
    }
    accepted: list[LegalOperation] = []
    rejected: list[RejectedItem[LegalOperation]] = []
    for op in ops_list:
        if op.op_id in skipped_op_ids:
            matching = [a for a in adjudications if a.op_id == op.op_id and a.kind in _NO_SKIP_ADJUDICATION_KINDS]
            reason = matching[0].message if matching else "NO replay op skipped without a typed reason."
            reason_code = matching[0].kind if matching else "no_replay_skipped_unspecified"
            rejected.append(
                RejectedItem(
                    item=op,
                    reason=reason,
                    reason_code=reason_code,
                    blocking=False,
                )
            )
        else:
            accepted.append(op)
    # Propagation: bare apply already mutated ``adjudications_out`` in place
    # (the caller's list when one was provided) — no local-copy / clear /
    # extend round-trip needed. The previous local-copy-then-extend pattern
    # silently dropped bare-apply's partial adjudication witness when bare
    # apply raised mid-fold (the §1.0 evidence-loss failure mode that
    # ``test_replay_no_to_pit_strict_action_family_rejects_recovery``
    # surfaced — bare apply raised after emitting the recovery adjudication
    # witness, but the caller's ``adjudications_out`` stayed empty); routing
    # the caller's list directly closes that hole.
    write_receipts: tuple[WriteReceipt, ...] = ()
    if emit_receipts:
        # Re-apply one op at a time to snapshot before/after body trees for
        # per-op WriteReceipt construction (§2.3 receipt contract). The final
        # statute from this per-op apply matches ``applied_statute`` for NO's
        # REPLACE/INSERT/REPEAL/RENUMBER op families under the same caveat
        # SE documents at ``sweden/grafter.py:3811``: the per-op fold is
        # order-preserving for these action families assuming the replay fold
        # does not branch on multi-op invariants. NO's renumber-group
        # ordering (``_ordered_renumber_group``) is recomputed per single-op
        # call — for a single renumber op there is no intra-group ordering
        # to interlock, so the per-op receipt is still a faithful record of
        # what landed for that op. The per-op fold is the same algorithm
        # :func:`no_replay_write_receipts` runs; routing it through the
        # conserved wrapper here makes the receipt lane reachable from
        # production (the §2.9 fix). Mirrors SE at ``sweden/grafter.py:3903``.
        _, write_receipts = no_replay_write_receipts(statute, ops_list)
    return NOApplyResult(
        statute=applied_statute,
        filter_result=FilterResult(
            accepted_items=tuple(accepted),
            rejected_items=tuple(rejected),
        ),
        write_receipts=write_receipts,
    )


# ---------------------------------------------------------------------------
# Per-op WriteReceipt emission (AGENTS.md §2.3 — receipt contract, second step).
#
# Mirrors the SE helper at ``sweden/grafter.py:4035``–``sweden/grafter.py:4220``.
# An opt-in wrapper around ``apply_no_ops`` that applies ops one at a time,
# snapshots the before/after body trees, and synthesizes a ``WriteReceipt`` per
# *applied* op (skipped ops emit no receipt — the conserved FilterResult's
# rejected_items lane carries the witness instead). The receipt carries the
# full §2.3 contract shape:
#   - op_id / helper / action / bound_target_path / landed_primary_path
#   - categorized mutation footprint (created/replaced/removed/renumbered)
#   - pre/post structural subtree hashes for the covering region
#   - migration_rule_ids=("no_section_renumber_relabel",) for RENUMBER ops
#     (the §1.6 unstated-migration invariant's identity-migration owner —
#     mirrors SE's ``("se_renumber_relabel",)`` at sweden/grafter.py:4157)
# ---------------------------------------------------------------------------


def _no_legal_path_to_tree_path(addr: LegalAddress) -> TreePath:
    """Coerce a LegalAddress path into the core TreePath shape.

    ``LegalAddress.path`` is a tuple of ``(kind, label | None)`` pairs; the
    core ``TreePath`` shape requires ``str`` labels (empty string for the
    root or None labels). Mirrors ``sweden/grafter.py:4035``.
    """
    return tuple((str(kind), str(label or "")) for kind, label in addr.path)


def _no_emit_one_op_receipt(
    before_body: IRNode,
    after_body: IRNode,
    op: LegalOperation,
) -> WriteReceipt | None:
    """Emit a :class:`WriteReceipt` for one op's apply, or ``None`` when skipped.

    Mirrors ``sweden/grafter.py::_se_emit_one_op_receipt`` (line 4046). The
    receipt synthesizes the typed §2.3 contract fields from the actual
    before/after IR tree diff (computed via core's identity-pruned diff) and
    the op's declared target. The mutation footprint is categorized by
    ``op.action.value`` — REPLACE/text-replace → ``replaced_paths``; INSERT →
    ``created_paths``; REPEAL → ``removed_paths``; RENUMBER → ``renumbered_paths``
    sourced from ``op.target.path`` and ``op.destination.path``.

    Pre/post hashes are taken at the landed primary path's covering region
    using :func:`structural_subtree_hash` (the canonical recipe from
    CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.2). For REPEAL the pre hash is
    the section-body subtree hash that existed before; the post hash is ``""``
    (the hash of an absent subtree).

    Per §4 of the apply-resolution/receipt contract
    (notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md), a divergence between
    ``bound_target_path`` (from) and ``landed_primary_path`` (to) MUST be
    explained by a named migration rule. The RENUMBER branch sets the bound
    to the source label and the landed to the destination label — they
    diverge by construction (a relabel IS the migration). The named rule
    ``no_section_renumber_relabel`` (registered in spec_ledger_no_catalog.py)
    explains that divergence so the receipt audits as ``qualified`` (not
    ``violation``) in ``build_observed_write_audit`` and
    ``WriteReceipt.divergence_explained`` returns True. Without it, the NO
    RENUMBER receipt is a §1.6 unstated-migration violation that strict mode
    must reject. This mirrors SE's exact shape at sweden/grafter.py:4155–4157
    (``se_renumber_relabel``).
    """
    changed = diff_ir_paths_identity_pruned(before_body, after_body)
    if not changed:
        # The op was filtered/skipped (the apply path emitted an adjudication).
        # No receipt — the conserved FilterResult's rejected_items lane will
        # carry the witness instead.
        return None

    action_value = op.action.value if op.action else "unknown"
    leaf_kind = op.target.leaf_kind() or "unknown"
    helper = f"apply_no_ops::{action_value}::{leaf_kind}"
    bound_target_path = _no_legal_path_to_tree_path(op.target)

    # Landed primary path: for INSERT, REPEAL and REPLACE/text_replace, audit at
    # the targeted legal address (bound == landed semantically; divergence is
    # only meaningful for RENUMBER, where the landed path is the destination).
    # SE uses ``changed[0]`` for REPLACE because its sections are top-level
    # children of body, so ``changed[0]`` equals ``bound_target_path`` for SE.
    # For NO where sections are typically nested under chapters, ``changed[0]``
    # is the deep tree path (e.g. ``chapter:kap1/section:2``) and the strict
    # bound != landed[0] divergence is a tree-nesting artifact, not a semantic
    # divergence. Source the landed primary path from ``bound_target_path``
    # for these action families — mirroring the same reasoning SE applies to
    # INSERT/REPEAL at sweden/grafter.py:4087–4104. The pre/post hashes still
    # resolve recursively via :func:`tree_ops.find` (below) so they audit at
    # the actual tree position. Mirrors sweden/grafter.py:4087–4104.
    if action_value in {"insert", "repeal", "replace", "text_replace"}:
        landed_primary_path: TreePath | None = bound_target_path or None
    elif action_value == "renumber":
        # RENUMBER removes the source section and re-inserts it under the
        # destination label — both are parent children-list changes, so the
        # identity-pruned diff reports the body-level change as a single
        # empty-path tuple ``((),)`` rather than any surviving coordinate.
        # Mirror the INSERT/REPEAL empty-diff handling: the section LANDED at
        # the destination, so point the receipt (and its pre/post hash) at the
        # destination path. Using ``changed[0]`` here would yield the empty
        # path ``()`` (a non-coordinate), which is falsy and would silently
        # blank the pre/post hashes — a malformed receipt.
        landed_destination_path = (
            _no_legal_path_to_tree_path(op.destination) if op.destination is not None else None
        )
        landed_primary_path = landed_destination_path or None
    else:
        landed_primary_path = changed[0] if changed else None

    created_paths: TreePaths = ()
    replaced_paths: TreePaths = ()
    removed_paths: TreePaths = ()
    renumbered_paths: tuple[tuple[TreePath, TreePath], ...] = ()

    # Same reasoning as landed_primary_path above: INSERT/REPEAL categorize
    # via the declared bound_target_path (the targeted section is the one
    # that was created/removed), not the diff's body-level change pair.
    # Mirrors sweden/grafter.py:4114–4138.
    if action_value in {"replace", "text_replace"}:
        replaced_paths = changed
    elif action_value == "insert":
        created_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "repeal":
        removed_paths = (bound_target_path,) if bound_target_path else ()
    elif action_value == "renumber":
        if op.destination is not None:
            destination_path = _no_legal_path_to_tree_path(op.destination)
            # The RENUMBER footprint is (from_path, to_path). The from_path
            # comes from the op's declared target; the to_path from the
            # destination. Both cover the section node's identity relabel
            # (the from_path is removed; the to_path is created with the
            # source's subtree content).
            renumbered_paths = ((bound_target_path, destination_path),)
        # Do NOT fold ``changed`` into replaced_paths here. A RENUMBER is a
        # parent children-list change (source removed, destination inserted),
        # so the identity-pruned diff reports it as a single empty-path tuple
        # ``((),)`` rather than any surviving coordinate. Assigning
        # ``replaced_paths = changed`` would put the bogus empty path ``()``
        # into the receipt footprint (a non-coordinate). The meaningful
        # RENUMBER footprint is the typed (from, to) pair carried by
        # ``renumbered_paths`` above — mirroring how INSERT/REPEAL source
        # their footprint from the declared bound target, not the body-level
        # diff pair.

    # Per §4 of the apply-resolution/receipt contract, the bound→landed
    # divergence on a RENUMBER is the typed named migration for a section
    # relabel/renumber — ``no_section_renumber_relabel`` is the rule id that
    # owns the divergence (mirrors SE's ``se_renumber_relabel`` at line
    # 4157). Without this stamp, the receipt audits as ``violation`` in
    # ``build_observed_write_audit`` and ``WriteReceipt.divergence_explained``
    # returns False (a §1.6 unstated-migration violation that strict mode
    # must reject). For non-RENUMBER actions, no migration rule applies —
    # bound==landed for REPLACE/INSERT/REPEAL, so divergence_explained is
    # True via the equality short-circuit without a named rule.
    migration_rule_ids: tuple[str, ...] = ()
    if action_value == "renumber" and op.destination is not None:
        migration_rule_ids = ("no_section_renumber_relabel",)

    # pre/post hashes at the covering region of the landed primary path.
    # For REPEAL, the landed path's post node is absent -> post_hash is "".
    #
    # NO sections typically live nested under a chapter/container (unlike SE
    # where sections are top-level children of body), so the single-segment
    # ``landed_primary_path`` from ``op.destination.path`` may not directly
    # resolve against ``before_body`` / ``after_body`` via
    # :func:`tree_ops.resolve` (which walks a strict path). The recursive
    # :func:`tree_ops.find` fallback — mirroring how ``_resolve_no_path``
    # resolves targets in :func:`apply_no_ops` — finds the section at any
    # depth when the direct resolve misses (the production-lane case where
    # §2 lives under ``chapter:kap1`` rather than directly on ``body``).
    pre_hashes: dict[str, str] = {}
    post_hashes: dict[str, str] = {}
    if landed_primary_path:
        key = receipt_address_string(landed_primary_path)
        before_node = tree_ops.resolve(before_body, list(landed_primary_path))
        if before_node is None and len(landed_primary_path) == 1:
            kind, label = landed_primary_path[0]
            if label:
                find_path = tree_ops.find(before_body, str(kind), str(label))
                if find_path is not None:
                    before_node = tree_ops.resolve(before_body, list(find_path))
        after_node = tree_ops.resolve(after_body, list(landed_primary_path))
        if after_node is None and len(landed_primary_path) == 1:
            kind, label = landed_primary_path[0]
            if label:
                find_path = tree_ops.find(after_body, str(kind), str(label))
                if find_path is not None:
                    after_node = tree_ops.resolve(after_body, list(find_path))
        pre_hashes[key] = structural_subtree_hash(before_node) if before_node is not None else ""
        post_hashes[key] = structural_subtree_hash(after_node) if after_node is not None else ""

    return WriteReceipt(
        op_id=op.op_id or "",
        helper=helper,
        action=action_value,
        bound_target_path=bound_target_path,
        landed_primary_path=landed_primary_path,
        created_paths=created_paths,
        replaced_paths=replaced_paths,
        removed_paths=removed_paths,
        renumbered_paths=renumbered_paths,
        migration_rule_ids=migration_rule_ids,
        pre_hashes=pre_hashes,
        post_hashes=post_hashes,
    )


def no_replay_write_receipts(
    statute: IRStatute,
    ops: list[LegalOperation] | tuple[LegalOperation, ...],
) -> tuple[IRStatute, tuple[WriteReceipt, ...]]:
    """Apply ops one at a time and emit per-op :class:`WriteReceipt` records (§2.3).

    Mirrors ``sweden/grafter.py::se_replay_write_receipts`` (line 4186). For
    each op, applies it via :func:`apply_no_ops` to a single-op list,
    snapshots the before/after body trees, and synthesizes a
    :class:`WriteReceipt` using core's identity-pruned diff +
    :func:`structural_subtree_hash`. Skipped ops (those that resulted in no
    tree change — the adjudication ledger recorded the skip) emit no receipt.

    The final statute matches the result of :func:`apply_no_ops` applied to
    the full op list (the per-op apply is associative and order-preserving
    for Norway's REPLACE/INSERT/REPEAL/RENUMBER op families, assuming the
    replay fold does not branch on multi-op invariants).

    Returns ``(final_statute, receipts_tuple)``. Consumers that want both the
    typed FilterResult conservation receipt (§1.8) AND per-op write receipts
    (§2.3) call this; callers that only need the apply fold itself keep using
    the cheaper :func:`apply_no_ops_conserved` with ``emit_receipts=False``.
    """
    current = statute
    receipts: list[WriteReceipt] = []
    for op in ops:
        adjudications: list[CompileAdjudication] = []
        next_statute = apply_no_ops(current, [op], adjudications_out=adjudications)
        if not adjudications:
            # Op applied — emit a receipt from the before/after body diff.
            receipt = _no_emit_one_op_receipt(current.body, next_statute.body, op)
            if receipt is not None:
                receipts.append(receipt)
        # If adjudications is non-empty, op was skipped — no receipt.
        current = next_statute
    return current, tuple(receipts)


def _no_record_archive_skip(
    rejected_items: list[RejectedItem[str]] | None,
    *,
    exc: ArchiveMemberTooLarge,
) -> None:
    """Append a typed ``RejectedItem`` receipt for an oversized archive member.

    Local twin of :func:`lawvm.norway.sources._no_record_archive_skip` (kept
    local to avoid a circular top-level import between ``norway.sources`` and
    ``norway.grafter``; mirrors the precedent at
    ``us_federal/import_plaw.py:63`` and ``tools/import_zip.py:95`` whose
    ``_record_import_skip`` helpers are local-per-module too). When
    ``rejected_items`` is ``None`` the prior structured stderr receipt via
    :func:`log_archive_member_too_large` is preserved so the skip stays
    greppable (the §1.8 minimum for destructuring consumers of
    ``open_lovdata_archive`` / ``open_lovdata_amendment_archive`` whose
    ``(id, bytes)`` yield shape cannot carry a typed rejection without
    breaking unpacking). When ``rejected_items`` is a list, a typed
    ``RejectedItem(item=member_name, reason=..., reason_code=..., blocking=False)``
    is appended instead — the §1.8 contract surface upstream tooling reads.

    Per AGENTS.md §1.10 the reason embeds the offending archive_path /
    member_name / declared_size / cap_bytes so triage does not have to
    re-run extraction to identify the rejected member (the companion
    ``ArchiveMemberTooLargeDiagnostic.render_reason`` in
    ``core/archive_safety.py`` omits these — this helper layers them on at
    the §1.8 receipt surface).
    """
    if rejected_items is None:
        log_archive_member_too_large(exc)
        return
    rejected_items.append(
        RejectedItem(
            item=exc.member_name,
            reason=(
                f"archive member {exc.member_name} from "
                f"{exc.archive_path or '<archive>'} declares "
                f"{exc.declared_size} bytes (cap {exc.cap_bytes}); "
                "refusing to materialise into memory. Raise "
                "LAWVM_MAX_ARCHIVE_MEMBER_BYTES to admit it, or trim "
                "the source archive."
            ),
            reason_code=NO_ARCHIVE_MEMBER_TOO_LARGE_REASON_CODE,
            blocking=False,
        )
    )


# §1.8 typed-receipt reason_code for archive members that declare more bytes
# than ``$LAWVM_MAX_ARCHIVE_MEMBER_BYTES``. Twin of
# :data:`lawvm.norway.sources.NO_ARCHIVE_MEMBER_TOO_LARGE_REASON_CODE` — kept
# local here to avoid a circular top-level import (sources.py imports from
# grafter at module load). Both must agree byte-for-byte.
NO_ARCHIVE_MEMBER_TOO_LARGE_REASON_CODE = "no_archive_member_too_large"


def open_lovdata_archive(
    tar_bz2_path: str,
    *,
    rejected_items: list[RejectedItem[str]] | None = None,
) -> Generator[Tuple[str, bytes], None, None]:
    """Yield ``(statute_id, bytes)`` pairs from a Lovdata public tarball.

    Members declaring more bytes than ``$LAWVM_MAX_ARCHIVE_MEMBER_BYTES`` are
    skipped. When ``rejected_items`` is threaded, a typed ``RejectedItem``
    receipt (``reason_code=no_archive_member_too_large``, ``blocking=False``)
    is appended so the §1.8 conservation lane inspects the skip in the
    accumulator surface (mirrors ``us_federal/import_plaw.py:212``). When no
    sink is threaded, the prior structured stderr receipt via
    :func:`log_archive_member_too_large` is preserved so the skip stays
    greppable — the destructuring consumer protocol
    (``for sid, html_bytes in open_lovdata_archive(...)``) is preserved either
    way (pattern B sink-threading: the union ``(id, bytes) | RejectedItem``
    yield would break the unpacking at out-of-scope call sites).
    """
    with tarfile.open(tar_bz2_path, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".xml"):
                continue
            statute_id = lovdata_filename_to_id(member.name)
            if statute_id is None:
                continue
            try:
                payload = safe_tar_read(
                    tf, member, archive_path=Path(tar_bz2_path).name
                )
            except ArchiveMemberTooLarge as exc:
                # §1.8 typed receipt (AGENTS.md §1.8) — see
                # :func:`_no_record_archive_skip`.
                _no_record_archive_skip(rejected_items, exc=exc)
                continue
            if payload is None:
                continue
            yield statute_id, payload


def open_lovdata_amendment_archive(
    tar_bz2_path: str,
    *,
    rejected_items: list[RejectedItem[str]] | None = None,
) -> Generator[Tuple[str, bytes], None, None]:
    """Yield ``(source_id, bytes)`` pairs from a Lovtidend tarball.

    Oversized members are skipped with a typed ``RejectedItem`` receipt when
    ``rejected_items`` is threaded (AGENTS.md §1.8) — see
    :func:`open_lovdata_archive`.
    """
    with tarfile.open(tar_bz2_path, "r:bz2") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(".xml"):
                continue
            source_id = lovdata_amendment_filename_to_id(member.name)
            if source_id is None:
                continue
            try:
                payload = safe_tar_read(
                    tf, member, archive_path=Path(tar_bz2_path).name
                )
            except ArchiveMemberTooLarge as exc:
                # §1.8 typed receipt (AGENTS.md §1.8) — see
                # :func:`_no_record_archive_skip`.
                _no_record_archive_skip(rejected_items, exc=exc)
                continue
            if payload is None:
                continue
            yield source_id, payload
