"""UK CLML source-tree parser: builds a frozen ``IRStatute`` / ``IRNode`` tree
WITHOUT in-place parse-time mutation.

Construction strategy (Wave N3d Sub-PR A of the mutable_ir ratchet, audit
XJUR-02, AGENTS.md §2.3 "never mutate the parsed source tree after parse"):

  Parse-time construction in this module uses ONLY:
    * direct ``IRNode(...)`` constructor calls with VALUED fields (kind,
      label, text, attrs, children) computed UP-FRONT from the source XML, and
    * ``dataclasses.replace(node, ...)`` for any post-construction adjustment
      (inferred container number, p1group heading attachment, sibling-label
      disambiguation).
  There are NO in-place ``node.x = y`` writes, ``node.attrs[k] = v`` writes,
  ``node.children = ...`` writes, or ``node.children.insert(…)`` / ``.append(…)``
  during parsing. This eliminates the parse-time mutation pattern (the prior
  ``_add_attrs`` + ``node.children = …`` + ``node.text = …`` sequence).

  String kinds produced by ``_get_kind`` are coerced to ``IRNodeKind`` via
  ``uk_ir_node_kind`` at the construction site: ``IRNode`` itself does not
  coerce strings to enum (the core IR contract holds ``kind: IRNodeKind``),
  whereas the previous ``UKMutableNode.__post_init__`` did. Sites that pass
  an ``IRNodeKind`` literal are unaffected.

  The IR-tree boundary (``parse_uk_statute_ir`` / ``parse_uk_statute_ir_bytes``
  → ``IRStatute`` with frozen ``IRNode`` body/supplements) returns the
  constructed ``IRNode`` tree directly: previously the parser built a
  ``UKMutableNode`` tree and converted at ``_build_ir_from_root`` via
  ``to_irnode()``; that boundary call is dropped because the parser already
  builds ``IRNode`` branches directly. The returned ``IRStatute`` is
  byte-identical to the pre-Sub-PR-A output (the prior ``UKMutableNode`` →
  ``to_irnode()`` round-trip produced the same frozen ``IRNode`` shape).

Sibling replay/lowering modules that consume these helpers directly today
(``effect_payload_normalization``, ``effect_schedule_lowering``,
``effect_special_lowering``, ``table_selectors``) read the returned ``IRNode``
via ``IRNode.to_jsonable_dict()`` (same JSON shape as the prior
``UKMutableNode.to_dict()``) or use it directly. Sub-PR F (mutable_ir Wave N3d,
final) deleted the ``mutable_ir.py`` shadow module: ``payload_conversion.py``'s
``_to_mutable_node`` now returns frozen ``IRNode`` directly
(identity for ``IRNode`` inputs, recursive dict→``IRNode`` builder for the
legacy dict-shaped source-payload path), and the in-place ``uk_*`` mutation
helpers were superseded by the CoW variants in ``apply_rebuild.py``.

Tests pin (1) no in-place mutation during parsing, and (2) byte-identical
IRNode tree at the parse boundary.
"""

from lxml import etree as ET
import dataclasses
import json
import re
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional, Dict, Any, Callable, cast

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.apply_rebuild import uk_ir_node_kind
from lawvm.core.quirks_disposition import QuirksDisposition

_LEG_NS = "http://www.legislation.gov.uk/namespaces/legislation"
_LEG_PNUMBER_PATH = f"./{{{_LEG_NS}}}Pnumber"
_LEG_NUMBER_PATH = f"./{{{_LEG_NS}}}Number"
_LEG_DESC_NUMBER_PATH = f".//{{{_LEG_NS}}}Number"
_LEG_TITLE_PATH = f"./{{{_LEG_NS}}}Title"
_LEG_P1GROUP_TITLE_PATH = f"./{{{_LEG_NS}}}P1group/{{{_LEG_NS}}}Title"
_USER_AGENT = "LawVM-Replayer/1.0"
_LEG_BASE = "http://www.legislation.gov.uk"
_ROMAN_IVX_RE = re.compile(r"^[ivx]+$", re.IGNORECASE)
_ROMAN_FULL_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)
_CLEAN_NUM_PREFIX_RE = re.compile(
    r"^(Part|Section|Schedule|Chapter|Paragraph|Article|Rule|Regulation|Annex)"
    r"(?=\s+|[0-9IVXLCDM]+[A-Za-z]?\b)\s*",
    re.IGNORECASE,
)
_CLEAN_NUM_TRAILING_PUNCT_RE = re.compile(r"[().]+$")
_DOT_OR_SPACE_ONLY_RE = re.compile(r"^[.\s]+$")
# A label (after cleaning) that contains no alphanumerics at all — e.g. a
# consolidated-XML curly-quote Pnumber like "\u201c" — cannot be a structural
# label.  Disambiguating such a sibling must NOT pick a fallback like "\u201c-1"
# because the shared core label normalizer strips non-alphanumeric chars and the
# synthesized label would collide with the real sibling "1".  Match the
# consolidated oracle EID convention ("section-322B-n1") by synthesizing an
# alphanumeric "n{N}" suffix instead.  See
# uk_quoted_substitution_payload_sibling_synthesized_label.
_LABEL_NO_ALNUM_RE = re.compile(r"^[^a-zA-Z0-9]+$")
_GROUNDING_NON_WORD_SPACE_RE = re.compile(r"[^\w\s]")
_EID_SPLIT_RE = re.compile(r"[-_]+")
_LEADING_DIGITS_RE = re.compile(r"([0-9]+)")
_SECTION_OR_ARTICLE_ROOT_RE = re.compile(r"^(section|article|rule|regulation)-([^-]+)")
_SLUGIFY_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")
_SEMANTIC_HASH_NOISE_RE = re.compile(
    r"\b(the|a|an|of|and|or|to|in|by|from|with|as|for|is|it|at|on|this|that|be|been|being)\b"
)

# Editorial element types added by legislation.gov.uk editors — NOT part of the
# enacted statute text.  Excluded from EID scoring so that their presence in the
# consolidated oracle does not inflate the apparent gap vs the enacted version.
#   Commentary  — editorial notes attached to provisions (live in <Commentaries>
#                 top-level section, but may also appear inline via CommentaryRef)
#   Citation    — inline bibliographic references to other legislation
#   CitationSubRef — sub-references within Citations (nested inside Commentaries)
#   Footnote    — editorial explanatory source notes, often carrying f000xx ids
#   Term        — markup for defined terms; carries eId="term-<name>" inline
_EDITORIAL_TAGS: frozenset[str] = frozenset({"Commentary", "Citation", "CitationSubRef", "Footnote", "Term"})
_VISIBLE_INLINE_TEXT_TAGS: frozenset[str] = frozenset({"Citation", "CitationSubRef", "Term"})
_NON_LEGAL_UNIT_EID_TAGS: frozenset[str] = frozenset({"Text"})
_EID_TRANSPARENT_TAGS: frozenset[str] = frozenset(
    {
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
    }
)
_ZOMBIE_LOCAL_TEXT_STRUCTURAL_TAGS: frozenset[str] = frozenset(
    {
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
    }
)
_ZOMBIE_LOCAL_TEXT_SKIP_TAGS: frozenset[str] = frozenset({"pnumber", "number", "title", "commentaryref"})

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _tag(el: ET._Element) -> str:
    if el is None:
        return ""
    tag = el.tag
    if not isinstance(tag, str):
        return ""  # PI/Comment nodes have callable .tag
    return _tag_local_name(tag)


@lru_cache(maxsize=256)
def _tag_local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _text_content(el: Optional[ET._Element]) -> str:
    if el is None:
        return ""
    if len(el) == 0:
        return (el.text or "").strip()
    return "".join(str(_t) for _t in el.itertext()).strip()


_PROVISION_NUM_TAGS: frozenset[str] = frozenset({"pnumber", "number"})


def _leaf_provision_text(el: Optional[ET._Element]) -> str:
    """Collect a leaf provision's text without its own ``Pnumber``/``Number``.

    Mirrors ``_text_content`` (``itertext`` traversal, edge-stripped) but drops
    the text of the direct-child ``Pnumber``/``Number`` element — the visible
    provision number — which is already captured as the node ``label``.  Without
    this, leaf provisions (``P1``/``P2``/``P3``/``P4`` with no structural
    children) embed ``"<num>\n\n<body>"`` in the body text, duplicating the
    label that the rewrite/amended parse path already keeps clean.  Only the
    provision's OWN number is dropped (a direct child), never numbers that are
    genuine body content deeper in the subtree.
    """
    if el is None:
        return ""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if _tag(child).lower() in _PROVISION_NUM_TAGS:
            # Skip the number element subtree, but keep its tail (live body text
            # that follows the number in source order).
            if child.tail:
                parts.append(child.tail)
            continue
        parts.append("".join(str(_t) for _t in child.itertext()))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Oracle presentation-cleanup: RetainText="true" repeal elision
# ---------------------------------------------------------------------------
#
# legislation.gov.uk marks a repealed inline phrase (or whole provision) with
# ``<Repeal RetainText="true">…retained wording…</Repeal>``.  This is the
# editor keeping the repealed text *visible* because a 1-dimensional-time-axis
# consolidation cannot represent partial / "for specified purposes"
# commencement: the cleanest single-snapshot rendering is to leave the words on
# the page and flag them as repealed.  It is the direct UK analogue of Finnish
# Finlex's "Aiempi sanamuoto kuuluu:" ("the earlier wording read…") marker
# (see ``lawvm.tools.editorial_hygiene`` /
# ``fi_oracle_aiempi_sanamuoto_marker``): a 1-D consolidation artifact, NOT law,
# orthogonal to LawVM's multi-dimensional state.
#
# LawVM's replay applies the repeal (the words are gone from materialized text),
# so the retained wording would otherwise show up only-in-oracle — as a spurious
# text_diff on the enclosing provision, or, for a whole-provision retained
# repeal, as an only-in-oracle EID.  We therefore ELIDE the retained subtree
# from the oracle comparison tree BEFORE comparison, exactly as Finland strips
# its marker.  This is a comparison-only ``presentation_cleanup`` normalization
# (AGENTS.md §7); it never touches LawVM's compiled ops or materialized text.
# The genuine partial-commencement modeling is LawVM's own contingent-
# commencement job, not the comparison's.
_RETAIN_TEXT_ELISION_RULE_ID = "uk_oracle_retain_text_repeal_elided"
_RETAIN_TEXT_ATTR = "RetainText"


def _is_retained_repeal(el: ET._Element) -> bool:
    """Return whether ``el`` is a ``<Repeal RetainText="true">`` retained node.

    These hold the *repealed-but-kept-visible* wording legislation.gov.uk
    renders for a 1-D consolidation snapshot; for comparison the repeal is
    treated as applied, so the retained subtree is elided.
    """
    return _tag(el) == "Repeal" and (el.get(_RETAIN_TEXT_ATTR) or "").lower() == "true"


def _contains_retained_repeal(el: ET._Element) -> bool:
    for descendant in el.iter():
        retain_text = descendant.get(_RETAIN_TEXT_ATTR)
        if (
            retain_text is not None
            and retain_text.lower() == "true"
            and _tag(descendant) == "Repeal"
        ):
            return True
    return False


def _oracle_text_eliding_retained_repeals(el: Optional[ET._Element]) -> tuple[str, bool]:
    """Collect provision text with ``RetainText="true"`` repeal subtrees elided.

    Mirrors ``_text_content`` (``itertext`` traversal, edge-stripped) but drops
    the text of any ``<Repeal RetainText="true">`` descendant — keeping the
    node's *tail* (text that follows it in source order, which is live wording,
    not retained).  Returns ``(text, elided)`` where ``elided`` is True iff at
    least one retained-repeal subtree was dropped, so the caller can emit the
    auditable presentation-cleanup observation.
    """
    if el is None:
        return "", False
    parts: list[str] = []
    elided = False

    def _walk(node: ET._Element) -> None:
        nonlocal elided
        if _is_retained_repeal(node):
            elided = True
            # Drop the retained wording (node text + descendants) but keep the
            # tail, which is live text following the repealed phrase.
            if node.tail:
                parts.append(node.tail)
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            _walk(child)
            if child.tail and not _is_retained_repeal(child):
                # A retained-repeal child's tail is appended inside its own
                # branch above, so it is not double-counted here.
                parts.append(child.tail)

    _walk(el)
    return "".join(parts).strip(), elided


def _local_structural_text(el: ET._Element) -> str:
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
        "orderedlist",
        "unorderedlist",
    }
    transparent_skip = {"pnumber", "number", "title", "commentaryref"}
    structural_text_skip = {tag.lower() for tag in _EDITORIAL_TAGS - _VISIBLE_INLINE_TEXT_TAGS}

    def _collect(node: ET._Element) -> list[str]:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = _tag(child).lower()
            if (
                (
                    tag in structural
                    and not (
                        tag == "unorderedlist"
                        and _contains_definition_ordered_list(child)
                        and not _definition_unordered_list_homes_intros(child)
                    )
                )
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


def _contains_definition_ordered_list(el: ET._Element) -> bool:
    """Return whether a list embeds a definition ordered-list payload."""
    for parent in el.iter():
        for child in parent:
            if _tag(child) == "OrderedList" and _definition_ordered_list_term(parent, child):
                return True
    return False


def _definition_unordered_list_homes_intros(el: ET._Element) -> bool:
    """Whether ``_parse_definition_unordered_list`` homes term-intros into children.

    Mirrors that parser's branching so ``_local_structural_text`` can decide
    whether the definition-item intros are carried by the homed child paragraphs
    (multi-item path) or only by the subsection's own text (single-nested flat
    path).  When the intros are homed, the subsection text must skip the whole
    definition list to avoid double-counting them against the grounding oracle;
    when they are not, the list must stay in the subsection text so the intros
    are not dropped.
    """
    if _tag(el) != "UnorderedList" or el.get("Class", "").lower() != "definition":
        return False
    nested_with_list = 0
    for item in el:
        if _tag(item) != "ListItem":
            continue
        for para in item:
            if _tag(para) != "Para":
                continue
            for pchild in para:
                if _tag(pchild) == "OrderedList" and _definition_ordered_list_term(para, pchild):
                    nested_with_list += 1
                    break
    # Exactly one nested ordered list -> legacy flat-paragraph path that returns
    # only the nested list's items as children (intros NOT homed).  Any other
    # shape uses the multi-item wrapper path where each intro becomes child text.
    return nested_with_list != 1


def _post_child_local_text_tail(el: ET._Element) -> str:
    """Return local text that appears after a structural child in source order."""
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
        "orderedlist",
        "unorderedlist",
    }
    transparent_skip = {"pnumber", "number", "title", "commentaryref"}
    transparent_containers = {"p1para", "p2para", "p3para", "p4para"}
    structural_text_skip = {tag.lower() for tag in _EDITORIAL_TAGS - _VISIBLE_INLINE_TEXT_TAGS}

    def _collect(node: ET._Element) -> list[str]:
        seen_structural = False
        parts: list[str] = []
        for child in node:
            tag = _tag(child).lower()
            if tag in structural:
                seen_structural = True
                continue
            if tag in transparent_containers:
                nested = _collect(child)
                if nested:
                    parts.extend(nested)
                continue
            if not seen_structural:
                continue
            if tag in transparent_skip or tag in structural_text_skip:
                continue
            text = _text_content(child)
            if text:
                parts.append(text)
        return parts

    parts = _collect(el)
    return " ".join(" ".join(parts).split())


def _extract_num(el: Optional[ET._Element]) -> str:
    if el is None:
        return ""
    return _text_content(el)


# Source-XML attributes copied onto each parsed IR node so replay can ground by
# ``eId``/``id`` and respect ``Status``/``RestrictStartDate``/``RestrictEndDate``.
# Kept as a tuple so callers cannot accidentally mutate the list and alter global
# state — each consumer materialises its own dict via ``_collect_source_attrs``.
_SOURCE_ATTR_NAMES: tuple[str, ...] = (
    "eId",
    "id",
    "Status",
    "RestrictStartDate",
    "RestrictEndDate",
)


def _collect_source_attrs(el: ET._Element) -> dict[str, str]:
    """Return the ``eId``/``id``/``Status``/etc. attrs from ``el`` as a dict.

    Pure helper: returns a fresh dict the caller merges into its constructed
    ``IRNode.attrs``. Replaces the prior ``_add_attrs(node, el)`` mutating helper
    so the parser never mutates an existing IR node (PR1 migration).
    """
    out: dict[str, str] = {}
    for attr in _SOURCE_ATTR_NAMES:
        val = el.get(attr)
        if val:
            out[attr] = val
    return out


def _roman_to_int(s: str) -> str:
    """Return the Arabic-string form of ``s`` if it is a canonical Roman
    numeral, otherwise return ``s`` unchanged.

    Delegates to ``lawvm.roman``; rejects non-canonical spellings via
    round-trip canonicalization.  The previous implementation only
    handled I..X.
    """
    if not _ROMAN_IVX_RE.match(s):
        return s
    value = _shared_roman_to_arabic(s)
    return s if value is None else str(value)



@lru_cache(maxsize=32768)
def _clean_num_cached(raw: str) -> str:
    if raw == "":
        return ""
    s = str(raw).strip()
    s = _CLEAN_NUM_PREFIX_RE.sub("", s)
    s = _CLEAN_NUM_TRAILING_PUNCT_RE.sub("", s).strip()
    if _ROMAN_FULL_RE.match(s):
        s = _roman_to_int(s)
    return s.lower().strip(".")


def _clean_num(raw: str) -> str:
    if not raw:
        return ""
    return _clean_num_cached(str(raw))


_clean_num_with_cache_attrs = cast(Any, _clean_num)
_clean_num_with_cache_attrs.cache_clear = _clean_num_cached.cache_clear
_clean_num_with_cache_attrs.cache_info = _clean_num_cached.cache_info


def _infer_container_number_from_source_uri(el: ET._Element, *, prefix: str) -> str:
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
    node: IRNode,
    el: ET._Element,
    *,
    prefix: str,
    original_label: str,
) -> IRNode:
    """Return ``node`` with a possible inferred container number applied.

    If ``el``'s eId/id uniquely identifies a container number not present in the
    visible label, return a NEW ``IRNode`` with the inferred label and the four
    ``source_*`` attrs that witness the inference (rule id, original/inferred
    label, source identifier). Otherwise return ``node`` unchanged. Pure (PR1
    migration): the prior helper performed in-place ``node.label = ...`` /
    ``node.attrs[...] = ...`` writes; this version rebuilds via
    ``dataclasses.replace``.
    """
    if _clean_num(original_label) not in {"", prefix}:
        return node
    inferred = _infer_container_number_from_source_uri(el, prefix=prefix)
    if not inferred:
        return node
    new_attrs = dict(node.attrs)
    new_attrs["source_rule_id"] = _UK_CONTAINER_NUMBER_INFERRED_RULE_ID
    new_attrs["source_original_label"] = original_label
    new_attrs["source_inferred_label"] = inferred
    new_attrs["source_identifier"] = str(el.get("eId") or el.get("id") or "")
    return dataclasses.replace(node, label=inferred, attrs=new_attrs)


_UK_TABLE_ROW_TAGS = frozenset({"row", "tr"})
_UK_TABLE_CELL_TAGS = frozenset({"entry", "td", "th"})
_UK_TABLE_HEADER_CONTAINERS = frozenset({"thead"})
_UK_TABLE_TRANSPARENT_CONTAINERS = frozenset({"tgroup", "tbody", "tfoot"})
_UK_SCHEDULE_LIST_ENTRY_RULE_ID = "uk_schedule_list_entry_preserved"
_UK_NON_SCHEDULE_LIST_ENTRY_RULE_ID = "uk_non_schedule_list_entry_preserved"
_UK_CONTAINER_NUMBER_INFERRED_RULE_ID = "uk_container_number_inferred_from_source_uri"
_UK_BLOCK_AMENDMENT_TABLE_RULE_ID = "uk_block_amendment_table_preserved"
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


def _definition_ordered_list_term(parent_el: ET._Element, list_el: ET._Element) -> str:
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


_ROMAN_VALUES = (
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"),
    (5, "v"), (4, "iv"), (1, "i"),
)


def _roman_label(index: int) -> str:
    if index < 0:
        return ""
    n = index + 1
    parts: list[str] = []
    for value, numeral in _ROMAN_VALUES:
        while n >= value:
            parts.append(numeral)
            n -= value
    return "".join(parts)


_LIST_TYPE_TO_LABEL_FACTORY: dict[str, Callable[[int], str]] = {
    "alpha": _alpha_label,
    "alphaUpper": _alpha_label,
    "a": _alpha_label,
    "A": _alpha_label,
    "roman": _roman_label,
    "Roman": _roman_label,
    "romanUpper": _roman_label,
    "i": _roman_label,
    "I": _roman_label,
}


def _ordered_list_type_label(el: ET._Element, index: int) -> str:
    list_type = (el.get("Type") or "").strip()
    if list_type:
        factory = _LIST_TYPE_TO_LABEL_FACTORY.get(list_type)
        if factory is not None:
            return factory(index)
    return _alpha_label(index)


def _parse_definition_ordered_list(
    el: ET._Element,
    parent_el: ET._Element,
    context: str = "",
) -> list[IRNode]:
    term = _definition_ordered_list_term(parent_el, el)
    if not term:
        return []
    nodes: list[IRNode] = []
    item_index = 0
    in_body = not context.startswith("schedule")
    for child in el:
        if _tag(child) != "ListItem":
            continue
        label = (child.get("NumberOverride") or "").strip() or _ordered_list_type_label(el, item_index)
        item_index += 1
        text = _text_content(child)
        if not label or not text:
            continue
        # Body-section definition ordered lists (e.g. "In this section—" followed by
        # terms whose meaning is given by an alpha sub-list) function as paragraphs
        # in the enacted source even though the XML encodes them as lists.  The
        # oracle materialises them as paragraph-* EIDs, and amendments target them as
        # section:/subsection:/paragraph:.  Replay-address them as paragraphs while
        # preserving the source-list provenance.
        if in_body:
            kind = IRNodeKind.PARAGRAPH
            node_label = label
        else:
            kind = IRNodeKind.ITEM
            node_label = None
        attrs: dict[str, Any] = {
            "source_rule_id": "uk_definition_ordered_list_child_preserved",
            "definition_term": term,
            "definition_child_label": label,
            "source_tag": _tag(el),
            "source_list_type": el.get("Type", ""),
        }
        attrs.update(_collect_source_attrs(child))
        new_node = IRNode(
            kind=uk_ir_node_kind(kind),
            label=node_label,
            text=text,
            attrs=attrs,
        )
        nodes.append(new_node)
    return nodes


def _parse_definition_unordered_list(
    el: ET._Element,
    context: str,
    force_active: bool = False,
    pit_date: Optional[str] = None,
    is_eur: bool = False,
) -> list[IRNode]:
    """Parse a ``<UnorderedList Class="Definition">`` in body context.

    Each top-level ``ListItem`` introduces a definition term and may carry a
    nested alpha ``OrderedList`` that expands the term.  When several such items
    appear under the same subsection, flattening the nested alpha sublists as
    direct paragraph children creates duplicate paragraph labels (a, b, c ...).
    Preserve each definition as its own paragraph node and attach the nested
    alpha sublist items as children of that paragraph.  A single-item definition
    list keeps the existing flat-paragraph behaviour so existing addressability
    (e.g. ``section:/subsection:/paragraph:a``) is unchanged.
    """
    if context.startswith("schedule"):
        return []
    if el.get("Class", "").lower() != "definition":
        return []

    def _item_info(item: ET._Element) -> tuple[Optional[ET._Element], Optional[ET._Element], str]:
        para: Optional[ET._Element] = None
        ordered_list: Optional[ET._Element] = None
        for child in item:
            if _tag(child) == "Para":
                para = child
                # Nested ordered list normally appears inside the Para.
                for pchild in child:
                    if _tag(pchild) == "OrderedList" and _definition_ordered_list_term(child, pchild):
                        ordered_list = pchild
                        break
        if para is None:
            return (None, None, "")
        intro = _definition_item_intro_text(para)
        return (para, ordered_list, intro)

    item_infos = []
    for child in el:
        if _tag(child) != "ListItem":
            continue
        para, ordered_list, intro = _item_info(child)
        if para is None:
            continue
        item_infos.append((child, para, ordered_list, intro))

    # If exactly one definition item contains a nested ordered list, keep the
    # legacy flat-paragraph behaviour used by existing body-section targets
    # (e.g. ``section:/subsection:/paragraph:a``).
    nested_with_list = [info for info in item_infos if info[2] is not None]
    if len(nested_with_list) == 1:
        _, para, ordered_list, _ = nested_with_list[0]
        assert ordered_list is not None
        return _parse_definition_ordered_list(ordered_list, para, context)

    nodes: list[IRNode] = []
    for item, para, ordered_list, intro in item_infos:
        if not intro and ordered_list is None:
            continue
        term = _definition_ordered_list_term(para, ordered_list) if ordered_list is not None else ""
        if not term:
            # Fallback: use the definition verb prefix as the term anchor.
            term = _definition_term_from_intro(intro)
        attrs: dict[str, Any] = {
            "source_rule_id": "uk_definition_unordered_list_item_preserved",
            "source_tag": _tag(item),
            "definition_term": term,
        }
        if ordered_list is not None and term:
            attrs["definition_term"] = term
        attrs.update(_collect_source_attrs(item))
        children = (
            list(_parse_definition_ordered_list(ordered_list, para, context))
            if ordered_list is not None
            else []
        )
        node = IRNode(
            kind=IRNodeKind.PARAGRAPH,
            label=None,
            text=intro,
            attrs=attrs,
            children=tuple(children),
        )
        nodes.append(node)
    return nodes


def _definition_item_intro_text(para_el: ET._Element) -> str:
    """Return text from a definition paragraph before any nested ordered list."""
    parts: list[str] = []
    for child in para_el:
        if _tag(child) == "OrderedList":
            break
        text = _text_content(child)
        if text:
            parts.append(text)
        if child.tail:
            parts.append(child.tail)
    return " ".join(" ".join(parts).split())


def _definition_term_from_intro(intro: str) -> str:
    """Extract a quoted term from a definition-item intro line."""
    quoted_match = re.search(
        r"[“\"'\u2018]\s*(?P<term>[^”\"'\u2019;]{1,160}?)\s*[”\"'\u2019]",
        intro,
        flags=re.I,
    )
    if quoted_match is not None:
        return " ".join(quoted_match.group("term").split())
    return ""


def _parse_generic_ordered_list(
    el: ET._Element,
    context: str,
    force_active: bool,
    pit_date: Optional[str],
    is_eur: bool,
) -> list[IRNode]:
    nodes: list[IRNode] = []
    item_index = 0
    for child in el:
        if _tag(child) != "ListItem":
            continue
        num_override = child.get("NumberOverride")
        if num_override:
            label = num_override.strip().strip("()")
        else:
            label = _ordered_list_type_label(el, item_index)
        item_index += 1

        if context.startswith("schedule"):
            kind = "paragraph"
        else:
            if is_eur:
                kind = "paragraph"
            else:
                if label.isdigit():
                    kind = "subsection"
                elif re.match(r"^[a-z]+$", label, re.IGNORECASE):
                    kind = "paragraph"
                elif re.match(r"^[ivx]+$", label, re.IGNORECASE):
                    kind = "subparagraph"
                else:
                    kind = "paragraph"

        children = _parse_children(child, context, force_active, pit_date, is_eur)
        # Original behavior: if the ListItem has structural children, the node
        # text is empty (children carry the body); otherwise the ListItem's
        # text content is the node text.  Preserved by computing text upfront.
        text = "" if children else _text_content(child)
        attrs = _collect_source_attrs(child)
        new_node = IRNode(
            kind=uk_ir_node_kind(kind),
            label=label,
            text=text,
            attrs=attrs,
            children=tuple(children),
        )
        nodes.append(new_node)
    return nodes


def _schedule_list_entry_node(
    el: ET._Element,
    *,
    source_ordinal: int,
    source_tag: str,
    source_list_type: str = "",
    source_decoration: str = "",
    source_context: str = "schedule_body",
    source_rule_id: str = _UK_SCHEDULE_LIST_ENTRY_RULE_ID,
    preserve_source_eid: bool = False,
):
    text = _text_content(el)
    if not text:
        return None
    attrs: dict[str, Any] = {
        "source_rule_id": source_rule_id,
        "source_tag": source_tag,
        "source_ordinal": str(source_ordinal),
        "source_context": source_context,
    }
    if source_list_type:
        attrs["source_list_type"] = source_list_type
    if source_decoration:
        attrs["source_decoration"] = source_decoration
    if preserve_source_eid:
        attrs.update(_collect_source_attrs(el))
    return IRNode(
        kind=IRNodeKind.SCHEDULE_ENTRY,
        label=None,
        text=text,
        attrs=attrs,
    )


def _parse_schedule_body_list_entries(el: ET._Element, *, start_ordinal: int) -> list[IRNode]:
    tag = _tag(el)
    if tag != "UnorderedList":
        return []
    nodes: list[IRNode] = []
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


def _parse_non_schedule_list_entries(el: ET._Element, *, context: str, start_ordinal: int) -> list[IRNode]:
    if _tag(el) != "UnorderedList":
        return []
    nodes: list[IRNode] = []
    for child in el:
        if _tag(child) != "ListItem":
            continue
        node = _schedule_list_entry_node(
            child,
            source_ordinal=start_ordinal + len(nodes),
            source_tag="ListItem",
            source_list_type=el.get("Type", ""),
            source_decoration=el.get("Decoration", ""),
            source_context=context,
            source_rule_id=_UK_NON_SCHEDULE_LIST_ENTRY_RULE_ID,
        )
        if node is not None:
            nodes.append(node)
    return nodes


def _parse_schedule_body_p_entries(
    el: ET._Element,
    *,
    start_ordinal: int,
    force_active: bool = False,
    pit_date: Optional[str] = None,
    is_eur: bool = False,
) -> list[IRNode]:
    if _tag(el) != "P":
        return []
    nodes: list[IRNode] = []
    for child in el:
        if _tag(child) == "UnorderedList":
            nodes.extend(_parse_schedule_body_list_entries(child, start_ordinal=start_ordinal + len(nodes)))
    if nodes:
        return nodes
    ordered_lists = [child for child in el if _tag(child) == "OrderedList"]
    if ordered_lists and is_eur:
        node = _schedule_list_entry_node(
            el,
            source_ordinal=start_ordinal,
            source_tag="P",
            preserve_source_eid=is_eur,
        )
        if node is None:
            return []
        # Override default text with structural text and extend children with
        # the parsed ordered-list items.  Old code did this as in-place
        # ``node.text = `` / ``node.children.extend(...)``; now an explicit
        # build via ``dataclasses.replace`` (PR1: no in-place mutation).
        children: list[IRNode] = []
        for ordered_list in ordered_lists:
            children.extend(
                _parse_generic_ordered_list(
                    ordered_list,
                    "schedule",
                    force_active,
                    pit_date,
                    is_eur,
                )
            )
        node = dataclasses.replace(
            node,
            text=_local_structural_text(el),
            children=tuple(children),
        )
        return [node]
    child_tags = {_tag(child).lower() for child in el}
    if child_tags & _UK_SCHEDULE_ENTRY_BLOCKING_TAGS:
        return []
    if child_tags and not child_tags <= _UK_SCHEDULE_ENTRY_TRANSPARENT_TAGS:
        return []
    node = _schedule_list_entry_node(
        el,
        source_ordinal=start_ordinal,
        source_tag="P",
        preserve_source_eid=is_eur,
    )
    return [node] if node is not None else []


def _table_attrs(el: ET._Element, names: tuple[str, ...]) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for name in names:
        value = el.get(name)
        if value:
            attrs[name] = value
    return attrs


def _parse_table_row(el: ET._Element, *, header_context: bool):
    cells: list[IRNode] = []
    for child in el:
        tag = _tag(child).lower()
        if tag not in _UK_TABLE_CELL_TAGS:
            continue
        cell_kind = IRNodeKind.HEADER_CELL if header_context or tag == "th" else IRNodeKind.CELL
        attrs = _table_attrs(
            child,
            ("eId", "id", "rowspan", "colspan", "morerows", "namest", "nameend"),
        )
        ordered_list_units = _table_cell_ordered_list_units(child)
        if ordered_list_units:
            attrs["source_ordered_list_units_json"] = json.dumps(
                ordered_list_units,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            attrs["source_rule_id"] = "uk_table_cell_ordered_list_units_preserved"
        cells.append(
            IRNode(
                kind=cell_kind,
                text=_text_content(child),
                attrs=attrs,
            )
        )
    if not cells:
        return None
    return IRNode(
        kind=IRNodeKind.ROW,
        attrs=_table_attrs(el, ("eId", "id")),
        children=tuple(cells),
    )


def _table_cell_ordered_list_units(el: ET._Element) -> list[dict[str, str]]:
    units: list[dict[str, str]] = []
    for ordered_list in el.iter():
        if _tag(ordered_list) != "OrderedList":
            continue
        item_index = 0
        for child in ordered_list:
            if _tag(child) != "ListItem":
                continue
            label = (child.get("NumberOverride") or "").strip() or _alpha_label(item_index)
            item_index += 1
            text = _text_content(child)
            if not label or not text:
                continue
            units.append(
                {
                    "source_list_type": str(ordered_list.get("Type") or ""),
                    "source_list_decoration": str(ordered_list.get("Decoration") or ""),
                    "label": label.strip().strip("()"),
                    "text": text,
                }
            )
    return units


def _parse_table_rows(el: ET._Element, *, header_context: bool = False) -> list[IRNode]:
    rows: list[IRNode] = []
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


def _local_table_text(el: ET._Element) -> str:
    """Collect table-local caption/text without duplicating row cell content."""
    skipped = _UK_TABLE_ROW_TAGS | _UK_TABLE_TRANSPARENT_CONTAINERS | _UK_TABLE_HEADER_CONTAINERS

    def _collect(node: ET._Element) -> list[str]:
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


def _parse_table(el: ET._Element, context, force_active=False, pit_date=None, is_eur=False):
    del context, is_eur
    if _is_zombie(el, force_active, pit_date):
        return None
    return IRNode(
        kind=IRNodeKind.TABLE,
        text=_local_table_text(el),
        attrs=_table_attrs(el, ("eId", "id")),
        children=tuple(_parse_table_rows(el)),
    )


def _parse_block_amendment_tables(
    el: ET._Element,
    context,
    force_active=False,
    pit_date=None,
    is_eur=False,
) -> list[IRNode]:
    tables: list[IRNode] = []
    for child in el.iter():
        if child is el or _tag(child) not in {"Table", "table"}:
            continue
        table = _parse_table(child, context, force_active, pit_date, is_eur)
        if table is None:
            continue
        # Old code mutated ``table.attrs[...]`` in place; now rebuild the node
        # via ``dataclasses.replace`` so the parser never mutates an existing
        # IRNode (PR1: no in-place mutation).
        new_attrs = {
            **dict(table.attrs),
            "source_rule_id": _UK_BLOCK_AMENDMENT_TABLE_RULE_ID,
            "source_container": "BlockAmendment",
        }
        table = dataclasses.replace(table, attrs=new_attrs)
        tables.append(table)
    return tables


@lru_cache(maxsize=512)
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
    parts = [part for part in _EID_SPLIT_RE.split(str(eid or "").lower()) if part]
    return parts[-1] if parts else ""


def _eid_with_leaf_label(eid: str, label: str) -> str:
    parts = [part for part in _EID_SPLIT_RE.split(str(eid or "").lower()) if part]
    if not parts or not label:
        return ""
    return "-".join([*parts[:-1], label.lower()])


def _leading_digits(label: str) -> str:
    match = _LEADING_DIGITS_RE.match(str(label or "").lower())
    return match.group(1) if match is not None else ""


def _section_or_article_root(eid: str) -> str:
    match = _SECTION_OR_ARTICLE_ROOT_RE.match(str(eid or "").lower())
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
            "quirks_disposition": QuirksDisposition.RECORD,
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
    if not eid or not clean_num:
        return
    eid_norm = str(eid or "").lower()
    supported_kind = kind in {"subsection", "paragraph", "subparagraph", "item", "point"}
    if not supported_kind:
        return
    leaf = _eid_leaf_label(eid_norm)
    clean_leaf = _clean_num(clean_num)
    if not leaf or not clean_leaf or leaf == clean_leaf:
        return
    visible_eid = _eid_with_leaf_label(eid_norm, clean_leaf)
    if not visible_eid or visible_eid == eid_norm:
        return
    if eid_norm.startswith("schedule-"):
        if "n" not in leaf:
            return
        if _leading_digits(leaf) != _leading_digits(clean_leaf):
            return
    else:
        original_root = _section_or_article_root(eid_norm)
        visible_root = _section_or_article_root(visible_eid)
        if not original_root or original_root != visible_root:
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
            "quirks_disposition": QuirksDisposition.RECORD,
        }
    )


def _is_zombie(el: ET._Element, force_active: bool = False, pit_date: Optional[str] = None) -> bool:
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

    if _local_content_is_dot_or_space_only(el):
        has_active = False
        for child in el:
            if _tag(child).lower() in _ZOMBIE_LOCAL_TEXT_STRUCTURAL_TAGS:
                if not _is_zombie(child, False, pit_date):
                    has_active = True
                    break
        if not has_active:
            return True
    return False


def _local_content_is_dot_or_space_only(el: ET._Element) -> bool:
    """Return whether local non-structural text is non-empty dot/space filler."""
    saw_dot = False

    def _scan(text: str) -> bool:
        nonlocal saw_dot
        for char in text:
            if char == ".":
                saw_dot = True
            elif not char.isspace():
                return False
        return True

    def _walk(node: ET._Element) -> bool:
        if node.text and not _scan(node.text):
            return False
        for child in node:
            ct = _tag(child).lower()
            if (
                ct not in _ZOMBIE_LOCAL_TEXT_STRUCTURAL_TAGS
                and ct not in _ZOMBIE_LOCAL_TEXT_SKIP_TAGS
                and not _walk(child)
            ):
                return False
            if child.tail and not _scan(child.tail):
                return False
        return True

    return _walk(el) and saw_dot


def _parse_children(parent_el, context, force_active=False, pit_date=None, is_eur=False) -> list[IRNode]:
    children: list[IRNode] = []
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
        elif ct == "BlockAmendment":
            children.extend(_parse_block_amendment_tables(child, context, force_active, pit_date, is_eur))
            continue
        elif ct == "OrderedList":
            definition_children = _parse_definition_ordered_list(child, parent_el, context)
            if definition_children:
                children.extend(definition_children)
                continue
            generic_children = _parse_generic_ordered_list(child, context, force_active, pit_date, is_eur)
            if generic_children:
                children.extend(generic_children)
                continue
        elif ct == "UnorderedList":
            if not context.startswith("schedule") and child.get("Class", "").lower() == "definition":
                definition_nodes = _parse_definition_unordered_list(
                    child,
                    context,
                    force_active,
                    pit_date,
                    is_eur,
                )
                if definition_nodes:
                    children.extend(definition_nodes)
                    continue
            schedule_entries = (
                _parse_schedule_body_list_entries(
                    child,
                    start_ordinal=schedule_entry_ordinal,
                )
                if context == "schedule"
                else []
            )
            if context != "schedule" and not _contains_definition_ordered_list(child):
                schedule_entries = _parse_non_schedule_list_entries(
                    child,
                    context=context,
                    start_ordinal=schedule_entry_ordinal,
                )
            if schedule_entries:
                schedule_entry_ordinal += len(schedule_entries)
                children.extend(schedule_entries)
                continue
        elif context == "schedule" and ct == "P":
            schedule_entries = _parse_schedule_body_p_entries(
                child,
                start_ordinal=schedule_entry_ordinal,
                force_active=force_active,
                pit_date=pit_date,
                is_eur=is_eur,
            )
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
    return _disambiguate_duplicate_labels(children, parent_el, context)


def _disambiguate_duplicate_labels(
    children: list[IRNode],
    parent_el: ET._Element,
    context: str,
) -> list[IRNode]:
    """Make sibling labels unique using the source element id when available.

    UK current/oracle CLML sometimes carries multiple provisions with the same
    visible Pnumber inside one parent (e.g. two ``paragraph a`` items in the
    same subsection, versioned as ``an1`` and ``an2`` in the element id).  The
    visible number is the canonical address, but multiple live children sharing
    it makes the tree structurally ambiguous and causes duplicate-label
    invariants to fire.  When the element id contains more than the canonical
    label suffix, preserve that extra material as the structural label.

    Schedule payloads rely on their own eId-synthesis duplication guard, so
    this disambiguation is applied only in body context.

    PR1: the prior implementation mutated each ``child.label`` and
    ``child.attrs`` in place; this returns a NEW list with rebuilt
    ``IRNode`` instances via ``dataclasses.replace``, so the parser never
    mutates a node already constructed.  Behavior is byte-identical (same
    labels, same attrs, same child order).
    """
    if context.startswith("schedule"):
        return children
    if not children:
        return children
    parent_id = (parent_el.get("id") or parent_el.get("eId") or "").strip()
    label_counts: dict[str, int] = {}
    for child in children:
        if child.label:
            label_counts[child.label] = label_counts.get(child.label, 0) + 1
    labels_to_fix = {label for label, count in label_counts.items() if count > 1}
    if not labels_to_fix:
        return children
    seen: dict[str, int] = {}
    new_children: list[IRNode] = []
    for child in children:
        canonical = child.label
        if not canonical or canonical not in labels_to_fix:
            new_children.append(child)
            continue
        child_id = (child.attrs.get("id") or child.attrs.get("eId") or "").strip()
        new_label = ""
        if parent_id and child_id.startswith(parent_id + "-"):
            after_parent = child_id[len(parent_id) + 1 :]
            # Use the id suffix only if it begins with the canonical label and
            # adds alphanumeric disambiguating characters.
            if (
                after_parent.startswith(canonical)
                and len(after_parent) > len(canonical)
                and after_parent[len(canonical)].isalnum()
            ):
                new_label = after_parent
        if not new_label:
            seen[canonical] = seen.get(canonical, 0) + 1
            if _LABEL_NO_ALNUM_RE.match(canonical):
                # Quoted-substitution bodies consolidated-XML unwraps as orphan
                # P2 siblings carry only a curly-quote Pnumber (no alphanumerics).
                # Synthesize an "n{N}" suffix to match the consolidated oracle EID
                # convention ("section-322B-n1") and avoid normalization
                # collision with the real numbered sibling of the same kind
                # (e.g. "\u201c-1" would normalize to "1" and clash with subsection 1).
                new_label = f"n{seen[canonical]}"
                # Original behavior: ``child.attrs.setdefault("source_rule_id",
                # "uk_quoted_substitution_payload_sibling_synthesized_label")``.
                # Rebuild the attrs dict and IRNode ONLY if the rule_id was
                # missing (matches setdefault's no-op-when-present semantics).
                if "source_rule_id" not in child.attrs:
                    new_attrs = dict(child.attrs)
                    new_attrs["source_rule_id"] = (
                        "uk_quoted_substitution_payload_sibling_synthesized_label"
                    )
                    new_children.append(
                        dataclasses.replace(child, label=new_label, attrs=new_attrs)
                    )
                    continue
            else:
                new_label = f"{canonical}-{seen[canonical]}"
        new_children.append(dataclasses.replace(child, label=new_label))
    return new_children


def _parse_part(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num_el = el.find(f"./{{{_LEG_NS}}}Number")
    num = _extract_num(num_el) or _text_content(num_el)
    title = _text_content(el.find(f"./{{{_LEG_NS}}}Title"))
    attrs = _collect_source_attrs(el)
    children = _parse_children(el, context, force_active, pit_date, is_eur)
    node = IRNode(
        kind=IRNodeKind.PART,
        label=num,
        text=title,
        attrs=attrs,
        children=tuple(children),
    )
    if not force_active:
        node = _maybe_infer_container_number(node, el, prefix="part", original_label=num)
    return node


def _parse_chapter(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Number"))
    title = _text_content(el.find(f"./{{{_LEG_NS}}}Title"))
    attrs = _collect_source_attrs(el)
    children = _parse_children(el, context, force_active, pit_date, is_eur)
    return IRNode(
        kind=IRNodeKind.CHAPTER,
        label=num,
        text=title,
        attrs=attrs,
        children=tuple(children),
    )


_P1GROUP_SECTIONLIKE_HEADING_KINDS = frozenset(
    {"section", "article", "rule", "regulation", "paragraph"}
)
_UK_P1GROUP_HEADING_CARRIER_RULE_ID = "uk_p1group_title_heading_carrier"


def _parse_p1group(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    title_el = el.find(f"./{{{_LEG_NS}}}Title")
    title = _text_content(title_el)
    attrs = _collect_source_attrs(el)
    children = _parse_children(el, context, force_active, pit_date, is_eur)
    node = IRNode(
        kind=IRNodeKind.P1GROUP,
        label=None,
        text=title,
        attrs=attrs,
        children=tuple(children),
    )
    return _attach_p1group_title_to_sole_section(node, title, context)


def _attach_p1group_title_to_sole_section(
    node: IRNode, title: str, context: str = ""
) -> IRNode:
    """Carry a ``P1group/Title`` as a ``heading`` child on its sole section.

    The CLML wraps each enacted section as
    ``P1group/Title + P1(/Pnumber + P1para)``.  Originally the title was kept
    only on the transparent ``P1group`` wrapper, so the enacted ``section`` node
    had no ``heading`` child — unlike inserted/amended sections (which get one
    via ``uk_inserted_section_p1group_heading_carrier``) and unlike Finland.
    When the group owns exactly one section-like provision we move the title
    down onto that provision as an explicit ``HEADING`` child and clear the
    wrapper text, mirroring the inserted/rewrite carrier shape so heading
    rendering and heading-facet replay resolution are consistent across the
    enacted and amended paths.  Multi-section / heading-less groups are left
    untouched.

    In schedules a ``P1group/Title`` groups one or more paragraphs as a
    crossheading, even when the group contains a single paragraph.  Moving the
    title onto that sole paragraph would erase the crossheading wrapper and lose
    the schedule crossheading EID, so the title is preserved on the wrapper in
    schedule context.

    PR1: previously this helper mutated ``section.children`` in place (inserted
    a heading at index 0) and cleared ``node.text``; it now RETURNS a new
    ``IRNode`` rebuilt via ``dataclasses.replace`` so the parser never
    mutates an existing node. Behavior byte-identical (same children, same attrs,
    same order).
    """
    if not title:
        return node
    if str(context or "").startswith("schedule"):
        return node
    section_indices = [
        i
        for i, child in enumerate(node.children)
        if child.kind.value in _P1GROUP_SECTIONLIKE_HEADING_KINDS
    ]
    if len(section_indices) != 1:
        return node
    section_idx = section_indices[0]
    section = node.children[section_idx]
    if any(child.kind is IRNodeKind.HEADING for child in section.children):
        return node
    heading_child = IRNode(
        kind=IRNodeKind.HEADING,
        label=None,
        text=title,
        attrs={
            "source_tag": "P1group",
            "source_rule_id": _UK_P1GROUP_HEADING_CARRIER_RULE_ID,
        },
    )
    new_section = dataclasses.replace(
        section,
        children=(heading_child, *section.children),
    )
    new_node_children = list(node.children)
    new_node_children[section_idx] = new_section
    return dataclasses.replace(
        node,
        children=tuple(new_node_children),
        text="",
    )


def _parse_pgroup(el, context, force_active=False, pit_date=None, is_eur=False):
    """Preserve subordinate UK PnGroup titles as explicit heading carriers."""
    if _is_zombie(el, force_active, pit_date):
        return None
    title_el = el.find(f"./{{{_LEG_NS}}}Title")
    title = _text_content(title_el)
    attrs: dict[str, Any] = {
        "source_tag": _tag(el),
        "source_rule_id": "uk_parse_subordinate_pgroup_heading_carrier",
    }
    attrs.update(_collect_source_attrs(el))
    children = _parse_children(el, context, force_active, pit_date, is_eur)
    return IRNode(
        kind=IRNodeKind.PGROUP,
        label=None,
        text=title,
        attrs=attrs,
        children=tuple(children),
    )


def _parse_section(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber")) or _extract_num(el.find(f"./{{{_LEG_NS}}}Number"))
    kind = _get_kind(_tag(el), context, is_eur)
    return _build_provisioned_node(el, kind, num, context, force_active, pit_date, is_eur)


def _parse_p2(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber"))
    kind = _get_kind(_tag(el), context, is_eur)
    return _build_provisioned_node(el, kind, num, context, force_active, pit_date, is_eur)


def _parse_p3(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber"))
    kind = _get_kind(_tag(el), context, is_eur)
    return _build_provisioned_node(el, kind, num, context, force_active, pit_date, is_eur)


def _parse_p4(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    num = _extract_num(el.find(f"./{{{_LEG_NS}}}Pnumber"))
    kind = _get_kind(_tag(el), context, is_eur)
    return _build_provisioned_node(el, kind, num, context, force_active, pit_date, is_eur)


def _build_provisioned_node(
    el: ET._Element,
    kind: str,
    num: str,
    context: str,
    force_active: bool,
    pit_date: Optional[str],
    is_eur: bool,
) -> IRNode:
    """Build a section-like ``IRNode`` from ``el`` (P1/P2/P3/P4/Section/Article/...).

    Replacement for the four near-identical ``_parse_section``/``_parse_p2``/
    ``_parse_p3``/``_parse_p4`` bodies that previously shared a
    ``UKMutableNode`` + ``_add_attrs`` + ``node.children = _parse_children()``
    + conditional ``node.text = …`` / ``node.attrs["uk_post_child_text_tail"] = …``
    pattern.  Computes attrs / text / children up front and constructs the
    ``IRNode`` ONCE (string ``kind`` coerced to ``IRNodeKind`` via
    ``uk_ir_node_kind``), so no in-place mutation occurs during parsing.
    """
    attrs = _collect_source_attrs(el)
    children = _parse_children(el, context, force_active, pit_date, is_eur)
    if not children:
        text = _leaf_provision_text(el)
    else:
        text = _local_structural_text(el)
        post_child_tail = _post_child_local_text_tail(el)
        if post_child_tail:
            attrs["uk_post_child_text_tail"] = post_child_tail
    return IRNode(
        kind=uk_ir_node_kind(kind),
        label=num,
        text=text,
        attrs=attrs,
        children=tuple(children),
    )


def _parse_pblock(el, context, force_active=False, pit_date=None, is_eur=False):
    if _is_zombie(el, force_active, pit_date):
        return None
    title = _text_content(el.find(f"./{{{_LEG_NS}}}Title"))
    attrs = _collect_source_attrs(el)
    children = _parse_children(el, context, force_active, pit_date, is_eur)
    return IRNode(
        kind=IRNodeKind.CROSSHEADING,
        label=None,
        text=title,
        attrs=attrs,
        children=tuple(children),
    )


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
    attrs = _collect_source_attrs(el)
    body = el.find(f".//{{{_LEG_NS}}}ScheduleBody")
    children: list[IRNode] = (
        _parse_children(body, "schedule", force_active, pit_date, is_eur)
        if body is not None
        else []
    )
    node = IRNode(
        kind=IRNodeKind.SCHEDULE,
        label=num,
        text=title,
        attrs=attrs,
        children=tuple(children),
    )
    if not force_active:
        node = _maybe_infer_container_number(node, el, prefix="schedule", original_label=raw_num)
    return node


def _parse_schedules(root_el, force_active=False, pit_date=None, is_eur=False) -> list[IRNode]:
    s_el = root_el.find(f".//{{{_LEG_NS}}}Schedules")
    if s_el is None:
        return []
    res: list[IRNode] = []
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
        _UK_NON_SCHEDULE_LIST_ENTRY_RULE_ID,
        _UK_BLOCK_AMENDMENT_TABLE_RULE_ID,
    }
)


def _visible_inline_text_preservation_observation(
    root: ET._Element,
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
    return diagnostic_detail(
        rule_id="uk_visible_inline_text_preserved",
        family="source_shape_preservation",
        phase="source_parse",
        reason=(
            "UK visible inline source tags such as Citation, CitationSubRef, and Term "
            "were preserved as host provision text while remaining non-addressable as "
            "standalone legal units."
        ),
        blocking=False,
        statute_id=statute_id,
        side=version_label,
        source_url=source_path,
        count=count,
        samples=tuple(samples),
    )


def _source_parse_observations(
    root_body: IRNode,
    supplements: list[IRNode],
    *,
    statute_id: str,
    version_label: str,
    source_path: str,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    samples: dict[str, list[dict[str, str]]] = {}

    def _walk(node: IRNode) -> None:
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
                elif rule_id in {
                    _UK_SCHEDULE_LIST_ENTRY_RULE_ID,
                    _UK_NON_SCHEDULE_LIST_ENTRY_RULE_ID,
                }:
                    sample.update(
                        {
                            "source_tag": str(node.attrs.get("source_tag") or ""),
                            "source_ordinal": str(node.attrs.get("source_ordinal") or ""),
                            "source_context": str(node.attrs.get("source_context") or ""),
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
                elif rule_id == _UK_BLOCK_AMENDMENT_TABLE_RULE_ID:
                    sample.update(
                        {
                            "source_container": str(node.attrs.get("source_container") or ""),
                            "row_count": str(len(node.children)),
                            "text": " ".join(node.text.split())[:160],
                        }
                    )
                bucket.append(sample)
        post_child_tail = str(node.attrs.get("uk_post_child_text_tail") or "")
        if post_child_tail:
            tail_rule_id = "uk_post_child_text_tail_preserved"
            counts[tail_rule_id] = counts.get(tail_rule_id, 0) + 1
            bucket = samples.setdefault(tail_rule_id, [])
            if len(bucket) < 5:
                bucket.append(
                    {
                        "kind": node.kind.value,
                        "label": str(node.label or ""),
                        "tail_text": " ".join(post_child_tail.split())[:160],
                    }
                )
        for child in node.children:
            _walk(child)

    _walk(root_body)
    for supplement in supplements:
        _walk(supplement)

    return [
        diagnostic_detail(
            rule_id=rule_id,
            family="source_shape_preservation",
            phase="source_parse",
            reason=(
                "UK source XML structure was preserved as replay-addressable IR rather "
                "than flattened into host text."
            ),
            blocking=False,
            statute_id=statute_id,
            side=version_label,
            source_url=source_path,
            count=count,
            samples=samples.get(rule_id, []),
        )
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
    root: ET._Element,
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

    body_nodes: list[IRNode] = []
    if body_el is not None:
        body_nodes = _parse_children(body_el, "body", False, pit_date, is_eur)

    if is_eur:
        for div in root.findall(f".//{{{_LEG_NS}}}Division"):
            node = _parse_section(div, "preamble", False, pit_date, True)
            if node:
                body_nodes.insert(0, node)

    # Parse boundary (Wave N3d Sub-PR A): ``root_body`` and ``schedule_nodes``
    # are ``IRNode`` trees built WITHOUT in-place parse-time mutation. Each
    # per-element ``_parse_*`` helper constructs the node once via the
    # ``IRNode`` constructor (string kinds coerced to ``IRNodeKind`` via
    # ``uk_ir_node_kind`` at the construction site) with attrs/text/children
    # computed up-front, and uses ``dataclasses.replace`` for any
    # post-construction adjustment.  The frozen ``IRStatute`` returned to
    # callers holds these ``IRNode`` trees directly (no ``UKMutableNode``
    # intermediate layer at the parse boundary).
    root_body = IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        text="",
        children=tuple(body_nodes),
    )
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
        body=root_body,
        supplements=schedule_nodes,
        metadata={
            "source_path": source_path,
            "is_eur": is_eur,
            "version_label": vlabel,
            "source_parse_observations": parse_observations,
            # Thread the CLI-supplied ``--pit-date`` so downstream probes
            # (timeline_invariants, materialization_totality, etc) can read
            # the source-side point-in-time without re-parsing. Resolves the
            # ``pit_date_unavailable`` probe-skip diagnostic emitted by
            # ``probe_uk_timeline_invariants`` when metadata lacks
            # effective_date/enacted_date. Per AGENTS.md §0 evidence-ledger-
            # monotone: don't synthesize — pass through what the caller
            # supplied; ``pit_date`` may be None when the CLI wasn't given
            # ``--pit-date`` (latest-date replay).
            "pit_date": pit_date or "",
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
        parse_corpus_xml(xml_bytes),
        statute_id=statute_id,
        version_label=version_label,
        pit_date=pit_date,
        source_path=source_path,
    )


def _slugify(text: str) -> str:
    if not text:
        return ""
    # Collapse apostrophes (straight and curly) so source headings such as
    # "children's hearings" are canonically sluggable without punctuation noise.
    text = text.replace("'", "").replace("\u2019", "").replace("\u2018", "")
    return _SLUGIFY_NON_ALNUM_RE.sub("-", text.lower()).strip("-")


def _normalize_text_for_grounding(text: str) -> str:
    text = _GROUNDING_NON_WORD_SPACE_RE.sub("", text.lower())
    return " ".join(text.split())


def _semantic_hash(text: str) -> str:
    s = _normalize_text_for_grounding(text)
    return _semantic_hash_from_normalized_text(s)


def _semantic_hash_from_normalized_text(text: str) -> str:
    s = text
    s = _SEMANTIC_HASH_NOISE_RE.sub("", s)
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
    retain_text_elided_text_map: dict[str, str],
    *,
    tag: Optional[str] = None,
    is_known_live: bool = False,
):
    if not is_known_live and _is_zombie(el, False, pit_date):
        return
    if tag is None:
        tag = _tag(el)
    # Skip editorial annotations entirely — they are absent from the enacted XML
    # and must not contribute eIds to the oracle scoring set.
    if tag in _EDITORIAL_TAGS:
        return
    skip_own_eid = tag in _NON_LEGAL_UNIT_EID_TAGS
    eid = el.get("eId") or el.get("id")
    _pnum = el.find(_LEG_PNUMBER_PATH)
    _nnum = el.find(_LEG_NUMBER_PATH)
    num_el = _pnum if _pnum is not None else _nnum
    kind = _get_kind(tag, context, is_eur)
    if num_el is None and kind in ("chapter", "part"):
        num_el = el.find(_LEG_DESC_NUMBER_PATH)
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

    title_el = el.find(_LEG_TITLE_PATH)
    title = _text_content(title_el) if title_el is not None else ""
    slug = _slugify(title)
    node_key_part = f"{kind}-{clean_num}" if clean_num else (f"{kind}-{slug}" if slug else kind)

    if kind in _EID_TRANSPARENT_TAGS:
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
        if text and not _DOT_OR_SPACE_ONLY_RE.match(text):
            norm = _normalize_text_for_grounding(text)
            text_map[eid] = norm
            h = _semantic_hash_from_normalized_text(norm)
            if f"hash:{h}" not in eid_map:
                eid_map[f"hash:{h}"] = eid
            # presentation_cleanup: when the oracle keeps a repealed phrase
            # visible via <Repeal RetainText="true"> (a 1-D consolidation
            # artifact, NOT law — the direct analogue of Finlex's "Aiempi
            # sanamuoto kuuluu:" marker), the provision text is AMBIGUOUS for
            # comparison: it matches both LawVM's repeal-not-applied replay
            # (retained wording present) AND a repeal-applied replay (wording
            # gone).  We register the elided-form text as a parallel,
            # comparison-only oracle variant so EITHER replay form scores as a
            # match — the artifact never raises a spurious text_diff in either
            # direction.  The primary ``text_map`` retained-included text is
            # left untouched; this is oracle-side, comparison-only, and never
            # changes LawVM's compiled ops or materialized text.  Auditable,
            # never silent (AGENTS.md §0/§7).
            if _contains_retained_repeal(el):
                elided_text, retained_repeal_elided = _oracle_text_eliding_retained_repeals(el)
            else:
                elided_text, retained_repeal_elided = "", False
            if retained_repeal_elided:
                elided_norm = _normalize_text_for_grounding(elided_text)
                if elided_norm != norm:
                    retain_text_elided_text_map[eid] = elided_norm
                oracle_identity_observations.append(
                    {
                        "rule_id": _RETAIN_TEXT_ELISION_RULE_ID,
                        "phase": "oracle_compare_normalization",
                        "family": "presentation_cleanup",
                        "original_eid": eid,
                        "xml_tag": tag,
                        "physical_path_key": this_node_path,
                        "strict_disposition": "record",
                        "quirks_disposition": QuirksDisposition.RECORD,
                    }
                )
        if clean_num:
            is_nested_schedule_descendant = False
            if context.startswith("schedule") and parent_path_key:
                if kind in {"paragraph", "subsection", "subparagraph", "item", "point", "p2", "p3", "p4"}:
                    clean_parent = parent_path_key.replace("body:", "")
                    if ":" in clean_parent:
                        is_nested_schedule_descendant = True

            if not is_nested_schedule_descendant:
                eid_map[f"{new_context}:{kind}-{clean_num}".lower()] = eid
                if is_eur and kind == "schedule":
                    eid_map[f"{new_context}:annex-{clean_num}".lower()] = eid
                eid_map[f"{new_context}:suffix:{kind}-{clean_num}".lower()] = eid
                # Also add title-slug alias so pblocks/crossheadings with matching headings
                # can find numbered nodes (e.g. Schedule 1 ECHR article chapters).
                if slug:
                    eid_map[f"{new_context}:suffix:{kind}-{slug}".lower()] = eid
        elif slug:
            is_nested_schedule_descendant = False
            if context.startswith("schedule") and parent_path_key:
                if kind in {"paragraph", "subsection", "subparagraph", "item", "point", "p2", "p3", "p4"}:
                    clean_parent = parent_path_key.replace("body:", "")
                    if ":" in clean_parent:
                        is_nested_schedule_descendant = True
            if not is_nested_schedule_descendant:
                eid_map[f"{new_context}:suffix:{kind}-{slug}".lower()] = eid

        # ontology_normalization: current-oracle CLML often wraps a schedule
        # P1group/Title in an explicit <Pblock id="...crossheading-slug"> whose
        # own <Title> is empty.  The crossheading text lives on the child P1group
        # in the enacted XML, but the current oracle may have removed the P1group
        # wrapper and placed the paragraph directly inside the Pblock while keeping
        # the slug only in the EID.  Register an alias from the child title slug or
        # from the EID's crossheading suffix so replay can ground the schedule
        # P1group wrapper to the official crossheading EID.
        if (
            eid
            and kind == "crossheading"
            and not clean_num
            and not slug
        ):
            _inferred_slug = ""
            _p1g_title_el = el.find(_LEG_P1GROUP_TITLE_PATH)
            if _p1g_title_el is not None:
                _inferred_slug = _slugify(_text_content(_p1g_title_el))
            if not _inferred_slug and "-crossheading-" in str(eid or ""):
                _eid_slug_part = str(eid).rsplit("-crossheading-", 1)[1]
                _inferred_slug = _slugify(_eid_slug_part)
            if _inferred_slug:
                _hierarchical_alias = (
                    f"{parent_path_key}:crossheading-{_inferred_slug}".lower()
                    if parent_path_key
                    else f"crossheading-{_inferred_slug}".lower()
                )
                for _alias_key in (
                    _hierarchical_alias,
                    f"{new_context}:suffix:crossheading-{_inferred_slug}".lower(),
                ):
                    if _alias_key not in eid_map:
                        eid_map[_alias_key] = eid
                oracle_identity_observations.append(
                    {
                        "rule_id": "uk_oracle_empty_crossheading_title_inferred_from_p1group",
                        "phase": "oracle_alignment",
                        "family": "ontology_normalization",
                        "eid": eid,
                        "inferred_crossheading_slug": _inferred_slug,
                        "xml_tag": tag,
                        "physical_path_key": this_node_path,
                        "strict_disposition": "record",
                        "quirks_disposition": QuirksDisposition.RECORD,
                    }
                )

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
        if ck not in _EID_TRANSPARENT_TAGS:
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
            retain_text_elided_text_map,
            tag=ct,
            is_known_live=True,
        )


def _extract_eid_map_from_root(root: Any, pit_date: Optional[str] = None) -> Dict[str, Any]:
    eid_map = {}
    text_map = {}
    physical_eid_aliases: dict[str, str] = {}
    visible_number_eid_aliases: dict[str, str] = {}
    oracle_identity_observations: list[dict[str, Any]] = []
    # presentation_cleanup: parallel, comparison-only variant of ``text_map`` —
    # for any provision whose oracle text holds a <Repeal RetainText="true">
    # retained phrase, the same text with that phrase elided.  Lets the
    # comparison accept EITHER form (repeal applied / not applied) so the 1-D
    # consolidation artifact is neutral.  See _oracle_text_eliding_retained_repeals.
    retain_text_elided_text_map: dict[str, str] = {}
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
            retain_text_elided_text_map,
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
            retain_text_elided_text_map,
        )
    return {
        "eid_map": eid_map,
        "text_map": text_map,
        "retain_text_elided_text_map": retain_text_elided_text_map,
        "physical_eid_aliases": physical_eid_aliases,
        "visible_number_eid_aliases": visible_number_eid_aliases,
        "oracle_identity_observations": oracle_identity_observations,
    }


def extract_eid_map(xml_path: Path, pit_date: Optional[str] = None) -> Dict[str, Any]:
    tree = ET.parse(str(xml_path))
    return _extract_eid_map_from_root(tree.getroot(), pit_date=pit_date)


def extract_eid_map_bytes(xml_bytes: bytes, pit_date: Optional[str] = None) -> Dict[str, Any]:
    root = parse_corpus_xml(xml_bytes)
    return _extract_eid_map_from_root(root, pit_date=pit_date)
