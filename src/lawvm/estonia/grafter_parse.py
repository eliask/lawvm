"""Estonia parse lane — XML / IR document → :class:`IRStatute`.

Mechanically split out of :mod:`lawvm.estonia.grafter` (pure packaging move;
no behavior change). Holds the tyviseadus (base act) XML parser
(:func:`parse_ee_statute`) and its supporting tree-construction helpers, plus
the shared inline-item / section-payload parse helpers used by the apply and
ops lanes. ``grafter`` re-imports every public name below so existing
``from lawvm.estonia.grafter import X`` call sites keep resolving unchanged.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, AbstractSet, List, Optional
import xml.etree.ElementTree as ET

if TYPE_CHECKING:
    XmlElement = ET.Element[str]
else:
    XmlElement = ET.Element

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.peg import (
    _EE_SUPERSCRIPT_DIGIT_CLASS,
    _normalize_num,
    parse_html_op_items,
)
from lawvm.estonia.target_resolution import (
    paragrahv_to_act_id as _tr_paragrahv_to_act_id,
)
from lawvm.estonia.text_morphology import (
    _ee_normalize_text_replace_surface,
)


def _try_parse_int(s: str) -> Optional[int]:
    """Parse a string as int, returning None if not purely numeric."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


_EE_ITEM_START_PATTERN = r"(?:(?<=^)|(?<=[\s;:]))(?P<label>\d[\d\s]*)\)\s"


def _parse_inline_item_children(
    raw_text: str,
    *,
    require_first_label_one: bool = True,
    ignored_marker_rules_out: list[str] | None = None,
) -> tuple[str, List[IRNode]]:
    """Split inline numbered item lists without breaking compound labels like ``8 1)``."""
    matches: list[re.Match[str]] = []
    segment_start = 0
    for match in re.finditer(_EE_ITEM_START_PATTERN, raw_text):
        prefix = raw_text[segment_start:match.start()]
        if prefix.rfind("(") > prefix.rfind(")"):
            if ignored_marker_rules_out is not None:
                ignored_marker_rules_out.append(_EE_INLINE_ITEM_PARENTHESES_MARKER_GUARD_RULE)
            continue
        matches.append(match)
        segment_start = match.end()
    if not matches:
        return _strip_rt_editorial_parentheticals(raw_text.strip()), []

    intro_text = _strip_rt_editorial_parentheticals(raw_text[: matches[0].start()].strip())
    item_children: List[IRNode] = []
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
        item_text = _strip_rt_editorial_parentheticals(raw_text[match.end() : next_start].strip())
        raw_label = re.sub(r"\s+", "_", match.group("label").strip()).rstrip("_")
        item_children.append(
            IRNode(
                kind=IRNodeKind.ITEM,
                label=raw_label,
                text=item_text,
            )
        )

    if require_first_label_one and item_children and item_children[0].label != "1":
        # Real inline item lists often follow a colon-introducer ("on muu hulgas:").
        # Without that cue, a lone citation suffix like "60–61)" is more likely.
        if not intro_text.endswith(":"):
            return _strip_rt_editorial_parentheticals(raw_text.strip()), []

    return intro_text, item_children


NS_BASE = "tyviseadus_1_10.02.2010"


def _ns(ns_str: str, tag: str) -> str:
    return f"{{{ns_str}}}{tag}"


def _find(el: XmlElement, ns_str: str, *tags: str) -> Optional[XmlElement]:
    """Traverse a sequence of namespace-qualified tags from el."""
    cur = el
    for tag in tags:
        cur = cur.find(_ns(ns_str, tag))
        if cur is None:
            return None
    return cur


def _text(el: Optional[XmlElement]) -> str:
    """Return stripped text content of an element, or empty string.

    Normalizes non-breaking spaces (\xa0) to regular spaces so that
    oracle text (which uses \xa0 in cross-references like "§-s\xa03")
    compares equal to replay text extracted from amendment payloads.
    """
    if el is None:
        return ""
    return (el.text or "").replace("\xa0", " ").strip()


def _title_text(el: Optional[XmlElement]) -> str:
    """Extract full section title text, including text inside inline child tags.

    <paragrahvPealkiri> elements often contain <sup>/<sub> children for
    superscript section numbers (e.g. §-s 93<sup>1</sup> sätestatud...).
    Plain _text() only reads el.text (before the first child), truncating
    the title.  This function collects el.text + each inline child's text +
    its tail, separated by a space, matching the style used by amendment
    payload extraction (which replaces <tags> with spaces).

    Normalizes \xa0 → space and collapses multiple spaces.
    """
    if el is None:
        return ""
    parts: List[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local in _INLINE_TAGS:
            child_text = "".join(str(_t) for _t in child.itertext())
            if child_text:
                parts.append(child_text)
            if child.tail:
                parts.append(child.tail)
        # Non-inline children (structural) are skipped
    result = " ".join(parts)
    result = result.replace("\xa0", " ")
    result = re.sub(r"\s+", " ", result).strip()
    return result


_INLINE_TAGS = frozenset({"i", "b", "em", "u", "strong", "span", "sub", "sup"})


def _looks_like_reavahetus_item_tail(text: str) -> bool:
    """Return True when a reavahetus tail starts a numbered item list entry."""
    return bool(re.match(r"^\s*\d[\d\s]*\)\s*", text or ""))


def _normalize_ee_statute_surface_text(text: str) -> str:
    """Normalize RT surface artifacts in parsed base-statute text."""
    if not text:
        return text
    text = re.sub(r"\s*\(RT\s+[IVX]+[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s*\[\s*(?:RT|RTL)\s+[^\]]*?(?:jõust\.|rakendatakse)[^\]]*?\]",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r" +([.,;:!?)])", r"\1", text)
    return text


def _element_text_with_bold_section_boundaries(el: XmlElement) -> str:
    """Extract element text while preserving bold whole-section title boundaries."""
    parts: list[str] = []

    def _walk(node: XmlElement) -> None:
        tag = node.tag.split("}")[-1] if "}" in node.tag else node.tag
        if tag in {"b", "strong"}:
            inner = " ".join(str(text) for text in node.itertext())
            inner = re.sub(r"\s+", " ", inner.replace("\xa0", " ")).strip()
            if inner:
                parts.append(f"{inner}\x01" if "§" in inner else inner)
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _walk(child)
        if node.tail:
            parts.append(node.tail)

    _walk(el)
    text = " ".join(part for part in parts if part)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _tavatekst_text(t: XmlElement, ns_str: str) -> str:
    """Extract text from a tavatekst element, including inline formatting children.

    Captures text from inline elements (<i>, <b>, <em>, <u>, <sup>, <sub>) which
    wrap formatted text.

    For structural/line-break elements like <reavahetus>, keep the tail only when
    it is ordinary continuation text. Numbered-item tails such as "1) item one"
    still belong to the item extractor and must not be concatenated into the
    parent subsection text.

    Without this, plain t.text misses text inside inline tags (e.g. italicised
    terms like "kaugtõestamine" in "<i>kaugtõestamine</i>").
    With a naive itertext(), all reavahetus tails get concatenated into the parent
    subsection text, breaking comparisons with oracle where numbered item lists
    are stored as separate structural nodes.
    """
    parts: list[str] = []
    if t.text:
        parts.append(t.text)
    for child in t:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local in _INLINE_TAGS:
            # Inline: include its full text recursively.
            # For <sup>/<sub> elements (superscript section numbers like
            # 93<sup>1</sup>), insert a space before the child text so that
            # the result matches amendment payload extraction which replaces
            # all HTML tags with a space (93 1, not 931).
            child_text = "".join(str(_t) for _t in child.itertext())
            if child_text:
                if local in ("sup", "sub"):
                    parts.append(" ")
                parts.append(child_text)
            # Include tail (text after the closing inline tag but before next sibling)
            if child.tail:
                parts.append(child.tail)
        else:
            # Structural (reavahetus, etc.): keep the tail only when it is
            # continuation prose, not a numbered item marker handled elsewhere.
            if child.tail and not _looks_like_reavahetus_item_tail(child.tail):
                if (
                    parts
                    and not parts[-1].endswith((" ", "\n", "\t"))
                    and not child.tail.startswith((" ", ".", ",", ";", ":", ")"))
                ):
                    parts.append(" ")
                parts.append(child.tail)
    result = re.sub(r"\s+", " ", "".join(parts)).replace("\xa0", " ").strip()
    result = _normalize_ee_statute_surface_text(result)
    # RT sometimes prepends a kehtetu editorial annotation directly into the
    # materialized subsection text while the oracle presents only the live text.
    # Treat this bracketed marker as non-substantive presentation metadata.
    result = re.sub(r"^\[Kehtetu\s*-\s*[^\]]+\]\s*", "", result, flags=re.I)
    # RT editorial convention: standalone hyphen marks a repealed section.
    # New tervikteksts use en-dash (–); old tyviseadus XML uses plain hyphen (-).
    # Normalize for consistent comparison with oracle.
    if result == "-":
        result = "–"
    # Older tervikteksts omit the trailing period from the standard repealed-section
    # placeholder; newer ones include it.  Normalize to the canonical form.
    if result == "[Käesolevast tekstist välja jäetud]":
        result = "[Käesolevast tekstist välja jäetud.]"
    return result


def _sisuTekst_text(st: XmlElement, ns_str: str) -> str:
    """Extract concatenated text from a sisuTekst element in document order.

    Handles two text-bearing child types:
      - <tavatekst>: plain text (possibly with inline formatting children)
      - <viide><kuvatavTekst>: hyperlink display text (must be included inline)

    viide elements are hyperlinks embedded in the legal text; their kuvatavTekst
    is display text that forms part of the sentence, not metadata.

    reavahetus children inside tavatekst separate list items — their tails
    are skipped by _tavatekst_text (list items are parsed separately).
    """
    parts: list[str] = []
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "tavatekst":
            txt = _tavatekst_text(child, ns_str)
            if txt:
                parts.append(txt)
        elif local == "viide":
            # Extract kuvatavTekst display text from hyperlink element
            kvt = child.find(_ns(ns_str, "kuvatavTekst"))
            if kvt is not None and kvt.text:
                parts.append(kvt.text.replace("\xa0", " ").strip())
        # muutmismarge, avaldamismarge, etc. — metadata, skip
    result = " ".join(p for p in parts if p)
    result = _normalize_ee_statute_surface_text(result)
    # Some RT tervikteksts encode a kehtetu editorial annotation across
    # tavatekst + viide + tavatekst. Strip that presentation marker after the
    # full sisuTekst has been linearized.
    result = re.sub(r"^\[Kehtetu\s*-\s*[^\]]+\]\s*", "", result, flags=re.I)
    return result


def _html_to_plain_text(fragment: str) -> str:
    """Strip HTML markup to normalized plain text."""
    import html as _html

    text = _html.unescape(fragment or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _appendix_html_payload_text(fragment: str) -> str:
    """Linearize appendix HTML payloads such as RT tables into plain text."""
    plain = _html_to_plain_text(fragment)
    marker = _extract_appendix_marker(fragment)
    if marker and plain == marker:
        return ""
    return plain


def _section_html_table_text(st: XmlElement, ns_str: str) -> str:
    """Return meaningful non-appendix table text from one section-level sisuTekst."""
    parts: list[str] = []
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local != "HTMLKonteiner" or not child.text:
            continue
        if _extract_appendix_marker(child.text):
            continue
        if not re.search(r"<table\b", child.text, re.IGNORECASE):
            continue
        plain = _html_to_plain_text(child.text)
        if plain:
            parts.append(plain)
    return " ".join(parts).strip()


def _extract_appendix_marker(fragment: str) -> str:
    """Extract a plain appendix marker like ``Lisa 1`` from HTMLKonteiner text."""
    plain = _html_to_plain_text(fragment)
    match = re.search(r"\bLisa\s+\d+\b", plain, re.IGNORECASE)
    return match.group(0) if match else ""


def _leading_appendix_marker(st: XmlElement) -> str:
    """Return the leading appendix marker for a sisuTekst, if it starts one."""
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "muutmismarge":
            continue
        if local == "HTMLKonteiner" and child.text:
            return _extract_appendix_marker(child.text)
        if local == "tavatekst":
            txt = _tavatekst_text(child, "")
            if txt:
                return ""
        if local == "viide":
            if "".join(str(_t) for _t in child.itertext()).strip():
                return ""
    return ""


def _subsection_uses_appendix_html(el: XmlElement, ns_str: str) -> bool:
    """True when subsection text should preserve appendix-style HTML payload text."""
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        for child in st:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if local != "HTMLKonteiner" or not child.text:
                continue
            if _extract_appendix_marker(child.text):
                return True
            if re.search(r"<table\b", child.text, re.IGNORECASE):
                return True
    return False


_EE_DROP_ORPHAN_APPENDIX_MARKER_RULE = "ee_drop_orphan_appendix_marker_html"


_EE_DROP_REPEALED_RANGE_RESIDUE_RULE = "ee_drop_repealed_range_residue"


_EE_SINGLETON_EMPTY_SECTION_LABEL_RULE = "ee_singleton_empty_section_label_to_1"


_EE_SECTION_LEVEL_INTRO_TO_FIRST_SUBSECTION_RULE = "ee_section_level_intro_attached_to_first_subsection"


_EE_SECTION_LEVEL_REAVAHETUS_ITEMS_TO_FIRST_SUBSECTION_RULE = (
    "ee_section_level_reavahetus_items_attached_to_first_subsection"
)


_EE_HTML_TABLE_TEXT_RULE = "ee_html_table_text_materialized"


_EE_HTML_TABLE_NUMBERED_ITEMS_RULE = "ee_html_table_numbered_items_materialized"


_EE_HTML_PARAGRAPH_NUMBERED_ITEMS_RULE = "ee_html_paragraph_numbered_items_materialized"


_EE_UNLABELED_LOIGE_CONTINUATION_RULE = "ee_unlabeled_loige_continuation_attached_to_previous_subsection"


_EE_SPACED_SUPERSCRIPT_SUBSECTION_MARKER_RULE = "ee_spaced_superscript_subsection_marker"


_EE_DROP_LOIKE_TEKST_PLACEHOLDER_RULE = "ee_drop_loike_tekst_placeholder"


_EE_RT_INLINE_CHANGE_NOTE_RE = re.compile(
    r"\s*\[\s*RT\s+[IVX]+\s*,\s*\d{1,2}\.\d{1,2}\.\d{4}\s*,\s*\d+"
    r"(?:\s*-\s*jõust\.\s*\d{1,2}\.\d{1,2}\.\d{4})?\s*\]",
    re.IGNORECASE,
)


# Matches a numbered item label at the start of a segment, e.g. "3) text".
_EE_ITEM_LABEL_RE = re.compile(r"^(\d[\d\s_]*)\)\s*(.*)", re.DOTALL)


def _element_has_kehtetu_marker(el: XmlElement, ns_str: str) -> bool:
    for mm in el.findall(_ns(ns_str, "muutmismarge")):
        for text_el in mm.findall(_ns(ns_str, "tavatekst")):
            marker = " ".join(str(part) for part in text_el.itertext()).strip().lower()
            if marker.startswith(("kehtetu", "kehtetud")):
                return True
    return False


def _is_repealed_range_residue_text(text: str) -> bool:
    cleaned = _ee_normalize_text_replace_surface(text).strip()
    if not cleaned:
        return False
    return bool(
        re.fullmatch(r"[–‒-]+\s*\(?\d+\)?", cleaned)
        or re.fullmatch(r"§-d\s+\d[\d_]*\s*[–‒-]\s*\d[\d_]*", cleaned)
    )


def _section_level_sisutekst_text(el: XmlElement, ns_str: str) -> str:
    """Return direct section-level text that precedes explicit subsections."""
    parts: list[str] = []
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        txt = _sisuTekst_text(st, ns_str)
        if txt:
            parts.append(txt)
    return " ".join(parts).strip()


def _strip_rt_inline_change_note(text: str) -> str:
    """Remove RT inline amendment notes from parsed structural item text."""
    return re.sub(r"\s+", " ", _EE_RT_INLINE_CHANGE_NOTE_RE.sub("", text)).strip()


def _html_fragment_plain_text(fragment: str) -> str:
    """Return plain text from a small RT HTML fragment without structural splitting."""
    import html as _html

    text = re.sub(r"<(?:sup|sub)\b[^>]*>", " ", fragment, flags=re.IGNORECASE)
    text = re.sub(r"</(?:sup|sub)>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return text


def _extract_numbered_html_table_row_items(html_text: str) -> list[IRNode]:
    """Materialize table rows whose first cell begins with an explicit item label.

    Table cells frequently contain legal citations such as ``art 53)``. Those
    parenthetical citation numbers are not item labels, so row extraction owns
    the top-level label before falling back to generic inline item splitting.
    """
    items: list[IRNode] = []
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html_text, flags=re.IGNORECASE | re.DOTALL)
    if not rows:
        return []
    for row in rows:
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue
        cell_texts = [_html_fragment_plain_text(cell) for cell in cells]
        first_cell = cell_texts[0]
        match = re.match(r"^(?P<label>\d[\d\s_]*)\)\s*(?P<body>.+)$", first_cell)
        if match is None:
            continue
        body_parts = [match.group("body").strip(), *[text for text in cell_texts[1:] if text]]
        item_text = _strip_rt_inline_change_note(" ".join(part for part in body_parts if part).strip())
        if not item_text:
            continue
        items.append(
            IRNode(
                kind=IRNodeKind.ITEM,
                label=_normalize_num(match.group("label")),
                text=item_text,
                attrs={"source_cleanup_rule": _EE_HTML_TABLE_NUMBERED_ITEMS_RULE},
            )
        )
    return items


def _extract_html_table_item_children(el: XmlElement, ns_str: str) -> list[IRNode]:
    """Materialize numbered HTML table rows as item children when the labels are explicit."""
    children: list[IRNode] = []
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        for child in st:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if local != "HTMLKonteiner" or not child.text:
                continue
            if _extract_appendix_marker(child.text):
                continue
            if not re.search(r"<table\b", child.text, re.IGNORECASE):
                continue
            row_items = _extract_numbered_html_table_row_items(child.text)
            if row_items:
                children.extend(row_items)
                continue
            for item_text in parse_html_op_items(child.text):
                intro_text, item_children = _parse_subsection_item_payload(item_text)
                if intro_text or not item_children:
                    continue
                for item in item_children:
                    children.append(
                        IRNode(
                            kind=item.kind,
                            label=item.label,
                            text=_strip_rt_inline_change_note(item.text),
                            attrs={
                                **dict(item.attrs),
                                "source_cleanup_rule": _EE_HTML_TABLE_NUMBERED_ITEMS_RULE,
                            },
                            children=tuple(item.children),
                        )
                    )
    return children


def _extract_numbered_html_paragraph_item_children(el: XmlElement, ns_str: str) -> tuple[str, list[IRNode]]:
    """Materialize ``HTMLKonteiner`` paragraphs that encode numbered item lists.

    Some RT consolidated-current XML surfaces keep the same provisions that old
    tyviseadus XML exposes as typed ``alampunkt`` children inside a raw
    ``HTMLKonteiner`` paragraph with ``1) ... <br/> 2) ...`` markers. This is a
    transport-shape repair: it preserves the legal item labels and records the
    source cleanup rule on both the host subsection and materialized items.
    """
    html_parts: list[str] = []
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        for child in st:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if local != "HTMLKonteiner" or not child.text:
                continue
            if _extract_appendix_marker(child.text):
                continue
            # The existing EE IR does not model table cell bodies as provision
            # descendants. Drop table payloads here too so this cleanup only
            # repairs paragraph/list structure and does not invent table nodes.
            html_without_tables = re.sub(
                r"<table\b.*?</table>",
                " ",
                child.text,
                flags=re.IGNORECASE | re.DOTALL,
            )
            plain = _html_fragment_plain_text(html_without_tables)
            plain = _normalize_ee_statute_surface_text(plain)
            if plain:
                html_parts.append(plain)
    if not html_parts:
        return "", []

    intro_text, item_children = _parse_inline_item_children(
        " ".join(html_parts),
        require_first_label_one=True,
    )
    if not item_children:
        return "", []
    owned_children = [
        IRNode(
            kind=item.kind,
            label=item.label,
            text=_strip_rt_inline_change_note(item.text),
            attrs={**dict(item.attrs), "source_cleanup_rule": _EE_HTML_PARAGRAPH_NUMBERED_ITEMS_RULE},
            children=tuple(item.children),
        )
        for item in item_children
    ]
    return intro_text, owned_children


def _sisuTekst_text_with_appendix_markers(
    st: XmlElement,
    ns_str: str,
    *,
    drop_first_appendix_marker: bool = False,
) -> str:
    """Extract sisuTekst text while preserving simple appendix markers from HTML."""
    parts: list[str] = []
    first_marker_dropped = False
    for child in st:
        local = child.tag.split("}")[1] if "}" in child.tag else child.tag
        if local == "tavatekst":
            txt = _tavatekst_text(child, ns_str)
            if txt:
                parts.append(txt)
        elif local == "viide":
            kvt = child.find(_ns(ns_str, "kuvatavTekst"))
            if kvt is not None and kvt.text:
                parts.append(kvt.text.replace("\xa0", " ").strip())
        elif local == "HTMLKonteiner" and child.text:
            marker = _extract_appendix_marker(child.text)
            if marker:
                if drop_first_appendix_marker and not first_marker_dropped:
                    first_marker_dropped = True
                    continue
                parts.append(marker)
                continue
            html_txt = _appendix_html_payload_text(child.text)
            if html_txt:
                parts.append(html_txt)
    result = " ".join(p for p in parts if p)
    result = re.sub(r" +([.,;:!?])", r"\1", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def _collect_text(el: XmlElement, ns_str: str) -> str:
    """Concatenate all tavatekst descendants of el.

    Normalizes \xa0 (non-breaking space) to regular space for consistency
    with amendment payload text which goes through HTML unescape + normalize.
    """
    parts = []
    for t in el.iter(_ns(ns_str, "tavatekst")):
        txt = _tavatekst_text(t, ns_str)
        if txt:
            parts.append(txt)
    return " ".join(parts)


def _parse_item(el: XmlElement, ns_str: str) -> IRNode:
    """Parse an alampunkt (item) element → IRNode(kind=IRNodeKind.ITEM)."""
    nr = (
        _extract_superscript_label(el, ns_str)
        or _text(_find(el, ns_str, "alampunktNr"))
        or _text(_find(el, ns_str, "kuvatavNr"))
    )
    # Normalize kuvatavNr like "1)" → "1"
    nr = re.sub(r"[^\w_¹²³⁴⁵⁶⁷⁸⁹⁰]", "", nr) if nr else ""

    # Gather direct text from sisuTekst (not from nested alampunkt).
    # _sisuTekst_text captures tavatekst + viide/kuvatavTekst in document order.
    text_parts = []
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        txt = _sisuTekst_text(st, ns_str)
        if txt:
            text_parts.append(txt)
    item_text = " ".join(text_parts)
    if _element_has_kehtetu_marker(el, ns_str) and _is_repealed_range_residue_text(item_text):
        return IRNode(
            kind=IRNodeKind.ITEM,
            label="",
            text="",
            attrs={
                "source_cleanup_rule": _EE_DROP_REPEALED_RANGE_RESIDUE_RULE,
                "dropped_repealed_residue": item_text,
            },
        )

    # Sub-items (alampunkt nested inside alampunkt — rare)
    children = [_parse_item(sub, ns_str) for sub in el.findall(_ns(ns_str, "alampunkt"))]
    return IRNode(kind=IRNodeKind.ITEM, label=nr, text=item_text, children=tuple(children))


def _extract_reavahetus_items(el: XmlElement, ns_str: str) -> List[IRNode]:
    """Extract list items from <tavatekst> elements whose items are separated
    by <reavahetus/> line-break elements (old tyviseadus format).

    Pattern (tavatekst-only):
        <tavatekst>Intro:<reavahetus/>1) item one;<reavahetus/>2) item two.</tavatekst>

    Pattern (with sibling viide for last item):
        <sisuTekst>
          <tavatekst>Intro:<reavahetus/>1) one;<reavahetus/>3) </tavatekst>
          <viide><kuvatavTekst>linked text</kuvatavTekst></viide>
          <tavatekst>.</tavatekst>
        </sisuTekst>

    Processes items from <reavahetus/> tails (INSIDE tavatekst) and continues
    collecting into the current item from sibling viide/tavatekst nodes in the
    SAME sisuTekst (for items that span the tavatekst boundary).

    Returns an empty list if no <reavahetus/> children with numbered-item tails
    are found (i.e., the subsection uses <alampunkt> XML items, handled elsewhere).
    """
    def _segments(st: XmlElement) -> list[str]:
        segments = [""]

        def add_text(text: str | None, *, prefix_space: bool = False) -> None:
            if not text:
                return
            cleaned = text.replace("\xa0", " ")
            if prefix_space and cleaned.strip():
                cleaned = " " + cleaned
            segments[-1] += cleaned

        def walk(node: XmlElement) -> None:
            local = node.tag.split("}")[1] if "}" in node.tag else node.tag
            if local == "reavahetus":
                segments.append("")
                add_text(node.tail)
                return
            if local == "viide":
                kvt = node.find(_ns(ns_str, "kuvatavTekst"))
                if kvt is not None:
                    add_text(kvt.text)
                add_text(node.tail)
                return
            add_text(node.text, prefix_space=(local == "sup"))
            for child in node:
                walk(child)
            add_text(node.tail)

        for child in st:
            walk(child)
        return [re.sub(r"\s+", " ", segment).strip() for segment in segments]

    items: List[IRNode] = []

    cur_label: Optional[str] = None
    cur_parts: List[str] = []
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        for segment in _segments(st):
            if not segment:
                continue
            match = _EE_ITEM_LABEL_RE.match(segment)
            if match is not None:
                if cur_label is not None:
                    item_text = _normalize_ee_statute_surface_text(" ".join(p for p in cur_parts if p).strip())
                    items.append(
                        IRNode(
                            kind=IRNodeKind.ITEM,
                            label=cur_label,
                            text=item_text,
                        )
                    )
                cur_label = re.sub(r"\s+", "_", match.group(1).strip()).rstrip("_")
                cur_parts = [match.group(2).strip()] if match.group(2).strip() else []
            elif cur_label is not None:
                cur_parts.append(segment)

    if cur_label is not None:
        item_text = _normalize_ee_statute_surface_text(" ".join(p for p in cur_parts if p).strip())
        items.append(
            IRNode(kind=IRNodeKind.ITEM, label=cur_label, text=item_text)
        )

    return items


def _extract_reavahetus_intro_text(el: XmlElement, ns_str: str) -> str:
    """Return text before the first reavahetus item list in an element."""
    intro_parts: list[str] = []

    def _tavatekst_prefix_before_item_list(tavatekst: XmlElement) -> tuple[str, bool]:
        parts: list[str] = []
        if _looks_like_reavahetus_item_tail(tavatekst.text or ""):
            return "", True
        if tavatekst.text:
            parts.append(tavatekst.text)
        for child in tavatekst:
            local = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if local == "reavahetus":
                if _looks_like_reavahetus_item_tail(child.tail or ""):
                    text = re.sub(r"\s+", " ", "".join(parts)).replace("\xa0", " ").strip()
                    return _normalize_ee_statute_surface_text(text), True
                if child.tail:
                    parts.append(child.tail)
                continue
            if local in _INLINE_TAGS:
                child_text = "".join(str(_t) for _t in child.itertext())
                if child_text:
                    if local in ("sup", "sub"):
                        parts.append(" ")
                    parts.append(child_text)
                if child.tail:
                    parts.append(child.tail)
                continue
            if child.tail:
                parts.append(child.tail)
        text = re.sub(r"\s+", " ", "".join(parts)).replace("\xa0", " ").strip()
        return _normalize_ee_statute_surface_text(text), False

    for st in el.findall(_ns(ns_str, "sisuTekst")):
        for tavatekst in st.findall(_ns(ns_str, "tavatekst")):
            text, found_item_list = _tavatekst_prefix_before_item_list(tavatekst)
            if found_item_list:
                if text:
                    intro_parts.append(text)
                return " ".join(part for part in intro_parts if part).strip()
            if text:
                intro_parts.append(text)
    return " ".join(part for part in intro_parts if part).strip()


def _parse_subsection(el: XmlElement, ns_str: str, default_nr: int = 1) -> IRNode:
    """Parse a loige (subsection) element → IRNode(kind=IRNodeKind.SUBSECTION)."""
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "loigeNr")) or str(default_nr)

    # Direct intro text (sisuTekst at this level, not under alampunkt).
    # _sisuTekst_text captures tavatekst + viide/kuvatavTekst in document order.
    text_parts = []
    html_table_item_children = _extract_html_table_item_children(el, ns_str)
    use_appendix_html = _subsection_uses_appendix_html(el, ns_str) and not html_table_item_children
    for st in el.findall(_ns(ns_str, "sisuTekst")):
        txt = _sisuTekst_text_with_appendix_markers(st, ns_str) if use_appendix_html else _sisuTekst_text(st, ns_str)
        if txt:
            text_parts.append(txt)
    sub_text = " ".join(text_parts)
    if _element_has_kehtetu_marker(el, ns_str) and _is_repealed_range_residue_text(sub_text):
        return IRNode(
            kind=IRNodeKind.SUBSECTION,
            label="",
            text="",
            attrs={
                "source_cleanup_rule": _EE_DROP_REPEALED_RANGE_RESIDUE_RULE,
                "dropped_repealed_residue": sub_text,
            },
        )

    # Prefer explicit <alampunkt> XML items; fall back to <reavahetus>-separated items.
    parsed_children = [_parse_item(item_el, ns_str) for item_el in el.findall(_ns(ns_str, "alampunkt"))]
    dropped_repealed_residues = [
        str(child.attrs["dropped_repealed_residue"])
        for child in parsed_children
        if child.attrs.get("source_cleanup_rule") == _EE_DROP_REPEALED_RANGE_RESIDUE_RULE
    ]
    children = [child for child in parsed_children if child.label or child.text or child.children]
    if not children:
        # Old tyviseadus format: items encoded as "N) text" in <tavatekst>
        # tails of <reavahetus> elements rather than as <alampunkt> XML nodes.
        children = _extract_reavahetus_items(el, ns_str)
        if children:
            # Rebuild sub_text as intro-only: the tavatekst.text before the first
            # <reavahetus/> separator, not the full _sisuTekst_text which also
            # captures sibling <viide> content that belongs to reavahetus items.
            sub_text = _extract_reavahetus_intro_text(el, ns_str)
    used_html_paragraph_item_children = False
    if not children:
        html_intro_text, html_paragraph_item_children = _extract_numbered_html_paragraph_item_children(el, ns_str)
        if html_paragraph_item_children:
            children = html_paragraph_item_children
            if html_intro_text:
                sub_text = " ".join(part for part in (sub_text, html_intro_text) if part).strip()
            used_html_paragraph_item_children = True
    used_html_table_item_children = False
    if not children and html_table_item_children:
        children = html_table_item_children
        used_html_table_item_children = True
    dropped_loike_tekst_placeholder = False
    if children and sub_text.strip() == "Lõike tekst":
        sub_text = ""
        dropped_loike_tekst_placeholder = True

    attrs = {}
    if dropped_repealed_residues:
        attrs["source_cleanup_rules"] = (_EE_DROP_REPEALED_RANGE_RESIDUE_RULE,)
        attrs["dropped_repealed_residues"] = tuple(dropped_repealed_residues)
    if used_html_table_item_children:
        attrs["source_cleanup_rules"] = (
            *tuple(attrs.get("source_cleanup_rules", ())),
            _EE_HTML_TABLE_NUMBERED_ITEMS_RULE,
        )
    if used_html_paragraph_item_children:
        attrs["source_cleanup_rules"] = (
            *tuple(attrs.get("source_cleanup_rules", ())),
            _EE_HTML_PARAGRAPH_NUMBERED_ITEMS_RULE,
        )
    if dropped_loike_tekst_placeholder:
        attrs["source_cleanup_rules"] = (
            *tuple(attrs.get("source_cleanup_rules", ())),
            _EE_DROP_LOIKE_TEKST_PLACEHOLDER_RULE,
        )

    return IRNode(kind=IRNodeKind.SUBSECTION, label=nr, text=sub_text, attrs=attrs, children=tuple(children))


def _loige_has_explicit_label(el: XmlElement, ns_str: str) -> bool:
    """Return whether an RT ``loige`` carries its own displayed/legal label."""
    return bool(_extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "loigeNr")))


def _attach_unlabeled_loige_continuation(previous: IRNode, continuation: IRNode) -> IRNode:
    """Attach an unlabeled in-between ``loige`` to the previous subsection.

    RT XML sometimes serializes formula explanations as a separate ``loige``
    without ``loigeNr`` between two explicitly numbered subsections. Numbering
    such a transport fragment by position creates duplicate legal labels.
    """
    cleanup_rules = tuple(previous.attrs.get("source_cleanup_rules", ()))
    attrs = {
        **dict(previous.attrs),
        "source_cleanup_rules": (
            *cleanup_rules,
            _EE_UNLABELED_LOIGE_CONTINUATION_RULE,
        ),
    }
    continuation_text = (continuation.text or "").strip()
    return replace(
        previous,
        text=" ".join(part for part in (previous.text, continuation_text) if part).strip(),
        attrs=attrs,
        children=tuple((*previous.children, *continuation.children)),
    )


def _parse_subsection_nodes(el: XmlElement, ns_str: str, default_nr: int = 1) -> List[IRNode]:
    """Parse one loige into one or more subsection nodes.

    Old EE source sometimes embeds appendix material inside a single subsection:
    ordinary subsection text first, then a later sisuTekst whose leading
    HTMLKonteiner is just ``Lisa N`` and whose remaining text is the appendix
    body. RT consolidated oracles materialize that as follow-on subsections.
    """
    base = _parse_subsection(el, ns_str, default_nr=default_nr)
    base_num = _try_parse_int(base.label) if base.label is not None else None
    if base_num is None:
        return [base]

    sisu_blocks = el.findall(_ns(ns_str, "sisuTekst"))
    appendix_start: Optional[int] = None
    appendix_marker = ""
    for idx, st in enumerate(sisu_blocks[1:], start=1):
        marker = _leading_appendix_marker(st)
        if marker:
            appendix_start = idx
            appendix_marker = marker
            break
    if appendix_start is None or not appendix_marker:
        return [base]

    intro_parts = [_sisuTekst_text(st, ns_str) for st in sisu_blocks[:appendix_start] if _sisuTekst_text(st, ns_str)]
    appendix_parts: list[str] = []
    for rel_idx, st in enumerate(sisu_blocks[appendix_start:]):
        txt = _sisuTekst_text_with_appendix_markers(
            st,
            ns_str,
            drop_first_appendix_marker=(rel_idx == 0),
        )
        if txt:
            appendix_parts.append(txt)
    appendix_text = " ".join(appendix_parts).strip()

    intro_text = " ".join(intro_parts).strip() or base.text
    if not appendix_text:
        return [
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=base.label,
                text=intro_text,
                attrs={
                    **dict(base.attrs),
                    "source_cleanup_rule": _EE_DROP_ORPHAN_APPENDIX_MARKER_RULE,
                    "dropped_appendix_marker": appendix_marker,
                },
                children=tuple(base.children),
            ),
        ]
    return [
        IRNode(
            kind=IRNodeKind.SUBSECTION,
            label=base.label,
            text=intro_text,
            attrs=dict(base.attrs),
            children=tuple(base.children),
        ),
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(base_num + 1), text=appendix_marker),
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(base_num + 2), text=appendix_text),
    ]


def _extract_superscript_label(el: XmlElement, ns_str: str) -> Optional[str]:
    """Extract label from kuvatavNr, handling <sup> superscript suffixes.

    Works for sections, subsections, and items.
    kuvatavNr patterns:
        "§ 1<sup>2</sup>."   → "1_2"  (section)
        "(3<sup>1</sup>)"    → "3_1"  (subsection)
        "1<sup>2</sup>)"     → "1_2"  (item)
    If no superscript is present, returns None (caller should use regular Nr).
    """
    knr = _find(el, ns_str, "kuvatavNr")
    if knr is None:
        return None
    # kuvatavNr is inside CDATA — get the raw text content
    raw = knr.text or ""
    if "<sup>" not in raw:
        return None
    # Extract: "PREFIX N<sup>M</sup> SUFFIX" → "N_M"
    m = re.search(r"(\d+)\s*<sup>(\d+)</sup>", raw)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return None


def _build_phantom_set(sisu_el: XmlElement, ns_str: str) -> set[XmlElement]:
    """Return the set of paragrahv elements that are phantom placeholder sections.

    A paragrahv is a phantom placeholder iff:
    1. Its kuvatavNr is blank (empty or whitespace-only).
    2. It has no loige children and no non-empty sisuTekst.
    3. At least one OTHER paragrahv with the same paragrahvNr text has a
       non-blank kuvatavNr — i.e. the real section exists alongside it.

    Condition 3 prevents us from dropping genuine empty sections that happen
    to lack a kuvatavNr (e.g. base sections that receive content in later
    amendments).  RT only generates duplicate-nr placeholders when the statute
    has been renumbered and the old slot must be preserved for display.

    Returns a set of lxml element objects (strong references) so callers can
    test `child_el in phantom_set` using lxml element identity (not Python id()).
    """
    # First pass: collect all nr → list[el] and note which have visible nr
    nr_to_els: dict[str, list[XmlElement]] = {}
    for para in sisu_el.iter(_ns(ns_str, "paragrahv")):
        nr_el = para.find(_ns(ns_str, "paragrahvNr"))
        nr = (nr_el.text or "").strip() if nr_el is not None else ""
        if not nr:
            continue
        nr_to_els.setdefault(nr, []).append(para)

    phantoms: list[XmlElement] = []
    for nr, els in nr_to_els.items():
        if len(els) < 2:
            continue  # only one element with this nr — keep it regardless
        # Multiple elements share this nr.  Mark blank-kuvatavNr + empty ones.
        has_real = any(
            (kn := e.find(_ns(ns_str, "kuvatavNr"))) is not None
            and bool((kn.text or "").strip())
            for e in els
        )
        if not has_real:
            continue  # all are blank-nr — keep them all (ambiguous)
        for e in els:
            kn = e.find(_ns(ns_str, "kuvatavNr"))
            if kn is None or not (kn.text or "").strip():
                # Check it's also empty (no loige, no content)
                if e.findall(_ns(ns_str, "loige")):
                    continue  # has content — keep
                has_content = False
                for st in e.findall(_ns(ns_str, "sisuTekst")):
                    for tt in st.iter():
                        if tt.text and tt.text.strip():
                            has_content = True
                            break
                if not has_content:
                    phantoms.append(e)

    # Return as a list-backed set using lxml element identity (el == other_el
    # is True for the same XML node, even across separate Python wrapper objects).
    # We keep strong references so the wrappers aren't GC'd before the caller
    # finishes its structural parse.
    return set(phantoms)


def _parse_section(el: XmlElement, ns_str: str) -> IRNode:
    """Parse a paragrahv (section §) element → IRNode(kind=IRNodeKind.SECTION)."""
    # Use kuvatavNr with <sup> suffix when available (paragrahvNr loses
    # superscript, causing label collisions: §1, §1¹, §1² all become "1").
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "paragrahvNr"))
    title = _title_text(_find(el, ns_str, "paragrahvPealkiri"))
    # "Paragrahvi pealkiri" is an RT placeholder meaning "no title assigned yet".
    # Oracle tervikteksts use an empty element; base tyviseadus may have this literal.
    if title == "Paragrahvi pealkiri":
        title = ""

    children: List[IRNode] = []
    dropped_repealed_residues: list[str] = []
    loige_els = el.findall(_ns(ns_str, "loige"))
    loige_has_explicit_labels = [
        _loige_has_explicit_label(loige_el, ns_str)
        for loige_el in loige_els
    ]
    for i, loige_el in enumerate(loige_els, start=1):
        is_unlabeled_between_numbered_subsections = (
            not loige_has_explicit_labels[i - 1]
            and bool(children)
            and any(loige_has_explicit_labels[i:])
        )
        for node in _parse_subsection_nodes(loige_el, ns_str, default_nr=i):
            if is_unlabeled_between_numbered_subsections:
                if node.attrs.get("source_cleanup_rule") == _EE_DROP_REPEALED_RANGE_RESIDUE_RULE:
                    dropped_repealed_residues.append(str(node.attrs["dropped_repealed_residue"]))
                    continue
                if node.text or node.children:
                    children[-1] = _attach_unlabeled_loige_continuation(children[-1], node)
                continue
            if node.label or node.text or node.children:
                children.append(node)
            elif node.attrs.get("source_cleanup_rule") == _EE_DROP_REPEALED_RANGE_RESIDUE_RULE:
                dropped_repealed_residues.append(str(node.attrs["dropped_repealed_residue"]))
    if children:
        section_reavahetus_items = _extract_reavahetus_items(el, ns_str)
        section_intro = (
            _extract_reavahetus_intro_text(el, ns_str)
            if section_reavahetus_items
            else _section_level_sisutekst_text(el, ns_str)
        )
        if section_intro:
            first = children[0]
            first_rules = tuple(first.attrs.get("source_cleanup_rules", ()))
            source_cleanup_rules = (
                *first_rules,
                _EE_SECTION_LEVEL_INTRO_TO_FIRST_SUBSECTION_RULE,
            )
            first_children = tuple(first.children)
            section_reavahetus_labels: tuple[str, ...] = ()
            if section_reavahetus_items:
                existing_labels = {child.label for child in first.children if child.label}
                prefix_labels = tuple(item.label or "" for item in section_reavahetus_items)
                if all(label and label not in existing_labels for label in prefix_labels):
                    first_children = tuple((*section_reavahetus_items, *first.children))
                    source_cleanup_rules = (
                        *source_cleanup_rules,
                        _EE_SECTION_LEVEL_REAVAHETUS_ITEMS_TO_FIRST_SUBSECTION_RULE,
                    )
                    section_reavahetus_labels = prefix_labels
            first_attrs = {
                **first.attrs,
                "source_cleanup_rules": source_cleanup_rules,
                "section_level_intro_text": section_intro,
            }
            if section_reavahetus_labels:
                first_attrs["section_level_reavahetus_item_labels"] = section_reavahetus_labels
            children[0] = replace(
                first,
                text=" ".join(part for part in (section_intro, first.text) if part).strip(),
                children=first_children,
                attrs=first_attrs,
            )

    # Section with no loige children — capture sisuTekst directly as single subsection.
    # _sisuTekst_text captures tavatekst + viide/kuvatavTekst in document order.
    if not children:
        reavahetus_items = _extract_reavahetus_items(el, ns_str)
        if reavahetus_items:
            children.append(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    text=_extract_reavahetus_intro_text(el, ns_str),
                    children=tuple(reavahetus_items),
                )
            )
        else:
            html_intro_text, html_paragraph_item_children = _extract_numbered_html_paragraph_item_children(
                el,
                ns_str,
            )
            text_parts = []
            used_html_table_text = False
            for st in el.findall(_ns(ns_str, "sisuTekst")):
                txt = _sisuTekst_text(st, ns_str)
                if txt:
                    text_parts.append(txt)
                html_table_text = _section_html_table_text(st, ns_str)
                if html_table_text:
                    text_parts.append(html_table_text)
                    used_html_table_text = True
            if html_intro_text:
                text_parts.append(html_intro_text)
            if html_paragraph_item_children:
                children.append(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        text=" ".join(text_parts),
                        attrs={"source_cleanup_rules": (_EE_HTML_PARAGRAPH_NUMBERED_ITEMS_RULE,)},
                        children=tuple(html_paragraph_item_children),
                    )
                )
            elif text_parts:
                attrs = (
                    {"source_cleanup_rules": (_EE_HTML_TABLE_TEXT_RULE,)}
                    if used_html_table_text
                    else {}
                )
                children.append(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=" ".join(text_parts), attrs=attrs))

    # Detect already-repealed sections: muutmismarge says "Kehtetu" and there is
    # no body content.  RT tervikteksts preserve the original title of such sections
    # without applying subsequent global text-replacements to it.  We mark these
    # with attrs={'kehtetu': True} so _ee_global_text_replace can skip their title.
    attrs: dict[str, object] = {}
    if dropped_repealed_residues:
        attrs["source_cleanup_rules"] = (_EE_DROP_REPEALED_RANGE_RESIDUE_RULE,)
        attrs["dropped_repealed_residues"] = tuple(dropped_repealed_residues)
    if not children:
        mm = el.find(_ns(ns_str, "muutmismarge"))
        if mm is not None:
            tt = mm.find(_ns(ns_str, "tavatekst"))
            if tt is not None and (tt.text or "").strip().startswith("Kehtetu"):
                attrs["kehtetu"] = True

    return IRNode(kind=IRNodeKind.SECTION, label=nr, text=title, attrs=attrs, children=tuple(children))


def _canonicalize_singleton_empty_section_label(children: tuple[IRNode, ...]) -> tuple[IRNode, ...]:
    """Map one unlabeled top-level section to section 1 with an explicit witness.

    Some old RT regulation XML stores the only section as an empty
    ``paragrahvNr`` while later consolidated surfaces expose the same unit as
    ``§ 1``. This is safe only for a single top-level section that actually has
    provision content; broader relabeling would be target hijacking.
    """
    if len(children) != 1:
        return children
    section = children[0]
    if section.kind != IRNodeKind.SECTION or section.label not in (None, ""):
        return children
    if not section.children and not (section.text or "").strip():
        return children
    attrs = dict(section.attrs)
    cleanup_rules = tuple(attrs.get("source_cleanup_rules", ()))
    attrs["source_cleanup_rules"] = cleanup_rules + (_EE_SINGLETON_EMPTY_SECTION_LABEL_RULE,)
    attrs["source_empty_section_label"] = section.label or ""
    return (replace(section, label="1", attrs=attrs),)


# Leading container ordinal carried in a heading, e.g. "1. PÕHISÄTTED" or
# "2¹. TEOSE KASUTAMINE ...".  Many RT divisions/subdivisions leave their
# numeric element (jaguNr / jaotisNr) empty and place the ordinal at the start
# of the heading text instead.  The ordinal is an arabic run optionally followed
# by superscript digits, terminated by a period.
_EE_HEADING_ORDINAL_RE = re.compile(
    r"^\s*(\d+[" + _EE_SUPERSCRIPT_DIGIT_CLASS + r"]*)\s*[.]"
)


def _extract_heading_ordinal_label(heading_text: str) -> str:
    """Return the normalized leading ordinal of a container heading, or "".

    ``"1. PÕHISÄTTED"`` → ``"1"``; ``"2¹. ..."`` → ``"2_1"``.  Empty when the
    heading does not begin with an ordinal (e.g. a plain titled division).
    """
    if not heading_text:
        return ""
    match = _EE_HEADING_ORDINAL_RE.match(heading_text)
    if not match:
        return ""
    return _normalize_num(match.group(1))


def _parse_division(el: XmlElement, ns_str: str, phantoms: AbstractSet[XmlElement] = frozenset()) -> IRNode:
    """Parse a jagu (division) element → IRNode(kind=IRNodeKind.DIVISION).

    ``<jaotis>`` subdivisions are materialized as their own SUBDIVISION level so
    that subdivision-qualified addresses (chapter/division/subdivision/section)
    resolve in the PIT.  Sections keep the legacy ``jaotis``/``alljaotis`` attrs
    as well, for downstream consumers that still key off the flattened form.
    """
    title = _text(_find(el, ns_str, "jaguPealkiri"))
    # jaguNr is frequently empty; fall back to the ordinal embedded in the
    # heading so divisions never carry an empty label (which both blocks
    # addressing and previously crashed duplicate-child classification).
    nr = (
        _extract_superscript_label(el, ns_str)
        or _text(_find(el, ns_str, "jaguNr"))
        or _extract_heading_ordinal_label(title)
    )
    children: List[IRNode] = []

    def _make_section(para_el: XmlElement, *, jaotis_label: str = "", alljaotis_label: str = "") -> Optional[IRNode]:
        if para_el in phantoms:
            return None
        section = _parse_section(para_el, ns_str)
        attrs = dict(section.attrs)
        if jaotis_label:
            attrs["jaotis"] = _normalize_num(jaotis_label)
        if alljaotis_label:
            attrs["alljaotis"] = _normalize_num(alljaotis_label)
        if attrs != section.attrs:
            section = replace(section, attrs=attrs)
        return section

    for child in el:
        local_tag = child.tag.split("}")[-1]
        if local_tag == "paragrahv":
            section = _make_section(child)
            if section is not None:
                children.append(section)
        elif local_tag == "jaotis":
            # EE jaotis sits below jagu — materialize it as a SUBDIVISION node
            # nesting its sections, preserving document order and the section
            # labels/titles that the oracle exposes (e.g. § 97^1, § 97^2).
            jaotis_title = _text(_find(child, ns_str, "jaotisPealkiri"))
            jaotis_label = (
                _extract_superscript_label(child, ns_str)
                or _text(_find(child, ns_str, "jaotisNr"))
                or _extract_heading_ordinal_label(jaotis_title)
            )
            sub_children: List[IRNode] = []
            for para_el in child.findall(_ns(ns_str, "paragrahv")):
                section = _make_section(para_el, jaotis_label=jaotis_label)
                if section is not None:
                    sub_children.append(section)
            for alljaotis_el in child.findall(_ns(ns_str, "alljaotis")):
                alljaotis_label = _extract_superscript_label(alljaotis_el, ns_str) or _text(
                    _find(alljaotis_el, ns_str, "alljaotisNr")
                )
                for para_el in alljaotis_el.findall(_ns(ns_str, "paragrahv")):
                    section = _make_section(
                        para_el,
                        jaotis_label=jaotis_label,
                        alljaotis_label=alljaotis_label,
                    )
                    if section is not None:
                        sub_children.append(section)
            children.append(
                IRNode(
                    kind=IRNodeKind.SUBDIVISION,
                    label=jaotis_label,
                    text=jaotis_title,
                    children=tuple(sub_children),
                )
            )
    return IRNode(kind=IRNodeKind.DIVISION, label=nr, text=title, children=tuple(children))


def _parse_chapter(el: XmlElement, ns_str: str, phantoms: AbstractSet[XmlElement] = frozenset()) -> IRNode:
    """Parse a peatykk (chapter) element → IRNode(kind=IRNodeKind.CHAPTER)."""
    nr = _extract_superscript_label(el, ns_str) or _text(_find(el, ns_str, "peatykkNr"))
    title = _title_text(_find(el, ns_str, "peatykkPealkiri"))

    children: List[IRNode] = []
    for child in el:
        local_tag = child.tag.split("}")[-1]
        if local_tag == "jagu":
            children.append(_parse_division(child, ns_str, phantoms))
        elif local_tag == "paragrahv":
            if child not in phantoms:
                children.append(_parse_section(child, ns_str))
        # Skip peatykkNr, peatykkPealkiri, kuvatavNr (metadata, not structure)

    return IRNode(kind=IRNodeKind.CHAPTER, label=nr, text=title, children=tuple(children))


_RANGE_LABEL_RE = re.compile(r"^(\d+)–(\d+)$")


def _expand_range_sections(children: List[IRNode]) -> List[IRNode]:
    """Expand range-label sections (e.g. section:3–4) into individual sections.

    Old tyviseadus base statutes sometimes encode repealed section ranges as a
    single element with label "3–4" (en-dash separated). The oracle terviktekst
    splits them into individual sections §3, §4 with the same content. Expanding
    here ensures the replay output matches the oracle structure.

    Only operates on top-level section nodes; chapter/division children are
    handled recursively via _parse_chapter.
    """
    result: List[IRNode] = []
    for node in children:
        if node.kind == IRNodeKind.SECTION and node.label is not None:
            m = _RANGE_LABEL_RE.match(node.label)
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                if 0 < (end - start) <= 20:  # sanity: expand only reasonable ranges
                    for n in range(start, end + 1):
                        result.append(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label=str(n),
                                text=node.text,
                                children=tuple(node.children),
                                attrs=dict(node.attrs),
                            )
                        )
                    continue
        result.append(node)
    return result


def _detect_ns(root: XmlElement) -> str:
    """Detect namespace from root element tag. Handles tyviseadus, maarus, juurakt."""
    if "}" in root.tag:
        return root.tag.split("}")[0].lstrip("{")
    return NS_BASE


def parse_ee_statute(xml_bytes: bytes, statute_id: str = "") -> IRStatute:
    """Parse a tyviseadus or maarus XML document → IRStatute.

    Handles tyviseadus_1_10.02.2010, maarus_1_10.02.2010, and juurakt_1_10.02.2010
    schemas — all share the same structural elements (peatykk, paragrahv, loige).

    statute_id: e.g. "ee/104012019011". If empty, extracted from globaalID metadata.
    """
    root = ET.fromstring(xml_bytes)
    ns_str = _detect_ns(root)

    # Statute ID from metadata if not provided
    if not statute_id:
        gid = root.find(f".//{_ns(ns_str, 'globaalID')}")
        statute_id = f"ee/{_text(gid)}" if gid is not None else "ee/unknown"

    # Title from aktinimi/nimi/pealkiri
    title = ""
    aktinimi = root.find(_ns(ns_str, "aktinimi"))
    if aktinimi is not None:
        nimi = aktinimi.find(_ns(ns_str, "nimi"))
        if nimi is not None:
            pealkiri = nimi.find(_ns(ns_str, "pealkiri"))
            title = _text(pealkiri)

    # Body: sisu → peatykk (chapters), osa (parts), or flat paragrahv (decrees)
    sisu = root.find(_ns(ns_str, "sisu"))
    body_children: List[IRNode] = []
    if sisu is not None:
        # Pre-scan: identify phantom placeholder sections (empty duplicate-nr rows).
        # Must be done before structural parsing so _parse_chapter/_parse_division
        # can skip them consistently.
        phantoms = _build_phantom_set(sisu, ns_str)

        for child in sisu:
            local_tag = child.tag.split("}")[-1]
            if local_tag == "osa":
                # Part level (above chapter) — rare
                osa_nr = _text(_find(child, ns_str, "osaNr"))
                osa_title = _text(_find(child, ns_str, "osaPealkiri"))
                part_children = [
                    _parse_chapter(peat_el, ns_str, phantoms) for peat_el in child.findall(_ns(ns_str, "peatykk"))
                ]
                body_children.append(
                    IRNode(kind=IRNodeKind.PART, label=osa_nr, text=osa_title, children=tuple(part_children))
                )
            elif local_tag == "peatykk":
                body_children.append(_parse_chapter(child, ns_str, phantoms))
            elif local_tag == "paragrahv":
                # Flat body (common in decrees): paragrahv directly under sisu
                if child not in phantoms:
                    body_children.append(_parse_section(child, ns_str))

    expanded_children = tuple(_expand_range_sections(body_children))
    expanded_children = _canonicalize_singleton_empty_section_label(expanded_children)
    body = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=expanded_children)

    # Metadata
    meta_el = root.find(_ns(ns_str, "metaandmed"))
    metadata: dict[str, str] = {}
    if meta_el is not None:
        for tag_name in ("lyhend", "dokumentLiik", "tekstiliik", "metaandmedVersioon"):
            el = meta_el.find(_ns(ns_str, tag_name))
            if el is not None and el.text:
                metadata[tag_name] = el.text.strip()
    metadata["schema"] = ns_str

    return IRStatute(
        statute_id=statute_id,
        title=title,
        body=body,
        supplements=[],
        metadata=metadata,
    )


def _paragrahv_to_act_id(title: str) -> str:
    """Compatibility wrapper; migrated to ``lawvm.estonia.target_resolution``."""
    return _tr_paragrahv_to_act_id(title)


def _strip_rt_editorial_parentheticals(text: str) -> str:
    """Strip inline RT publication-reference parentheticals from payload text.

    Amendment payloads sometimes include source-side references like
    ``autoveoseaduse (RT I 2000, 54, 346) kohase ...`` while the consolidated
    oracle materializes only ``autoveoseaduse kohase ...``. Treat these
    parentheticals as editorial citation residue, but only inside payload-derived
    replacement text.
    """
    if not text:
        return text
    stripped = re.sub(r"\s*\(RT\s+[IVX]+[^)]*\)", "", text, flags=re.IGNORECASE)
    stripped = re.sub(r"(?<=\d)\s*[–-]\s*(?=\d)", "–", stripped)
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = re.sub(r" +([.,;:!?)])", r"\1", stripped)
    return stripped.strip()


_EE_INLINE_ITEM_PARENTHESES_MARKER_GUARD_RULE = "ee_inline_item_parentheses_marker_guard"


def _parse_subsection_item_payload(
    raw_text: str,
    *,
    require_first_label_one: bool = True,
) -> tuple[str, List[IRNode]]:
    """Split a subsection payload into intro text plus numbered item children."""
    return _parse_inline_item_children(
        raw_text,
        require_first_label_one=require_first_label_one,
    )


def _parse_inline_subsection_payload_nodes(raw_text: str) -> List[IRNode]:
    """Parse payload text that inlines one or more numbered subsections.

    Some EE subsection-level ``replace`` ops carry the full replacement for the
    targeted subsection plus one or more immediately following subsections, for
    example ``(2) ... 1) ... 5) ... (2 1) ...``.  RT consolidated oracles
    materialize those later blocks as real subsection nodes, so replay must do
    the same instead of discarding everything after the first label.
    """
    parts = re.split(r"(?=\(\s*\d{1,3}(?:[\s_]\d{1,3})?\s*\)\s)", raw_text.strip())
    nodes: List[IRNode] = []
    for part in parts:
        match = re.match(r"^\(\s*(\d{1,3}(?:\s\d{1,3})?)\s*\)\s*(.*)$", part.strip(), re.DOTALL)
        if match is None:
            continue
        label = _normalize_num(match.group(1))
        attrs = (
            {"source_cleanup_rule": _EE_SPACED_SUPERSCRIPT_SUBSECTION_MARKER_RULE}
            if "_" in label and re.match(r"^\(\s*\d{1,3}\s\d{1,3}\s+\)", part.strip())
            else {}
        )
        body_text = _strip_rt_editorial_parentheticals(match.group(2).strip())
        intro_text, item_children = _parse_subsection_item_payload(
            body_text,
            require_first_label_one=False,
        )
        nodes.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=label,
                text=intro_text,
                attrs=attrs,
                children=tuple(item_children),
            )
        )
    return nodes
