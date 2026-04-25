"""Item and special-target executor helpers for Finland apply.

This module owns the level-K and level-S executor bodies plus the local shape
helpers they share. The deterministic subsection dispatcher stays in
``apply.py`` for now, but the remaining item/special executor mass no longer
needs to cohabit with typed/legacy routing.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Set

from lawvm.core.compile_result import SourcePathology
from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.finland.ops import AmendmentOp, ReplayProfile, ResolvedOp
from lawvm.finland.helpers import _is_omission_ir, _norm_num_token, _previous_item_token
from lawvm.finland.source_pathology import (
    build_destructive_shape_loss_risk_pathology,
    build_item_target_anchor_absent_pathology,
    build_item_target_structure_absent_pathology,
    build_item_target_slot_occupied_pathology,
    build_subsection_target_rebound_pathology,
)
from lawvm.finland.apply_ir_ops import (

    _relabel_paragraph_ir,
    _relabel_subsection_ir,
    _rebuild_section_with_subsections_ir,
    _shift_lettered_item_labels_after_repeal,
    _insert_item_with_suffix_renumber_ir,
)
from lawvm.finland.apply_payload_ops import (
    _has_intro_list_moment_shape_ir,
    _find_amend_paragraph,
    _find_amend_intro,
    _sanitize_shared_tail_item_replace_paragraph_ir,
)
from lawvm.finland.apply_runtime_support import (
    _legacy_target_section_for_scope,
    _legacy_target_special_for_scope,
    _with_preserved_provision_index,
)
from lawvm.finland.apply_subsection_ops import _resolve_item_subsection_index
from lawvm.finland.merge import (
    _paragraph_to_subparagraph_ir,
    _merge_sparse_alakohta_insert_ir,
    _merge_sparse_alakohta_replace_ir,
    _merge_letter_item_into_content_only_subsection_ir,
    _merge_letter_item_from_content_subsection_ir,
)

if TYPE_CHECKING:
    from lawvm.finland.statute import ReplayState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ItemApplyView:
    op_type: str
    source_statute: str
    target_unit_kind: TargetUnitKind
    target_section: str
    target_paragraph: int | None
    target_item: str | None
    target_special: str | None
    post_repeal_item_shift_label: str | None


def _coerce_item_apply_view(op: "_ItemApplyView | AmendmentOp | ResolvedOp") -> _ItemApplyView:
    if isinstance(op, _ItemApplyView):
        return op
    return _item_apply_view_for_op(op)


def _item_apply_view_for_op(op: AmendmentOp | ResolvedOp) -> _ItemApplyView:
    if isinstance(op, ResolvedOp):
        scope = op.resolved_target_scope_view
        source_statute = op.resolved_source_statute
        op_type = op.resolved_action_type
        target_section = _legacy_target_section_for_scope(scope, op.target_unit_kind)
        target_item = scope.target_item
        target_special = _legacy_target_special_for_scope(scope, op.effective_target_special)
    else:
        source_statute = op.source_statute or ""
        op_type = op.op_type
        target_section = op.target_section or ""
        target_item = op.target_item
        target_special = op.target_special
    return _ItemApplyView(
        op_type=op_type,
        source_statute=source_statute,
        target_unit_kind=op.target_unit_kind,
        target_section=target_section,
        target_paragraph=scope.target_paragraph if isinstance(op, ResolvedOp) else op.target_paragraph,
        target_item=target_item,
        target_special=target_special,
        post_repeal_item_shift_label=(
            op.resolved_post_repeal_item_shift_label if isinstance(op, ResolvedOp) else op.post_repeal_item_shift_label
        ),
    )


def _tail_norm(text: str) -> str:
    return (
        text.replace("―", "-")
        .replace("–", "-")
        .replace("§", "")
        .replace(" ", "")
        .strip(" .;,:")
        .lower()
    )


def _report_item_intro_list_rebound(
    *,
    source_pathologies_out: Optional[List[SourcePathology]],
    view: _ItemApplyView,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    strict_profile: Optional[StrictProfile] = None,
) -> bool:
    if view.target_paragraph != 1 or not _has_intro_list_moment_shape_ir(subsecs):
        return False
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_subsection_target_rebound_pathology(
                source_statute=view.source_statute,
                target_section=view.target_section,
                target_paragraph=str(view.target_paragraph or ""),
                rebound_kind="intro_list_moment_shape",
                stale_fragment_idx=-1,
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in subsec.children) for subsec in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
    return bool(
        strict_profile is not None and not strict_profile.allows_context_dependent_anchor_resolution
    )


def _report_item_johd_subsection_rebound(
    *,
    source_pathologies_out: Optional[List[SourcePathology]],
    view: _ItemApplyView,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    target_idx: int,
) -> None:
    if source_pathologies_out is None or view.target_paragraph is None:
        return
    if not (0 <= target_idx < len(subsecs)):
        return
    rebound_sub = subsecs[target_idx]
    if rebound_sub.label and _norm_num_token(rebound_sub.label) == str(view.target_paragraph):
        return
    source_pathologies_out.append(
        build_subsection_target_rebound_pathology(
            source_statute=view.source_statute,
            target_section=view.target_section,
            target_paragraph=str(view.target_paragraph or ""),
            rebound_kind="missing_exact_subsection_label",
            stale_fragment_idx=-1,
            live_has_paragraphs=any(
                any(child.kind == IRNodeKind.PARAGRAPH for child in subsec.children) for subsec in subsecs
            ),
            amend_has_paragraphs=bool(
                amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
            ),
        )
    )


def _report_item_missing_exact_subsection_label_rebound(
    *,
    source_pathologies_out: Optional[List[SourcePathology]],
    view: _ItemApplyView,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    target_idx: int,
    strict_profile: Optional[StrictProfile] = None,
) -> bool:
    if view.target_paragraph is None:
        return False
    if not (0 <= target_idx < len(subsecs)):
        return False
    if len(subsecs) == 1 and view.target_paragraph > len(subsecs):
        return False
    if view.target_paragraph == 1 and _has_intro_list_moment_shape_ir(subsecs):
        return False
    rebound_sub = subsecs[target_idx]
    if rebound_sub.label and _norm_num_token(rebound_sub.label) == str(view.target_paragraph):
        return False
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_subsection_target_rebound_pathology(
                source_statute=view.source_statute,
                target_section=view.target_section,
                target_paragraph=str(view.target_paragraph or ""),
                rebound_kind="missing_exact_subsection_label",
                stale_fragment_idx=-1,
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in subsec.children) for subsec in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
    return bool(
        strict_profile is not None and not strict_profile.allows_context_dependent_anchor_resolution
    )


def _collapse_absorbed_tail_subsection_ir(
    sec: IRNode,
    subsection_idx: int,
    merged_para: IRNode,
    source_statute: str = "",
    source_pathologies_out: Optional[List[SourcePathology]] = None,
) -> Optional[IRNode]:
    """Drop a stale carried subsection whose text was absorbed under an item tail.

    Some sparse item amendments move the closing sentence of a malformed
    content-only subsection under the inserted/replaced item as a subparagraph.
    When that happens, the immediately following carried subsection becomes
    stale residue and later numbered subsections should shift down by one.
    """
    subsecs = [child for child in sec.children if child.kind == IRNodeKind.SUBSECTION]
    if not (0 <= subsection_idx < len(subsecs) - 1):
        return None

    last_subparagraph = next(
        (child for child in reversed(merged_para.children) if child.kind == IRNodeKind.SUBPARAGRAPH),
        None,
    )
    if last_subparagraph is None:
        return None

    absorbed_tail_text = " ".join(irnode_to_text(last_subparagraph).split())
    absorbed_norm = _tail_norm(absorbed_tail_text)
    if not absorbed_norm:
        return None

    stale_subsection = subsecs[subsection_idx + 1]
    stale_text = " ".join(irnode_to_text(stale_subsection).split())
    stale_norm = _tail_norm(stale_text)
    if not stale_norm or not (
        absorbed_norm.startswith(stale_norm) or stale_norm.startswith(absorbed_norm)
    ):
        return None

    removed_label_norm = _tops._norm(stale_subsection.label or "")
    removed_numeric = int(removed_label_norm) if removed_label_norm.isdigit() else None

    rebuilt_subsections: List[IRNode] = []
    for idx, subsection in enumerate(subsecs):
        if idx == subsection_idx + 1:
            continue
        relabelled = subsection
        subsection_norm = _tops._norm(subsection.label or "")
        if (
            removed_numeric is not None
            and idx > subsection_idx + 1
            and subsection_norm.isdigit()
            and int(subsection_norm) > removed_numeric
            ):
            relabelled = _relabel_subsection_ir(subsection, str(int(subsection_norm) - 1))
        rebuilt_subsections.append(relabelled)

    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=source_statute,
                target_unit_kind="section",
                target_label=stale_subsection.label or str(subsection_idx + 2),
                recovery_kind="absorbed_tail_subsection_collapse",
                live_sibling_count=len(subsecs),
                payload_sibling_count=1,
            )
        )

    return _rebuild_section_with_subsections_ir(sec, rebuilt_subsections)
def _count_content_row_markers(node: IRNode) -> int:
    """Approximate count of lettered content rows in content-only list text."""
    text = irnode_to_text(node)
    return len(re.findall(r"(?<!\w)[A-ZÅÄÖ]\.", text))


def _subsection_exposes_targetable_item_structure(sub: Optional[IRNode], item_norm: str) -> bool:
    """Whether a subsection exposes explicit structure for the targeted item."""
    if sub is None:
        return False
    paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
    if not paras:
        return False
    compound = re.match(r"^(\d+)([a-z])$", item_norm)
    if compound:
        para_digit, sub_label = compound.groups()
        for para in paras:
            if not para.label or _tops._norm(para.label) != para_digit:
                continue
            if any(
                child.kind == IRNodeKind.SUBPARAGRAPH
                and child.label
                and _tops._norm(child.label) == sub_label
                for child in para.children
            ):
                return True
        return False
    return any(para.label and _tops._norm(para.label) == item_norm for para in paras)


def _prune_duplicate_tail_subsection_after_sparse_item_merge(
    sec: IRNode,
    subsection_index: int,
    merged_para: IRNode,
    source_statute: str = "",
    source_pathologies_out: Optional[List[SourcePathology]] = None,
) -> IRNode:
    """Remove an immediately following duplicate tail subsection after item merge.

    Sparse 1994/1420 section 21 style payloads absorb the carried tail sentence
    into the new subparagraph under item 7. If the live section still has the
    same sentence as the next content-only subsection, keep the merged item and
    drop the stale subsection so replay does not materialize the tail twice.
    """
    next_idx = subsection_index + 1
    if next_idx >= len(sec.children):
        return sec

    next_sub = sec.children[next_idx]
    if next_sub.kind != IRNodeKind.SUBSECTION:
        return sec
    if any(child.kind == IRNodeKind.PARAGRAPH for child in next_sub.children):
        return sec

    merged_subparagraphs = [child for child in merged_para.children if child.kind == IRNodeKind.SUBPARAGRAPH]
    if len(merged_subparagraphs) != 1:
        return sec

    merged_text = " ".join(irnode_to_text(merged_subparagraphs[0]).split())
    next_text = " ".join(irnode_to_text(next_sub).split())
    if not merged_text or merged_text != next_text:
        return sec

    new_children = list(sec.children)
    del new_children[next_idx]
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=source_statute,
                target_unit_kind="section",
                target_label=next_sub.label or str(next_idx + 1),
                recovery_kind="sparse_item_tail_subsection_prune",
                live_sibling_count=len([c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]),
                payload_sibling_count=1,
            )
        )
    logger.debug(
        "  sparse alakohta merge pruned consumed tail subsection %s",
        next_sub.label or str(next_idx + 1),
    )
    return _tops._with_children(sec, new_children)


def _build_compound_item_omission_children(master_para: IRNode, amend_para_src: IRNode) -> List[IRNode]:
    """Build replacement children for the compound item omission path."""
    amend_sp_children = amend_para_src.children
    omission_idx_sp = next((i for i, c in enumerate(amend_sp_children) if _is_omission_ir(c)), None)
    if omission_idx_sp is None:
        merged = _paragraph_to_subparagraph_ir(amend_para_src)
        return list(master_para.children) if merged is None else list(merged.children)

    trailing_sp: List[IRNode] = [c for c in amend_sp_children[omission_idx_sp + 1 :] if not _is_omission_ir(c)]
    trailing_sp_map: Dict[str, IRNode] = {
        c.label: c for c in trailing_sp if c.kind == IRNodeKind.SUBPARAGRAPH and c.label
    }
    master_subparas = [c for c in master_para.children if c.kind == IRNodeKind.SUBPARAGRAPH]
    master_non_sp = [c for c in master_para.children if c.kind != IRNodeKind.SUBPARAGRAPH]
    merged_sp: List[IRNode] = []
    replaced_sp_labels: Set[str] = set()
    for sp in master_subparas:
        if sp.label and sp.label in trailing_sp_map:
            merged_sp.append(trailing_sp_map[sp.label])
            replaced_sp_labels.add(sp.label)
        else:
            merged_sp.append(sp)
    for lbl, sp in trailing_sp_map.items():
        if lbl not in replaced_sp_labels:
            merged_sp.append(sp)
    amend_intro_sp = next(
        (c for c in amend_sp_children[:omission_idx_sp] if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)),
        None,
    )
    if amend_intro_sp is not None:
        return [amend_intro_sp] + [c for c in master_non_sp if c.kind not in (IRNodeKind.INTRO, IRNodeKind.CONTENT)] + merged_sp
    return master_non_sp + merged_sp


def _is_carried_tail_subparagraph(sp: IRNode) -> bool:
    """Return True for sparse carried-tail residue, not genuine lettered subitems."""
    if sp.kind != IRNodeKind.SUBPARAGRAPH:
        return False
    label_norm = _tops._norm(sp.label or "")
    return not label_norm or label_norm.isdigit()


def _apply_item_repeal(
    state: "ReplayState",
    op: "_ItemApplyView | AmendmentOp | ResolvedOp",
    sec_path: Sequence[tuple[str, str]],
    sec: IRNode,
    subsecs: List[IRNode],
    profile: ReplayProfile,
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> Optional["ReplayState"]:
    """REPEAL an item (kohta) within a subsection. Returns updated state or None if not applicable."""
    sec_path = _tops._as_path(sec_path)
    view = _coerce_item_apply_view(op)
    if view.op_type != "REPEAL" or not view.target_paragraph or not view.target_item:
        return None
    n = _resolve_item_subsection_index(subsecs, view.target_paragraph)
    item_norm = re.sub(r"[)\s.]", "", view.target_item).strip().lower()
    explicit_shift_label = str(view.post_repeal_item_shift_label or "").strip().lower() or None
    explicit_post_repeal_shift = explicit_shift_label == item_norm
    if 0 <= n < len(subsecs):
        if view.target_special != "johd":
            intro_list_blocked = False
            if view.target_paragraph == 1:
                intro_list_blocked = _report_item_intro_list_rebound(
                    source_pathologies_out=source_pathologies_out,
                    view=view,
                    subsecs=subsecs,
                    amend_sub=None,
                    strict_profile=strict_profile,
                )
            if intro_list_blocked:
                return None
            if _report_item_missing_exact_subsection_label_rebound(
                source_pathologies_out=source_pathologies_out,
                view=view,
                subsecs=subsecs,
                amend_sub=None,
                target_idx=n,
                strict_profile=strict_profile,
            ):
                return None
        sub = subsecs[n]
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        para_idx = next((i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == item_norm), None)
        if para_idx is not None:
            # Always synthesize a placeholder when the profile requests it.
            # Repeal and visibility are separate questions: the placeholder
            # retains the address and label so later amendments can resolve
            # anchors like "insert after kohta 15" even after kohta 15 is
            # repealed.  (PRO_RESPONSE_5_1 §8, bug: 2008/878 → 2025/163)
            synthesize_item_placeholder = (
                not explicit_post_repeal_shift
                and profile.synthesize_repeal_placeholders
            )
            if synthesize_item_placeholder:
                para = paras[para_idx]
                label = para.label or (view.target_item or "")
                placeholder = _relabel_paragraph_ir(
                    IRNode(
                        kind=IRNodeKind.PARAGRAPH,
                        label=label,
                        attrs={"lawvm_repeal_placeholder": "1"},
                    ),
                    label,
                )
                new_sub = _tops.replace_nth(sub, "paragraph", para_idx, placeholder)
            else:
                new_sub = _tops.remove_nth(sub, "paragraph", para_idx)
                if explicit_post_repeal_shift:
                    new_sub = _shift_lettered_item_labels_after_repeal(new_sub, item_norm)
            new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
            logger.debug("  %s → kohta repeal", ctx_label)
            return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
    return state


def _apply_item_replace(
    state: "ReplayState",
    op: "_ItemApplyView | AmendmentOp | ResolvedOp",
    sec_path: Sequence[tuple[str, str]],
    sec: IRNode,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    muutos_ir: Optional[IRNode],
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> Optional["ReplayState"]:
    """REPLACE an item (kohta) — all variants including compound and OOR."""
    sec_path = _tops._as_path(sec_path)
    view = _coerce_item_apply_view(op)
    if view.op_type != "REPLACE" or not view.target_paragraph or not view.target_item:
        return None
    # Item-level johtolause (intro) replacement: "N kohdan johtolause"
    # Replace only the intro/content of the matched paragraph while preserving
    # existing subparagraph children. Without this guard the whole paragraph
    # would be replaced, destroying subitems.
    _is_item_johd = view.target_special == "johd"
    item_johd_absent_attempted = False
    unlabelled_relabel_attempted = False
    n = _resolve_item_subsection_index(subsecs, view.target_paragraph)
    item_norm = re.sub(r"[)\s.]", "", view.target_item).strip().lower()
    if view.target_paragraph == 1 and view.target_special != "johd":
        if _report_item_intro_list_rebound(
            source_pathologies_out=source_pathologies_out,
            view=view,
            subsecs=subsecs,
            amend_sub=amend_sub,
            strict_profile=strict_profile,
        ):
            return None
    if _is_item_johd and 0 <= n < len(subsecs):
        sub = subsecs[n]
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        para_idx = next((i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == item_norm), None)
        if para_idx is not None:
            master_para = paras[para_idx]
            from lawvm.finland.apply_payload_ops import _find_amend_intro
            amend_intro = _find_amend_intro(amend_sub, muutos_ir)
            if amend_intro is None and muutos_ir is not None:
                amend_intro = _find_amend_intro(None, muutos_ir)
            if amend_intro is not None:
                replaced = False
                new_children = []
                for c in master_para.children:
                    if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT) and not replaced:
                        new_children.append(amend_intro)
                        replaced = True
                    else:
                        new_children.append(c)
                new_para = IRNode(
                    kind=master_para.kind,
                    label=master_para.label,
                    text=master_para.text,
                    attrs=dict(master_para.attrs),
                    children=tuple(new_children),
                )
                new_sub = _tops.replace_nth(sub, "paragraph", para_idx, new_para)
                new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                logger.debug("  %s → kohta johd replace (intro-only, preserving subitems)", ctx_label)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
            item_johd_absent_attempted = True
    if 0 <= n < len(subsecs):
        if view.target_special == "johd" and view.target_paragraph == 1 and _has_intro_list_moment_shape_ir(subsecs):
            if _report_item_intro_list_rebound(
                source_pathologies_out=source_pathologies_out,
                view=view,
                subsecs=subsecs,
                amend_sub=amend_sub,
                strict_profile=strict_profile,
            ):
                return None
        elif view.target_special != "johd" and _report_item_missing_exact_subsection_label_rebound(
            source_pathologies_out=source_pathologies_out,
            view=view,
            subsecs=subsecs,
            amend_sub=amend_sub,
            target_idx=n,
            strict_profile=strict_profile,
        ):
            return None
        sub = subsecs[n]
        paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
        if not paras and amend_sub is not None:
            merged_content_sub = _merge_letter_item_from_content_subsection_ir(sub, amend_sub, item_norm)
            if merged_content_sub is not None:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.source_statute,
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                            recovery_kind="content_only_row_merge",
                            live_sibling_count=_count_content_row_markers(sub),
                            payload_sibling_count=_count_content_row_markers(amend_sub),
                        )
                    )
                if strict_profile is not None:
                    return None
                new_sec = _tops.replace_nth(sec, "subsection", n, merged_content_sub)
                logger.debug("  %s → kohta replace (content-only row merge)", ctx_label)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
        para_idx = next((i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == item_norm), None)
        # When the amendment body contains multiple explicitly-labeled subsections,
        # use target_paragraph to select the correct one.  Without this, the slot
        # assignment can assign an item op for subsection N to the wrong amendment
        # subsection when two subsections both carry a paragraph with the same label
        # (e.g. both subsection 1 and subsection 2 have a "2)" paragraph).
        if amend_sub is None and muutos_ir is not None and view.target_paragraph is not None:
            _amend_subs_multi = [c for c in muutos_ir.children if c.kind == IRNodeKind.SUBSECTION]
            if len(_amend_subs_multi) > 1:
                _tp_norm = _tops._norm(str(view.target_paragraph))
                _matched = next(
                    (s for s in _amend_subs_multi if _tops._norm(s.label or "") == _tp_norm),
                    None,
                )
                if _matched is not None:
                    amend_sub = _matched
        amend_para = _find_amend_paragraph(item_norm, amend_sub, muutos_ir)
        if para_idx is not None and amend_para is not None:
            if amend_sub is not None:
                merged_para = _merge_sparse_alakohta_replace_ir(paras[para_idx], amend_sub, item_norm)
                if merged_para is not None:
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=view.source_statute,
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                                recovery_kind="sparse_alakohta_replace_merge",
                                live_sibling_count=len(
                                    [c for c in paras[para_idx].children if c.kind == IRNodeKind.SUBPARAGRAPH]
                                ),
                                payload_sibling_count=len(
                                    [
                                        c
                                        for c in amend_sub.children
                                        if c.kind == IRNodeKind.PARAGRAPH
                                        and c.label
                                        and re.fullmatch(r"[a-z]", _tops._norm(c.label))
                                    ]
                                ),
                            )
                        )
                    if strict_profile is not None:
                        return None
                    new_sub = _tops.replace_nth(sub, "paragraph", para_idx, merged_para)
                    new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                    new_sec = _prune_duplicate_tail_subsection_after_sparse_item_merge(
                        new_sec,
                        n,
                        merged_para,
                        source_statute=view.source_statute,
                        source_pathologies_out=source_pathologies_out,
                    )
                    logger.debug("  %s → sparse alakohta replace", ctx_label)
                    return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
                sanitized_para = _sanitize_shared_tail_item_replace_paragraph_ir(
                    sub, paras[para_idx], amend_para, para_idx=para_idx
                )
                if sanitized_para is not None:
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=view.source_statute,
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                                recovery_kind="shared_tail_item_replace_sanitize",
                                live_sibling_count=len([c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]),
                                payload_sibling_count=len(
                                    [
                                        c
                                        for c in amend_para.children
                                        if c.kind in (
                                            IRNodeKind.SUBPARAGRAPH,
                                            IRNodeKind.CONTENT,
                                            IRNodeKind.INTRO,
                                        )
                                    ]
                                ),
                            )
                        )
                    if strict_profile is not None:
                        return None
                    new_sub = _tops.replace_nth(sub, "paragraph", para_idx, sanitized_para)
                    new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                    logger.debug("  %s → kohta replace (shared-tail sanitize)", ctx_label)
                    return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
            # Provenance: 2006/603 section:2 — amendment 2014/1186 REPLACE(4)+INSERT(4a) duplication
            # Strip compound-label subparagraphs (digit+letter, e.g. "4a") from the
            # replacement payload. The Finlex AKN body XML nests <subparagraph num="4 a)">
            # inside <paragraph num="4)"> when a sibling INSERT op introduces item:4a.
            # Those subparagraphs will be materialized by the INSERT op as flat paragraph
            # siblings, so including them here would produce a duplicate.
            # Pure letter subparagraphs (a, b, c …) are legitimate sub-enumerations and
            # must NOT be stripped.
            _compound_sp_re = re.compile(r"^\d+[a-z]+$")
            _has_compound_sp = any(
                c.kind == IRNodeKind.SUBPARAGRAPH
                and c.label
                and _compound_sp_re.match(_tops._norm(c.label))
                for c in amend_para.children
            )
            if _has_compound_sp:
                orig_children = amend_para.children
                stripped_children = tuple(
                    c for c in orig_children
                    if not (
                        c.kind == IRNodeKind.SUBPARAGRAPH
                        and c.label
                        and _compound_sp_re.match(_tops._norm(c.label))
                    )
                )
                amend_para = IRNode(
                    kind=amend_para.kind,
                    label=amend_para.label,
                    text=amend_para.text,
                    attrs=dict(amend_para.attrs),
                    children=stripped_children,
                )
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.source_statute,
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                            recovery_kind="compound_label_subparagraph_strip",
                            live_sibling_count=len(
                                [c for c in orig_children if c.kind == IRNodeKind.SUBPARAGRAPH]
                            ),
                            payload_sibling_count=len(
                                [
                                    c
                                    for c in orig_children
                                    if c.kind == IRNodeKind.SUBPARAGRAPH
                                    and c.label
                                    and _compound_sp_re.match(_tops._norm(c.label))
                                ]
                            ),
                        )
                    )
                if strict_profile is not None:
                    return None
                logger.debug(
                    "  %s → stripped %d compound-label subparagraph(s) from REPLACE payload",
                    ctx_label,
                    len(orig_children) - len(stripped_children),
                )
            # Safety net: when the master paragraph has subparagraph children
            # but the replacement does not, preserve them only for true
            # intro-only payloads. A normal content replace must be allowed to
            # delete obsolete subparagraphs (e.g. 1987/990 §2 / 2008/342 item 4).
            master_sps = [c for c in paras[para_idx].children if c.kind == IRNodeKind.SUBPARAGRAPH]
            amend_sps = [c for c in amend_para.children if c.kind == IRNodeKind.SUBPARAGRAPH]
            amend_has_intro = any(c.kind == IRNodeKind.INTRO for c in amend_para.children)
            amend_has_content = any(c.kind == IRNodeKind.CONTENT for c in amend_para.children)
            amend_has_omission = amend_sub is not None and any(c.kind == IRNodeKind.OMISSION for c in amend_sub.children)
            preserve_master_sps = False
            if master_sps and not amend_sps:
                if not amend_has_content and amend_has_intro:
                    preserve_master_sps = True
                elif amend_has_omission and len(master_sps) == 1 and _is_carried_tail_subparagraph(master_sps[0]):
                    preserve_master_sps = True
            if preserve_master_sps:
                amend_para = IRNode(
                    kind=amend_para.kind,
                    label=amend_para.label,
                    text=amend_para.text,
                    attrs=dict(amend_para.attrs),
                    children=tuple(amend_para.children) + tuple(master_sps),
                )
                logger.debug(
                    "  %s → preserved %d master subparagraph(s) during bounded omission-aware item replace",
                    ctx_label,
                    len(master_sps),
                )
            if item_johd_absent_attempted and source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_item_target_structure_absent_pathology(
                        source_statute=view.source_statute,
                        target_section=view.target_section,
                        target_paragraph=str(view.target_paragraph or ""),
                        target_item=str(view.target_item or ""),
                        live_has_paragraphs=any(
                            any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                        ),
                        amend_has_paragraphs=bool(
                            amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                        ),
                    )
                )
            new_sub = _tops.replace_nth(sub, "paragraph", para_idx, amend_para)
            new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
            logger.debug("  %s → kohta replace", ctx_label)
            return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
        if para_idx is None and amend_para is not None and not paras and amend_sub is not None:
            merged_text_sub = _merge_letter_item_into_content_only_subsection_ir(sub, amend_para, item_norm)
            if merged_text_sub is not None:
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.source_statute,
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                            recovery_kind="content_only_letter_row_merge",
                            live_sibling_count=_count_content_row_markers(sub),
                            payload_sibling_count=1,
                        )
                    )
                if strict_profile is not None:
                    return None
                new_sec = _tops.replace_nth(sec, "subsection", n, merged_text_sub)
                logger.debug("  %s → kohta replace (content-only letter-row merge)", ctx_label)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_item_target_structure_absent_pathology(
                        source_statute=view.source_statute,
                        target_section=view.target_section,
                        target_paragraph=str(view.target_paragraph),
                        target_item=str(view.target_item or ""),
                        live_has_paragraphs=False,
                        amend_has_paragraphs=any(c.kind == IRNodeKind.PARAGRAPH for c in amend_sub.children),
                    )
                )
        if (
            para_idx is None
            and amend_para is not None
            and paras
            and re.fullmatch(r"\d+", item_norm)
            and sub.label == str(view.target_paragraph)
        ):
            prev_tok = _previous_item_token(item_norm)
            anchor_idx = None
            if prev_tok is not None:
                anchor_idx = next(
                    (i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == prev_tok), None
                )
            if anchor_idx is not None or item_norm == "1":
                new_sub = _insert_item_with_suffix_renumber_ir(
                    sub,
                    amend_para,
                    item_norm,
                    anchor_idx,
                    source_statute=view.source_statute,
                    source_pathologies_out=source_pathologies_out,
                )
                new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                logger.debug("  %s → kohta replace-as-insert (exact subsection numeric recovery)", ctx_label)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
        if para_idx is not None and amend_para is None and amend_sub is not None:
            unlabelled_relabel_attempted = True
            amend_unlabelled_paras = [c for c in amend_sub.children if c.kind == IRNodeKind.PARAGRAPH and not c.label]
            amend_labelled_paras = [c for c in amend_sub.children if c.kind == IRNodeKind.PARAGRAPH and c.label]
            if len(amend_unlabelled_paras) == 1 and not amend_labelled_paras:
                unlabelled_para = amend_unlabelled_paras[0]
                relabelled = IRNode(
                    kind=unlabelled_para.kind,
                    label=item_norm,
                    text=unlabelled_para.text,
                    attrs=dict(unlabelled_para.attrs),
                    children=tuple(unlabelled_para.children),
                )
                new_sub = _tops.replace_nth(sub, "paragraph", para_idx, relabelled)
                new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                logger.debug("  %s → kohta replace (unlabelled amend para)", ctx_label)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
    item_norm_cmpd = re.sub(r"[)\s.]", "", view.target_item).strip().lower()
    m_cmpd = re.match(r"^(\d+)([a-z])$", item_norm_cmpd)
    if m_cmpd:
        para_digit = m_cmpd.group(1)
        n = view.target_paragraph - 1
        if 0 <= n < len(subsecs):
            sub = subsecs[n]
            paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
            master_para_idx = next(
                (i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == para_digit),
                None,
            )
            amend_para_src = _find_amend_paragraph(para_digit, amend_sub, muutos_ir)
            _amend_has_sp = amend_para_src is not None and any(
                c.kind in (IRNodeKind.SUBPARAGRAPH, IRNodeKind.OMISSION)
                for c in amend_para_src.children
            )
            if master_para_idx is not None and _amend_has_sp and amend_para_src is not None:
                master_para = paras[master_para_idx]
                new_para_children = _build_compound_item_omission_children(master_para, amend_para_src)
                replacement = IRNode(
                    kind=master_para.kind,
                    label=master_para.label,
                    text=master_para.text,
                    attrs=dict(master_para.attrs),
                    children=tuple(new_para_children),
                )
                new_sub = _tops.replace_nth(sub, "paragraph", master_para_idx, replacement)
                new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                logger.debug("  %s → compound kohta replace (%s→subpara)", ctx_label, para_digit)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))

    if amend_sub is not None:
        n = view.target_paragraph - 1
        if n >= len(subsecs):
            item_norm_oor = re.sub(r"[)\s.]", "", view.target_item).strip().lower()
            amend_para_oor = _find_amend_paragraph(item_norm_oor, amend_sub, muutos_ir)
            if amend_para_oor is not None and source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.source_statute,
                        target_unit_kind=view.target_unit_kind,
                        target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                        recovery_kind="blocked_oor_subsection_append",
                        live_sibling_count=len(subsecs),
                        payload_sibling_count=1,
                    )
                )
    if source_pathologies_out is not None:
        live_has_structure = any(_subsection_exposes_targetable_item_structure(sub, item_norm) for sub in subsecs)
        amend_has_structure = _subsection_exposes_targetable_item_structure(amend_sub, item_norm)
        if not live_has_structure and not amend_has_structure:
            source_pathologies_out.append(
                build_item_target_structure_absent_pathology(
                    source_statute=view.source_statute,
                    target_section=view.target_section,
                    target_paragraph=str(view.target_paragraph or ""),
                    target_item=str(view.target_item or ""),
                    live_has_paragraphs=any(any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs),
                    amend_has_paragraphs=bool(
                        amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                    ),
                )
            )
    if (item_johd_absent_attempted or unlabelled_relabel_attempted) and source_pathologies_out is not None:
        source_pathologies_out.append(
            build_item_target_structure_absent_pathology(
                source_statute=view.source_statute,
                target_section=view.target_section,
                target_paragraph=str(view.target_paragraph or ""),
                target_item=str(view.target_item or ""),
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
    return None


def _apply_item_insert(
    state: "ReplayState",
    op: "_ItemApplyView | AmendmentOp | ResolvedOp",
    sec_path: Sequence[tuple[str, str]],
    sec: IRNode,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    muutos_ir: Optional[IRNode],
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> Optional["ReplayState"]:
    """INSERT an item (kohta) — standard and compound variants."""
    sec_path = _tops._as_path(sec_path)
    view = _coerce_item_apply_view(op)
    if view.op_type != "INSERT" or not view.target_paragraph or not view.target_item:
        return None
    n = _resolve_item_subsection_index(subsecs, view.target_paragraph)
    item_norm = re.sub(r"[)\s.]", "", view.target_item).strip().lower()
    amend_para = _find_amend_paragraph(item_norm, amend_sub, muutos_ir)
    numeric_anchor_missing = False
    if amend_para is not None:
        if view.target_special != "johd":
            intro_list_blocked = False
            if view.target_paragraph == 1:
                intro_list_blocked = _report_item_intro_list_rebound(
                    source_pathologies_out=source_pathologies_out,
                    view=view,
                    subsecs=subsecs,
                    amend_sub=amend_sub,
                    strict_profile=strict_profile,
                )
            if intro_list_blocked:
                return None
            if _report_item_missing_exact_subsection_label_rebound(
                source_pathologies_out=source_pathologies_out,
                view=view,
                subsecs=subsecs,
                amend_sub=amend_sub,
                target_idx=n,
                strict_profile=strict_profile,
            ):
                return None
        compound_slot_collision_label: Optional[str] = None
        compound_slot_collision_reported = False
        candidates = []
        if 0 <= n < len(subsecs):
            candidates.append((n, subsecs[n]))
        for alt_i, alt_sub in enumerate(subsecs):
            if alt_i != n and any(c.kind == IRNodeKind.PARAGRAPH for c in alt_sub.children):
                candidates.append((alt_i, alt_sub))
        for sub_i, sub in candidates:
            paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
            existing_idx = next(
                (i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == item_norm),
                None,
            )
            if existing_idx is not None and paras[existing_idx].attrs.get("lawvm_repeal_placeholder") == "1":
                # INSERT targets a slot that was previously repealed and now
                # has only a placeholder.  The placeholder must be replaced
                # (not inserted alongside) so the label stays in place and
                # subsequent items keep their numbers.  Inserting *after* the
                # predecessor anchor would push the placeholder one slot
                # forward and trigger a cascade renumber.
                # (PRO_RESPONSE_5_1 §8 follow-up; bug: 2008/878 insert 16 after
                # repeal of 16 cascades 17→18, 18→19 … shifting all later kohdas)
                relabelled = _relabel_paragraph_ir(amend_para, item_norm)
                new_sub = _tops.replace_nth(sub, "paragraph", existing_idx, relabelled)
                new_sec = _tops.replace_nth(sec, "subsection", sub_i, new_sub)
                logger.debug("  %s → kohta insert-over-repeal-placeholder (sub %s)", ctx_label, sub_i + 1)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
            if existing_idx is not None and amend_sub is not None:
                merged_para = _merge_sparse_alakohta_insert_ir(paras[existing_idx], amend_sub, item_norm)
                if merged_para is not None:
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=view.source_statute,
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                                recovery_kind="sparse_alakohta_insert_merge",
                                live_sibling_count=len(
                                    [c for c in paras[existing_idx].children if c.kind == IRNodeKind.SUBPARAGRAPH]
                                ),
                                payload_sibling_count=len(
                                    [
                                        c
                                        for c in amend_sub.children
                                        if c.kind == IRNodeKind.PARAGRAPH
                                        and c.label
                                        and re.fullmatch(r"[a-z]", _tops._norm(c.label))
                                    ]
                                ),
                            )
                        )
                    if strict_profile is not None:
                        return None
                    new_sub = _tops.replace_nth(sub, "paragraph", existing_idx, merged_para)
                    new_sec = _tops.replace_nth(sec, "subsection", sub_i, new_sub)
                    new_sec = _prune_duplicate_tail_subsection_after_sparse_item_merge(
                        new_sec,
                        sub_i,
                        merged_para,
                        source_statute=view.source_statute,
                        source_pathologies_out=source_pathologies_out,
                    )
                    logger.debug("  %s → sparse alakohta insert (sub %s)", ctx_label, sub_i + 1)
                    return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
            if existing_idx is not None and re.fullmatch(r"\d+[a-z]", item_norm):
                compound_slot_collision_label = paras[existing_idx].label or item_norm
                if source_pathologies_out is not None and not compound_slot_collision_reported:
                    source_pathologies_out.append(
                        build_item_target_slot_occupied_pathology(
                            source_statute=view.source_statute,
                            target_section=view.target_section,
                            target_paragraph=str(view.target_paragraph or ""),
                            target_item=str(view.target_item or ""),
                            occupied_item_label=compound_slot_collision_label,
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
                    compound_slot_collision_reported = True
                continue
            prev_tok = _previous_item_token(item_norm)
            anchor_idx = None
            if prev_tok is not None:
                anchor_idx = next(
                    (i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == prev_tok), None
                )
            if anchor_idx is not None or sub_i == n:
                pathology_count_before = len(source_pathologies_out) if source_pathologies_out is not None else 0
                new_sub = _insert_item_with_suffix_renumber_ir(
                    sub,
                    amend_para,
                    item_norm,
                    anchor_idx,
                    source_statute=view.source_statute,
                    source_pathologies_out=source_pathologies_out,
                )
                if strict_profile is not None and source_pathologies_out is not None:
                    new_pathologies = source_pathologies_out[pathology_count_before:]
                    if any(
                        pathology.code == "DESTRUCTIVE_SHAPE_LOSS_RISK"
                        and pathology.detail.get("recovery_kind") == "item_insert_suffix_renumber"
                        for pathology in new_pathologies
                    ):
                        return None
                merged_para = next(
                    (
                        child
                        for child in new_sub.children
                        if child.kind == IRNodeKind.PARAGRAPH and child.label and _tops._norm(child.label) == item_norm
                    ),
                    None,
                )
                new_sec = _tops.replace_nth(sec, "subsection", sub_i, new_sub)
                collapsed_sec = (
                    _collapse_absorbed_tail_subsection_ir(
                        new_sec,
                        sub_i,
                        merged_para,
                        source_statute=view.source_statute,
                        source_pathologies_out=source_pathologies_out,
                    )
                    if merged_para is not None
                    else None
                )
                logger.debug("  %s → kohta insert (sub %s)", ctx_label, sub_i + 1)
                final_sec = collapsed_sec if collapsed_sec is not None else new_sec
                new_ir = _tops.replace_at(state.ir, sec_path, final_sec)
                if collapsed_sec is not None:
                    return state.with_ir(new_ir)
                return _with_preserved_provision_index(state, new_ir)
            if prev_tok is not None and anchor_idx is None and item_norm != "1":
                numeric_anchor_missing = True

    item_norm_ci = re.sub(r"[)\s.]", "", view.target_item).strip().lower()
    m_ci = re.match(r"^(\d+)([a-z])$", item_norm_ci)
    compound_recovery_attempted = False
    compound_slot_collision_label: Optional[str] = None
    compound_slot_collision_reported = False
    if m_ci:
        para_digit_ci = m_ci.group(1)
        sub_letter_ci = m_ci.group(2)
        n = _resolve_item_subsection_index(subsecs, view.target_paragraph)
        if 0 <= n < len(subsecs):
            compound_recovery_attempted = True
            sub = subsecs[n]
            paras = [c for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
            master_para_idx_ci = next(
                (i for i, p in enumerate(paras) if p.label and _tops._norm(p.label) == para_digit_ci),
                None,
            )
            amend_para_ci = _find_amend_paragraph(para_digit_ci, amend_sub, muutos_ir)
            if master_para_idx_ci is not None and amend_para_ci is not None:
                master_para_ci = paras[master_para_idx_ci]
                new_sp = next(
                    (c for c in amend_para_ci.children if c.kind == IRNodeKind.SUBPARAGRAPH and c.label == sub_letter_ci),
                    None,
                )
                if new_sp is not None:
                    existing_sps = [c for c in master_para_ci.children if c.kind == IRNodeKind.SUBPARAGRAPH]
                    prev_letter = _previous_item_token(sub_letter_ci)
                    anchor_sp_idx = None
                    if prev_letter is not None:
                        anchor_sp_idx = next((i for i, sp in enumerate(existing_sps) if sp.label == prev_letter), None)
                    if anchor_sp_idx is not None:
                        new_sp_children: List[IRNode] = []
                        for i, c in enumerate(master_para_ci.children):
                            new_sp_children.append(c)
                            if c.kind == IRNodeKind.SUBPARAGRAPH and c is existing_sps[anchor_sp_idx]:
                                new_sp_children.append(new_sp)
                    else:
                        new_sp_children = list(master_para_ci.children) + [new_sp]
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=view.source_statute,
                                target_unit_kind="section",
                                target_label=f"{view.target_section} § {view.target_paragraph} mom {view.target_item} kohta",
                                recovery_kind="compound_item_insert_append",
                                live_sibling_count=len(existing_sps),
                                payload_sibling_count=1,
                            )
                        )
                    merged_para_ci = IRNode(
                        kind=master_para_ci.kind,
                        label=master_para_ci.label,
                        text=master_para_ci.text,
                        attrs=dict(master_para_ci.attrs),
                        children=tuple(new_sp_children),
                    )
                    if strict_profile is not None:
                        return None
                    new_sub = _tops.replace_nth(sub, "paragraph", master_para_idx_ci, merged_para_ci)
                    new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                    logger.debug("  %s → compound kohta insert (%s→%s)", ctx_label, para_digit_ci, sub_letter_ci)
                    return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
    if compound_recovery_attempted and source_pathologies_out is not None and compound_slot_collision_label is None:
        source_pathologies_out.append(
            build_item_target_structure_absent_pathology(
                source_statute=view.source_statute,
                target_section=view.target_section,
                target_paragraph=str(view.target_paragraph or ""),
                target_item=str(view.target_item or ""),
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
    if compound_slot_collision_label is not None and source_pathologies_out is not None and not compound_slot_collision_reported:
        source_pathologies_out.append(
            build_item_target_slot_occupied_pathology(
                source_statute=view.source_statute,
                target_section=view.target_section,
                target_paragraph=str(view.target_paragraph or ""),
                target_item=str(view.target_item or ""),
                occupied_item_label=compound_slot_collision_label,
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
    if numeric_anchor_missing and source_pathologies_out is not None:
        source_pathologies_out.append(
            build_item_target_anchor_absent_pathology(
                source_statute=view.source_statute,
                target_section=view.target_section,
                target_paragraph=str(view.target_paragraph or ""),
                target_item=str(view.target_item or ""),
                live_label="",
                live_has_paragraphs=any(
                    any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                ),
                amend_has_paragraphs=bool(
                    amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                ),
            )
        )
    return None


def _apply_special_targets(
    state: "ReplayState",
    op: "_ItemApplyView | AmendmentOp | ResolvedOp",
    sec_path: Sequence[tuple[str, str]],
    sec: IRNode,
    subsecs: List[IRNode],
    amend_sub: Optional[IRNode],
    muutos_ir: Optional[IRNode],
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> Optional["ReplayState"]:
    """Apply heading, intro, and structural fallback operations."""
    sec_path = _tops._as_path(sec_path)
    view = _coerce_item_apply_view(op)
    if view.op_type in ("REPLACE", "INSERT") and view.target_special == "otsikko" and muutos_ir is not None:
        amend_heading = next((c for c in muutos_ir.children if c.kind == IRNodeKind.HEADING), None)
        if amend_heading is not None:
            has_existing_heading = any(c.kind == IRNodeKind.HEADING for c in sec.children)
            if has_existing_heading:
                # Replace existing heading in place
                new_children = [amend_heading if c.kind == IRNodeKind.HEADING else c for c in sec.children]
            else:
                # Insert heading before the first subsection/content child
                new_children = [amend_heading] + list(sec.children)
            logger.debug("  %s → otsikko %s", ctx_label, view.op_type.lower())
            return _with_preserved_provision_index(
                state, _tops.replace_at(state.ir, sec_path, _tops._with_children(sec, new_children))
            )
    if view.op_type == "REPEAL" and view.target_special == "otsikko":
        if any(c.kind == IRNodeKind.HEADING for c in sec.children):
            new_children = [c for c in sec.children if c.kind != IRNodeKind.HEADING]
            logger.debug("  %s → otsikko repeal", ctx_label)
            return _with_preserved_provision_index(
                state, _tops.replace_at(state.ir, sec_path, _tops._with_children(sec, new_children))
            )
        logger.debug("  %s → otsikko repeal noop (no heading)", ctx_label)
        return state

    if view.op_type == "REPLACE" and view.target_special == "johd" and muutos_ir is not None:
        target_label = str(view.target_paragraph or "")
        exact_live_idx = next(
            (
                idx
                for idx, sub in enumerate(subsecs)
                if sub.label and _norm_num_token(sub.label) == target_label
            ),
            None,
        )
        n = exact_live_idx if exact_live_idx is not None else _resolve_item_subsection_index(subsecs, view.target_paragraph or 1)
        if 0 <= n < len(subsecs):
            sub = subsecs[n]
            amend_intro = _find_amend_intro(amend_sub, muutos_ir)
            if amend_intro is not None:
                if (
                    exact_live_idx is None
                    and view.target_paragraph is not None
                    and view.target_paragraph == 1
                    and _has_intro_list_moment_shape_ir(subsecs)
                ):
                    blocked = _report_item_intro_list_rebound(
                        source_pathologies_out=source_pathologies_out,
                        view=view,
                        subsecs=subsecs,
                        amend_sub=amend_sub,
                        strict_profile=strict_profile,
                    )
                    if blocked:
                        return None
                elif exact_live_idx is None:
                    _report_item_johd_subsection_rebound(
                        source_pathologies_out=source_pathologies_out,
                        view=view,
                        subsecs=subsecs,
                        amend_sub=amend_sub,
                        target_idx=n,
                    )
                if not any(c.kind in {IRNodeKind.INTRO, IRNodeKind.CONTENT} for c in sub.children):
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_item_target_structure_absent_pathology(
                                source_statute=view.source_statute,
                                target_section=view.target_section,
                                target_paragraph=str(view.target_paragraph or ""),
                                target_item=str(view.target_item or ""),
                                live_has_paragraphs=any(
                                    any(child.kind == IRNodeKind.PARAGRAPH for child in current.children)
                                    for current in subsecs
                                ),
                                amend_has_paragraphs=bool(
                                    amend_sub is not None
                                    and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                                ),
                            )
                        )
                    return None
                replaced = False
                new_children = []
                for c in sub.children:
                    if c.kind in {IRNodeKind.INTRO, IRNodeKind.CONTENT} and not replaced:
                        new_children.append(amend_intro)
                        replaced = True
                    else:
                        new_children.append(c)
                new_sub = _tops._with_children(sub, new_children)
                new_sec = _tops.replace_nth(sec, "subsection", n, new_sub)
                logger.debug("  %s → johd replace", ctx_label)
                return _with_preserved_provision_index(state, _tops.replace_at(state.ir, sec_path, new_sec))
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_item_target_structure_absent_pathology(
                        source_statute=view.source_statute,
                        target_section=view.target_section,
                        target_paragraph=str(view.target_paragraph or ""),
                        target_item=str(view.target_item or ""),
                        live_has_paragraphs=any(
                            any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in subsecs
                        ),
                        amend_has_paragraphs=bool(
                            amend_sub is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub.children)
                        ),
                    )
                )
    return None
