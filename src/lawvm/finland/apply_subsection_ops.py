"""Subsection-level executor helpers for Finland apply.

This module owns the level-M subsection handlers plus the subsection-index
resolver they share. The deterministic dispatcher still lives in
``apply.py`` for now, but the subsection executor bodies no longer need to
cohabit with typed/legacy dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING, List, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
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
            and _tops._norm(mapped.label) == str(scope.target_paragraph)
        )
    else:
        legacy_source_statute_id = (
            op.source_statute
            or (op.lo.source.statute_id if op.lo is not None and op.lo.source is not None else "")
        )
        is_temporary = temporary_signal_for_op(op)
        has_exact_bound_payload = op.has_exact_bound_payload
        op_type = op.op_type
        target_section = op.target_section or ""
        target_item = op.target_item
        target_special = op.target_special
    return _SubsectionApplyView(
        op_type=op_type,
        target_section=target_section,
        target_paragraph=scope.target_paragraph if isinstance(op, ResolvedOp) else op.target_paragraph,
        target_item=target_item,
        target_special=target_special,
        legacy_source_statute_id=legacy_source_statute_id,
        is_temporary=is_temporary,
        has_exact_bound_payload=has_exact_bound_payload,
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


def _matches_standalone_tail_subsection_prune_witness(
    replacement: IRNode,
    successor: IRNode,
) -> bool:
    """Return True when replacement already absorbs the successor tail text."""
    if not (
        _looks_like_standalone_tail_subsection(replacement)
        and _looks_like_standalone_tail_subsection(successor)
    ):
        return False

    replacement_text = " ".join(irnode_to_text(replacement).split())
    successor_text = " ".join(irnode_to_text(successor).split())
    if not replacement_text or not successor_text:
        return False

    tail_text = _tail_after_first_sentence(replacement_text)
    if not tail_text:
        return False

    return tail_text.rstrip(" .;:!?") == successor_text.rstrip(" .;:!?")


def _tail_after_first_sentence(text: str) -> str:
    """Return the trailing text after the first sentence, if any."""
    stripped = text.strip()
    if not stripped:
        return ""
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
    if slot_kinds != [IRNodeKind.OMISSION, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION]:
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
    if not replacement_text or not predecessor_tail_text or current_target_text == replacement_text:
        return None
    if predecessor_tail.kind == IRNodeKind.PARAGRAPH:
        if predecessor_tail_text != replacement_text:
            return None
    elif predecessor_tail.kind == IRNodeKind.CONTENT:
        if not has_numbered_predecessor_body:
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


def _strip_context_carried_omission_for_complete_numbered_replace(
    replacement_subsection: IRNode,
) -> IRNode | None:
    """Strip a context-carried omission from complete numbered whole-subsection replaces.

    Some amendment XML encodes a complete moment replacement as:

    - intro/context prose
    - omission marker
    - explicit new numbered items
    - optional wrap-up

    For a whole-subsection REPLACE carrying a full numbered list plus wrap-up,
    that omission is not a claim to preserve unmatched old item tail. It only
    marks that the intro was carried in the amendment body. When the numbered
    payload is explicit and contiguous, keep the amendment intro plus its
    explicit trailing children and drop the omission before the generic
    omission-merge lane can splice stale master items back in.
    """
    children = replacement_subsection.children
    omission_idx = next((i for i, child in enumerate(children) if _is_omission_ir(child)), None)
    if omission_idx is None:
        return None

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
    numbered_labels = [_tops._norm(child.label or "") for child in numbered]
    if any(not label.isdigit() for label in numbered_labels):
        return None
    if numbered_labels != [str(i) for i in range(1, len(numbered_labels) + 1)]:
        return None
    if not any(child.kind is IRNodeKind.WRAP_UP for child in trailing):
        return None

    return IRNode(
        kind=replacement_subsection.kind,
        label=replacement_subsection.label,
        text=replacement_subsection.text,
        attrs=dict(replacement_subsection.attrs),
        children=tuple((*pre_omission, *trailing)),
    )


def _resolve_subsection_index(
    subsecs: List[IRNode],
    target_paragraph: int,
) -> int:
    """Compute the 0-based subsection index for a target_paragraph value."""
    n = target_paragraph - 1
    exact_idx = next(
        (idx for idx, sub in enumerate(subsecs) if sub.label and _tops._norm(sub.label) == str(target_paragraph)),
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
        (idx for idx, sub in enumerate(subsecs) if sub.label and _tops._norm(sub.label) == target_label),
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
) -> tuple[Optional[int], Optional[int], Optional[str], bool]:
    """Resolve a subsection index and classify any rebound shape explicitly."""
    n, stale_fragment_idx, rebound_from_fragment = _resolve_subsection_index_with_fragment(subsecs, target_paragraph)
    exact_match = n is not None and any(
        sub.label and _tops._norm(sub.label) == str(target_paragraph) for sub in subsecs
    )
    if n is None:
        n = _resolve_subsection_index(subsecs, target_paragraph)
    if rebound_from_fragment:
        return n, stale_fragment_idx, "continuation_fragment_skip", exact_match
    if _has_colon_led_intro_list_moment_shape(subsecs) and target_paragraph >= 2 and target_paragraph < len(subsecs):
        return target_paragraph, stale_fragment_idx, "intro_list_moment_shape", exact_match
    if n is not None and not exact_match:
        return n, stale_fragment_idx, "missing_exact_subsection_label", exact_match
    return n, stale_fragment_idx, None, exact_match


def _apply_subsection_repeal(
    state: "ReplayState",
    view: "_SubsectionApplyView | AmendmentOp | ResolvedOp",
    sec_path: list,
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
    replay_print(f"  {ctx_label} → FAILED (momentti {view.target_paragraph} not found)")
    return state


def _apply_subsection_replace(
    state: "ReplayState",
    view: "_SubsectionApplyView | AmendmentOp | ResolvedOp",
    sec_path: list,
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
        return _tops._with_children(section, children)

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
                        recovery_kind="subsection_replace_predecessor_tail_extract_insert",
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
                recovery_kind = "subsection_replace_standalone_tail_append"
                if (
                    next_idx < len(subsecs)
                    and _matches_standalone_tail_subsection_prune_witness(
                        _replace_sub,
                        subsecs[next_idx],
                    )
                ):
                    recovery_kind = "subsection_replace_standalone_tail_sibling_prune"
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
                        new_sec = _tops.remove_nth(new_sec, "subsection", next_idx)
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
            append_label = str(len(subsecs) + 1)
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
                        recovery_kind="subsection_replace_append",
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
                            recovery_kind="omission_bracketed_single_subsection_rewrite",
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
                        recovery_kind="subsection_replace_omission_merge_fallback",
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
                                recovery_kind="subsection_replace_standalone_tail_sibling_prune",
                                live_sibling_count=len(subsecs[next_idx].children),
                                payload_sibling_count=len(tail_source.children),
                            )
                        )
                    if strict_profile is not None:
                        return None
                    new_sec = _tops.remove_nth(new_sec, "subsection", next_idx)
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
            append_label = str(len(subsecs) + 1)
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
                        recovery_kind="subsection_replace_forced_append",
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=1,
                    )
                )
            if strict_profile is not None:
                return None
            logger.debug("  %s → momentti replace (forced append, master had %s subsecs)", ctx_label, len(subsecs))
            _report_fragment_rebound()
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, new_sec),
            )
    return None


def _apply_subsection_insert(
    state: "ReplayState",
    view: "_SubsectionApplyView | AmendmentOp | ResolvedOp",
    sec_path: list,
    sec: IRNode,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
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
                if sub.label and _tops._norm(sub.label) == target_label
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
                        recovery_kind="subsection_insert_repeal_placeholder_replace",
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
                        recovery_kind="subsection_insert_temporary_duplicate_label_replace",
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
