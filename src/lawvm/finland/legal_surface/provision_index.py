"""Recover the AKN provision structure (§/momentti/kohta) the body decode drops.

The body decode (:func:`lawvm.finland.legal_surface.bundle.decode_body_text`)
flattens the statute body to ``<p>`` content joined by newlines. That flattening
DROPS the sibling ``<num>`` markers (``1 §``, ``1)``) and the
``<section>``/``<subsection>``/``<paragraph>`` container nesting, so the decoded
text carries no provision boundaries: there is no way to know which §/momentti/
kohta a given char span sits in. This blocks enclosing-section anaphora
(``Tätä pykälää ei sovelleta…``), span-scoped Layer-2 composition, and any
"which provision owns this norm" query.

This module re-attaches that identity as an ADDITIVE side index. It re-walks the
SAME ``<p>`` set, in the SAME document order, joined the SAME way, so each ``<p>``
maps to the EXACT char range it occupies in the decoded body text — and records,
for each, the enclosing provision path (eId + decomposed §/momentti/kohta labels)
read from the AKN ancestry. The provision path is SOURCED FROM THE STRUCTURE
(eId, then ``<num>`` surface), never regex-guessed from the flattened text. A
``<p>`` whose ancestry yields no provision identity is recorded fail-loud
(``mapped=False`` with a reason), never given a fabricated path.

This NEVER touches ``decode_body_text`` or the segmentation/clause indices: it is
a parallel :class:`ProvisionIndex` over the same coordinate space, attached to the
bundle unit's ``metadata`` like ``segmentation_graph``. The existing flattened
text + sentence/clause/structural segmentation the construction islands consume
stay byte-identical.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from lawvm.core.legal_surface_tokens import ProvisionIndex, ProvisionSpan
from lawvm.finland.legal_surface.editorial_filter import (
    iter_operative_paragraphs,
    operative_itertext,
)

# These container localnames carry provision identity. Mirrors the AKN hierarchy
# Finlex emits (chapter ▸ section ▸ subsection ▸ paragraph). ``article`` is the
# treaty/EU analogue of ``section``; both anchor the §-level label.
_CHAPTER_TAGS = frozenset({"chapter"})
_SECTION_TAGS = frozenset({"section", "article"})
_SUBSECTION_TAGS = frozenset({"subsection"})
_ITEM_TAGS = frozenset({"paragraph", "point", "item"})

# eId label extractors — the SAME forms the reference resolver parses
# (``chp_N``, ``sec_M[vNNN]``, ``subsec_K[vNNN]``, ``para_L``). Version suffix
# ``vNNNN`` is stripped so the label is version-agnostic.
_CHP_EID_RE = re.compile(r"(?:^|__)chp_([0-9a-zA-Z]{1,32})v\d{1,8}(?=__|$)|(?:^|__)chp_([0-9a-zA-Z]{1,32})(?=__|$)")
_SEC_EID_RE = re.compile(r"(?:^|__)sec_([0-9a-zA-Z]{1,32})v\d{1,8}(?=__|$)|(?:^|__)sec_([0-9a-zA-Z]{1,32})(?=__|$)")
_SUBSEC_EID_RE = re.compile(r"(?:^|__)subsec_(\d{1,8})v\d{1,8}(?=__|$)|(?:^|__)subsec_(\d{1,8})(?=__|$)")
_ITEM_EID_RE = re.compile(r"(?:^|__)(?:para|point|item)_([0-9a-zA-Z]{1,32})v\d{1,8}(?=__|$)|(?:^|__)(?:para|point|item)_([0-9a-zA-Z]{1,32})(?=__|$)")

# Bare label from a ``<num>`` surface, used when a container carries no eId
# (pre-eId Finlex consolidations). ``1 §`` -> ``1``; ``115 a §`` -> ``115a``;
# ``1 luku`` -> ``1``; ``2)`` -> ``2``; ``a)`` -> ``a``.
_NUM_LABEL_RE = re.compile(r"([0-9]{1,6})\s*([a-zA-Z\xe4\xf6\xc4\xd6])?")
_NUM_ALPHA_RE = re.compile(r"([a-zA-Z\xe4\xf6\xc4\xd6])")


def _localname(tag: object) -> str:
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _first_match_group(match: re.Match[str]) -> str:
    for value in match.groups():
        if value:
            return value
    return ""


def _num_surface(el: ET.Element) -> str | None:
    """The ``<num>`` child surface of ``el`` (trimmed), or None."""
    for child in el:
        if _localname(child.tag) == "num":
            return (child.text or "").strip() or None
    return None


def _section_label_from_num(num: str) -> str | None:
    m = _NUM_LABEL_RE.match(num)
    if m is None:
        return None
    return m.group(1) + (m.group(2) or "").lower()


def _item_label_from_num(num: str) -> str | None:
    """Bare item label from a kohta ``<num>`` (``2)`` -> ``2``, ``a)`` -> ``a``)."""
    m = _NUM_LABEL_RE.match(num)
    if m is not None and m.group(1):
        return m.group(1) + (m.group(2) or "").lower()
    am = _NUM_ALPHA_RE.match(num)
    if am is not None:
        return am.group(1).lower()
    return None


class _PathState:
    """The provision path accumulated down one ancestry chain.

    Each enclosing container contributes its level's label (eId-derived where
    present, else the ``<num>`` surface). ``mapped`` is True once any level was
    identified; an isolated ``<p>`` with no provision ancestor stays unmapped.
    """

    __slots__ = (
        "eid",
        "chapter_label",
        "section_label",
        "subsection_num",
        "item_label",
        "mapped",
    )

    def __init__(self) -> None:
        self.eid = ""
        self.chapter_label = ""
        self.section_label = ""
        self.subsection_num: int | None = None
        self.item_label = ""
        self.mapped = False


def _apply_container(state: _PathState, el: ET.Element, local: str) -> None:
    """Fold one enclosing provision container into ``state`` (deepest wins eId)."""
    eid = el.get("eId") or ""
    num = _num_surface(el)
    if eid:
        state.eid = eid  # deepest container's eId is the canonical identity

    if local in _CHAPTER_TAGS:
        label = None
        if eid:
            m = _CHP_EID_RE.search(eid)
            if m is not None:
                label = _first_match_group(m)
        if label is None and num is not None:
            label = _section_label_from_num(num)
        if label is not None:
            state.chapter_label = label
            state.mapped = True
    elif local in _SECTION_TAGS:
        label = None
        if eid:
            m = _SEC_EID_RE.search(eid)
            if m is not None:
                label = _first_match_group(m)
        if label is None and num is not None:
            label = _section_label_from_num(num)
        if label is not None:
            state.section_label = label
            state.mapped = True
    elif local in _SUBSECTION_TAGS:
        n: int | None = None
        if eid:
            m = _SUBSEC_EID_RE.search(eid)
            if m is not None:
                n = int(_first_match_group(m))
        if n is not None:
            state.subsection_num = n
            state.mapped = True
    elif local in _ITEM_TAGS:
        label = None
        if eid:
            m = _ITEM_EID_RE.search(eid)
            if m is not None:
                label = _first_match_group(m)
        if label is None and num is not None:
            label = _item_label_from_num(num)
        if label is not None:
            state.item_label = label
            state.mapped = True


def build_provision_index(
    xml_bytes: bytes,
    source_unit_id: str,
    *,
    body_text: str,
    text_hash: str,
) -> ProvisionIndex:
    """Build a :class:`ProvisionIndex` parallel to the decoded body text.

    Re-walks the ``<p>`` set in document order, reproducing
    :func:`decode_body_text`'s newline join EXACTLY, so each ``<p>``'s char range
    in ``body_text`` is known. For each ``<p>`` it folds the enclosing provision
    containers (chapter ▸ section ▸ subsection ▸ paragraph) into a path, sourced
    from the AKN structure (eId then ``<num>``). A ``<p>`` with no provision
    ancestor is recorded fail-loud (``mapped=False``).

    ``body_text`` / ``text_hash`` are passed in (the bundle already decoded them)
    so this never re-decodes; the reproduced join is asserted byte-identical to
    ``body_text`` (a drift guard — if it ever diverges from ``decode_body_text``
    the index would be built over the wrong coordinate space, so it raises rather
    than emit a silently-misaligned index).
    """
    spans: list[ProvisionSpan] = []
    if not xml_bytes or not body_text:
        return ProvisionIndex(
            source_unit_id=source_unit_id, text_hash=text_hash, spans=()
        )
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ProvisionIndex(
            source_unit_id=source_unit_id, text_hash=text_hash, spans=()
        )

    # Consume the SHARED operative-<p> enumeration (editorial_filter) that
    # ``decode_body_text`` also consumes: same paragraph set, same document
    # order, same per-<p> operative text (non-operative editorial subtrees
    # skipped identically). Sharing the enumeration keeps the reproduced join
    # byte-identical to ``decode_body_text`` BY CONSTRUCTION; the drift guard
    # below is then a redundant safety net. ``stack`` is the enclosing
    # provision-container ancestry for each <p>.
    rebuilt: list[str] = []  # the reproduced per-<p> content, for the join guard
    cursor = 0
    for p_el, stack in iter_operative_paragraphs(root):
        content = "".join(operative_itertext(p_el))
        # Reproduce the newline join: paragraphs after the first are preceded by
        # a single '\n' in the decoded body.
        if rebuilt:
            cursor += 1  # the '\n' separator (a residual gap, not in the index)
        start = cursor
        end = start + len(content)
        cursor = end
        rebuilt.append(content)

        state = _PathState()
        for anc in stack:
            _apply_container(state, anc, _localname(anc.tag))
        if state.mapped:
            spans.append(
                ProvisionSpan(
                    char_start=start,
                    char_end=end,
                    eid=state.eid,
                    chapter_label=state.chapter_label,
                    section_label=state.section_label,
                    subsection_num=state.subsection_num,
                    item_label=state.item_label,
                    mapped=True,
                )
            )
        else:
            spans.append(
                ProvisionSpan(
                    char_start=start,
                    char_end=end,
                    mapped=False,
                    unmapped_reason="no_provision_ancestor_eid_or_num",
                )
            )

    # Drift guard: the reproduced join MUST equal the bundle's body text, else the
    # index is anchored in the wrong coordinate space (fail-loud, never misalign).
    if "\n".join(rebuilt) != body_text:
        raise ValueError(
            "provision index join diverged from decode_body_text output for "
            f"{source_unit_id!r}; refusing to emit a misaligned index"
        )

    return ProvisionIndex(
        source_unit_id=source_unit_id, text_hash=text_hash, spans=tuple(spans)
    )
