"""Named Finland XML normalization rule registry.

Each rule is a named callable that rewrites a list of IRNode children.
Rules are grouped by the structural level at which they fire:

- ``SECTION_RULES``       -- applied to the children of a ``<section>`` node,
                             before the positional-label counter.
- ``SUBSECTION_PRE_RULES``  -- applied to the children of a ``<subsection>``
                             node, before the positional-label counter.
- ``SUBSECTION_POST_RULES`` -- applied to the children of a ``<subsection>``
                             node, after the positional-label counter.

Each rule has:
- ``name``   -- observation identifier, e.g. ``"fi.renest_flat_digit_item_subsections"``
- ``apply``  -- callable ``(children: List[IRNode]) -> List[IRNode]``
- ``description`` -- one-line English description

When a rule fires (returns a *different* list object than its input), an
observation dict ``{"rule": rule.name, "fired": True}`` is appended to
``observations_out`` if that list is provided.

Usage::

    from lawvm.finland.profile.normalize import (
        apply_section_rules,
        apply_subsection_pre_rules,
        apply_subsection_post_rules,
    )

    section_children = apply_section_rules(section_children, observations_out=obs)
    # ... positional counter runs ...
    subsection_children = apply_subsection_pre_rules(sub_children, obs)
    # ... positional counter runs ...
    subsection_children = apply_subsection_post_rules(sub_children, obs)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _norm_num_token, may_attach_post_list_loppukappale
from lawvm.finland.labels import roman_to_arabic as _roman_to_arabic
from lawvm.xml_ingest import (
    _SENTENCING_START_RE,
    _paragraph_ends_with_terminal_punctuation,
    _paragraph_first_text,
    _paragraph_has_num,
    _paragraph_is_content_only,
    _split_trailing_content_only_paragraphs_into_subsections as _split_trailing_base,
)


# ---------------------------------------------------------------------------
# Rule descriptor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizationRule:
    """A named, independently-testable Finland XML normalization rule.

    Each rule carries a ``family`` tag classifying the kind of normalization
    it performs.  Downstream adjudication can filter observations by family
    (e.g. "show me all ontology normalization events for this statute").

    Family vocabulary
    -----------------
    ``"transport_cleanup"``
        Mechanical XML artifacts with no legal-ontology implication —
        omission marker hoisting, wrapper splitting at artificial boundaries.
    ``"ontology_normalization"``
        Repairs an ontology mismatch: reparenting, nesting flat items,
        recovering embedded numbers, hoisting trailing prose to wrapUp.
        The result is a structurally valid Finnish legal-ontology shape.
    ``"historical_tolerance"``
        Accepts or normalizes a shape that the opas forbids but that older
        statutes in the corpus use.  These rules exist to avoid breaking
        pre-reform statutes that predate the opas conventions.
    ``"presentation_cleanup"``
        Strips editorial annotations or display artifacts that are not part
        of the statute's legal content.  (Currently no rules in this
        registry have this family; editorial normalization lives in
        ``inline_repeal_stub.py`` and ``editorial_hygiene.py``.)
    """

    name: str
    apply: Callable[[List[IRNode]], List[IRNode]]
    description: str
    family: str = "ontology_normalization"


# ---------------------------------------------------------------------------
# Registry application helper
# ---------------------------------------------------------------------------

def apply_all(
    children: List[IRNode],
    rules: List[NormalizationRule],
    observations_out: Optional[List[Dict[str, object]]] = None,
) -> List[IRNode]:
    """Apply each rule in *rules* to *children* in order.

    When a rule fires (returns a different list object), an observation dict
    is appended to *observations_out* if provided.
    """
    for rule in rules:
        result = rule.apply(children)
        if result is not children:
            if observations_out is not None:
                observations_out.append({"rule": rule.name, "family": rule.family, "fired": True})
            children = result
    return children


# ---------------------------------------------------------------------------
# Regexes used across rules
# ---------------------------------------------------------------------------

_EMBEDDED_PARAGRAPH_NUM_RE = re.compile(r"^\s*([0-9]+[a-zA-Z]?|[a-zA-Z]+)\s*\)\s*(.+)$")
_EMBEDDED_DOTTED_NUM_RE = re.compile(r"^\s*([0-9]+[a-zA-Z]?)\.\s+(.+)$")
_EMBEDDED_PLAIN_NUM_RE = re.compile(r"^\s*([0-9]+[a-zA-Z]?)\s+(.+)$")
_EMBEDDED_SECTION_MARK_RE = re.compile(r"^\s*(\d+[a-zA-Z]?)\s*§\.?\s*$")
_FLAT_DIGIT_ITEM_RE = re.compile(r"^(\d+[a-z]?)\)\s")
_FLAT_DOT_ITEM_RE = re.compile(r"^(\d+[a-z]?)\.\s+(.+)$")
_FLAT_DASH_ITEM_RE = re.compile(r"^[–—\-]\s")


# ---------------------------------------------------------------------------
# Shared structural helpers used by multiple rules
# ---------------------------------------------------------------------------

def _paragraph_has_introducer_signal(para: IRNode) -> bool:
    """Return True when a paragraph's content text carries a sublist-introducer signal.

    Signals checked:
    1. Content text ends with ``:`` (canonical Finnish drafting signal).
    2. Content text ends with a continuation comma or terminal ``ja`` / ``tai``.
    3. Content text contains a recognised Finnish introducer phrase.
    """
    _FI_INTRODUCER_PHRASES = (
        "johon kuuluu",
        "seuraavasti",
        "tarkoitetaan",
        "jossa",
        "joiden mukaan",
        "joilla tarkoitetaan",
        "jolla tarkoitetaan",
    )
    parts: List[str] = []
    for child in para.children:
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO):
            t = irnode_to_text(child).strip()
            if t:
                parts.append(t)
    if not parts and para.text:
        parts.append(para.text.strip())
    text = " ".join(parts).strip()
    if not text:
        return False
    if text.endswith(":"):
        return True
    text_lower_stripped = text.lower().rstrip()
    if text.endswith(",") or text_lower_stripped.endswith((" ja", " tai")):
        return True
    text_lower = text_lower_stripped
    return any(phrase in text_lower for phrase in _FI_INTRODUCER_PHRASES)


def _subsection_leaf_text(node: IRNode) -> Optional[str]:
    """Extract the leaf text content from a simple subsection."""
    parts: List[str] = []
    for child in node.children:
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO):
            if child.text:
                parts.append(child.text.strip())
    if parts:
        return " ".join(parts)
    if node.text and node.text.strip():
        return node.text.strip()
    return None


def _subsection_has_structured_children(node: IRNode) -> bool:
    """Return True if the subsection has paragraph/subparagraph/subsection children."""
    return any(
        child.kind in (IRNodeKind.PARAGRAPH, IRNodeKind.SUBPARAGRAPH, IRNodeKind.SUBSECTION)
        for child in node.children
    )


def _renumber_subsections(children: List[IRNode]) -> List[IRNode]:
    """Renumber subsection labels sequentially after container-level section splitting."""
    counter = 0
    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue
        counter += 1
        rewritten.append(
            IRNode(
                kind=child.kind,
                label=str(counter),
                text=child.text,
                attrs=child.attrs,
                children=child.children,
            )
        )
    return rewritten


def _apply_fi_split_embedded_section_restarts(children: List[IRNode]) -> List[IRNode]:
    """Split a malformed section carrying later section markers into sibling sections.

    Historical Finlex base XML sometimes flattens multiple later sections into one
    earlier section by serializing plain content-only subsections like ``20 §.``,
    ``21 §.``, ``22 §.`` inside that section. Those marker subsections should
    become real sibling SECTION nodes under the enclosing chapter/body container.
    """
    rewritten: List[IRNode] = []
    changed = False
    for child in children:
        if child.kind != IRNodeKind.SECTION or not child.label:
            rewritten.append(child)
            continue

        section_children = list(child.children)
        marker_positions: List[int] = []
        marker_labels: List[str] = []
        marker_texts: List[str] = []
        previous_numeric_match = re.match(r"^\d+", _norm_num_token(str(child.label)))
        if previous_numeric_match is None:
            rewritten.append(child)
            continue
        previous_numeric = int(previous_numeric_match.group(0))

        for idx, grandchild in enumerate(section_children):
            if grandchild.kind != IRNodeKind.SUBSECTION or _subsection_has_structured_children(grandchild):
                continue
            marker_text = _subsection_leaf_text(grandchild)
            if not marker_text:
                continue
            marker_match = _EMBEDDED_SECTION_MARK_RE.match(marker_text.strip())
            if marker_match is None:
                continue
            marker_label = _norm_num_token(marker_match.group(1))
            numeric_match = re.match(r"^\d+", marker_label)
            if numeric_match is None:
                continue
            numeric_value = int(numeric_match.group(0))
            if numeric_value <= previous_numeric:
                continue
            marker_positions.append(idx)
            marker_labels.append(marker_label)
            marker_texts.append(marker_text.strip().rstrip("."))
            previous_numeric = numeric_value

        if not marker_positions:
            rewritten.append(child)
            continue

        split_groups: List[tuple[str, str, List[IRNode]]] = []
        valid = marker_positions[0] > 0
        for marker_idx, marker_pos in enumerate(marker_positions):
            next_pos = marker_positions[marker_idx + 1] if marker_idx + 1 < len(marker_positions) else len(section_children)
            body_children = list(section_children[marker_pos + 1 : next_pos])
            if not body_children:
                valid = False
                break
            if any(
                body_child.kind != IRNodeKind.SUBSECTION or _subsection_has_structured_children(body_child)
                for body_child in body_children
            ):
                valid = False
                break
            split_groups.append((marker_labels[marker_idx], marker_texts[marker_idx], body_children))

        if not valid:
            rewritten.append(child)
            continue

        kept_children = tuple(section_children[: marker_positions[0]])
        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=kept_children,
            )
        )
        for marker_label, marker_text, body_children in split_groups:
            rewritten.append(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label=marker_label,
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text=marker_text),
                        *_renumber_subsections(body_children),
                    ),
                )
            )
        changed = True
    return rewritten if changed else children


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _apply_recover_embedded_numbered_paragraphs(children: List[IRNode]) -> List[IRNode]:
    """Recover malformed paragraph numbering serialized inside content text."""
    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue
        if any(grandchild.kind == IRNodeKind.NUM for grandchild in child.children):
            rewritten.append(child)
            continue
        if len(child.children) != 1 or child.children[0].kind != IRNodeKind.CONTENT:
            rewritten.append(child)
            continue
        content = child.children[0].text or ""
        match = _EMBEDDED_PARAGRAPH_NUM_RE.match(content)
        if match is None:
            rewritten.append(child)
            continue
        label, remainder = match.groups()
        remainder = remainder.strip()
        if not remainder:
            rewritten.append(child)
            continue
        rewritten.append(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=label.lower(),
                text=child.text,
                attrs=child.attrs,
                children=(
                    IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
                    IRNode(kind=IRNodeKind.CONTENT, text=remainder),
                ),
            )
        )
    return rewritten


def _apply_recover_intro_labeled_paragraphs(children: List[IRNode]) -> List[IRNode]:
    """Recover item labels encoded in <intro> text when a <paragraph> has no <num> child."""
    rewritten: List[IRNode] = []
    changed = False
    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue
        if any(grandchild.kind == IRNodeKind.NUM for grandchild in child.children):
            rewritten.append(child)
            continue
        intro_node = next(
            (grandchild for grandchild in child.children if grandchild.kind == IRNodeKind.INTRO),
            None,
        )
        if intro_node is None:
            rewritten.append(child)
            continue
        intro_text = intro_node.text or ""
        match = _EMBEDDED_PARAGRAPH_NUM_RE.match(intro_text)
        dotted = False
        if match is None:
            match = _EMBEDDED_DOTTED_NUM_RE.match(intro_text)
            dotted = match is not None
        if match is None:
            rewritten.append(child)
            continue
        label, remainder = match.groups()
        remainder = remainder.strip()
        new_intro = IRNode(
            kind=IRNodeKind.INTRO,
            label=intro_node.label,
            text=remainder,
            attrs=intro_node.attrs,
            children=intro_node.children,
        )
        new_children = tuple(
            new_intro if grandchild is intro_node else grandchild
            for grandchild in child.children
        )
        # Carry the __num_from_intro__ marker so that later passes
        # (e.g. _reparent_sub_clause_with_list_peers in source_normalize)
        # can tell this paragraph had its number encoded in <intro> text
        # rather than in a <num> element.  It is a genuine numbered kohta,
        # not an unnumbered continuation peer.
        new_attrs = dict(child.attrs)
        new_attrs["__num_from_intro__"] = "1"
        rewritten.append(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=(f"{label.lower()}." if dotted else label.lower()),
                text=child.text,
                attrs=new_attrs,
                children=new_children,
            )
        )
        changed = True
    return rewritten if changed else children


def _apply_nest_lettered_subparagraphs(children: List[IRNode]) -> List[IRNode]:
    """Nest letter-labeled paragraphs as subparagraph children of the correct digit paragraph."""
    para_label_counts: Dict[str, int] = {}
    for child in children:
        if child.kind == IRNodeKind.PARAGRAPH and child.label:
            para_label_counts[child.label] = para_label_counts.get(child.label, 0) + 1
    duplicate_labels = {lbl for lbl, cnt in para_label_counts.items() if cnt > 1}
    if not duplicate_labels:
        return children

    _letter_re = re.compile(r"^[a-z]+$")

    def _is_roman_label(lbl: Optional[str]) -> bool:
        return bool(lbl and _roman_to_arabic(lbl) is not None)

    if not any(_letter_re.match(lbl) for lbl in duplicate_labels):
        return children

    has_mixed_compound_family = any(
        len(lbl) == 1 and cnt > 1 for lbl, cnt in para_label_counts.items() if _letter_re.match(lbl)
    ) and any(len(lbl) > 1 for lbl in para_label_counts if _letter_re.match(lbl))

    def _is_digit_label(lbl: Optional[str]) -> bool:
        return bool(lbl and lbl.rstrip(".)").isdigit())

    def _is_letter_label(lbl: Optional[str]) -> bool:
        return bool(lbl and _letter_re.match(lbl))

    digit_labels = [lbl for lbl in para_label_counts if _is_digit_label(lbl)]
    has_dense_digit_family = len(digit_labels) >= 3
    duplicate_roman_labels = {
        lbl for lbl, cnt in para_label_counts.items()
        if cnt > 1 and _is_roman_label(lbl)
    }

    def _make_subparagraph(para: IRNode) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label=para.label,
            text=para.text,
            attrs=para.attrs,
            children=para.children,
        )

    def _attach_subs(parent: IRNode, subs: List[IRNode]) -> IRNode:
        return IRNode(
            kind=parent.kind,
            label=parent.label,
            text=parent.text,
            attrs=parent.attrs,
            children=tuple(parent.children) + tuple(subs),
        )

    result: List[IRNode] = []
    pending_parent: Optional[IRNode] = None
    pending_parent_has_introducer: bool = False
    deferred_letters: List[IRNode] = []

    def _flush_pending_flat() -> None:
        nonlocal pending_parent, pending_parent_has_introducer, deferred_letters
        if pending_parent is not None:
            result.append(pending_parent)
            pending_parent = None
            pending_parent_has_introducer = False
        for letter_para in deferred_letters:
            result.append(letter_para)
        deferred_letters = []

    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            _flush_pending_flat()
            result.append(child)
            continue

        lbl = child.label
        if _is_digit_label(lbl):
            if deferred_letters:
                if _paragraph_has_introducer_signal(child):
                    if pending_parent is not None:
                        result.append(pending_parent)
                        pending_parent = None
                        pending_parent_has_introducer = False
                    subs = [_make_subparagraph(lp) for lp in deferred_letters]
                    deferred_letters = []
                    pending_parent = _attach_subs(child, subs)
                    pending_parent_has_introducer = True
                    continue
                else:
                    _flush_pending_flat()
            else:
                if pending_parent is not None:
                    result.append(pending_parent)
                    pending_parent = None
                    pending_parent_has_introducer = False
            pending_parent = child
            pending_parent_has_introducer = _paragraph_has_introducer_signal(child)

        elif pending_parent is not None and (
            _is_letter_label(lbl) or (_is_roman_label(lbl) and lbl in duplicate_roman_labels)
        ):
            if pending_parent_has_introducer:
                sub = _make_subparagraph(child)
                pending_parent = _attach_subs(pending_parent, [sub])
            elif (
                _is_roman_label(lbl)
                and lbl in duplicate_roman_labels
                and pending_parent.label is not None
                and len(str(pending_parent.label)) == 1
                and _letter_re.match(str(pending_parent.label))
            ):
                sub = _make_subparagraph(child)
                pending_parent = _attach_subs(pending_parent, [sub])
            elif has_mixed_compound_family:
                sub = _make_subparagraph(child)
                pending_parent = _attach_subs(pending_parent, [sub])
            elif has_dense_digit_family and _is_letter_label(lbl):
                sub = _make_subparagraph(child)
                pending_parent = _attach_subs(pending_parent, [sub])
            else:
                deferred_letters.append(child)
        else:
            _flush_pending_flat()
            result.append(child)

    _flush_pending_flat()
    return result


def _apply_nest_repeated_alpha_subparagraphs_under_alpha_parents(
    children: List[IRNode],
) -> List[IRNode]:
    """Nest repeated alphabetic subitems under alphabetic parent items with introducers."""
    alpha_re = re.compile(r"^[a-z]+$")
    para_label_counts: Dict[str, int] = {}
    for child in children:
        if child.kind == IRNodeKind.PARAGRAPH and child.label and alpha_re.match(child.label):
            para_label_counts[child.label] = para_label_counts.get(child.label, 0) + 1
    duplicate_alpha_labels = {
        lbl for lbl, cnt in para_label_counts.items() if cnt > 1 and alpha_re.match(lbl)
    }
    if not duplicate_alpha_labels:
        return children

    def _make_subparagraph(para: IRNode) -> IRNode:
        return IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label=para.label,
            text=para.text,
            attrs=para.attrs,
            children=para.children,
        )

    def _attach_subs(parent: IRNode, subs: List[IRNode]) -> IRNode:
        return IRNode(
            kind=parent.kind,
            label=parent.label,
            text=parent.text,
            attrs=parent.attrs,
            children=tuple(parent.children) + tuple(subs),
        )

    result: List[IRNode] = []
    pending_parent: Optional[IRNode] = None

    def _flush_pending() -> None:
        nonlocal pending_parent
        if pending_parent is not None:
            result.append(pending_parent)
            pending_parent = None

    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH or not child.label or not alpha_re.match(child.label):
            _flush_pending()
            result.append(child)
            continue

        if (
            child.label not in duplicate_alpha_labels
            and _paragraph_has_introducer_signal(child)
        ):
            _flush_pending()
            pending_parent = child
            continue

        if pending_parent is not None and child.label in duplicate_alpha_labels:
            pending_parent = _attach_subs(pending_parent, [_make_subparagraph(child)])
            continue

        _flush_pending()
        result.append(child)

    _flush_pending()
    return result


def _apply_nest_repeated_digit_subparagraphs(children: List[IRNode]) -> List[IRNode]:
    """Nest repeated digit-labeled paragraphs as subparagraphs of the earlier item."""
    para_label_counts: Dict[str, int] = {}
    for child in children:
        if child.kind == IRNodeKind.PARAGRAPH and child.label:
            para_label_counts[child.label] = para_label_counts.get(child.label, 0) + 1
    duplicate_digit_labels = {
        lbl for lbl, cnt in para_label_counts.items()
        if cnt > 1 and lbl and lbl.rstrip(".)").isdigit()
    }
    if not duplicate_digit_labels:
        return children

    def _is_digit_label(lbl: Optional[str]) -> bool:
        return bool(lbl and lbl.rstrip(".)").isdigit())

    def _body_text(para: IRNode) -> str:
        parts: List[str] = []
        for child in para.children:
            if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO):
                text = irnode_to_text(child).strip()
                if text:
                    parts.append(text)
        if not parts and para.text:
            parts.append(para.text.strip())
        return " ".join(parts).strip()

    def _rewrite_as_subparagraph(para: IRNode, label: str, remainder: str) -> IRNode:
        new_children: List[IRNode] = []
        replaced_content = False
        for child in para.children:
            if child.kind == IRNodeKind.NUM:
                continue
            if not replaced_content and child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO):
                new_children.append(
                    IRNode(
                        kind=child.kind,
                        label=child.label,
                        text=remainder,
                        attrs=child.attrs,
                        children=child.children,
                    )
                )
                replaced_content = True
            else:
                new_children.append(child)
        if not replaced_content:
            new_children.append(IRNode(kind=IRNodeKind.CONTENT, text=remainder))
        return IRNode(
            kind=IRNodeKind.SUBPARAGRAPH,
            label=label,
            text=para.text,
            attrs=para.attrs,
            children=tuple(new_children),
        )

    def _attach_subs(parent: IRNode, subs: List[IRNode]) -> IRNode:
        return IRNode(
            kind=parent.kind,
            label=parent.label,
            text=parent.text,
            attrs=parent.attrs,
            children=tuple(parent.children) + tuple(subs),
        )

    result: List[IRNode] = []
    pending_parent: Optional[IRNode] = None
    pending_parent_norm: Optional[str] = None

    def _flush_pending() -> None:
        nonlocal pending_parent, pending_parent_norm
        if pending_parent is not None:
            result.append(pending_parent)
            pending_parent = None
            pending_parent_norm = None

    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH or not _is_digit_label(child.label):
            _flush_pending()
            result.append(child)
            continue

        norm = _norm_num_token(str(child.label))
        body_text = _body_text(child)
        match = _EMBEDDED_PLAIN_NUM_RE.match(body_text)
        if pending_parent is not None and pending_parent_norm == norm and match is not None:
            nested_label, remainder = match.groups()
            remainder = remainder.strip()
            if remainder:
                pending_parent = _attach_subs(
                    pending_parent,
                    [_rewrite_as_subparagraph(child, nested_label.lower(), remainder)],
                )
                continue

        _flush_pending()
        pending_parent = child
        pending_parent_norm = norm

    _flush_pending()
    return result


def _apply_fi_renest_flat_digit_item_subsections(children: List[IRNode]) -> List[IRNode]:
    """Re-nest flat digit-item subsections as paragraph children of an intro subsection."""
    rewritten: List[IRNode] = []
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue
        intro_text = _subsection_leaf_text(child)
        if not intro_text or not intro_text.rstrip().endswith(":"):
            rewritten.append(child)
            i += 1
            continue
        if _subsection_has_structured_children(child):
            rewritten.append(child)
            i += 1
            continue
        digit_items: List[IRNode] = []
        j = i + 1
        while j < len(children):
            sib = children[j]
            if sib.kind != IRNodeKind.SUBSECTION:
                break
            if _subsection_has_structured_children(sib):
                break
            sib_text = _subsection_leaf_text(sib)
            if not sib_text:
                break
            m = _FLAT_DIGIT_ITEM_RE.match(sib_text)
            if m is None:
                break
            digit_items.append(sib)
            j += 1
        if not digit_items:
            rewritten.append(child)
            i += 1
            continue
        new_children: List[IRNode] = []
        new_children.append(IRNode(kind=IRNodeKind.INTRO, text=intro_text.strip()))
        for item_sub in digit_items:
            item_text = _subsection_leaf_text(item_sub) or ""
            m = _FLAT_DIGIT_ITEM_RE.match(item_text)
            assert m is not None
            label = m.group(1)
            remainder = item_text[m.end():].strip()
            new_children.append(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label=label,
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
                        IRNode(kind=IRNodeKind.CONTENT, text=remainder),
                    ),
                )
            )
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(new_children),
            )
        )
        i = j
        continue
    return rewritten


def _apply_fi_renest_flat_dash_item_subsections(children: List[IRNode]) -> List[IRNode]:
    """Re-nest flat dash-item subsections as paragraph children of an intro subsection."""
    rewritten: List[IRNode] = []
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue
        intro_text = _subsection_leaf_text(child)
        if not intro_text or not intro_text.rstrip().endswith(":"):
            rewritten.append(child)
            i += 1
            continue
        if _subsection_has_structured_children(child):
            rewritten.append(child)
            i += 1
            continue
        dash_items: List[IRNode] = []
        j = i + 1
        while j < len(children):
            sib = children[j]
            if sib.kind != IRNodeKind.SUBSECTION:
                break
            if _subsection_has_structured_children(sib):
                break
            sib_text = _subsection_leaf_text(sib)
            if not sib_text or not _FLAT_DASH_ITEM_RE.match(sib_text):
                break
            dash_items.append(sib)
            j += 1
        if not dash_items:
            rewritten.append(child)
            i += 1
            continue
        new_children: List[IRNode] = [IRNode(kind=IRNodeKind.INTRO, text=intro_text.strip())]
        for item_sub in dash_items:
            item_text = _subsection_leaf_text(item_sub) or ""
            new_children.append(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    children=(IRNode(kind=IRNodeKind.CONTENT, text=item_text),),
                )
            )
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(new_children),
            )
        )
        i = j
        continue
    return rewritten


def _apply_fi_renest_flat_dot_item_subsections(children: List[IRNode]) -> List[IRNode]:
    """Re-nest flat N. text subsections as paragraph children of a header subsection."""
    rewritten: List[IRNode] = []
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue
        intro_text = _subsection_leaf_text(child)
        if not intro_text:
            rewritten.append(child)
            i += 1
            continue
        if _subsection_has_structured_children(child):
            rewritten.append(child)
            i += 1
            continue
        dot_items: List[IRNode] = []
        j = i + 1
        while j < len(children):
            sib = children[j]
            if sib.kind != IRNodeKind.SUBSECTION:
                break
            if _subsection_has_structured_children(sib):
                break
            sib_text = _subsection_leaf_text(sib)
            if not sib_text:
                break
            m = _FLAT_DOT_ITEM_RE.match(sib_text)
            if m is None:
                break
            dot_items.append(sib)
            j += 1
        if len(dot_items) < 2:
            rewritten.append(child)
            i += 1
            continue
        valid = True
        for expected, item_sub in enumerate(dot_items, start=1):
            sib_text = _subsection_leaf_text(item_sub) or ""
            m = _FLAT_DOT_ITEM_RE.match(sib_text)
            if m is None or m.group(1) != str(expected):
                valid = False
                break
        if not valid:
            rewritten.append(child)
            i += 1
            continue
        new_children: List[IRNode] = [IRNode(kind=IRNodeKind.INTRO, text=intro_text.strip())]
        for item_sub in dot_items:
            item_text = _subsection_leaf_text(item_sub) or ""
            m = _FLAT_DOT_ITEM_RE.match(item_text)
            assert m is not None
            label = m.group(1)
            remainder = m.group(2).strip()
            new_children.append(
                IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label=label,
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text=f"{label}."),
                        IRNode(kind=IRNodeKind.CONTENT, text=remainder),
                    ),
                )
            )
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(new_children),
            )
        )
        i = j
        continue
    return rewritten


def _apply_hoist_trailing_wrapup_paragraph(children: List[IRNode]) -> List[IRNode]:
    """Promote trailing prose after numbered items to wrapUp."""
    numbered_positions = [
        idx
        for idx, child in enumerate(children)
        if child.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(child)
    ]
    if not numbered_positions:
        return children

    last_numbered_idx = numbered_positions[-1]
    trailing = children[last_numbered_idx + 1:]
    if not trailing:
        return children

    def _trailing_starts_with_sentencing(nodes: List[IRNode]) -> bool:
        for node in nodes:
            text = (
                _paragraph_first_text(node)
                if node.kind == IRNodeKind.PARAGRAPH
                else irnode_to_text(node).strip()
            )
            if text:
                return bool(_SENTENCING_START_RE.match(text))
        return False

    if not may_attach_post_list_loppukappale(
        IRNode(kind=IRNodeKind.SUBSECTION, children=tuple(children))
    ) and not _trailing_starts_with_sentencing(trailing):
        return children

    def _is_wrapup_candidate(child: IRNode) -> bool:
        if child.kind == IRNodeKind.PARAGRAPH:
            return _paragraph_is_content_only(child)
        return child.kind == IRNodeKind.CONTENT and bool(irnode_to_text(child).strip())

    if not all(_is_wrapup_candidate(child) for child in trailing):
        return children

    rewritten: List[IRNode] = list(children[: last_numbered_idx + 1])
    for para in trailing:
        wrap_text = irnode_to_text(para).strip()
        if not wrap_text:
            continue
        rewritten.append(
            IRNode(
                kind=IRNodeKind.WRAP_UP,
                text=wrap_text,
                attrs=dict(para.attrs),
            )
        )
    return rewritten


def _apply_split_trailing_content_only_paragraphs_into_subsections(
    children: List[IRNode],
) -> List[IRNode]:
    """Split only the final subsection's trailing prose into standalone subsections."""
    last_subsection_idx = max(
        (i for i, child in enumerate(children) if child.kind == IRNodeKind.SUBSECTION),
        default=None,
    )
    if last_subsection_idx is None:
        return children

    rewritten: List[IRNode] = []
    for idx, child in enumerate(children):
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue

        if idx != last_subsection_idx:
            numbered_positions = [
                i
                for i, c in enumerate(child.children)
                if c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(c)
            ]
            if numbered_positions:
                last_numbered_idx = numbered_positions[-1]
                trailing = child.children[last_numbered_idx + 1:]
                if trailing and _paragraph_ends_with_terminal_punctuation(
                    child.children[last_numbered_idx]
                ) and all(_paragraph_is_content_only(node) for node in trailing):
                    rewritten_children = list(child.children[: last_numbered_idx + 1])
                    rewritten_children.extend(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text=irnode_to_text(node).strip(),
                            attrs=dict(node.attrs),
                        )
                        for node in trailing
                        if irnode_to_text(node).strip()
                    )
                    rewritten.append(
                        IRNode(
                            kind=child.kind,
                            label=child.label,
                            text=child.text,
                            attrs=child.attrs,
                            children=tuple(rewritten_children),
                        )
                    )
                    continue

        if idx == last_subsection_idx:
            numbered_positions = [
                i
                for i, c in enumerate(child.children)
                if c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(c)
            ]
            if numbered_positions:
                last_numbered_idx = numbered_positions[-1]
                trailing = child.children[last_numbered_idx + 1 :]
                has_intro = any(c.kind == IRNodeKind.INTRO for c in child.children)
                if (
                    has_intro
                    and trailing
                    and _paragraph_ends_with_terminal_punctuation(
                        child.children[last_numbered_idx]
                    )
                    and all(_paragraph_is_content_only(node) for node in trailing)
                ):
                    rewritten_children = list(child.children[: last_numbered_idx + 1])
                    for trailing_idx, node in enumerate(trailing):
                        text = irnode_to_text(node).strip()
                        if not text:
                            continue
                        rewritten_children.append(
                            IRNode(
                                kind=(
                                    IRNodeKind.WRAP_UP
                                    if trailing_idx == len(trailing) - 1
                                    else IRNodeKind.CONTENT
                                ),
                                text=text,
                                attrs=dict(node.attrs),
                            )
                        )
                    rewritten.append(
                        IRNode(
                            kind=child.kind,
                            label=child.label,
                            text=child.text,
                            attrs=child.attrs,
                            children=tuple(rewritten_children),
                        )
                    )
                    continue
            rewritten.extend(_split_trailing_base([child]))
        else:
            rewritten.append(child)

    return rewritten


def _apply_fi_merge_split_intro_item_subsections(children: List[IRNode]) -> List[IRNode]:
    """Merge a content-only intro subsection with its following paragraph-bearing subsection."""
    rewritten: List[IRNode] = []
    i = 0
    while i < len(children):
        child = children[i]
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue
        intro_text = _subsection_leaf_text(child)
        if not intro_text or not intro_text.rstrip().endswith(":"):
            rewritten.append(child)
            i += 1
            continue
        if _subsection_has_structured_children(child):
            rewritten.append(child)
            i += 1
            continue
        if i + 1 >= len(children):
            rewritten.append(child)
            i += 1
            continue
        next_child = children[i + 1]
        if next_child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            i += 1
            continue
        if next_child.label is not None:
            rewritten.append(child)
            i += 1
            continue
        has_own_intro = any(gc.kind == IRNodeKind.INTRO for gc in next_child.children)
        if has_own_intro:
            rewritten.append(child)
            i += 1
            continue
        has_numbered_paragraph = any(
            gc.kind == IRNodeKind.PARAGRAPH and any(ggc.kind == IRNodeKind.NUM for ggc in gc.children)
            for gc in next_child.children
        )
        if not has_numbered_paragraph:
            rewritten.append(child)
            i += 1
            continue
        new_children: List[IRNode] = [IRNode(kind=IRNodeKind.INTRO, text=intro_text.strip())]
        new_children.extend(next_child.children)
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(new_children),
            )
        )
        i += 2
        continue
    return rewritten


def _apply_fi_split_intro_then_numbered_list_subsections(children: List[IRNode]) -> List[IRNode]:
    """Split malformed subsections carrying a standalone sentence before a new item list.

    Source witness family:
    - subsection encodes one complete content-only moment as ``INTRO``
    - immediately followed by one content-only paragraph ending with ``:``
    - then a fresh numbered list restart ``1) ... N)``

    This is not one legal moment. It is two sibling moments:
    1. a standalone content-only subsection carrying the first sentence
    2. a new subsection whose intro is the colon-ended paragraph and whose
       numbered paragraphs are the restarted list
    """
    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue

        semantic_children = [c for c in child.children if c.kind != IRNodeKind.NUM]
        if len(semantic_children) < 3:
            rewritten.append(child)
            continue
        if semantic_children[0].kind != IRNodeKind.INTRO:
            rewritten.append(child)
            continue
        lead_para = semantic_children[1]
        if not (
            lead_para.kind == IRNodeKind.PARAGRAPH
            and _paragraph_is_content_only(lead_para)
        ):
            rewritten.append(child)
            continue

        intro_text = (semantic_children[0].text or "").strip()
        lead_text = irnode_to_text(lead_para).strip()
        remaining = semantic_children[2:]
        if not intro_text or not lead_text:
            rewritten.append(child)
            continue
        if intro_text.endswith(":") or not intro_text.endswith((".", "!", "?")):
            rewritten.append(child)
            continue
        if not lead_text.endswith(":"):
            rewritten.append(child)
            continue
        if not remaining or not all(
            c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(c) for c in remaining
        ):
            rewritten.append(child)
            continue
        if remaining[0].label != "1":
            rewritten.append(child)
            continue

        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=(IRNode(kind=IRNodeKind.CONTENT, text=intro_text),),
            )
        )
        rewritten.append(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                children=(IRNode(kind=IRNodeKind.INTRO, text=lead_text), *remaining),
            )
        )
    return rewritten if rewritten != children else children


def _apply_fi_split_inner_omission_paragraph_subsections(children: List[IRNode]) -> List[IRNode]:
    """Split content-only paragraphs bracketed by omissions out of their enclosing subsection."""
    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue
        sub_children = list(child.children)
        semantic_children = [c for c in sub_children if c.kind != IRNodeKind.NUM]
        if not semantic_children or semantic_children[0].kind != IRNodeKind.INTRO:
            rewritten.append(child)
            continue
        if not any(c.kind == IRNodeKind.OMISSION for c in sub_children):
            rewritten.append(child)
            continue
        first_omission_idx: Optional[int] = None
        for idx, c in enumerate(sub_children):
            if c.kind == IRNodeKind.OMISSION:
                first_omission_idx = idx
                break
        if first_omission_idx is None:
            rewritten.append(child)
            continue
        split_paragraph_indices = [
            idx
            for idx, c in enumerate(sub_children)
            if idx > first_omission_idx
            and c.kind == IRNodeKind.PARAGRAPH
            and not _paragraph_has_num(c)
        ]
        if not split_paragraph_indices:
            rewritten.append(child)
            continue
        first_split_idx = split_paragraph_indices[0]
        retained_children = sub_children[:first_split_idx]
        retained_non_omission = [c for c in retained_children if c.kind != IRNodeKind.OMISSION]
        if not retained_non_omission:
            rewritten.append(child)
            continue
        rewritten.append(
            IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs=child.attrs,
                children=tuple(retained_children),
            )
        )
        for split_idx in split_paragraph_indices:
            para = sub_children[split_idx]
            new_sub_children = []
            for c in para.children:
                if c.kind == IRNodeKind.SUBPARAGRAPH:
                    new_sub_children.append(
                        IRNode(
                            kind=IRNodeKind.PARAGRAPH,
                            label=c.label,
                            text=c.text,
                            attrs=c.attrs,
                            children=c.children,
                        )
                    )
                else:
                    new_sub_children.append(c)
            rewritten.append(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    children=tuple(new_sub_children),
                )
            )
    return rewritten


def _apply_fi_split_subsection_at_numbered_list_restart(children: List[IRNode]) -> List[IRNode]:
    """Split a flat subsection into multiple subsections at internal numbered-list restarts."""
    rewritten: List[IRNode] = []
    for child in children:
        if child.kind != IRNodeKind.SUBSECTION:
            rewritten.append(child)
            continue
        sub_children = list(child.children)
        has_intermediate_split = False
        first_numbered_seen = False
        for i, c in enumerate(sub_children):
            if c.kind == IRNodeKind.PARAGRAPH and _paragraph_has_num(c):
                first_numbered_seen = True
            elif (
                first_numbered_seen
                and _paragraph_is_content_only(c)
                and i + 1 < len(sub_children)
                and any(_paragraph_has_num(sub_children[j]) for j in range(i + 1, len(sub_children)))
            ):
                has_intermediate_split = True
                break
        if not has_intermediate_split:
            rewritten.append(child)
            continue
        groups: List[List[IRNode]] = [[]]
        for i, c in enumerate(sub_children):
            if (
                _paragraph_is_content_only(c)
                and _paragraph_ends_with_terminal_punctuation(c)
                and groups[-1]
                and any(_paragraph_has_num(prev) for prev in groups[-1])
                and (
                    (
                        i + 1 < len(sub_children)
                        and any(_paragraph_has_num(sub_children[j]) for j in range(i + 1, len(sub_children)))
                    )
                    or i + 1 == len(sub_children)
                )
            ):
                groups.append([c])
            else:
                groups[-1].append(c)
        if len(groups) == 1:
            rewritten.append(child)
            continue
        for g_idx, group in enumerate(groups):
            if not group:
                continue
            group_children: List[IRNode] = []
            first_c = group[0]
            if (
                _paragraph_is_content_only(first_c)
                and _paragraph_ends_with_terminal_punctuation(first_c)
                and any(_paragraph_has_num(c) for c in group[1:])
            ):
                intro_text = " ".join(
                    c.text or "" for c in first_c.children if c.kind == IRNodeKind.CONTENT
                ).strip()
                group_children.append(IRNode(kind=IRNodeKind.INTRO, text=intro_text))
                group_children.extend(group[1:])
            else:
                if (
                    len(group) == 1
                    and _paragraph_is_content_only(first_c)
                    and _paragraph_ends_with_terminal_punctuation(first_c)
                ):
                    group_children.append(
                        IRNode(
                            kind=IRNodeKind.CONTENT,
                            text=irnode_to_text(first_c).strip(),
                            attrs=dict(first_c.attrs),
                        )
                    )
                else:
                    group_children.extend(group)
            if g_idx == 0:
                rewritten.append(
                    IRNode(
                        kind=child.kind,
                        label=child.label,
                        text=child.text,
                        attrs=child.attrs,
                        children=tuple(group_children),
                    )
                )
            else:
                rewritten.append(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        children=tuple(group_children),
                    )
                )
    return rewritten


def _apply_hoist_inline_content_omissions(children: List[IRNode]) -> List[IRNode]:
    """Hoist omission markers nested inside paragraph/content to subsection level."""
    rewritten: List[IRNode] = []
    changed = False
    for child in children:
        if child.kind != IRNodeKind.PARAGRAPH:
            rewritten.append(child)
            continue
        hoisted_omissions: List[IRNode] = []
        new_para_children: List[IRNode] = []
        para_changed = False
        for para_child in child.children:
            if para_child.kind != IRNodeKind.CONTENT or not para_child.children:
                new_para_children.append(para_child)
                continue
            kept_children: List[IRNode] = []
            for content_child in para_child.children:
                if content_child.kind == IRNodeKind.OMISSION:
                    hoisted_omissions.append(content_child)
                    para_changed = True
                else:
                    kept_children.append(content_child)
            if para_changed:
                new_para_children.append(
                    IRNode(
                        kind=para_child.kind,
                        label=para_child.label,
                        text=para_child.text,
                        attrs=para_child.attrs,
                        children=tuple(kept_children),
                    )
                )
            else:
                new_para_children.append(para_child)
        if para_changed:
            changed = True
            rewritten.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=child.text,
                    attrs=child.attrs,
                    children=tuple(new_para_children),
                )
            )
            rewritten.extend(hoisted_omissions)
        else:
            rewritten.append(child)
    return rewritten if changed else children


# ---------------------------------------------------------------------------
# Rule registries (order must match xml_ir.py call order)
# ---------------------------------------------------------------------------

# Applied to children of SECTION nodes, before the positional-label counter.
SECTION_RULES: List[NormalizationRule] = [
    NormalizationRule(
        name="fi.merge_split_intro_item_subsections",
        apply=_apply_fi_merge_split_intro_item_subsections,
        description="Merge a content-only intro subsection with its following paragraph-bearing subsection.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.split_intro_then_numbered_list_subsections",
        apply=_apply_fi_split_intro_then_numbered_list_subsections,
        description="Split malformed subsections that encode a standalone sentence before a new numbered-list moment.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.renest_flat_digit_item_subsections",
        apply=_apply_fi_renest_flat_digit_item_subsections,
        description="Re-nest flat digit-item subsections as paragraph children of an intro subsection.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.renest_flat_dash_item_subsections",
        apply=_apply_fi_renest_flat_dash_item_subsections,
        description="Re-nest flat dash-item subsections as paragraph children of an intro subsection.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.renest_flat_dot_item_subsections",
        apply=_apply_fi_renest_flat_dot_item_subsections,
        description="Re-nest flat N. text subsections as paragraph children of a header subsection.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.split_inner_omission_paragraph_subsections",
        apply=_apply_fi_split_inner_omission_paragraph_subsections,
        description="Split content-only paragraphs bracketed by omissions out of their enclosing subsection.",
        family="transport_cleanup",
    ),
    NormalizationRule(
        name="fi.split_subsection_at_numbered_list_restart",
        apply=_apply_fi_split_subsection_at_numbered_list_restart,
        description="Split a flat subsection into multiple subsections at internal numbered-list restarts.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.split_trailing_content_only_paragraphs_into_subsections",
        apply=_apply_split_trailing_content_only_paragraphs_into_subsections,
        description="Split only the final subsection's trailing prose into standalone subsections.",
        family="ontology_normalization",
    ),
]

# Applied to children of SUBSECTION nodes, BEFORE the positional-label counter.
SUBSECTION_PRE_RULES: List[NormalizationRule] = [
    NormalizationRule(
        name="fi.recover_intro_labeled_paragraphs",
        apply=_apply_recover_intro_labeled_paragraphs,
        description="Recover item labels encoded in <intro> text when a <paragraph> has no <num> child.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.hoist_inline_content_omissions",
        apply=_apply_hoist_inline_content_omissions,
        description="Hoist omission markers nested inside paragraph/content to subsection level.",
        family="transport_cleanup",
    ),
]

# Applied to children of SUBSECTION nodes, AFTER the positional-label counter.
# NOTE: The full post-counter sequence interleaves two non-Finland-specific
# xml_ingest passes that are NOT in this registry:
#   _merge_split_numbered_paragraph_continuations  (between slot 1 and slot 2 below)
#   _rehome_orphaned_letter_paragraphs             (between slot 3 and slot 4 below)
# xml_ir.py calls them directly, in correct order, around apply_all() calls.
# The three sub-registries below correspond to the three contiguous Finland-specific
# segments of the post-counter sequence.

# Segment A: before _merge_split_numbered_paragraph_continuations
SUBSECTION_POST_RULES_A: List[NormalizationRule] = [
    NormalizationRule(
        name="fi.recover_embedded_numbered_paragraphs",
        apply=_apply_recover_embedded_numbered_paragraphs,
        description="Recover malformed paragraph numbering serialized inside content text.",
        family="ontology_normalization",
    ),
]

# Segment B: after _merge_split_numbered_paragraph_continuations, before _rehome_orphaned_letter_paragraphs
SUBSECTION_POST_RULES_B: List[NormalizationRule] = [
    NormalizationRule(
        name="fi.hoist_trailing_wrapup_paragraph",
        apply=_apply_hoist_trailing_wrapup_paragraph,
        description="Promote trailing prose after numbered items to wrapUp.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.nest_lettered_subparagraphs",
        apply=_apply_nest_lettered_subparagraphs,
        description="Nest letter-labeled paragraphs as subparagraph children of the correct digit paragraph.",
        family="ontology_normalization",
    ),
]

# Segment C: after _rehome_orphaned_letter_paragraphs
SUBSECTION_POST_RULES_C: List[NormalizationRule] = [
    NormalizationRule(
        name="fi.nest_repeated_alpha_subparagraphs_under_alpha_parents",
        apply=_apply_nest_repeated_alpha_subparagraphs_under_alpha_parents,
        description="Nest repeated alphabetic subitems under alphabetic parent items with introducers.",
        family="ontology_normalization",
    ),
    NormalizationRule(
        name="fi.nest_repeated_digit_subparagraphs",
        apply=_apply_nest_repeated_digit_subparagraphs,
        description="Nest repeated digit-labeled paragraphs as subparagraphs of the earlier item.",
        family="historical_tolerance",
    ),
]

# Flat view of all post-counter rules (for registry ordering tests and enumeration).
# Does NOT include the two xml_ingest passes that interleave between segments.
SUBSECTION_POST_RULES: List[NormalizationRule] = (
    SUBSECTION_POST_RULES_A + SUBSECTION_POST_RULES_B + SUBSECTION_POST_RULES_C
)


# ---------------------------------------------------------------------------
# Convenience application helpers
# ---------------------------------------------------------------------------

def apply_section_rules(
    children: List[IRNode],
    observations_out: Optional[List[Dict[str, object]]] = None,
) -> List[IRNode]:
    """Apply all SECTION_RULES to *children* in registry order."""
    return apply_all(children, SECTION_RULES, observations_out)


def apply_subsection_pre_rules(
    children: List[IRNode],
    observations_out: Optional[List[Dict[str, object]]] = None,
) -> List[IRNode]:
    """Apply all SUBSECTION_PRE_RULES to *children* in registry order."""
    return apply_all(children, SUBSECTION_PRE_RULES, observations_out)


def apply_subsection_post_rules_a(
    children: List[IRNode],
    observations_out: Optional[List[Dict[str, object]]] = None,
) -> List[IRNode]:
    """Apply SUBSECTION_POST_RULES_A (before _merge_split_numbered_paragraph_continuations)."""
    return apply_all(children, SUBSECTION_POST_RULES_A, observations_out)


def apply_subsection_post_rules_b(
    children: List[IRNode],
    observations_out: Optional[List[Dict[str, object]]] = None,
) -> List[IRNode]:
    """Apply SUBSECTION_POST_RULES_B (after _merge_split, before _rehome_orphaned)."""
    return apply_all(children, SUBSECTION_POST_RULES_B, observations_out)


def apply_subsection_post_rules_c(
    children: List[IRNode],
    observations_out: Optional[List[Dict[str, object]]] = None,
) -> List[IRNode]:
    """Apply SUBSECTION_POST_RULES_C (after _rehome_orphaned_letter_paragraphs)."""
    return apply_all(children, SUBSECTION_POST_RULES_C, observations_out)


__all__ = [
    "NormalizationRule",
    "apply_all",
    "apply_section_rules",
    "apply_subsection_pre_rules",
    "apply_subsection_post_rules_a",
    "apply_subsection_post_rules_b",
    "apply_subsection_post_rules_c",
    "SECTION_RULES",
    "SUBSECTION_PRE_RULES",
    "SUBSECTION_POST_RULES",
    "SUBSECTION_POST_RULES_A",
    "SUBSECTION_POST_RULES_B",
    "SUBSECTION_POST_RULES_C",
    # Shared helpers (re-exported for testing)
    "_paragraph_has_introducer_signal",
    "_subsection_leaf_text",
    "_subsection_has_structured_children",
]
