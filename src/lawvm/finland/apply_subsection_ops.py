"""Subsection-level executor helpers for Finland apply.

This module owns the level-M subsection handlers plus the subsection-index
resolver they share. The deterministic dispatcher still lives in
``apply.py`` for now, but the subsection executor bodies no longer need to
cohabit with typed/legacy dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import re
from typing import TYPE_CHECKING, List, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.recovery_kind import RecoveryKind
from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.mutation_boundary import TreePathStep
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.ops import AmendmentOp, ReplayProfile, ResolvedOp, temporary_signal_for_op
from lawvm.finland.replay_notices import replay_print
from lawvm.finland.apply_ir_ops import (

    _rewrite_bracketed_single_subsection_replace_ir,
    _insert_subsection_with_renumber_ir,
)
from lawvm.finland.apply_payload_ops import (
    _has_intro_list_moment_shape_ir,
)
from lawvm.finland.apply_runtime_support import (
    _legacy_target_section_for_scope,
    _legacy_target_special_for_scope,
    _with_preserved_provision_index,
)
from lawvm.finland.merge import (
    _merge_subsection_accumulate_inner_omission_ir,
    _merge_subsection_with_omission_ir,
    _strip_leading_text_prefix,
)
from lawvm.finland.helpers import _is_omission_ir
from lawvm.finland.source_pathology import (
    build_destructive_shape_loss_risk_pathology,
    build_subsection_target_absent_pathology,
    build_subsection_target_rebound_pathology,
)

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SubsectionApplyView:
    op_type: str
    target_section: str
    target_paragraph: int | None
    target_item: str | None
    target_special: str | None
    legacy_source_statute_id: str
    is_temporary: bool
    has_exact_bound_payload: bool
    resolved_op: ResolvedOp | None = None


@dataclass(frozen=True)
class _SparseSubsectionItemMergeResult:
    node: IRNode
    recovery_kind: RecoveryKind
    payload_sibling_count: int


def _coerce_subsection_apply_view(op: "_SubsectionApplyView | AmendmentOp | ResolvedOp") -> _SubsectionApplyView:
    if isinstance(op, _SubsectionApplyView):
        return op
    return _subsection_apply_view_for_op(op)


def _subsection_apply_view_for_op(op: AmendmentOp | ResolvedOp) -> _SubsectionApplyView:
    has_exact_bound_payload = False
    if isinstance(op, ResolvedOp):
        scope = op.resolved_target_scope_view
        legacy_source_statute_id = (
            op.resolved_source_statute
            or (op.resolved_op_source.statute_id if op.resolved_op_source is not None and op.resolved_op_source.statute_id else "")
        )
        is_temporary = temporary_signal_for_op(op)
        op_type = op.resolved_action_type
        target_section = _legacy_target_section_for_scope(scope, op.target_unit_kind)
        target_paragraph = scope.target_paragraph
        target_item = scope.target_item
        target_special = _legacy_target_special_for_scope(scope, op.effective_target_special)
        mapped = op.slot_assignment.for_stable_op_id(op.op_id) if op.slot_assignment is not None else None
        has_exact_bound_payload = (
            op.slot_assignment is not None
            and op.slot_assignment.has_owned_bound_payload_for_stable_op_id(op.op_id)
        ) or (
            mapped is not None
            and scope.target_paragraph is not None
            and mapped.label is not None
            and normalized_label_key(mapped.label) == str(scope.target_paragraph)
        )
    else:
        legacy_source_statute_id = (
            op.source_statute
            or (op.lo.source.statute_id if op.lo is not None and op.lo.source is not None else "")
        )
        is_temporary = temporary_signal_for_op(op)
        has_exact_bound_payload = op.has_exact_bound_payload
        op_type = op.op_type
        target_section = op.target_cols.target_section or ""
        target_paragraph = op.target_cols.target_paragraph
        target_item = op.target_cols.target_item
        target_special = op.target_cols.target_special
    return _SubsectionApplyView(
        op_type=op_type,
        target_section=target_section,
        target_paragraph=target_paragraph,
        target_item=target_item,
        target_special=target_special,
        legacy_source_statute_id=legacy_source_statute_id,
        is_temporary=is_temporary,
        has_exact_bound_payload=has_exact_bound_payload,
        resolved_op=op if isinstance(op, ResolvedOp) else None,
    )


def _is_trailing_only_omission_sub(sub: IRNode) -> bool:
    """True when *sub* ends with an omission marker and has no other omissions.

    Used to detect the Finlex editorial trailing ``<hcontainer name="omission"/>``
    that appears at the end of a whole-subsection replacement payload.  A trailing-only
    omission (nothing after it, no omissions earlier) means "old content ends here"
    and must NOT be used as a signal to splice master items back in.

    Inner omissions (before the last item) carry real merge semantics and must NOT
    be stripped — returning False for those preserves the accumulate-inner merge path.
    """
    children = sub.children
    if not children:
        return False
    last = children[-1]
    if last.kind != IRNodeKind.OMISSION and not (
        last.kind == IRNodeKind.HCONTAINER and last.attrs.get("name") == "omission"
    ):
        return False
    # Must have no other omission earlier
    return not any(
        c.kind == IRNodeKind.OMISSION
        or (c.kind == IRNodeKind.HCONTAINER and c.attrs.get("name") == "omission")
        for c in children[:-1]
    )


def _is_content_only_continuation_fragment(
    subsecs: List[IRNode],
    idx: int,
) -> bool:
    """Return True when a matched subsection is really a carried text fragment.

    Historical Finland trees sometimes encode the trailing sentence of the
    previous numbered subsection as a standalone content-only subsection with a
    numeric label. That fake slot should not satisfy later moment targeting.
    """
    if not (0 < idx < len(subsecs)):
        return False
    sub = subsecs[idx]
    if any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children):
        return False
    content_children = [child for child in sub.children if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and irnode_to_text(child).strip()]
    if len(content_children) != 1:
        return False
    continuation_text = " ".join(irnode_to_text(content_children[0]).split())
    # lawvm-regex: owning_parser own-subtree (irnode_to_text) leading-lowercase continuation shape test on owned live-section text, not source-plane mint
    if not continuation_text or not re.match(r"^[a-zåäö]", continuation_text, flags=re.I) or continuation_text[:1].upper() == continuation_text[:1]:
        return False

    prev = subsecs[idx - 1]
    prev_paras = [child for child in prev.children if child.kind == IRNodeKind.PARAGRAPH]
    if not prev_paras:
        return False
    last_para_text = " ".join(irnode_to_text(prev_paras[-1]).split()).rstrip()
    if not last_para_text:
        return False
    if last_para_text[-1] not in ".;:!?":
        return True

    prev_norm = " ".join(last_para_text.split()).rstrip(" .;:!?")
    frag_norm = continuation_text.rstrip(" .;:!?")
    return bool(frag_norm) and prev_norm.endswith(frag_norm)


def _looks_like_standalone_tail_subsection(subsection: IRNode) -> bool:
    """Return True for a single-sentence content-only tail subsection.

    Historical source trees sometimes carry a trailing standalone sentence as
    a separate subsection. When a later subsection replacement already
    absorbs the operative text, keeping that leftover sentence doubles the
    tail. This helper stays conservative: it only matches plain content-only
    subsections with one prose child and an uppercase opening.
    """
    if subsection.kind != IRNodeKind.SUBSECTION:
        return False
    if any(child.kind == IRNodeKind.PARAGRAPH for child in subsection.children):
        return False

    content_children = [
        child
        for child in subsection.children
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and irnode_to_text(child).strip()
    ]
    if len(content_children) != 1:
        return False

    text = " ".join(irnode_to_text(content_children[0]).split())
    return bool(text) and text[:1].isalpha() and text[:1].isupper()


def _looks_like_content_only_tail_subsection(subsection: IRNode) -> bool:
    """Return True for a content-only tail subsection regardless of casing."""
    if subsection.kind != IRNodeKind.SUBSECTION:
        return False
    if any(child.kind == IRNodeKind.PARAGRAPH for child in subsection.children):
        return False
    content_children = [
        child
        for child in subsection.children
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and irnode_to_text(child).strip()
    ]
    return len(content_children) == 1


def _matches_standalone_tail_subsection_prune_witness(
    replacement: IRNode,
    successor: IRNode,
) -> bool:
    """Return True when replacement already absorbs the successor tail text."""
    if not _looks_like_content_only_tail_subsection(successor):
        return False

    successor_text = " ".join(irnode_to_text(successor).split())
    if not successor_text:
        return False

    if _looks_like_standalone_tail_subsection(replacement) and _looks_like_standalone_tail_subsection(successor):
        replacement_text = " ".join(irnode_to_text(replacement).split())
        if not replacement_text:
            return False
        tail_text = _tail_after_first_sentence(replacement_text)
        if not tail_text:
            return False
        return tail_text.rstrip(" .;:!?") == successor_text.rstrip(" .;:!?")

    for tail_text in _owned_paragraph_tail_texts(replacement):
        if _tail_text_supersedes_successor(tail_text, successor_text):
            return True
    return False


def _owned_paragraph_tail_texts(replacement: IRNode) -> tuple[str, ...]:
    """Return explicit paragraph tail descendants that can absorb a sibling tail."""
    if replacement.kind is not IRNodeKind.SUBSECTION:
        return ()
    tails: list[str] = []
    for paragraph in replacement.children:
        if paragraph.kind is not IRNodeKind.PARAGRAPH:
            continue
        for child in paragraph.children:
            if child.kind in {IRNodeKind.SUBPARAGRAPH, IRNodeKind.WRAP_UP}:
                text = " ".join(irnode_to_text(child).split()).strip()
                if text:
                    tails.append(text)
    return tuple(tails)


def _tail_text_supersedes_successor(tail_text: str, successor_text: str) -> bool:
    """Conservative typed-tail equivalence for stale successor pruning.

    This is not a raw source-language parser. It compares two already-owned IR
    tails: an explicit paragraph descendant in the replacement payload and the
    immediately following content-only live sibling. The recovery is still
    emitted as a named pathology and strict mode blocks it.
    """
    tail_norm = tail_text.rstrip(" .;:!?")
    successor_norm = successor_text.rstrip(" .;:!?")
    if min(len(tail_norm), len(successor_norm)) < 80:
        return False
    if tail_norm == successor_norm:
        return True
    common_prefix = 0
    for left, right in zip(tail_norm, successor_norm, strict=False):
        if left != right:
            break
        common_prefix += 1
    if common_prefix < 40:
        return False
    return SequenceMatcher(None, tail_norm, successor_norm).ratio() >= 0.84


def _tail_after_first_sentence(text: str) -> str:
    """Return the trailing text after the first sentence, if any."""
    stripped = text.strip()
    if not stripped:
        return ""
    # lawvm-regex: owning_parser sentence-boundary split on owned payload text (tail-paragraph extraction), not source text
    match = re.search(r"[.!?]\s+", stripped)
    if match is None:
        return ""
    return stripped[match.end() :].strip()


def _extract_predecessor_tail_paragraph_as_insert(
    sec: IRNode,
    *,
    target_paragraph: int,
    replacement_subsection: IRNode,
    muutos_ir: Optional[IRNode],
) -> IRNode | None:
    """Lift a predecessor tail paragraph into a new inserted subsection.

    Narrow recovery for omission-bracketed one-slot section payloads where the
    new replacement text is actually the final paragraph of the preceding live
    subsection. In that family the old target subsection must be shifted
    forward, not overwritten.
    """
    if muutos_ir is None or target_paragraph <= 1:
        return None
    slot_kinds = [c.kind for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION or _is_omission_ir(c)]
    omission_bracketed_single = slot_kinds == [IRNodeKind.OMISSION, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION]
    mixed_predecessor_payload = tuple(slot_kinds) in {
        (IRNodeKind.SUBSECTION, IRNodeKind.SUBSECTION),
        (IRNodeKind.SUBSECTION, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION),
    }
    if not (omission_bracketed_single or mixed_predecessor_payload):
        return None
    if any(child.kind == IRNodeKind.PARAGRAPH for child in replacement_subsection.children):
        return None

    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]
    target_idx = next(
        (
            i
            for i, sub in enumerate(subsecs)
            if sub.label and re.sub(r"[)\s.]", "", sub.label).strip() == str(target_paragraph)
        ),
        None,
    )
    if target_idx is None or target_idx <= 0:
        return None

    predecessor = subsecs[target_idx - 1]
    predecessor_tail_idx = next(
        (
            idx
            for idx in range(len(predecessor.children) - 1, -1, -1)
            if predecessor.children[idx].kind in (IRNodeKind.PARAGRAPH, IRNodeKind.CONTENT)
            and irnode_to_text(predecessor.children[idx]).strip()
        ),
        None,
    )
    if predecessor_tail_idx is None:
        return None
    if len(predecessor.children) <= 1 or predecessor_tail_idx == 0:
        return None

    replacement_text = " ".join(irnode_to_text(replacement_subsection).split()).strip()
    predecessor_tail = predecessor.children[predecessor_tail_idx]
    predecessor_tail_text = " ".join(irnode_to_text(predecessor_tail).split()).strip()
    current_target_text = " ".join(irnode_to_text(subsecs[target_idx]).split()).strip()
    has_numbered_predecessor_body = any(child.kind == IRNodeKind.PARAGRAPH for child in predecessor.children[:predecessor_tail_idx])
    if (
        not replacement_text
        or not predecessor_tail_text
        or current_target_text == replacement_text
        or predecessor_tail_text == current_target_text
    ):
        return None
    prefix = replacement_text[:40]
    pred_score = SequenceMatcher(None, replacement_text[:240], predecessor_tail_text[:240]).ratio()
    target_score = SequenceMatcher(None, replacement_text[:240], current_target_text[:240]).ratio()
    tail_is_better_payload_match = (
        len(replacement_text) >= 40
        and len(predecessor_tail_text) >= 40
        and predecessor_tail_text.startswith(prefix)
        and not current_target_text.startswith(prefix)
        and pred_score >= 0.60
        and pred_score > target_score + 0.10
    )
    if predecessor_tail.kind == IRNodeKind.PARAGRAPH:
        if predecessor_tail_text != replacement_text and not (
            mixed_predecessor_payload and tail_is_better_payload_match
        ):
            return None
    elif predecessor_tail.kind == IRNodeKind.CONTENT:
        if not has_numbered_predecessor_body:
            return None
        if mixed_predecessor_payload and not tail_is_better_payload_match:
            return None
    else:
        return None

    trimmed_predecessor_children = list(predecessor.children[:predecessor_tail_idx])
    trimmed_predecessor = _tops._with_children(predecessor, trimmed_predecessor_children)
    children = list(sec.children)
    subsection_positions = [i for i, child in enumerate(children) if child.kind == IRNodeKind.SUBSECTION]
    predecessor_pos = subsection_positions[target_idx - 1]
    children[predecessor_pos] = trimmed_predecessor
    trimmed_sec = _tops._with_children(sec, children)
    return _insert_subsection_with_renumber_ir(
        trimmed_sec,
        replacement_subsection,
        target_paragraph,
    )


def _promote_content_only_intro_replace(subsection: IRNode) -> IRNode:
    """Promote a content-only subsection ending with ':' into intro form."""
    if subsection.kind is not IRNodeKind.SUBSECTION or len(subsection.children) != 1:
        return subsection
    child = subsection.children[0]
    if child.kind is not IRNodeKind.CONTENT:
        return subsection
    text = irnode_to_text(child).strip()
    if not text or not text.endswith(":"):
        return subsection
    return _tops._with_children(
        subsection,
        [
            IRNode(
                kind=IRNodeKind.INTRO,
                label=child.label,
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
        ],
    )


def _merge_intro_only_subsection_replace(
    current_subsection: IRNode,
    replacement_subsection: IRNode,
) -> IRNode | None:
    """Preserve live paragraph items when a subsection replace changes only intro."""
    amend_intro = next((c for c in replacement_subsection.children if c.kind is IRNodeKind.INTRO), None)
    if amend_intro is None:
        return None
    if any(c.kind is IRNodeKind.PARAGRAPH for c in replacement_subsection.children):
        return None
    live_paragraphs = [c for c in current_subsection.children if c.kind is IRNodeKind.PARAGRAPH]
    if not live_paragraphs:
        return None
    live_intro = next((c for c in current_subsection.children if c.kind is IRNodeKind.INTRO), None)
    if live_intro is not None:
        if " ".join(irnode_to_text(live_intro).split()) == " ".join(irnode_to_text(amend_intro).split()):
            return None
    return IRNode(
        kind=replacement_subsection.kind,
        label=replacement_subsection.label,
        text=replacement_subsection.text,
        attrs=dict(replacement_subsection.attrs),
        children=(amend_intro, *live_paragraphs),
    )


def _merge_preserved_tail_into_replacement(
    current_subsection: IRNode,
    replacement_subsection: IRNode,
) -> IRNode:
    """Preserve trailing prose when a sparse replacement targets a collapsed moment.

    Historical Finland source trees sometimes collapse multiple logical moments
    into one subsection.  When a later sparse `REPLACE N § 1 mom` carries only
    the replaced first sentence, the payload-normalization lane classifies that
    shape as `preserve_unstated_tail`.  The replay executor must then keep the
    remaining trailing sentences from the old live subsection instead of
    overwriting the whole collapsed node.
    """
    old_content_children = [
        child
        for child in current_subsection.children
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and irnode_to_text(child).strip()
    ]
    new_content_children = [
        child
        for child in replacement_subsection.children
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and irnode_to_text(child).strip()
    ]
    if len(old_content_children) != 1 or len(new_content_children) != 1:
        return replacement_subsection
    if any(child.kind == IRNodeKind.PARAGRAPH for child in current_subsection.children):
        return replacement_subsection
    if any(child.kind == IRNodeKind.PARAGRAPH for child in replacement_subsection.children):
        return replacement_subsection

    old_text = irnode_to_text(old_content_children[0]).strip()
    new_text = irnode_to_text(new_content_children[0]).strip()
    trailing = _tail_after_first_sentence(old_text)
    if not trailing or trailing in new_text:
        return replacement_subsection

    merged_text = f"{new_text} {trailing}".strip()
    new_children = list(replacement_subsection.children)
    for idx, child in enumerate(new_children):
        if child is new_content_children[0]:
            new_children[idx] = IRNode(
                kind=child.kind,
                label=child.label,
                text=merged_text,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
            break
    return IRNode(
        kind=replacement_subsection.kind,
        label=replacement_subsection.label,
        text=replacement_subsection.text,
        attrs=dict(replacement_subsection.attrs),
        children=tuple(new_children),
    )


def _owned_sparse_gap_insert_subsection(
    section: IRNode,
    replacement_subsection: IRNode,
    *,
    target_paragraph: int,
) -> IRNode | None:
    """Insert an owned sparse replacement into a live numeric-label gap.

    This intentionally does not renumber later subsections.  Historical replay
    state can contain live labels like ``1, 2, 6`` after prior sparse materialization;
    a source-owned replacement for moment ``3`` or ``5`` should occupy that legal
    label before ``6``, not shift ``6`` to another legal identity.
    """
    target_label = str(target_paragraph)
    replacement = replacement_subsection
    if normalized_label_key(replacement.label) != target_label:
        replacement = IRNode(
            kind=replacement.kind,
            label=target_label,
            text=replacement.text,
            attrs=dict(replacement.attrs),
            children=tuple(replacement.children),
        )

    children: list[IRNode] = []
    inserted = False
    for child in section.children:
        if (
            not inserted
            and child.kind is IRNodeKind.SUBSECTION
            and (child.label or "").strip().isdigit()
            and int((child.label or "").strip()) > target_paragraph
        ):
            children.append(replacement)
            inserted = True
        children.append(child)
    if not inserted:
        return None
    return _tops._with_children(section, children)


def _has_owned_sparse_gap_replace_witness(
    view: _SubsectionApplyView,
    replacement_subsection: IRNode,
    *,
    exact_idx_found: bool,
    has_higher_live_numeric_label: bool,
) -> bool:
    """Return True when elaboration, not apply fallback, owns a gap fill."""
    if exact_idx_found or not has_higher_live_numeric_label:
        return False
    if view.op_type != "REPLACE" or view.target_paragraph is None:
        return False
    if view.target_item or view.target_special:
        return False
    rop = view.resolved_op
    if rop is None or not rop.has_assigned_subsection_payload():
        return False
    if rop.payload_completeness is None or rop.payload_completeness.kind != "sparse_certified":
        return False
    if rop.slot_assignment is None:
        return False
    if not rop.slot_assignment.has_owned_bound_payload_for_stable_op_id(rop.op_id):
        return False
    binding = next(
        (
            candidate
            for candidate in rop.slot_assignment.sparse_slot_bindings
            if candidate.op_type == view.op_type
            and candidate.target_paragraph == view.target_paragraph
            and candidate.target_item is None
            and candidate.target_special is None
        ),
        None,
    )
    if binding is None:
        return False
    payload_label = normalized_label_key(binding.payload_slot_label)
    if payload_label and payload_label != str(view.target_paragraph):
        return False
    replacement_label = normalized_label_key(replacement_subsection.label)
    return not replacement_label or replacement_label == str(view.target_paragraph)


def _strip_context_carried_omission_for_complete_numbered_replace(
    replacement_subsection: IRNode,
) -> IRNode | None:
    """Strip a context-carried omission from complete numbered whole-subsection replaces.

    Some amendment XML encodes a complete moment replacement as:

    - intro/context prose
    - omission marker
    - explicit new numbered items
    - optional wrap-up OR a trailing editorial omission marker

    For a whole-subsection REPLACE carrying a full numbered list, that leading
    omission is not a claim to preserve unmatched old item tail. It only marks
    that the intro was carried in the amendment body. When the numbered payload
    is explicit and contiguous from 1, the list is closed either by an explicit
    wrap-up clause or by a trailing editorial ``<hcontainer name="omission"/>``
    that brackets this moment off from its sibling moments within the section
    (e.g. ``muutetaan N §:n M momentti seuraavasti`` restating the whole moment
    with fewer items than the prior law). In both closures, keep the amendment
    intro plus its explicit numbered items and drop the omissions before the
    generic omission-merge lane can splice stale master items back in.
    """
    children = replacement_subsection.children
    omission_idx = next((i for i, child in enumerate(children) if _is_omission_ir(child)), None)
    if omission_idx is None:
        return None

    # A trailing editorial omission (last child) closes the moment the same way
    # an explicit wrap-up does: it brackets the replaced moment off from sibling
    # moments rather than claiming an unstated old item tail.
    has_trailing_omission = _is_omission_ir(children[-1])

    pre_omission = children[:omission_idx]
    trailing = tuple(child for child in children[omission_idx + 1 :] if not _is_omission_ir(child))
    if not pre_omission or not trailing:
        return None

    context_kinds = {IRNodeKind.INTRO, IRNodeKind.CONTENT, IRNodeKind.PARAGRAPH}
    if not all(child.kind in context_kinds and not child.label for child in pre_omission):
        return None

    numbered = [child for child in trailing if child.kind is IRNodeKind.PARAGRAPH and child.label]
    if not numbered:
        return None
    numbered_labels = [normalized_label_key(child.label) for child in numbered]
    if any(not label.isdigit() for label in numbered_labels):
        return None
    if numbered_labels != [str(i) for i in range(1, len(numbered_labels) + 1)]:
        return None
    has_wrap_up = any(child.kind is IRNodeKind.WRAP_UP for child in trailing)
    if not has_wrap_up and not has_trailing_omission:
        return None

    return IRNode(
        kind=replacement_subsection.kind,
        label=replacement_subsection.label,
        text=replacement_subsection.text,
        attrs=dict(replacement_subsection.attrs),
        children=tuple((*pre_omission, *trailing)),
    )


def _strip_owned_bound_omissions_for_complete_numbered_replace(
    replacement_subsection: IRNode,
) -> IRNode | None:
    """Strip omission delimiters from an exact-bound whole-subsection payload.

    Sparse subsection payloads use omission nodes to preserve unstated live
    item rows.  Once elaboration has bound a single owned payload to a
    whole-subsection ``REPLACE``, a contiguous numbered list starting at 1 owns
    the target subsection's child list even if the source XML still carries a
    context omission before the rows.  In that shape omission nodes delimit the
    source excerpt; they do not authorize splicing stale live children back in.
    """
    if not any(_is_omission_ir(child) for child in replacement_subsection.children):
        return None

    non_omission_children = tuple(
        child for child in replacement_subsection.children if not _is_omission_ir(child)
    )
    numbered = [
        child
        for child in non_omission_children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    if not numbered:
        return None
    numbered_labels = [normalized_label_key(child.label) for child in numbered]
    if any(not label.isdigit() for label in numbered_labels):
        return None
    if numbered_labels != [str(i) for i in range(1, len(numbered_labels) + 1)]:
        return None
    if any(
        child.kind is IRNodeKind.PARAGRAPH and child.label and child not in numbered
        for child in non_omission_children
    ):
        return None

    return IRNode(
        kind=replacement_subsection.kind,
        label=replacement_subsection.label,
        text=replacement_subsection.text,
        attrs=dict(replacement_subsection.attrs),
        children=non_omission_children,
    )


def _split_sparse_omission_item_row_text(text: str) -> tuple[str, str] | None:
    stripped = text.strip()
    for idx, char in enumerate(stripped[:8]):
        if char not in ").":
            continue
        raw_label = stripped[:idx].strip().replace(" ", "")
        body = stripped[idx + 1 :].strip()
        if raw_label and body:
            return raw_label, body
        return None
    return None


def _item_row_from_sparse_omission_subsection(subsection: IRNode) -> IRNode | None:
    """Extract one source-owned numbered item row from a content-only sparse slot."""
    paragraphs = [child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH and child.label]
    if len(paragraphs) == 1:
        return paragraphs[0]
    if paragraphs:
        return None
    content_children = [
        child
        for child in subsection.children
        if child.kind in {IRNodeKind.CONTENT, IRNodeKind.INTRO} and irnode_to_text(child).strip()
    ]
    if len(content_children) != 1 or len(content_children) != len(subsection.children):
        return None
    text = irnode_to_text(content_children[0]).strip()
    split = _split_sparse_omission_item_row_text(text)
    if split is None:
        return None
    raw_label, body = split
    label = normalized_label_key(raw_label)
    if not label or not body:
        return None
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=body,
                attrs=dict(content_children[0].attrs),
            ),
        ),
    )


def _leading_anchor_tokens(text: str, *, count: int = 4) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip(" \t\r\n.,;:()[]{}")
        if token:
            tokens.append(token.casefold())
        if len(tokens) == count:
            return tuple(tokens)
    return ()


def _anchor_token_set(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in text.split():
        token = raw.strip(" \t\r\n.,;:()[]{}").casefold()
        if len(token) >= 3:
            tokens.add(token)
    return tokens


def _unique_sparse_row_match_by_text(
    row_text: str,
    live_rows: list[IRNode],
) -> IRNode | None:
    leading_anchor = _leading_anchor_tokens(row_text)
    if len(leading_anchor) >= 4:
        leading_matches = [
            live_row
            for live_row in live_rows
            if _leading_anchor_tokens(irnode_to_text(live_row)) == leading_anchor
        ]
        if len(leading_matches) == 1:
            return leading_matches[0]

    payload_tokens = _anchor_token_set(row_text)
    if len(payload_tokens) < 8:
        return None
    scored: list[tuple[float, IRNode]] = []
    for live_row in live_rows:
        live_tokens = _anchor_token_set(irnode_to_text(live_row))
        if not live_tokens:
            continue
        common = len(payload_tokens & live_tokens)
        coverage = common / max(1, min(len(payload_tokens), len(live_tokens)))
        scored.append((coverage, live_row))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_row = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < 0.55 or best_score - runner_up < 0.20:
        return None
    return best_row


def _single_unlabeled_sparse_item_row(
    master_subsection: IRNode,
    replacement_subsection: IRNode,
) -> IRNode | None:
    """Recover one table-carried item row whose source lost the item label.

    Historical Finland XML sometimes encodes a single changed numbered-list row
    as a table directly after the live subsection intro, with no item number in
    the source row. This is only safe when the carried intro matches the live
    intro exactly and the row's leading tokens uniquely identify one existing
    live item.
    """
    if replacement_subsection.kind is not IRNodeKind.SUBSECTION:
        return None
    if any(child.kind is IRNodeKind.PARAGRAPH for child in replacement_subsection.children):
        return None

    master_intro = next(
        (child for child in master_subsection.children if child.kind is IRNodeKind.INTRO),
        None,
    )
    if master_intro is None:
        return None
    live_rows = [
        child
        for child in master_subsection.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    if len(live_rows) < 3:
        return None

    content_children = [
        child
        for child in replacement_subsection.children
        if child.kind in {IRNodeKind.CONTENT, IRNodeKind.INTRO} and irnode_to_text(child).strip()
    ]
    if len(content_children) != len(replacement_subsection.children) or not content_children:
        return None

    replacement_text = " ".join(
        " ".join(irnode_to_text(child).split())
        for child in content_children
    ).strip()
    row_text = _strip_leading_text_prefix(replacement_text, irnode_to_text(master_intro))
    if row_text is None:
        return None
    row_text = " ".join(row_text.split())
    anchor = _leading_anchor_tokens(row_text)
    if len(anchor) < 4:
        if len(_anchor_token_set(row_text)) < 8:
            return None

    matched_row = _unique_sparse_row_match_by_text(row_text, live_rows)
    if matched_row is None:
        return None

    label = normalized_label_key(matched_row.label)
    if not label:
        return None
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
            IRNode(
                kind=IRNodeKind.CONTENT,
                text=row_text,
                attrs=dict(content_children[-1].attrs),
            ),
        ),
    )


def _merge_single_unlabeled_sparse_item_row_into_subsection(
    master_subsection: IRNode,
    muutos_ir: IRNode | None,
) -> _SparseSubsectionItemMergeResult | None:
    if master_subsection.kind is not IRNodeKind.SUBSECTION:
        return None
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return None
    payload_subsections = [
        child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION
    ]
    non_structural = [
        child
        for child in muutos_ir.children
        if child.kind not in {IRNodeKind.NUM, IRNodeKind.SUBSECTION}
        and not _is_omission_ir(child)
    ]
    if len(payload_subsections) != 1 or non_structural:
        return None

    payload_row = _single_unlabeled_sparse_item_row(
        master_subsection,
        payload_subsections[0],
    )
    if payload_row is None:
        return None

    live_children = list(master_subsection.children)
    replaced = False
    merged_children: list[IRNode] = []
    for child in live_children:
        if (
            child.kind is IRNodeKind.PARAGRAPH
            and child.label
            and normalized_label_key(child.label) == payload_row.label
        ):
            merged_children.append(payload_row)
            replaced = True
            continue
        merged_children.append(child)
    if not replaced:
        return None
    return _SparseSubsectionItemMergeResult(
        node=IRNode(
            kind=master_subsection.kind,
            label=master_subsection.label,
            text=master_subsection.text,
            attrs=dict(master_subsection.attrs),
            children=tuple(merged_children),
        ),
        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_UNLABELED_SPARSE_ITEM_MERGE,
        payload_sibling_count=1,
    )


def _merge_sparse_omission_item_rows_into_subsection(
    master_subsection: IRNode,
    muutos_ir: IRNode | None,
) -> _SparseSubsectionItemMergeResult | None:
    """Merge omission-bracketed sparse item rows into one live subsection.

    Finland amendment bodies sometimes say "replace section N subsection M" but
    then present only numbered item rows inside section-level omission brackets:

        [omission] 6) ... [omission] 8) ... 9) ... 10) ... [omission]

    The explicit legal target is the subsection, while the body shape owns only
    item rows inside that subsection.  Preserve untouched live item siblings and
    apply/append only the numbered rows witnessed by the payload.
    """
    if master_subsection.kind is not IRNodeKind.SUBSECTION:
        return None
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return None
    unlabeled_row_merge = _merge_single_unlabeled_sparse_item_row_into_subsection(
        master_subsection,
        muutos_ir,
    )
    if unlabeled_row_merge is not None:
        return unlabeled_row_merge

    slots = [
        child
        for child in muutos_ir.children
        if child.kind is IRNodeKind.SUBSECTION or _is_omission_ir(child)
    ]
    if len(slots) < 3 or not slots or not _is_omission_ir(slots[0]):
        return None
    if not any(_is_omission_ir(child) for child in slots):
        return None

    payload_rows: list[IRNode] = []
    for slot in slots:
        if _is_omission_ir(slot):
            continue
        if slot.kind is not IRNodeKind.SUBSECTION:
            return None
        row = _item_row_from_sparse_omission_subsection(slot)
        if row is None:
            return None
        payload_rows.append(row)
    if not payload_rows:
        return None

    payload_labels = [normalized_label_key(row.label) for row in payload_rows]
    if any(not label.isdigit() for label in payload_labels):
        return None
    payload_nums = [int(label) for label in payload_labels]
    if payload_nums != sorted(payload_nums) or len(set(payload_nums)) != len(payload_nums):
        return None
    if payload_nums == list(range(1, len(payload_nums) + 1)):
        return None

    live_children = list(master_subsection.children)
    paragraph_positions = [
        idx
        for idx, child in enumerate(live_children)
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    if len(paragraph_positions) < 3:
        return None
    first_para = paragraph_positions[0]
    last_para = paragraph_positions[-1]
    if any(
        child.kind is not IRNodeKind.PARAGRAPH
        for child in live_children[first_para : last_para + 1]
    ):
        return None

    live_rows = [
        child
        for child in live_children[first_para : last_para + 1]
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    live_labels = [normalized_label_key(row.label) for row in live_rows]
    if any(not label.isdigit() for label in live_labels):
        return None
    live_nums = [int(label) for label in live_labels]
    if live_nums != sorted(live_nums) or len(set(live_nums)) != len(live_nums):
        return None

    live_by_num = dict(zip(live_nums, live_rows, strict=True))
    max_live = live_nums[-1]
    extension_nums = [num for num in payload_nums if num not in live_by_num]
    if extension_nums:
        if extension_nums != list(range(max_live + 1, max_live + 1 + len(extension_nums))):
            return None

    payload_by_num = dict(zip(payload_nums, payload_rows, strict=True))
    merged_nums = sorted(set(live_nums).union(payload_nums))
    merged_rows = [
        payload_by_num[num] if num in payload_by_num else live_by_num[num]
        for num in merged_nums
    ]
    return _SparseSubsectionItemMergeResult(
        node=IRNode(
            kind=master_subsection.kind,
            label=master_subsection.label,
            text=master_subsection.text,
            attrs=dict(master_subsection.attrs),
            children=tuple([*live_children[:first_para], *merged_rows, *live_children[last_para + 1 :]]),
        ),
        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_SPARSE_OMISSION_ITEM_MERGE,
        payload_sibling_count=len(payload_rows),
    )


def _resolve_subsection_index(
    subsecs: List[IRNode],
    target_paragraph: int,
) -> int:
    """Compute the 0-based subsection index for a target_paragraph value."""
    n = target_paragraph - 1
    exact_idx = next(
        (idx for idx, sub in enumerate(subsecs) if sub.label and normalized_label_key(sub.label) == str(target_paragraph)),
        None,
    )
    if exact_idx is not None:
        return exact_idx
    return n


def _resolve_item_subsection_index(subsecs: List[IRNode], target_paragraph: int) -> int:
    """Compute the item-side subsection index for intro-list moment shapes."""
    if _has_intro_list_moment_shape_ir(subsecs) and target_paragraph == 1:
        return 1
    return _resolve_subsection_index(subsecs, target_paragraph)


def _resolve_subsection_index_with_fragment(
    subsecs: List[IRNode],
    target_paragraph: int,
) -> tuple[Optional[int], Optional[int], bool]:
    """Resolve target_paragraph and report any skipped stale continuation slot."""
    target_label = str(target_paragraph)
    exact_idx = next(
        (idx for idx, sub in enumerate(subsecs) if sub.label and normalized_label_key(sub.label) == target_label),
        None,
    )
    if exact_idx is None:
        return None, None, False
    if _is_content_only_continuation_fragment(subsecs, exact_idx) and exact_idx + 1 < len(subsecs):
        return exact_idx + 1, exact_idx, True
    return exact_idx, None, False


def _has_colon_led_intro_list_moment_shape(subsecs: List[IRNode]) -> bool:
    """True only for list-carrier shapes with an explicit colon-led intro."""
    if not _has_intro_list_moment_shape_ir(subsecs):
        return False
    first_sub = subsecs[0]
    text = " ".join(
        (child.text or "").strip()
        for child in first_sub.children
        if child.kind in {IRNodeKind.INTRO, IRNodeKind.CONTENT}
    ).strip()
    return text.endswith(":")


def _resolve_subsection_index_with_rebound_kind(
    subsecs: List[IRNode],
    target_paragraph: int,
) -> tuple[Optional[int], Optional[int], Optional[RecoveryKind], bool]:
    """Resolve a subsection index and classify any rebound shape explicitly."""
    n, stale_fragment_idx, rebound_from_fragment = _resolve_subsection_index_with_fragment(subsecs, target_paragraph)
    exact_match = n is not None and any(
        sub.label and normalized_label_key(sub.label) == str(target_paragraph) for sub in subsecs
    )
    if n is None:
        n = _resolve_subsection_index(subsecs, target_paragraph)
    if rebound_from_fragment:
        return n, stale_fragment_idx, RecoveryKind.CONTINUATION_FRAGMENT_SKIP, exact_match
    if _has_colon_led_intro_list_moment_shape(subsecs) and target_paragraph >= 2 and target_paragraph < len(subsecs):
        return target_paragraph, stale_fragment_idx, RecoveryKind.INTRO_LIST_MOMENT_SHAPE, exact_match
    if n is not None and not exact_match:
        return n, stale_fragment_idx, RecoveryKind.MISSING_EXACT_SUBSECTION_LABEL, exact_match
    return n, stale_fragment_idx, None, exact_match


def _apply_subsection_repeal(
    state: "ReplayState",
    view: "_SubsectionApplyView | AmendmentOp | ResolvedOp",
    sec_path: list[TreePathStep],
    sec: IRNode,
    subsecs: List[IRNode],
    profile: ReplayProfile,
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> Optional["ReplayState"]:
    """REPEAL a whole subsection (momentti). Returns updated state or None if not applicable."""
    view = _coerce_subsection_apply_view(view)
    if view.op_type != "REPEAL" or not view.target_paragraph or view.target_item:
        return None
    n, stale_fragment_idx, rebound_kind, exact_idx_found = _resolve_subsection_index_with_rebound_kind(
        subsecs, view.target_paragraph
    )
    if (
        rebound_kind is not None
        and strict_profile is not None
        and not strict_profile.allows_context_dependent_anchor_resolution
    ):
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_subsection_target_rebound_pathology(
                    source_statute=view.legacy_source_statute_id,
                    target_section=view.target_section,
                    target_paragraph=view.target_paragraph or "",
                    rebound_kind=rebound_kind,
                    stale_fragment_idx=stale_fragment_idx if stale_fragment_idx is not None else -1,
                    live_has_paragraphs=any(
                        any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                    ),
                    amend_has_paragraphs=False,
                )
            )
        return None
    if n is None:
        return None
    if 0 <= n < len(subsecs):
        rebound_reported = False
        def _report_fragment_rebound() -> None:
            nonlocal rebound_reported
            if source_pathologies_out is None or rebound_reported:
                return
            if rebound_kind is None:
                return
            source_pathologies_out.append(
                build_subsection_target_rebound_pathology(
                    source_statute=view.legacy_source_statute_id,
                    target_section=view.target_section,
                    target_paragraph=view.target_paragraph or "",
                    rebound_kind=rebound_kind,
                    stale_fragment_idx=stale_fragment_idx if stale_fragment_idx is not None else -1,
                    live_has_paragraphs=any(
                        any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                    ),
                    amend_has_paragraphs=False,
                )
            )
            rebound_reported = True
        if profile.synthesize_repeal_placeholders:
            ph = IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=subsecs[n].label,
                attrs={"lawvm_repeal_placeholder": "1"},
            )
            new_sec = _tops.replace_nth(sec, "subsection", n, ph)
        else:
            new_sec = _tops.remove_nth(sec, "subsection", n)
        if stale_fragment_idx is not None:
            new_sec = _tops.remove_nth(new_sec, "subsection", stale_fragment_idx)
        _report_fragment_rebound()
        logger.debug("  %s → momentti repeal", ctx_label)
        return _with_preserved_provision_index(
            state,
            _tops.replace_at(state.ir, sec_path, new_sec),
        )
    # The momentti index resolved but fell outside the live subsection range: the
    # authored REPEAL applies nothing. Witness the dropped op on the
    # source-pathology ledger before declining rather than vanish as a silent
    # no-op (LAWVM_PIPELINE_CONTRACT §1.1 no silent drop).
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_subsection_target_absent_pathology(
                source_statute=view.legacy_source_statute_id,
                target_section=view.target_section,
                target_paragraph=view.target_paragraph or "",
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children)
                    for sub in subsecs
                ),
            )
        )
    replay_print(f"  {ctx_label} → FAILED (momentti {view.target_paragraph} not found)")
    return state


def _apply_subsection_replace(
    state: "ReplayState",
    view: "_SubsectionApplyView | AmendmentOp | ResolvedOp",
    sec_path: list[TreePathStep],
    sec: IRNode,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    muutos_ir: Optional[IRNode],
    profile: ReplayProfile,
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> Optional["ReplayState"]:
    """REPLACE a whole subsection (momentti). Returns updated state or None if not applicable."""
    view = _coerce_subsection_apply_view(view)
    if view.op_type != "REPLACE" or not view.target_paragraph or view.target_item or view.target_special:
        return None
    n, stale_fragment_idx, rebound_kind, exact_idx_found = _resolve_subsection_index_with_rebound_kind(
        subsecs, view.target_paragraph
    )
    has_higher_live_numeric_label = any(
        (sub.label or "").strip().isdigit() and int((sub.label or "").strip()) > view.target_paragraph
        for sub in subsecs
    )
    rebound_reported = False

    if (
        rebound_kind is not None
        and strict_profile is not None
        and not strict_profile.allows_context_dependent_anchor_resolution
    ):
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_subsection_target_rebound_pathology(
                    source_statute=view.legacy_source_statute_id,
                    target_section=view.target_section,
                    target_paragraph=view.target_paragraph or "",
                    rebound_kind=rebound_kind,
                    stale_fragment_idx=stale_fragment_idx if stale_fragment_idx is not None else -1,
                    live_has_paragraphs=any(
                        any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                    ),
                    amend_has_paragraphs=bool(
                        amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                    ),
                )
            )
        return None
    if n is None:
        return None

    def _report_fragment_rebound() -> None:
        nonlocal rebound_reported
        if source_pathologies_out is None or rebound_reported:
            return
        if rebound_kind is None:
            return
        source_pathologies_out.append(
            build_subsection_target_rebound_pathology(
                source_statute=view.legacy_source_statute_id,
                target_section=view.target_section,
                target_paragraph=view.target_paragraph or "",
                rebound_kind=rebound_kind,
                stale_fragment_idx=stale_fragment_idx if stale_fragment_idx is not None else -1,
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
        rebound_reported = True

    def _collapse_absorbed_successor_and_rebase_labels(
        section: IRNode,
        replace_idx: int,
        replacement_node: IRNode,
    ) -> IRNode:
        """Collapse an absorbed next sibling and shift later numeric labels down.

        Historical Finland sparse subsection rewrites sometimes encode the new
        leading moment so that it absorbs the full operative text of the next
        live moment. When that happens, keeping the old next sibling produces a
        duplicated sentence, and later sparse targets in the same section still
        need to land on the logically rebased numbering. Collapse only when the
        replacement clearly contains the next sibling's full text.
        """
        if replace_idx < 0 or replace_idx + 1 >= len(subsecs):
            return section
        current_sub = subsecs[replace_idx]
        next_sub = subsecs[replace_idx + 1]
        current_label = (current_sub.label or "").strip()
        next_label = (next_sub.label or "").strip()
        if not (current_label.isdigit() and next_label.isdigit()):
            return section
        if int(next_label) != int(current_label) + 1:
            return section

        replacement_text = " ".join(irnode_to_text(replacement_node).split())
        next_text = " ".join(irnode_to_text(next_sub).split())
        if not replacement_text or not next_text or len(next_text) < 25:
            return section
        if next_text not in replacement_text:
            return section

        children = list(section.children)
        subsection_positions = [
            i for i, child in enumerate(children) if child.kind is IRNodeKind.SUBSECTION
        ]
        if replace_idx + 1 >= len(subsection_positions):
            return section

        absorbed_child_pos = subsection_positions[replace_idx + 1]
        children.pop(absorbed_child_pos)
        for child_pos in subsection_positions[replace_idx + 2 :]:
            adjusted_pos = child_pos - 1
            child = children[adjusted_pos]
            label = (child.label or "").strip()
            if not label.isdigit():
                continue
            rebased_label = str(int(label) - 1)
            children[adjusted_pos] = IRNode(
                kind=child.kind,
                label=rebased_label,
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
        return _mark_rebased_section_complete(_tops._with_children(section, children))

    def _mark_rebased_section_complete(section: IRNode) -> IRNode:
        attrs = dict(section.attrs)
        attrs["lawvm_tail_policy"] = "replace_if_target_scope_requires"
        attrs["lawvm_payload_completeness_kind"] = "complete"
        return IRNode(
            kind=section.kind,
            label=section.label,
            text=section.text,
            attrs=attrs,
            children=tuple(section.children),
        )

    def _remove_subsection_and_rebase_following(section: IRNode, remove_idx: int) -> IRNode:
        children = list(section.children)
        subsection_positions = [
            i for i, child in enumerate(children) if child.kind is IRNodeKind.SUBSECTION
        ]
        if remove_idx < 0 or remove_idx >= len(subsection_positions):
            return section
        removed_child_pos = subsection_positions[remove_idx]
        children.pop(removed_child_pos)
        for child_pos in subsection_positions[remove_idx + 1 :]:
            adjusted_pos = child_pos - 1
            child = children[adjusted_pos]
            label = (child.label or "").strip()
            if not label.isdigit():
                continue
            children[adjusted_pos] = IRNode(
                kind=child.kind,
                label=str(int(label) - 1),
                text=child.text,
                attrs=dict(child.attrs),
                children=tuple(child.children),
            )
        return _mark_rebased_section_complete(_tops._with_children(section, children))

    _replace_sub = amend_sub
    if _replace_sub is not None:
        _replace_sub = _promote_content_only_intro_replace(_replace_sub)
        predecessor_tail_insert = _extract_predecessor_tail_paragraph_as_insert(
            sec,
            target_paragraph=view.target_paragraph,
            replacement_subsection=_replace_sub,
            muutos_ir=muutos_ir,
        )
        if predecessor_tail_insert is not None:
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_PREDECESSOR_TAIL_EXTRACT_INSERT,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=len(_replace_sub.children),
                    )
                )
            if strict_profile is not None:
                return None
            logger.debug("  %s → momentti replace (predecessor tail extract insert)", ctx_label)
            _report_fragment_rebound()
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, predecessor_tail_insert),
            )
        sparse_gap_insert = None
        if _has_owned_sparse_gap_replace_witness(
            view,
            _replace_sub,
            exact_idx_found=exact_idx_found,
            has_higher_live_numeric_label=has_higher_live_numeric_label,
        ):
            sparse_gap_insert = _owned_sparse_gap_insert_subsection(
                sec,
                _replace_sub,
                target_paragraph=view.target_paragraph,
            )
        if sparse_gap_insert is not None:
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_SPARSE_GAP_INSERT,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=len(_replace_sub.children),
                    )
                )
            if strict_profile is not None:
                return None
            logger.debug("  %s → momentti replace (owned sparse gap insert)", ctx_label)
            _report_fragment_rebound()
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, sparse_gap_insert),
            )
        unlabeled_sparse_item_merge = _merge_single_unlabeled_sparse_item_row_into_subsection(
            subsecs[n],
            muutos_ir,
        ) if n < len(subsecs) else None
        if unlabeled_sparse_item_merge is not None:
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=unlabeled_sparse_item_merge.recovery_kind,
                        live_sibling_count=len(
                            [
                                child
                                for child in subsecs[n].children
                                if child.kind is IRNodeKind.PARAGRAPH
                            ]
                        ),
                        payload_sibling_count=unlabeled_sparse_item_merge.payload_sibling_count,
                    )
                )
            if strict_profile is not None:
                return None
            new_sec = _tops.replace_nth(sec, "subsection", n, unlabeled_sparse_item_merge.node)
            if stale_fragment_idx is not None:
                new_sec = _tops.remove_nth(new_sec, "subsection", stale_fragment_idx)
            logger.debug("  %s → momentti replace (unlabeled sparse item merge)", ctx_label)
            _report_fragment_rebound()
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        if _looks_like_standalone_tail_subsection(_replace_sub):
            if n >= len(subsecs):
                # Target subsection does not exist in the replay state (missed by an
                # earlier degraded-confidence plan).  Do not append — a REPLACE op
                # requires an existing target; cascading into an insert would silently
                # build wrong state when the missing subsection is later repealed.
                #
                # Exception: if this is the immediate next moment (n == len(subsecs)),
                # let the normal append path below handle it even when the sparse
                # payload still carries an amendment-local label. Finland sparse
                # subsection bodies often reproduce the last changed moment as a
                # content-only local slot labeled "2" even when the live target is
                # legal moment 5. That is still a valid append to the next live
                # moment, not a stale standalone tail fragment.
                #
                # For true gaps (n > len(subsecs)), keep the historical guard unless
                # the payload already carries the exact legal target label.
                target_label = str(view.target_paragraph)
                if n > len(subsecs) and _replace_sub.label != target_label:
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_subsection_target_absent_pathology(
                                source_statute=view.legacy_source_statute_id,
                                target_section=view.target_section,
                                target_paragraph=view.target_paragraph or "",
                                live_label="",
                                has_higher_live_numeric_label=True,
                                live_has_paragraphs=any(
                                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children)
                                    for sub in subsecs
                                ),
                                amend_has_paragraphs=bool(
                                    amend_sub is not None
                                    and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                                ),
                            )
                        )
                    return None
                # Fall through to the normal append path.
            else:
                if not exact_idx_found:
                    resolved_label = (subsecs[n].label or "").strip()
                    if resolved_label.isdigit() and int(resolved_label) > view.target_paragraph:
                        return None
                master_label = str(view.target_paragraph) if stale_fragment_idx is not None else subsecs[n].label
                if master_label and _replace_sub.label != master_label:
                    _replace_sub = IRNode(
                        kind=_replace_sub.kind,
                        label=master_label,
                        text=_replace_sub.text,
                        attrs=dict(_replace_sub.attrs),
                        children=tuple(_replace_sub.children),
                    )
                if (
                    stale_fragment_idx is None
                    and view.target_paragraph == 1
                    and len(subsecs) == 1
                ):
                    _replace_sub = _merge_preserved_tail_into_replacement(subsecs[n], _replace_sub)
                new_sec = _tops.replace_nth(sec, "subsection", n, _replace_sub)
                new_sec = _collapse_absorbed_successor_and_rebase_labels(new_sec, n, _replace_sub)
                if stale_fragment_idx is not None:
                    new_sec = _tops.remove_nth(new_sec, "subsection", stale_fragment_idx)
                next_idx = n + 1
                recovery_kind = RecoveryKind.SUBSECTION_REPLACE_STANDALONE_TAIL_APPEND
                if (
                    next_idx < len(subsecs)
                    and _matches_standalone_tail_subsection_prune_witness(
                        _replace_sub,
                        subsecs[next_idx],
                    )
                ):
                    recovery_kind = RecoveryKind.SUBSECTION_REPLACE_STANDALONE_TAIL_SIBLING_PRUNE
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.legacy_source_statute_id,
                            target_unit_kind="section",
                            target_label=f"{view.target_section} § {view.target_paragraph} mom",
                            recovery_kind=recovery_kind,
                            live_sibling_count=len(subsecs),
                            payload_sibling_count=len(
                                [
                                    c
                                    for c in (_replace_sub.children if _replace_sub is not None else ())
                                    if c.kind == IRNodeKind.CONTENT
                                    or c.kind == IRNodeKind.INTRO
                                    or c.kind == IRNodeKind.PARAGRAPH
                                ]
                            ),
                        )
                    )
                if strict_profile is not None:
                    return None
                if recovery_kind == "subsection_replace_standalone_tail_sibling_prune":
                    current_subsecs = [c for c in new_sec.children if c.kind == IRNodeKind.SUBSECTION]
                    if (
                        next_idx < len(current_subsecs)
                        and _matches_standalone_tail_subsection_prune_witness(
                            _replace_sub,
                            current_subsecs[next_idx],
                        )
                    ):
                        new_sec = _remove_subsection_and_rebase_following(new_sec, next_idx)
                logger.debug("  %s → momentti replace (standalone tail)", ctx_label)
                _report_fragment_rebound()
                return _with_preserved_provision_index(
                    state,
                    _tops.replace_at(state.ir, sec_path, new_sec),
                )

        def _trim_earlier_sibling_duplicate_prefix(
            section: IRNode,
            replace_idx: int,
            replacement_node: IRNode,
        ) -> IRNode:
            """Trim duplicated leading prose from preserved earlier siblings.

            Some sparse whole-subsection replacements carry a repeated prefix
            sentence in a later subsection.  If an earlier preserved sibling
            still begins with that same sentence, trim the duplicated prefix
            from the preserved sibling rather than keeping the same prose twice.
            """
            replacement_text = " ".join(irnode_to_text(replacement_node).split())
            if not replacement_text:
                return section

            children = list(section.children)
            subsection_positions = [
                i for i, child in enumerate(children) if child.kind is IRNodeKind.SUBSECTION
            ]
            if replace_idx >= len(subsection_positions):
                return section

            for sibling_pos in subsection_positions[:replace_idx]:
                sibling = children[sibling_pos]
                if not sibling.children:
                    continue
                first_child = sibling.children[0]
                if first_child.kind not in {IRNodeKind.CONTENT, IRNodeKind.INTRO} or not first_child.text:
                    continue
                trimmed = _strip_leading_text_prefix(first_child.text, replacement_text)
                if trimmed is None:
                    continue
                trimmed_children = list(sibling.children)
                # The duplicated prose is a carried lead-in, not a structural
                # unit.  Drop the whole leading text node so the later explicit
                # subsection owns that prose once, while the numbered material
                # that follows remains intact.
                trimmed_children.pop(0)
                children[sibling_pos] = _tops._with_children(sibling, trimmed_children)
                return _tops._with_children(section, children)
            return section

        if not exact_idx_found and has_higher_live_numeric_label:
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_subsection_target_absent_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_section=view.target_section,
                        target_paragraph=view.target_paragraph or "",
                        live_label=(subsecs[n].label or "") if 0 <= n < len(subsecs) else "",
                        has_higher_live_numeric_label=True,
                        live_has_paragraphs=any(
                            any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                        ),
                        amend_has_paragraphs=bool(
                            amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                        ),
                    )
                )
            return None
        if n == len(subsecs):
            append_label = str(view.target_paragraph)
            if _replace_sub.label != append_label:
                _replace_sub = IRNode(
                    kind=_replace_sub.kind,
                    label=append_label,
                    text=_replace_sub.text,
                    attrs=dict(_replace_sub.attrs),
                    children=tuple(_replace_sub.children),
                )
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_APPEND,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=1,
                    )
                )
            if strict_profile is not None:
                return None
            new_sec = _tops._with_children(sec, list(sec.children) + [_replace_sub])
            logger.debug("  %s → momentti replace (append)", ctx_label)
            _report_fragment_rebound()
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        if 0 <= n < len(subsecs):
            if not exact_idx_found:
                resolved_label = (subsecs[n].label or "").strip()
                if resolved_label.isdigit() and int(resolved_label) > view.target_paragraph:
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_subsection_target_absent_pathology(
                                source_statute=view.legacy_source_statute_id,
                                target_section=view.target_section,
                                target_paragraph=view.target_paragraph or "",
                                live_label=subsecs[n].label or "",
                                has_higher_live_numeric_label=True,
                                live_has_paragraphs=any(
                                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children)
                                    for sub in subsecs
                                ),
                                amend_has_paragraphs=bool(
                                    amend_sub is not None
                                    and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                                ),
                            )
                        )
                    return None
            sparse_item_merge = _merge_sparse_omission_item_rows_into_subsection(
                subsecs[n],
                muutos_ir,
            )
            if sparse_item_merge is not None:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.legacy_source_statute_id,
                            target_unit_kind="section",
                            target_label=f"{view.target_section} § {view.target_paragraph} mom",
                            recovery_kind=sparse_item_merge.recovery_kind,
                            live_sibling_count=len(
                                [
                                    child
                                    for child in subsecs[n].children
                                    if child.kind is IRNodeKind.PARAGRAPH
                                ]
                            ),
                            payload_sibling_count=sparse_item_merge.payload_sibling_count,
                        )
                    )
                if strict_profile is not None:
                    return None
                new_sec = _tops.replace_nth(sec, "subsection", n, sparse_item_merge.node)
                if stale_fragment_idx is not None:
                    new_sec = _tops.remove_nth(new_sec, "subsection", stale_fragment_idx)
                logger.debug("  %s → momentti replace (sparse omission item merge)", ctx_label)
                _report_fragment_rebound()
                return _with_preserved_provision_index(
                    state,
                    _tops.replace_at(state.ir, sec_path, new_sec),
                )
            bracketed_rewrite = _rewrite_bracketed_single_subsection_replace_ir(
                sec,
                _replace_sub,
                view.target_paragraph,
                muutos_ir,
                view.legacy_source_statute_id,
            )
            if bracketed_rewrite is not None:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.legacy_source_statute_id,
                            target_unit_kind="section",
                            target_label=f"{view.target_section} § {view.target_paragraph} mom",
                            recovery_kind=RecoveryKind.OMISSION_BRACKETED_SINGLE_SUBSECTION_REWRITE,
                            live_sibling_count=len(subsecs),
                            payload_sibling_count=len(
                                [
                                    c
                                    for c in muutos_ir.children
                                    if c.kind == IRNodeKind.SUBSECTION or c.kind == IRNodeKind.OMISSION
                                ]
                            )
                            if muutos_ir is not None
                            else 0,
                        )
                    )
                logger.debug("  %s → momentti replace (omission-bracketed rewrite)", ctx_label)
                _report_fragment_rebound()
                return _with_preserved_provision_index(
                    state,
                    _tops.replace_at(state.ir, sec_path, bracketed_rewrite),
                )
            master_label = str(view.target_paragraph) if stale_fragment_idx is not None else subsecs[n].label
            # Whole-subsection replace: strip a trailing-only omission from the
            # amendment payload before passing it to the merge functions.
            # A trailing omission in this context is a Finlex editorial artifact
            # meaning "the old content ends here" — it must not cause
            # _merge_subsection_with_omission_ir to splice stale master items
            # back into the replacement.  Inner omissions (before the last item)
            # are left untouched because they carry real merge semantics.
            if _is_trailing_only_omission_sub(_replace_sub):
                logger.debug(
                    "  %s → stripped trailing omission from whole-subsection replace payload",
                    ctx_label,
                )
                _replace_sub = IRNode(
                    kind=_replace_sub.kind,
                    label=_replace_sub.label,
                    text=_replace_sub.text,
                    attrs=dict(_replace_sub.attrs),
                    children=tuple(_replace_sub.children[:-1]),
                )
            complete_numbered_rewrite = _strip_context_carried_omission_for_complete_numbered_replace(_replace_sub)
            if complete_numbered_rewrite is not None:
                logger.debug(
                    "  %s → stripped context-carried omission from complete numbered whole-subsection replace payload",
                    ctx_label,
                )
                _replace_sub = complete_numbered_rewrite
            elif view.has_exact_bound_payload:
                owned_bound_rewrite = _strip_owned_bound_omissions_for_complete_numbered_replace(
                    _replace_sub
                )
                if owned_bound_rewrite is not None:
                    logger.debug(
                        "  %s → stripped owned bound omission from complete numbered whole-subsection replace payload",
                        ctx_label,
                    )
                    _replace_sub = owned_bound_rewrite
            merged = _merge_intro_only_subsection_replace(subsecs[n], _replace_sub)
            if merged is None:
                merged = _merge_subsection_accumulate_inner_omission_ir(subsecs[n], _replace_sub)
            if merged is None:
                merged = _merge_subsection_with_omission_ir(subsecs[n], _replace_sub)
            if merged is None and source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_OMISSION_MERGE_FALLBACK,
                        live_sibling_count=len(subsecs[n].children),
                        payload_sibling_count=len(_replace_sub.children),
                    )
                )
            if merged is None and strict_profile is not None:
                return None
            replacement = merged if merged is not None else _replace_sub
            if master_label and replacement.label != master_label:
                replacement = IRNode(
                    kind=replacement.kind,
                    label=master_label,
                    text=replacement.text,
                    attrs=replacement.attrs,
                    children=replacement.children,
                )
            new_sec = _tops.replace_nth(sec, "subsection", n, replacement)
            new_sec = _collapse_absorbed_successor_and_rebase_labels(new_sec, n, replacement)
            tail_source = amend_sub if amend_sub is not None else replacement
            if tail_source is not None:
                next_idx = n + 1
                if (
                    next_idx < len(subsecs)
                    and _matches_standalone_tail_subsection_prune_witness(
                        tail_source,
                        subsecs[next_idx],
                    )
                ):
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=view.legacy_source_statute_id,
                                target_unit_kind="section",
                                target_label=f"{view.target_section} § {view.target_paragraph} mom",
                                recovery_kind=RecoveryKind.SUBSECTION_REPLACE_STANDALONE_TAIL_SIBLING_PRUNE,
                                live_sibling_count=len(subsecs[next_idx].children),
                                payload_sibling_count=len(tail_source.children),
                            )
                        )
                    if strict_profile is not None:
                        return None
                    new_sec = _remove_subsection_and_rebase_following(new_sec, next_idx)
            new_sec = _trim_earlier_sibling_duplicate_prefix(new_sec, n, replacement)
            if stale_fragment_idx is not None:
                new_sec = _tops.remove_nth(new_sec, "subsection", stale_fragment_idx)
            logger.debug("  %s → momentti replace", ctx_label)
            _report_fragment_rebound()
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        if n > len(subsecs):
            if not exact_idx_found and has_higher_live_numeric_label:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_subsection_target_absent_pathology(
                            source_statute=view.legacy_source_statute_id,
                            target_section=view.target_section,
                            target_paragraph=view.target_paragraph or "",
                            live_label=(subsecs[n].label or "") if 0 <= n < len(subsecs) else "",
                            has_higher_live_numeric_label=True,
                            live_has_paragraphs=any(
                                any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children)
                                for sub in subsecs
                            ),
                            amend_has_paragraphs=bool(
                                amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                            ),
                        )
                    )
                return None
            append_label = str(view.target_paragraph)
            if _replace_sub.label != append_label:
                _replace_sub = IRNode(
                    kind=_replace_sub.kind,
                    label=append_label,
                    text=_replace_sub.text,
                    attrs=dict(_replace_sub.attrs),
                    children=tuple(_replace_sub.children),
                )
            new_sec = _tops._with_children(sec, list(sec.children) + [_replace_sub])
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_REPLACE_FORCED_APPEND,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=1,
                    )
                )
            if strict_profile is not None:
                return None
            logger.debug("  %s → momentti replace (forced append, master had %s subsecs)", ctx_label, len(subsecs))
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
    return None


def _apply_subsection_insert(
    state: "ReplayState",
    view: "_SubsectionApplyView | AmendmentOp | ResolvedOp",
    sec_path: list[TreePathStep],
    sec: IRNode,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
    allow_expired_temporary_duplicate_label_replace: bool = False,
) -> Optional["ReplayState"]:
    """INSERT a new subsection (momentti). Returns updated state or None if not applicable."""
    view = _coerce_subsection_apply_view(view)
    if view.op_type != "INSERT" or not view.target_paragraph or view.target_item:
        return None
    if amend_sub is not None:
        target_label = str(view.target_paragraph)
        existing_idx = next(
            (
                idx
                for idx, sub in enumerate(subsecs)
                if sub.label and normalized_label_key(sub.label) == target_label
            ),
            None,
        )
        # In-place merge: the subsection payload was produced by merging new
        # items into the existing subsection content (e.g., item INSERT
        # accumulated via _merge_section_inner_subsection_omission_ir with
        # mark_in_place=True).  Treat as an in-place REPLACE — the existing
        # subsection gets the merged content; it must NOT be shifted upward to
        # make room for a spurious "new" subsection:N.
        # This guard is unconditional on has_exact_bound_payload / is_temporary
        # because the marker itself is the authoritative signal.
        if existing_idx is not None and amend_sub.attrs.get("lawvm_in_place_merge") == "1":
            existing_sub = subsecs[existing_idx]
            replacement = amend_sub
            if existing_sub.label and replacement.label != existing_sub.label:
                replacement = IRNode(
                    kind=replacement.kind,
                    label=existing_sub.label,
                    text=replacement.text,
                    attrs=dict(replacement.attrs),
                    children=tuple(replacement.children),
                )
            new_sec = _tops.replace_nth(sec, "subsection", existing_idx, replacement)
            logger.debug("  %s → momentti insert-as-replace (in-place merge)", ctx_label)
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        if existing_idx is not None and (view.is_temporary or view.has_exact_bound_payload):
            # If the same-labeled subsection already carries the exact payload,
            # the INSERT is a routed duplicate of content that was already
            # materialized, either by an earlier temporary overlay or by a
            # late-waist exact sparse-slot binding paired with whole-section
            # replacement. Re-running the renumber path would create a spurious
            # extra subsection.
            existing_sub = subsecs[existing_idx]
            existing_text = " ".join(irnode_to_text(existing_sub).split())
            amend_text = " ".join(irnode_to_text(amend_sub).split())
            if existing_text and existing_text == amend_text:
                replacement = amend_sub
                if existing_sub.label and replacement.label != existing_sub.label:
                    replacement = IRNode(
                        kind=replacement.kind,
                        label=existing_sub.label,
                        text=replacement.text,
                        attrs=dict(replacement.attrs),
                        children=tuple(replacement.children),
                    )
                new_sec = _tops.replace_nth(sec, "subsection", existing_idx, replacement)
                logger.debug("  %s → momentti insert-as-replace (duplicate payload)", ctx_label)
                return _with_preserved_provision_index(
                    state,
                    _tops.replace_at(state.ir, sec_path, new_sec),
                )
        # Guard: if the target subsection label already resolves to a repeal
        # placeholder, consume that placeholder instead of shifting later live
        # moments upward. This preserves "kumotun N momentin tilalle uusi N
        # momentti" semantics: the new content occupies the reserved slot
        # rather than renumbering the following substantive moment.
        if existing_idx is not None and subsecs[existing_idx].attrs.get("lawvm_repeal_placeholder") == "1":
            replacement = amend_sub
            master_label = subsecs[existing_idx].label
            if master_label and replacement.label != master_label:
                replacement = IRNode(
                    kind=replacement.kind,
                    label=master_label,
                    text=replacement.text,
                    attrs=dict(replacement.attrs),
                    children=tuple(replacement.children),
                )
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_INSERT_REPEAL_PLACEHOLDER_REPLACE,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=len(
                            [c for c in amend_sub.children if c.kind == IRNodeKind.PARAGRAPH]
                        ),
                    )
                )
            if strict_profile is not None:
                return None
            new_sec = _tops.replace_nth(sec, "subsection", existing_idx, replacement)
            logger.debug("  %s → momentti insert-as-replace (repeal placeholder)", ctx_label)
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        if existing_idx is not None and allow_expired_temporary_duplicate_label_replace:
            replacement = amend_sub
            master_label = subsecs[existing_idx].label
            if master_label and replacement.label != master_label:
                replacement = IRNode(
                    kind=replacement.kind,
                    label=master_label,
                    text=replacement.text,
                    attrs=dict(replacement.attrs),
                    children=tuple(replacement.children),
                )
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_INSERT_EXPIRED_TEMPORARY_SLOT_REPLACE,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=len(
                            [c for c in amend_sub.children if c.kind == IRNodeKind.PARAGRAPH]
                        ),
                    )
                )
            if strict_profile is not None:
                return None
            new_sec = _tops.replace_nth(sec, "subsection", existing_idx, replacement)
            logger.debug("  %s → momentti insert-as-replace (expired temporary slot)", ctx_label)
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        # Guard: if the target subsection label already exists AND the op is
        # temporary, treat as REPLACE rather than inserting a duplicate.
        # This handles successive temporary amendments (e.g. 2020/708 and
        # 2022/108 both doing "lisätään väliaikaisesti 1 §:ään uusi 3 momentti")
        # where the second INSERT should overwrite the first, not create a
        # duplicate.
        #
        # IMPORTANT: Only apply for temporary ops.  Permanent INSERT ops mean
        # "add a new momentti here, shifting existing ones up" — the fact that
        # the target label already exists is expected (the renumber logic will
        # handle it).  Applying the dedup guard unconditionally breaks cases
        # where a permanent amendment inserts a new momenti 2 when momenti 2
        # already exists in the master text (e.g. 1982/710 §17, 1969/327 §4).
        if existing_idx is not None and view.is_temporary:
            # Relabel amend_sub to match the canonical label of the existing slot
            master_label = subsecs[existing_idx].label
            replacement = amend_sub
            if master_label and replacement.label != master_label:
                replacement = IRNode(
                    kind=replacement.kind,
                    label=master_label,
                    text=replacement.text,
                    attrs=dict(replacement.attrs),
                    children=tuple(replacement.children),
                )
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.legacy_source_statute_id,
                        target_unit_kind="section",
                        target_label=f"{view.target_section} § {view.target_paragraph} mom",
                        recovery_kind=RecoveryKind.SUBSECTION_INSERT_TEMPORARY_DUPLICATE_LABEL_REPLACE,
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=len(
                            [c for c in amend_sub.children if c.kind == IRNodeKind.PARAGRAPH]
                        ),
                    )
                )
            if strict_profile is not None:
                return None
            new_sec = _tops.replace_nth(sec, "subsection", existing_idx, replacement)
            logger.debug("  %s → momentti insert-as-replace (duplicate label, temporary)", ctx_label)
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
        new_sec = _insert_subsection_with_renumber_ir(
            sec,
            amend_sub,
            view.target_paragraph,
            source_pathologies_out=source_pathologies_out,
        )
        logger.debug("  %s → momentti insert", ctx_label)
        return _with_preserved_provision_index(
            state,
            _tops.replace_at(state.ir, sec_path, new_sec),
        )
    return None
