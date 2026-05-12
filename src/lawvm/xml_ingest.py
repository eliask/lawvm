"""Shared XML/AKN ingress helpers for LawVM IR construction."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, cast

import icontract
import lxml.etree as etree

from lawvm.core.frozen_values import FrozenDict
from lawvm.core.ir_helpers import irnode_to_text, kind_for_tag as _kind_for_tag
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.ir import IRNode, IRStatute


_TEXT_LEAF_TAGS = {
    IRNodeKind.CONTENT,
    IRNodeKind.INTRO,
    IRNodeKind.HEADING,
    IRNodeKind.NUM,
    IRNodeKind.P,
    IRNodeKind.I,
}
_STRUCTURAL_TAGS = {
    IRNodeKind.BODY,
    IRNodeKind.CHAPTER,
    IRNodeKind.PART,
    IRNodeKind.SECTION,
    IRNodeKind.SUBSECTION,
    IRNodeKind.PARAGRAPH,
    IRNodeKind.SUBPARAGRAPH,
    IRNodeKind.BLOCK,
    IRNodeKind.HCONTAINER,
    IRNodeKind.APPENDIX,
    IRNodeKind.SCHEDULE,
}

_TOP_LEVEL_SUPPLEMENT_TAGS = {
    "schedule",
    "appendix",
    "annex",
}

_SUPPORTED_TOP_LEVEL_SUPPLEMENT_KINDS = {
    IRNodeKind.APPENDIX,
    IRNodeKind.SCHEDULE,
}

_TABLE_TAG_TO_KIND: Dict[str, IRNodeKind] = {
    "tr": IRNodeKind.ROW,
    "td": IRNodeKind.CELL,
    "th": IRNodeKind.HEADER_CELL,
}


def _tag(el: etree._Element) -> str:
    return str(el.tag).split("}")[-1]


def _known_ir_kind(tag: str) -> IRNodeKind | None:
    return _kind_for_tag(tag)


def _norm_num(text: str) -> str:
    """Normalize a legal coordinate label (e.g. '12 a §' -> '12a')."""
    return re.sub(r"[)\s§]", "", text or "").strip().lower()


def _node_label(
    el: etree._Element,
    tag: str,
    label_postprocessor: Optional[Callable[[str, str], str]] = None,
) -> Optional[str]:
    """Extract the canonical label for a structural AKN element."""
    if tag == "hcontainer":
        return None
    num_el = el.find("{*}num")
    if num_el is not None:
        num_text = "".join(str(_t) for _t in num_el.itertext()).strip()
        if num_text:
            norm = _norm_num(num_text)
            if label_postprocessor is not None:
                norm = label_postprocessor(tag, norm)
            return norm
    return None


def _collapse_text(el: etree._Element) -> str:
    """Collect all text from an XML element, ensuring whitespace between sibling elements."""
    fragments: list[str] = []

    def _collect(elem: etree._Element) -> None:
        if elem.text:
            fragments.append(elem.text)
        children = list(elem)
        for i, child in enumerate(children):
            _collect(child)
            if child.tail:
                fragments.append(child.tail)
            elif i < len(children) - 1:
                fragments.append(" ")

    _collect(el)
    return " ".join("".join(fragments).split())


def xml_element_to_text(el: Any) -> str:
    """Extract text from an lxml element, consistent with irnode_to_text.

    This is the canonical oracle-side text extractor for comparison paths.
    It converts the element to an IRNode via ``xml_to_ir_node`` and then
    applies ``irnode_to_text``, ensuring that oracle text extraction produces
    identical output to replay IR text extraction for the same content.

    Use this function wherever oracle lxml elements are compared against replay
    IRNodes (diff, bench, evidence pipelines). Do NOT use it for metadata,
    titles, johtolause, or other non-comparison extractions — those callers
    have their own appropriate extraction patterns.

    Args:
        el: An lxml ``_Element`` representing an AKN section, subsection, or
            any other structural unit extracted from a Finlex oracle XML tree.

    Returns:
        Flat text string consistent with what ``irnode_to_text`` would produce
        if the same element were converted via ``xml_to_ir_node``.
    """
    ir = xml_to_ir_node(el)
    return irnode_to_text(ir)


_POSITIONAL_LABEL_KINDS = {IRNodeKind.SUBSECTION, IRNodeKind.PARAGRAPH}

# Finnish criminal-law sentencing formula that opens a loppukappale (closing
# paragraph after a numbered list).  A content-only paragraph that starts with
# this phrase must NOT be merged into the preceding numbered item — it is the
# main predicate of the entire Joka-clause and belongs as a separate wrapUp.
_SENTENCING_START_RE = re.compile(r"^\s*on tuomittava\b", re.I)


def _paragraph_has_num(node: IRNode) -> bool:
    return any(child.kind == IRNodeKind.NUM for child in node.children)


def _paragraph_is_content_only(node: IRNode) -> bool:
    return (
        node.kind == IRNodeKind.PARAGRAPH
        and not _paragraph_has_num(node)
        and all(child.kind == IRNodeKind.CONTENT for child in node.children)
    )


def _paragraph_last_text(node: IRNode) -> str:
    for child in reversed(node.children):
        text = irnode_to_text(child).strip()
        if text:
            return text
    return node.text.strip()


def _paragraph_ends_with_terminal_punctuation(node: IRNode) -> bool:
    tail = _paragraph_last_text(node)
    return bool(tail) and tail[-1] in ".;:"


def _paragraph_first_text(node: IRNode) -> str:
    """Return the first non-empty text segment of a paragraph's children."""
    for child in node.children:
        text = irnode_to_text(child).strip()
        if text:
            return text
    return node.text.strip() if node.text else ""


def _merge_split_numbered_paragraph_continuations(children: List[IRNode]) -> List[IRNode]:
    merged: List[IRNode] = []
    for child in children:
        if (
            merged
            and child.kind == IRNodeKind.PARAGRAPH
            and _paragraph_is_content_only(child)
            and not _paragraph_last_text(child).endswith(":")
            # Do not absorb a criminal-law sentencing loppukappale ("on tuomittava...")
            # into the preceding numbered item — it must remain as a trailing paragraph
            # so _hoist_trailing_wrapup_paragraph can promote it to wrapUp.
            and not _SENTENCING_START_RE.match(_paragraph_first_text(child))
            and merged[-1].kind == IRNodeKind.PARAGRAPH
            and _paragraph_has_num(merged[-1])
            and not _paragraph_ends_with_terminal_punctuation(merged[-1])
        ):
            prev = merged[-1]
            merged[-1] = IRNode(
                kind=prev.kind,
                label=prev.label,
                text=prev.text,
                attrs=prev.attrs,
                children=prev.children + child.children,
            )
            continue
        merged.append(child)
    return merged


def _rehome_orphaned_letter_paragraphs(children: List[IRNode]) -> List[IRNode]:
    """Heal Finlex source encoding errors where lettered subparagraph items are
    mis-encoded as paragraph siblings of the containing numbered paragraph.

    Pattern (e.g. 2025/1178 §2 mom.1): a numbered paragraph (1)) has
    subparagraph children a) and b), but items c) and d) are encoded as sibling
    paragraphs at the subsection level.  The letter sequence continuity is the
    key signal — if the next sibling paragraph has a single-letter label that
    immediately follows the last subparagraph of the preceding paragraph, it
    belongs inside that paragraph as an additional subparagraph.
    """
    rewritten: List[IRNode] = []
    for child in children:
        if (
            child.kind == IRNodeKind.PARAGRAPH
            and child.label is not None
            and len(child.label) == 1
            and child.label.isalpha()
            and rewritten
            and rewritten[-1].kind == IRNodeKind.PARAGRAPH
        ):
            prev = rewritten[-1]
            prev_subparas = [c for c in prev.children if c.kind == IRNodeKind.SUBPARAGRAPH]
            if prev_subparas:
                last_label = prev_subparas[-1].label or ""
                if (
                    len(last_label) == 1
                    and last_label.isalpha()
                    and ord(child.label) == ord(last_label) + 1
                ):
                    new_subpara = IRNode(
                        kind=IRNodeKind.SUBPARAGRAPH,
                        label=child.label,
                        text=child.text,
                        attrs=child.attrs,
                        children=child.children,
                    )
                    rewritten[-1] = IRNode(
                        kind=prev.kind,
                        label=prev.label,
                        text=prev.text,
                        attrs=prev.attrs,
                        children=prev.children + (new_subpara,),
                    )
                    continue
        rewritten.append(child)
    return rewritten


def _absorb_orphaned_subsections_into_preceding_section(
    children: List[IRNode],
) -> List[IRNode]:
    """Absorb orphaned subsection nodes into their preceding sibling section.

    A small number of finlex source XMLs have malformed structure where
    ``<subsection>`` elements appear as direct children of a body/chapter/
    hcontainer element, immediately following a ``<section>`` that was closed
    before its subsections (e.g. ``<section><num>6 §</num></section>`` followed
    by sibling ``<subsection>``).  Reparent them to the preceding section.
    Positional labels (1, 2, …) are assigned to unlabelled absorbed subsections
    continuing any that are already in the section.
    """
    result: List[IRNode] = []
    for child in children:
        if (
            child.kind == IRNodeKind.SUBSECTION
            and result
            and result[-1].kind == IRNodeKind.SECTION
        ):
            prev = result[-1]
            absorbed = child
            if absorbed.label is None:
                existing = sum(1 for c in prev.children if c.kind == IRNodeKind.SUBSECTION)
                absorbed = IRNode(
                    kind=absorbed.kind,
                    label=str(existing + 1),
                    text=absorbed.text,
                    attrs=absorbed.attrs,
                    children=absorbed.children,
                )
            result[-1] = IRNode(
                kind=prev.kind,
                label=prev.label,
                text=prev.text,
                attrs=prev.attrs,
                children=tuple(list(prev.children) + [absorbed]),
            )
        else:
            result.append(child)
    return result


def _split_trailing_content_only_paragraphs_into_subsections(children: List[IRNode]) -> List[IRNode]:
    """Split later-moment paragraphs out of list-shaped subsections at section level."""
    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue

        paragraphs = [c for c in child.children if c.kind == IRNodeKind.PARAGRAPH]
        if not paragraphs:
            rewritten.append(child)
            continue

        numbered_positions = [
            i for i, c in enumerate(child.children) if c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(c)
        ]
        if not numbered_positions:
            rewritten.append(child)
            continue

        last_numbered_idx = numbered_positions[-1]
        last_numbered_para = child.children[last_numbered_idx]
        trailing = child.children[last_numbered_idx + 1 :]
        if not trailing:
            rewritten.append(child)
            continue
        if not _paragraph_ends_with_terminal_punctuation(last_numbered_para):
            rewritten.append(child)
            continue
        if not all(_paragraph_is_content_only(node) for node in trailing):
            rewritten.append(child)
            continue

        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=child.children[: last_numbered_idx + 1],
            )
        )
        for trailing_para in trailing:
            rewritten.append(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=tuple(trailing_para.children),
                )
            )
    return rewritten


def _xml_table_to_ir(el: etree._Element) -> IRNode:
    rows: list[IRNode] = []
    for child in el:
        child_tag = _tag(child)
        if child_tag != "tr":
            continue
        cells: list[IRNode] = []
        for td in child:
            td_tag = _tag(td)
            ir_kind = _TABLE_TAG_TO_KIND.get(td_tag)
            if ir_kind is None:
                continue
            cell_text = _collapse_text(td).strip()
            cell_attrs: Dict[str, Any] = {}
            rowspan = td.get("rowspan")
            colspan = td.get("colspan")
            if rowspan and rowspan != "1":
                cell_attrs["rowspan"] = rowspan
            if colspan and colspan != "1":
                cell_attrs["colspan"] = colspan
            cells.append(IRNode(kind=ir_kind, text=cell_text, attrs=cell_attrs))
        if cells:
            rows.append(IRNode(kind=IRNodeKind.ROW, children=tuple(cells)))
    return IRNode(kind=IRNodeKind.TABLE, children=tuple(rows))


@icontract.require(lambda el: el is not None, "XML element must not be None")
@icontract.ensure(lambda result: result.kind, "resulting IRNode must have a kind")
def xml_to_ir_node(
    el: etree._Element,
    label_postprocessor: Optional[Callable[[str, str], str]] = None,
) -> IRNode:
    tag_str = _tag(el)
    tag = IRNodeKind(tag_str)
    label = _node_label(el, tag_str, label_postprocessor)
    attrs = {k.split("}")[-1]: v for k, v in el.attrib.items()}

    if tag == IRNodeKind.HCONTAINER and attrs.get("name") == "omission":
        return IRNode(kind=IRNodeKind.OMISSION, label=label, attrs=attrs)

    if tag in _TEXT_LEAF_TAGS:
        has_table = any(_tag(child) == "table" for child in el)
        if has_table:
            text_parts: list[str] = []
            table_children: list[IRNode] = []
            for child in el:
                if _tag(child) == "table":
                    table_children.append(_xml_table_to_ir(child))
                else:
                    ct = _collapse_text(child).strip()
                    if ct:
                        text_parts.append(ct)
            if el.text and el.text.strip():
                text_parts.insert(0, el.text.strip())
            return IRNode(
                kind=tag,
                label=label,
                text=" ".join(text_parts),
                attrs=FrozenDict(attrs),
                children=tuple(table_children),
            )
        return IRNode(kind=tag, label=label, text=_collapse_text(el), attrs=attrs)

    if tag == IRNodeKind.TABLE:
        return _xml_table_to_ir(el)

    children: list[IRNode] = []
    for child in el:
        child_tag = _tag(child)
        child_kind = _kind_for_tag(child_tag)
        if child_kind == IRNodeKind.TABLE:
            children.append(_xml_table_to_ir(child))
        elif child_kind in _STRUCTURAL_TAGS or child_kind in _TEXT_LEAF_TAGS:
            children.append(xml_to_ir_node(child, label_postprocessor))
        else:
            text = _collapse_text(child)
            if text:
                children.append(IRNode(kind=cast(IRNodeKind, child_kind or child_tag), text=text))

    if tag in _STRUCTURAL_TAGS:
        if tag in (IRNodeKind.BODY, IRNodeKind.CHAPTER, IRNodeKind.PART, IRNodeKind.HCONTAINER):
            children = _absorb_orphaned_subsections_into_preceding_section(children)
        if tag == IRNodeKind.SECTION:
            children = _split_trailing_content_only_paragraphs_into_subsections(children)
        counters: Dict[IRNodeKind, int] = {}
        for i, child in enumerate(children):
            if child.label is None and child.kind in _POSITIONAL_LABEL_KINDS:
                counters[child.kind] = counters.get(child.kind, 0) + 1
                children[i] = IRNode(
                    kind=child.kind,
                    label=str(counters[child.kind]),
                    text=child.text,
                    attrs=child.attrs,
                    children=child.children,
                )
        if tag == IRNodeKind.SUBSECTION:
            children = _merge_split_numbered_paragraph_continuations(children)
            children = _rehome_orphaned_letter_paragraphs(children)

    return IRNode(
        kind=tag,
        label=label,
        text="" if tag in _STRUCTURAL_TAGS else _collapse_text(el),
        attrs=FrozenDict(attrs),
        children=tuple(children),
    )


def xml_body_to_ir(
    tree: etree._Element,
    label_postprocessor: Optional[Callable[[str, str], str]] = None,
) -> IRStatute:
    num_el = tree.find(".//{*}docNumber")
    title_el = tree.find(".//{*}docTitle")
    body_el = tree.find(".//{*}body")
    statute_id = num_el.text.strip() if num_el is not None and num_el.text else "0/0"
    title = _collapse_text(title_el) if title_el is not None else "Unknown"
    supplements: list[IRNode] = []
    ingest_observations: list[dict[str, Any]] = []
    if body_el is not None and body_el is not tree:
        for child in tree:
            child_tag = _tag(child)
            if child_tag not in _TOP_LEVEL_SUPPLEMENT_TAGS:
                continue
            child_kind = _known_ir_kind(child_tag)
            if child_kind in _SUPPORTED_TOP_LEVEL_SUPPLEMENT_KINDS:
                supplements.append(xml_to_ir_node(child, label_postprocessor))
                continue
            ingest_observations.append(
                {
                    "kind": "XML_INGEST.UNSUPPORTED_TOP_LEVEL_SUPPLEMENT",
                    "family": "source_pathology",
                    "phase": "ingest",
                    "tag": child_tag,
                    "message": "Top-level supplement tag is not mapped to a supported IR supplement kind.",
                }
            )
    if body_el is None:
        body_node = xml_to_ir_node(tree, label_postprocessor)
    else:
        body_node = xml_to_ir_node(body_el, label_postprocessor)
    metadata: dict[str, Any] = {}
    if ingest_observations:
        metadata["xml_ingest_observations"] = ingest_observations
    return IRStatute(statute_id=statute_id, title=title, body=body_node, supplements=tuple(supplements), metadata=metadata)
