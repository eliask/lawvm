"""UK replay text action branch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec
from lawvm.core.semantic_types import FacetKind, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind
from lawvm.uk_legislation.heading_facets import (
    _CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE,
    _CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE,
    _heading_facet_carrier_for_target,
)
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.provenance_notes import (
    NOTE_TEXT_REWRITE_RULE as _NOTE_TEXT_REWRITE_RULE,
    _table_cell_selector,
)
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_action_target_detail,
    uk_replay_blocking_action_target_detail,
)
from lawvm.uk_legislation.replay_recovery_observations import uk_replay_recovery_observation
from lawvm.uk_legislation.replay_table_geometry import (
    resolve_uk_table_entry_inline_cell,
    resolve_unique_uk_table_entry_cells,
)
from lawvm.uk_legislation.replay_target_diagnostics import (
    _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.replay_target_gaps import (
    uk_table_target_shape_gap,
)
from lawvm.uk_legislation.replay_text import (
    _normalized_replay_subtree_text,
    _replay_subtree_text_preview,
)
from lawvm.uk_legislation.replay_text_miss import classify_uk_replay_text_miss
from lawvm.uk_legislation.source_table_entry_paragraph import (
    SOURCE_TABLE_CELL_PARAGRAPH_SENTINEL_RE as _TABLE_CELL_PARAGRAPH_SENTINEL_RE,
)
from lawvm.uk_legislation.text_matching import (
    _node_text_patch_preimage_present,
    _rotated_trailing_comma_omission_match,
    _text_match_has_word_punctuation_elision_candidate,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    _fragment_substitution,
)


_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID = "uk_replay_table_entry_inline_text_insertion_unresolved"
_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID = "uk_replay_table_entry_inline_text_preimage_gap"
def _text_patch_detail(
    op: LegalOperation,
    target: LegalAddress,
    text_patch: TextPatchSpec,
    replacement: str,
    *,
    blocking: bool,
    strict_disposition: str | None = None,
    quirks_disposition: str = "record",
    **extra: Any,
) -> dict[str, Any]:
    detail = uk_replay_action_target_detail(
        op,
        target,
        blocking=blocking,
        text_match=text_patch.selector.match_text,
        replacement_text=replacement,
        **extra,
    )
    if strict_disposition is not None:
        detail["strict_disposition"] = strict_disposition
    detail["quirks_disposition"] = quirks_disposition
    return detail


class UKReplayTextActionApplyMixin:
    adjudications_out: list[CompileAdjudication]
    _applied_text_patch_targets: dict[str, list[str]]

    if TYPE_CHECKING:

        def _log(self, message: str) -> None: ...

        def _record_invariant_violations(self, op: LegalOperation) -> None: ...

        def _emit_top_section_snapshot(self, op: LegalOperation) -> None: ...

        def _apply_text_replace_on_node_text_only(
            self,
            node: UKMutableNode,
            match: str,
            replacement: str,
            occurrence: int,
            end_occurrence: int = 0,
            *,
            allow_punctuation_spacing: bool = False,
            allow_word_punctuation_elision: bool = False,
            recovery_rule_ids_out: Optional[list[str]] = None,
        ) -> tuple[UKMutableNode, bool]: ...

        def _apply_text_replace_on_subtree(
            self,
            node: UKMutableNode,
            match: str,
            replacement: str,
            occurrence: int,
            end_occurrence: int = 0,
            *,
            allow_punctuation_spacing: bool = False,
            allow_word_punctuation_elision: bool = False,
            recovery_rule_ids_out: Optional[list[str]] = None,
        ) -> tuple[UKMutableNode, bool]: ...

        def _apply_text_append_on_node_text_only(
            self,
            node: UKMutableNode,
            replacement: str,
        ) -> tuple[UKMutableNode, bool]: ...

        def _apply_text_append_on_subtree_text_end(
            self,
            node: UKMutableNode,
            replacement: str,
        ) -> tuple[UKMutableNode, bool]: ...

        def _apply_source_carried_table_cell_paragraph_substitution(
            self,
            cell: UKMutableNode,
            match_text: str,
            replacement: str,
        ) -> Any: ...

        def _apply_numeric_list_trailing_comma_anchor_on_node_text_only(
            self,
            node: UKMutableNode,
            match: str,
            replacement: str,
            occurrence: int,
            end_occurrence: int,
        ) -> Any: ...

        def _apply_numeric_list_trailing_comma_anchor_on_subtree(
            self,
            node: UKMutableNode,
            match: str,
            replacement: str,
            occurrence: int,
            end_occurrence: int = 0,
        ) -> Any: ...

        def _recover_text_patch_on_implicit_first_subparagraph_parent_text(
            self,
            op: LegalOperation,
            target: LegalAddress,
            text_patch: TextPatchSpec,
            replacement: str,
        ) -> bool: ...

        def _recover_source_carried_labeled_child_text_substitution(
            self,
            op: LegalOperation,
            target: LegalAddress,
            node: UKMutableNode,
            text_patch: TextPatchSpec,
            replacement: str,
        ) -> bool: ...

        def _recover_text_patch_on_empty_descendant_parent(
            self,
            op: LegalOperation,
            target: LegalAddress,
            text_patch: TextPatchSpec,
            replacement: str,
        ) -> bool: ...

        def _recover_text_patch_on_direct_section_paragraph_child_text(
            self,
            op: LegalOperation,
            target: LegalAddress,
            text_patch: TextPatchSpec,
            replacement: str,
        ) -> bool: ...

        def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool: ...

        def _target_under_repealed_prefix(self, target: LegalAddress) -> bool: ...

        def _doubled_alpha_gap(self, target: LegalAddress) -> bool: ...

        def _missing_sibling_range_gap(self, target: LegalAddress) -> bool: ...

        def _annex_schedule_mismatch_gap(self, op: LegalOperation) -> bool: ...

        def _container_text_target_gap(self, op: LegalOperation) -> bool: ...

        def _subsection_alpha_text_target_gap(self, op: LegalOperation) -> bool: ...

        def _malformed_target_gap(self, target: LegalAddress) -> bool: ...

        def _malformed_target_gap_kind(self, target: LegalAddress) -> str: ...

        def _missing_schedule_branch_gap(self, target: LegalAddress) -> bool: ...

        def _missing_parent_shape_gap(self, target: LegalAddress) -> bool: ...

        def _missing_parent_shape_gap_kind(self, target: LegalAddress) -> str: ...

        def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool: ...

        def _schedule_paragraph_carrier_gap_kind(self, target: LegalAddress) -> str: ...

        def _direct_section_paragraph_carrier_gap(self, target: LegalAddress) -> bool: ...

        def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool: ...

        def _missing_sectionlike_gap(self, target: LegalAddress) -> bool: ...

        def _missing_schedule_root_gap(self, target: LegalAddress) -> bool: ...

        def _prior_same_target_gap_kind(self, target: LegalAddress) -> str | None: ...

    def _apply_text_action_op(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode | None,
        parent: UKMutableNode | None,
    ) -> None:
        text_patch = op.text_patch
        if text_patch is None:
            self._log(
                f"  EXECUTOR: WARN text_replace/text_repeal op has no structured text patch — skipping {op.op_id}"
            )
            _append_uk_replay_adjudication(
                self.adjudications_out,
                kind="uk_replay_text_patch_missing_structured_payload",
                message=(
                    "UK replay skipped text-based op: the operation has no "
                    "structured text_patch payload."
                ),
                op=op,
                detail=uk_replay_blocking_action_target_detail(
                    op,
                    target,
                    family="unsupported_or_unresolved_action",
                    reason_code="missing_structured_text_patch",
                ),
            )
            return
        replacement = (
            text_patch.replacement
            if text_patch.kind in {TextPatchKindEnum.REPLACE, TextPatchKindEnum.APPEND}
            and text_patch.replacement is not None
            else ""
        )
        if node:
            recovery_rule_ids: list[str] = []
            allow_crossheading_parent = any(
                str(note)
                in {
                    f"{_NOTE_TEXT_REWRITE_RULE}{_CROSSHEADING_BEFORE_ANCHOR_REPLACEMENT_RULE}",
                    f"{_NOTE_TEXT_REWRITE_RULE}{_CROSSHEADING_BEFORE_ANCHOR_TEXT_PATCH_RULE}",
                }
                for note in (op.provenance_tags or ())
            )
            heading_carrier = _heading_facet_carrier_for_target(
                target,
                node,
                parent,
                allow_crossheading_parent=allow_crossheading_parent,
            )
            if target.special is FacetKind.HEADING and heading_carrier is None:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_heading_facet_target_gap",
                    message=(
                        "UK replay skipped heading-facet text op: target "
                        "has no unique replay heading carrier."
                    ),
                    op=op,
                    detail=_text_patch_detail(
                        op,
                        target,
                        text_patch,
                        replacement,
                        blocking=True,
                    ),
                )
                return
            if (
                target.special is None
                and self._recover_text_patch_on_implicit_first_subparagraph_parent_text(
                    op,
                    target,
                    text_patch,
                    replacement,
                )
            ):
                target_key = str(target)
                if target_key:
                    self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
                return
            table_cell_selector = _table_cell_selector(op)
            if table_cell_selector is not None:
                if str(table_cell_selector.get("selector_mode") or "") == "unique_entry_cells":
                    table_cells_result = resolve_unique_uk_table_entry_cells(
                        node,
                        table_cell_selector,
                    )
                    if not table_cells_result.cells:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                            message=(
                                "UK replay skipped multi-entry table text op: the "
                                "source-owned table cell selector did not resolve."
                            ),
                            op=op,
                            detail=_text_patch_detail(
                                op,
                                target,
                                text_patch,
                                replacement,
                                blocking=True,
                                selector=dict(table_cell_selector),
                                reason_code=table_cells_result.reason_code,
                                **table_cells_result.detail,
                                family="source_table_elaboration",
                            ),
                        )
                        return
                    if text_patch.kind not in {TextPatchKindEnum.REPLACE, TextPatchKindEnum.DELETE}:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                            message="UK replay skipped multi-entry table text op: unsupported text-patch kind.",
                            op=op,
                            detail=_text_patch_detail(
                                op,
                                target,
                                text_patch,
                                replacement,
                                blocking=True,
                                selector=dict(table_cell_selector),
                                reason_code="unsupported_multi_cell_text_patch_kind",
                                **table_cells_result.detail,
                                family="source_table_elaboration",
                            ),
                        )
                        return
                    preimage_gaps = [
                        str(cell.text or "")[:240]
                        for cell in table_cells_result.cells
                        if not _node_text_patch_preimage_present(
                            cell,
                            text_patch.selector.match_text,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                    ]
                    if preimage_gaps:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID,
                            message=(
                                "UK replay skipped multi-entry table text op: at least one "
                                "selected table cell lacked the source text preimage."
                            ),
                            op=op,
                            detail=_text_patch_detail(
                                op,
                                target,
                                text_patch,
                                replacement,
                                blocking=True,
                                selector=dict(table_cell_selector),
                                reason_code="multi_cell_text_preimage_gap",
                                preimage_gap_cells=tuple(preimage_gaps),
                                **table_cells_result.detail,
                                family="source_table_elaboration",
                            ),
                        )
                        return
                    for table_cell in table_cells_result.cells:
                        _new_cell, applied = self._apply_text_replace_on_node_text_only(
                            table_cell,
                            text_patch.selector.match_text,
                            replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                        if not applied:
                            _append_uk_replay_adjudication(
                                self.adjudications_out,
                                kind=_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID,
                                message=(
                                    "UK replay skipped multi-entry table text op: "
                                    "preflight passed but apply failed."
                                ),
                                op=op,
                                detail=_text_patch_detail(
                                    op,
                                    target,
                                    text_patch,
                                    replacement,
                                    blocking=True,
                                    selector=dict(table_cell_selector),
                                    reason_code="multi_cell_text_apply_gap",
                                    **table_cells_result.detail,
                                    family="source_table_elaboration",
                                ),
                            )
                            return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_table_entry_multi_cell_text_patch_resolved",
                        message="UK replay applied a source-owned text patch to multiple table cells.",
                        op=op,
                        detail=_text_patch_detail(
                            op,
                            target,
                            text_patch,
                            replacement,
                            blocking=False,
                            quirks_disposition="apply",
                            selector=dict(table_cell_selector),
                            **table_cells_result.detail,
                            family="source_table_elaboration",
                        ),
                    )
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                    return
                table_cell_result = resolve_uk_table_entry_inline_cell(
                    node,
                    table_cell_selector,
                )
                if table_cell_result.cell is None:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                        message=(
                            "UK replay skipped table-entry text op: the source-owned "
                            "table cell selector did not resolve to a replay cell."
                        ),
                        op=op,
                        detail=_text_patch_detail(
                            op,
                            target,
                            text_patch,
                            replacement,
                            blocking=True,
                            selector=dict(table_cell_selector),
                            reason_code=table_cell_result.reason_code,
                            **table_cell_result.detail,
                            family="source_table_elaboration",
                        ),
                    )
                    return
                table_cell = table_cell_result.cell
                symbolic_detail: dict[str, Any] = {}
                symbolic_reason = ""
                if _TABLE_CELL_PARAGRAPH_SENTINEL_RE.match(text_patch.selector.match_text):
                    substitution_result = self._apply_source_carried_table_cell_paragraph_substitution(
                        table_cell,
                        text_patch.selector.match_text,
                        replacement,
                    )
                    table_cell = substitution_result.cell
                    applied = substitution_result.applied
                    symbolic_reason = substitution_result.reason_code
                    symbolic_detail = substitution_result.detail
                elif (
                    text_patch.kind is TextPatchKindEnum.APPEND
                    and text_patch.selector.match_text == "TEXT_END"
                ):
                    table_cell, applied = self._apply_text_append_on_node_text_only(
                        table_cell,
                        replacement,
                    )
                else:
                    table_cell, applied = self._apply_text_replace_on_node_text_only(
                        table_cell,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                    )
                if applied:
                    if symbolic_detail:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_source_carried_table_entry_paragraph_substitution_resolved",
                            message=(
                                "UK replay applied a source-carried table-entry "
                                "paragraph substitution to one resolved table cell."
                            ),
                            op=op,
                            detail=_text_patch_detail(
                                op,
                                target,
                                text_patch,
                                replacement,
                                blocking=False,
                                quirks_disposition="apply",
                                selector=dict(table_cell_selector),
                                **table_cell_result.detail,
                                **symbolic_detail,
                                family="source_table_elaboration",
                            ),
                        )
                else:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID,
                        message=(
                            "UK replay skipped table-entry text op: the selected "
                            "table cell lacked the source text preimage."
                        ),
                        op=op,
                        detail=_text_patch_detail(
                            op,
                            target,
                            text_patch,
                            replacement,
                            blocking=True,
                            selector=dict(table_cell_selector),
                            reason_code=symbolic_reason or "cell_text_preimage_gap",
                            **table_cell_result.detail,
                            **symbolic_detail,
                            family="source_table_elaboration",
                        ),
                    )
                    return
            elif (
                heading_carrier is None
                and text_patch.kind is TextPatchKindEnum.REPLACE
                and self._recover_source_carried_labeled_child_text_substitution(
                    op,
                    target,
                    node,
                    text_patch,
                    replacement,
                )
            ):
                applied = True
                applied_rule_id = _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID
            elif heading_carrier is not None and text_patch.kind is TextPatchKindEnum.APPEND:
                node, applied = self._apply_text_append_on_node_text_only(
                    heading_carrier,
                    replacement,
                )
            elif (
                text_patch.kind is TextPatchKindEnum.APPEND
                and text_patch.selector.match_text == "TEXT_END"
            ):
                node, applied = self._apply_text_append_on_subtree_text_end(
                    node,
                    replacement,
                )
            elif heading_carrier is not None:
                node, applied = self._apply_text_replace_on_node_text_only(
                    heading_carrier,
                    text_patch.selector.match_text,
                    replacement,
                    text_patch.selector.occurrence,
                    text_patch.selector.end_occurrence,
                    recovery_rule_ids_out=recovery_rule_ids,
                )
            else:
                node, applied = self._apply_text_replace_on_subtree(
                    node,
                    text_patch.selector.match_text,
                    replacement,
                    text_patch.selector.occurrence,
                    text_patch.selector.end_occurrence,
                    recovery_rule_ids_out=recovery_rule_ids,
                )
            applied_match = text_patch.selector.match_text
            applied_replacement = replacement
            applied_rule_id = ""
            for recovery_rule_id in recovery_rule_ids:
                observation = uk_replay_recovery_observation(recovery_rule_id)
                source_shape = observation.source_shape
                message = observation.message
                family = observation.family
                strict_disposition = observation.strict_disposition
                detail = _text_patch_detail(
                    op,
                    target,
                    text_patch,
                    replacement,
                    blocking=False,
                    strict_disposition=strict_disposition,
                    family=family,
                )
                if source_shape is not None:
                    detail["source_shape"] = source_shape
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=recovery_rule_id,
                    message=message,
                    op=op,
                    detail=detail,
                )
            if not applied:
                if heading_carrier is not None:
                    heading_carrier, punctuation_applied = self._apply_text_replace_on_node_text_only(
                        heading_carrier,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                        allow_punctuation_spacing=True,
                    )
                else:
                    node, punctuation_applied = self._apply_text_replace_on_subtree(
                        node,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                        allow_punctuation_spacing=True,
                    )
                if punctuation_applied:
                    applied = True
                    applied_rule_id = "uk_replay_text_match_punctuation_space_normalized"
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=applied_rule_id,
                        message=(
                            "UK replay applied text-based op after normalizing "
                            "citation punctuation spacing in text_match."
                        ),
                        op=op,
                        detail=_text_patch_detail(
                            op,
                            target,
                            text_patch,
                            replacement,
                            blocking=False,
                            family="text_match_recovery",
                        ),
                    )
            if (
                not applied
                and _text_match_has_word_punctuation_elision_candidate(text_patch.selector.match_text)
            ):
                if heading_carrier is not None:
                    heading_carrier, word_punctuation_applied = self._apply_text_replace_on_node_text_only(
                        heading_carrier,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                        allow_word_punctuation_elision=True,
                    )
                else:
                    node, word_punctuation_applied = self._apply_text_replace_on_subtree(
                        node,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                        allow_word_punctuation_elision=True,
                    )
                if word_punctuation_applied:
                    applied = True
                    applied_rule_id = "uk_replay_text_match_word_punctuation_elided"
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=applied_rule_id,
                        message=(
                            "UK replay applied text-based op after normalizing "
                            "word-internal apostrophe/hyphen elision in text_match."
                        ),
                        op=op,
                        detail=_text_patch_detail(
                            op,
                            target,
                            text_patch,
                            replacement,
                            blocking=False,
                            family="text_match_recovery",
                        ),
                    )
            if (
                not applied
                and text_patch.kind is TextPatchKindEnum.REPLACE
                and bool(replacement)
            ):
                if heading_carrier is not None:
                    numeric_comma_result = self._apply_numeric_list_trailing_comma_anchor_on_node_text_only(
                        heading_carrier,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                    )
                    heading_carrier = numeric_comma_result.node
                else:
                    numeric_comma_result = self._apply_numeric_list_trailing_comma_anchor_on_subtree(
                        node,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                    )
                    node = numeric_comma_result.node
                if numeric_comma_result.applied:
                    applied = True
                    applied_match = numeric_comma_result.anchor or text_patch.selector.match_text
                    applied_rule_id = "uk_replay_numeric_list_trailing_comma_anchor_normalized"
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=applied_rule_id,
                        message=(
                            "UK replay applied insertion-style text op after proving "
                            "a unique numeric list anchor whose source selector carried "
                            "a trailing comma absent before a conjunction in the target."
                        ),
                        op=op,
                        detail=_text_patch_detail(
                            op,
                            target,
                            text_patch,
                            replacement,
                            blocking=False,
                            applied_match=applied_match,
                            family="text_match_recovery",
                            source_shape="numeric_list_trailing_comma_before_conjunction",
                            prior_same_target_text_patch_op_ids=tuple(
                                self._applied_text_patch_targets.get(str(target), ())
                            ),
                            prior_same_target_text_patch_count=len(
                                self._applied_text_patch_targets.get(str(target), ())
                            ),
                        ),
                    )
            if (
                not applied
                and text_patch.kind is TextPatchKindEnum.DELETE
                and not replacement
                and text_patch.selector.occurrence == 0
                and text_patch.selector.end_occurrence == 0
            ):
                rotated_match = _rotated_trailing_comma_omission_match(
                    text_patch.selector.match_text,
                    heading_carrier if heading_carrier is not None else node,
                )
                if rotated_match:
                    if heading_carrier is not None:
                        heading_carrier, rotated_comma_applied = self._apply_text_replace_on_node_text_only(
                            heading_carrier,
                            rotated_match,
                            replacement,
                            0,
                            0,
                        )
                    else:
                        node, rotated_comma_applied = self._apply_text_replace_on_subtree(
                            node,
                            rotated_match,
                            replacement,
                            0,
                            0,
                        )
                    if rotated_comma_applied:
                        applied = True
                        applied_match = rotated_match
                        applied_rule_id = "uk_replay_text_match_rotated_trailing_comma_omission"
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=applied_rule_id,
                            message=(
                                "UK replay applied omission after proving a unique "
                                "rotated trailing-comma selector preimage."
                            ),
                            op=op,
                            detail=_text_patch_detail(
                                op,
                                target,
                                text_patch,
                                replacement,
                                blocking=False,
                                applied_match=rotated_match,
                                family="text_match_recovery",
                                source_shape="trailing_comma_rotated_before_phrase",
                            ),
                        )
            if not applied:
                for frag_sub in _fragment_substitution(op) or []:
                    alt_match = str(frag_sub.get("original") or "").strip()
                    alt_replacement = str(frag_sub.get("replacement") or "")
                    if not alt_match or (
                        alt_match == text_patch.selector.match_text and alt_replacement == replacement
                    ):
                        continue
                    if heading_carrier is not None:
                        node, alt_applied = self._apply_text_replace_on_node_text_only(
                            node,
                            alt_match,
                            alt_replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                    else:
                        node, alt_applied = self._apply_text_replace_on_subtree(
                            node,
                            alt_match,
                            alt_replacement,
                            text_patch.selector.occurrence,
                            text_patch.selector.end_occurrence,
                        )
                    if alt_applied:
                        applied = True
                        applied_match = alt_match
                        applied_replacement = alt_replacement
                        self._log(
                            f"  EXECUTOR: text_replace fallback in {node.kind} {node.label}: {alt_match!r} -> {alt_replacement!r}"
                        )
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind="uk_replay_text_fragment_substitution_fallback_applied",
                            message=(
                                "UK replay applied a text replacement using a source-carried "
                                "fragment substitution after the primary selector missed."
                            ),
                            op=op,
                            detail=_text_patch_detail(
                                op,
                                target,
                                text_patch,
                                replacement,
                                blocking=False,
                                applied_match=alt_match,
                                applied_replacement=alt_replacement,
                                family="text_rewrite_recovery",
                                source_shape="fragment_substitution_provenance_tag",
                            ),
                        )
                        break
            if applied:
                self._log(
                    f"  EXECUTOR: text_replace in {node.kind} {node.label}: {applied_match!r} -> {applied_replacement!r}"
                )
                if applied_rule_id:
                    self._log(f"  EXECUTOR: text_replace recovery rule: {applied_rule_id}")
                target_key = str(target)
                if target_key:
                    self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            else:
                text_miss = classify_uk_replay_text_miss(
                    op=op,
                    target=target,
                    node=node,
                    heading_carrier=heading_carrier,
                    text_patch=text_patch,
                    replacement=replacement,
                    prior_same_target_text_patch_count=len(
                        self._applied_text_patch_targets.get(str(target), ())
                    ),
                )
                self._log(
                    f"  EXECUTOR: WARN text_replace target found but text_match not in subtree: {text_patch.selector.match_text!r} in {node.kind} {node.label}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=text_miss.kind,
                    message=text_miss.message,
                    op=op,
                    detail=_text_patch_detail(
                        op,
                        target,
                        text_patch,
                        replacement,
                        blocking=text_miss.blocking,
                        prior_same_target_text_patch_op_ids=tuple(
                            self._applied_text_patch_targets.get(str(target), ())
                        ),
                        prior_same_target_text_patch_count=len(
                            self._applied_text_patch_targets.get(str(target), ())
                        ),
                        target_container=_addr_container(target),
                        target_granularity=_addr_leaf_kind(target) or "",
                        source_shape=text_miss.source_shape,
                        target_text_preview=_replay_subtree_text_preview(node),
                        target_text_normalized_preview=_normalized_replay_subtree_text(node)[:240],
                    ),
                )
        else:
            self._log(f"  EXECUTOR: WARN text_replace target not found: {op.target}")
            if self._recover_text_patch_on_empty_descendant_parent(op, target, text_patch, replacement):
                target_key = str(target)
                if target_key:
                    self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            elif self._recover_text_patch_on_implicit_first_subparagraph_parent_text(
                op,
                target,
                text_patch,
                replacement,
            ):
                target_key = str(target)
                if target_key:
                    self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            elif uk_table_target_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_table_shape_gap",
                    message="UK replay skipped text-based op: table target has no structural table node.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._empty_descendant_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_empty_descendant_shape_gap",
                    message="UK replay skipped text-based op: parent target exists but has no descendant structural shape.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._target_under_repealed_prefix(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repealed_target_gap",
                    message="UK replay skipped text-based op: target path was already repealed earlier in the chain.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._doubled_alpha_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent doubled-alpha sibling range under the parent path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_sibling_range_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent sibling range under the parent path.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._annex_schedule_mismatch_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_annex_schedule_reference_gap",
                    message="UK replay skipped text-based op: Annex reference was lowered to a missing schedule root target.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._container_text_target_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_schedule_container_text_target_gap",
                    message="UK replay skipped text-based op: lowered target points at a missing schedule container instead of the textual descendant.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._subsection_alpha_text_target_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_subsection_descendant_target_collapse_gap",
                    message="UK replay skipped text-based op: lowered target collapsed a numeric subsection and alphabetic descendant into one subsection label.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._malformed_target_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._malformed_target_gap_kind(target),
                    message="UK replay skipped text-based op: lowered target path is malformed.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped text-based op: schedule root branch is absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_parent_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._missing_parent_shape_gap_kind(target),
                    message="UK replay skipped text-based op: immediate parent target path is structurally absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._schedule_paragraph_carrier_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._schedule_paragraph_carrier_gap_kind(target),
                    message="UK replay skipped text-based op: schedule paragraph carrier is structurally absent or wrapped.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._recover_text_patch_on_direct_section_paragraph_child_text(
                op,
                target,
                text_patch,
                replacement,
            ):
                target_key = str(target)
                if target_key:
                    self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                self._record_invariant_violations(op)
                self._emit_top_section_snapshot(op)
            elif self._direct_section_paragraph_carrier_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_direct_section_paragraph_carrier_gap",
                    message=(
                        "UK replay skipped text-based op: direct section paragraph "
                        "target is not represented as an addressable carrier in source XML."
                    ),
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._leading_blank_subparagraph_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_sectionlike_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_sectionlike_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent sectionlike range gap.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif target.special is FacetKind.HEADING:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_heading_facet_target_gap",
                    message=(
                        "UK replay skipped heading-facet text op: target "
                        "has no unique replay heading carrier."
                    ),
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_schedule_root_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_range_gap",
                    message=(
                        "UK replay skipped text-based op: schedule target falls "
                        "inside an absent schedule range gap."
                    ),
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif self._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped text-based op: schedule root branch is absent.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            elif prior_kind := self._prior_same_target_gap_kind(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=prior_kind,
                    message="UK replay skipped text-based op: target already exhibited the same structural gap earlier in the chain.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_target_not_found",
                    message="UK replay skipped text-based op: target not found.",
                    op=op,
                    detail=uk_replay_blocking_action_target_detail(op, target),
                )
