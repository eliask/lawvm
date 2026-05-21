"""UK replay text action branch."""

from __future__ import annotations

from typing import Any

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import FacetKind, TextPatchKindEnum
from lawvm.uk_legislation.addressing import _action_name, _addr_container, _addr_leaf_kind
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
from lawvm.uk_legislation.replay_records import _append_uk_replay_adjudication
from lawvm.uk_legislation.replay_table_geometry import (
    resolve_uk_table_entry_inline_cell,
    resolve_unique_uk_table_entry_cells,
)
from lawvm.uk_legislation.replay_target_diagnostics import (
    _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID,
)
from lawvm.uk_legislation.replay_target_gaps import (
    uk_broad_schedule_table_shape_gap,
    uk_table_target_shape_gap,
)
from lawvm.uk_legislation.replay_text import (
    _article_phrase_content_word_present,
    _citation_connector_elided_text_match_present,
    _citation_stripped_text_match_present,
    _definition_entry_term_absent,
    _monetary_amount_text_selector,
    _multi_fragment_text_selector,
    _node_text_contains_text,
    _non_substantive_text_selector,
    _normalized_replay_subtree_text,
    _normalized_replacement_text_present,
    _normalized_text_match_present,
    _parenthetical_omission_text_selector,
    _replay_subtree_text_preview,
    _subtree_contains_text,
    _synthetic_text_selector,
    _text_patch_replacement_preserves_anchor,
)
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
    _text_rewrite_rule_ids_for_op,
)


_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID = "uk_replay_table_entry_inline_text_insertion_unresolved"
_UK_REPLAY_TABLE_ENTRY_INLINE_PREIMAGE_GAP_RULE_ID = "uk_replay_table_entry_inline_text_preimage_gap"
_UK_RESPECTIVELY_ALL_OCCURRENCES_TEXT_REWRITE_RULE_ID = (
    "uk_effect_respectively_all_occurrences_substitution_text_patch"
)


class UKReplayTextActionApplyMixin:

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
                detail={
                    "action": _action_name(op.action),
                    "target": str(target),
                    "family": "unsupported_or_unresolved_action",
                    "reason_code": "missing_structured_text_patch",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
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
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "text_match": text_patch.selector.match_text,
                        "replacement_text": replacement,
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
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
                    table_cells, table_cell_reason, table_cell_detail = resolve_unique_uk_table_entry_cells(
                        node,
                        table_cell_selector,
                    )
                    if not table_cells:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                            message=(
                                "UK replay skipped multi-entry table text op: the "
                                "source-owned table cell selector did not resolve."
                            ),
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                "reason_code": table_cell_reason,
                                **table_cell_detail,
                                "family": "source_table_elaboration",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                            },
                        )
                        return
                    if text_patch.kind not in {TextPatchKindEnum.REPLACE, TextPatchKindEnum.DELETE}:
                        _append_uk_replay_adjudication(
                            self.adjudications_out,
                            kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                            message="UK replay skipped multi-entry table text op: unsupported text-patch kind.",
                            op=op,
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                "reason_code": "unsupported_multi_cell_text_patch_kind",
                                **table_cell_detail,
                                "family": "source_table_elaboration",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                            },
                        )
                        return
                    preimage_gaps = [
                        str(cell.text or "")[:240]
                        for cell in table_cells
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
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                "reason_code": "multi_cell_text_preimage_gap",
                                "preimage_gap_cells": tuple(preimage_gaps),
                                **table_cell_detail,
                                "family": "source_table_elaboration",
                                "blocking": True,
                                "strict_disposition": "block",
                                "quirks_disposition": "record",
                            },
                        )
                        return
                    for table_cell in table_cells:
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
                                detail={
                                    "action": _action_name(op.action),
                                    "target": str(target),
                                    "text_match": text_patch.selector.match_text,
                                    "replacement_text": replacement,
                                    "selector": dict(table_cell_selector),
                                    "reason_code": "multi_cell_text_apply_gap",
                                    **table_cell_detail,
                                    "family": "source_table_elaboration",
                                    "blocking": True,
                                    "strict_disposition": "block",
                                    "quirks_disposition": "record",
                                },
                            )
                            return
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind="uk_replay_table_entry_multi_cell_text_patch_resolved",
                        message="UK replay applied a source-owned text patch to multiple table cells.",
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "selector": dict(table_cell_selector),
                            **table_cell_detail,
                            "family": "source_table_elaboration",
                            "blocking": False,
                            "strict_disposition": "record",
                            "quirks_disposition": "apply",
                        },
                    )
                    target_key = str(target)
                    if target_key:
                        self._applied_text_patch_targets.setdefault(target_key, []).append(op.op_id)
                    self._record_invariant_violations(op)
                    self._emit_top_section_snapshot(op)
                    return
                table_cell, table_cell_reason, table_cell_detail = resolve_uk_table_entry_inline_cell(
                    node,
                    table_cell_selector,
                )
                if table_cell is None:
                    _append_uk_replay_adjudication(
                        self.adjudications_out,
                        kind=_UK_REPLAY_TABLE_ENTRY_INLINE_UNRESOLVED_RULE_ID,
                        message=(
                            "UK replay skipped table-entry text op: the source-owned "
                            "table cell selector did not resolve to a replay cell."
                        ),
                        op=op,
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "selector": dict(table_cell_selector),
                            "reason_code": table_cell_reason,
                            **table_cell_detail,
                            "family": "source_table_elaboration",
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        },
                    )
                    return
                symbolic_detail: dict[str, Any] = {}
                symbolic_reason = ""
                if _TABLE_CELL_PARAGRAPH_SENTINEL_RE.match(text_patch.selector.match_text):
                    table_cell, applied, symbolic_reason, symbolic_detail = (
                        self._apply_source_carried_table_cell_paragraph_substitution(
                            table_cell,
                            text_patch.selector.match_text,
                            replacement,
                        )
                    )
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
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "selector": dict(table_cell_selector),
                                **table_cell_detail,
                                **symbolic_detail,
                                "family": "source_table_elaboration",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "apply",
                            },
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
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "selector": dict(table_cell_selector),
                            "reason_code": symbolic_reason or "cell_text_preimage_gap",
                            **table_cell_detail,
                            **symbolic_detail,
                            "family": "source_table_elaboration",
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                        },
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
                source_shape = None
                if recovery_rule_id == "uk_replay_definition_predicate_shall_construed_normalized":
                    message = (
                        "UK replay applied definition-entry text op after recognizing "
                        "the definition predicate variant 'shall be construed'."
                    )
                    family = "definition_entry_predicate_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_after_definition_child_structured_insert_applied":
                    message = (
                        "UK replay inserted source-carried definition children after "
                        "a preserved structured definition child."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "structured_after_definition_child_insert_selector"
                elif recovery_rule_id == "uk_replay_after_definition_child_flat_ordinal_insert_applied":
                    message = (
                        "UK replay inserted source-carried definition text after "
                        "a flat definition entry using a bounded ordinal child segment."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "flat_after_definition_child_ordinal_insert_selector"
                elif recovery_rule_id == "uk_replay_after_definition_text_insert_applied":
                    message = (
                        "UK replay inserted definition text after proving a unique "
                        "definition surface."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "after_definition_text_insert_selector"
                elif recovery_rule_id == "uk_replay_definition_entry_qualifier_phrase_normalized":
                    message = (
                        "UK replay applied definition-entry text op after recognizing "
                        "a qualifier phrase between the defined term and predicate."
                    )
                    family = "definition_entry_predicate_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_definition_entry_orphan_separator_normalized":
                    message = (
                        "UK replay applied definition-entry text op after normalizing "
                        "an orphan comma after a definition-entry separator."
                    )
                    family = "definition_entry_separator_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_definition_entry_text_rewrite_applied":
                    message = (
                        "UK replay applied a definition-entry text rewrite after "
                        "proving a unique definition entry surface."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "definition_entry_selector"
                elif recovery_rule_id == "uk_replay_definition_anchor_lexical_variant_recovered":
                    message = (
                        "UK replay applied definition-anchor text op after resolving "
                        "a narrow education/educational lexical variant in the source anchor."
                    )
                    family = "target_resolution_recovery"
                    strict_disposition = "block"
                elif recovery_rule_id == "uk_replay_definition_anchor_parenthetical_translation_normalized":
                    message = (
                        "UK replay applied definition-anchor text op after recognizing "
                        "a parenthetical translation between the defined term and predicate."
                    )
                    family = "target_resolution_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_definition_anchor_qualifier_phrase_normalized":
                    message = (
                        "UK replay applied definition-anchor text op after recognizing "
                        "a qualifier phrase between the anchor term and predicate."
                    )
                    family = "target_resolution_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_definition_anchor_conjoined_term_normalized":
                    message = (
                        "UK replay applied definition-anchor text op after recognizing "
                        "the anchor as the final term in a conjoined definition entry."
                    )
                    family = "target_resolution_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_definition_child_structured_text_rewrite_applied":
                    message = (
                        "UK replay applied a definition-child text rewrite against "
                        "a preserved structured definition child."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "structured_definition_child_selector"
                elif recovery_rule_id == "uk_replay_definition_child_flat_ordinal_text_rewrite_applied":
                    message = (
                        "UK replay applied a definition-child text rewrite against "
                        "a flat definition entry using a bounded ordinal child segment."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "flat_definition_child_ordinal_selector"
                elif recovery_rule_id == "uk_replay_in_definition_child_structured_text_rewrite_applied":
                    message = (
                        "UK replay applied a scoped in-definition-child text rewrite "
                        "against a preserved structured definition child."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "structured_in_definition_child_selector"
                elif recovery_rule_id == "uk_replay_in_definition_child_flat_ordinal_text_rewrite_applied":
                    message = (
                        "UK replay applied a scoped in-definition-child text rewrite "
                        "against a flat definition entry using a bounded ordinal child segment."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "flat_in_definition_child_ordinal_selector"
                elif recovery_rule_id == "uk_replay_text_range_anchor_word_boundary_normalized":
                    message = (
                        "UK replay applied range text op after matching a quoted "
                        "single-word range anchor as a word token."
                    )
                    family = "text_match_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_labeled_child_end_range_applied":
                    message = (
                        "UK replay applied a text range from a parent text anchor "
                        "through the end of an explicitly labelled child provision."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                elif recovery_rule_id == "uk_replay_source_carried_child_tail_text_rewrite_applied":
                    message = (
                        "UK replay applied a source-carried child-tail text rewrite "
                        "against the collapsed parent text after proving the named "
                        "child is the final child."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "source_carried_child_tail_selector"
                elif recovery_rule_id == "uk_replay_source_carried_before_child_text_rewrite_applied":
                    message = (
                        "UK replay applied a source-carried before-child text rewrite "
                        "against the parent text after proving the named child is unique."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "source_carried_before_child_selector"
                elif recovery_rule_id == "uk_replay_source_carried_after_child_text_rewrite_applied":
                    message = (
                        "UK replay applied a source-carried after-child text rewrite "
                        "against the named child text after proving the child anchor is unique."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "source_carried_after_child_selector"
                elif recovery_rule_id == "uk_replay_source_carried_multi_child_text_rewrite_applied":
                    message = (
                        "UK replay applied a source-carried multi-child text rewrite "
                        "after proving every named child target is present and unique."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "source_carried_multi_child_selector"
                elif recovery_rule_id == "uk_replay_amendment_insert_tail_text_rewrite_applied":
                    message = (
                        "UK replay applied an amendment-instruction inserted-text rewrite "
                        "after proving the target text contains an insert anchor."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "amendment_instruction_insert_tail_selector"
                elif recovery_rule_id == "uk_replay_before_definition_text_rewrite_applied":
                    message = (
                        "UK replay applied a before-definition text rewrite after "
                        "proving the target has a flat definition text surface."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "flat_definition_text_selector"
                elif recovery_rule_id == "uk_replay_in_definition_at_end_text_rewrite_applied":
                    message = (
                        "UK replay applied an at-end definition text rewrite after "
                        "proving a unique definition surface."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "definition_at_end_selector"
                elif recovery_rule_id == "uk_replay_in_definition_range_to_end_text_rewrite_applied":
                    message = (
                        "UK replay applied a definition range-to-end text rewrite "
                        "after proving a unique definition surface and start anchor."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "definition_range_to_end_selector"
                elif recovery_rule_id == "uk_replay_in_definition_range_text_rewrite_applied":
                    message = (
                        "UK replay applied a definition range text rewrite after "
                        "proving a unique definition surface, start anchor, and end anchor."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "definition_range_selector"
                elif recovery_rule_id == "uk_replay_in_definition_after_each_text_rewrite_applied":
                    message = (
                        "UK replay applied an all-occurrences definition text rewrite "
                        "after proving a unique definition surface and at least one anchor."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "definition_after_each_anchor_selector"
                elif recovery_rule_id == "uk_replay_in_definition_after_anchor_text_rewrite_applied":
                    message = (
                        "UK replay applied a definition after-anchor text rewrite after "
                        "proving a unique definition surface and unique anchor."
                    )
                    family = "text_rewrite_recovery"
                    strict_disposition = "record"
                    source_shape = "definition_after_anchor_selector"
                else:
                    message = (
                        "UK replay applied text-based op after normalizing "
                        "a contextual selector anchor kind."
                    )
                    family = "text_match_recovery"
                    strict_disposition = "record"
                detail = {
                    "action": _action_name(op.action),
                    "target": str(target),
                    "text_match": text_patch.selector.match_text,
                    "replacement_text": replacement,
                    "family": family,
                    "blocking": False,
                    "strict_disposition": strict_disposition,
                    "quirks_disposition": "record",
                }
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
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "family": "text_match_recovery",
                            "blocking": False,
                            "strict_disposition": "record",
                            "quirks_disposition": "record",
                        },
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
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "replacement_text": replacement,
                            "family": "text_match_recovery",
                            "blocking": False,
                            "strict_disposition": "record",
                            "quirks_disposition": "record",
                        },
                    )
            if (
                not applied
                and text_patch.kind is TextPatchKindEnum.REPLACE
                and bool(replacement)
            ):
                if heading_carrier is not None:
                    (
                        heading_carrier,
                        numeric_comma_applied,
                        numeric_comma_anchor,
                    ) = self._apply_numeric_list_trailing_comma_anchor_on_node_text_only(
                        heading_carrier,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                    )
                else:
                    (
                        node,
                        numeric_comma_applied,
                        numeric_comma_anchor,
                    ) = self._apply_numeric_list_trailing_comma_anchor_on_subtree(
                        node,
                        text_patch.selector.match_text,
                        replacement,
                        text_patch.selector.occurrence,
                        text_patch.selector.end_occurrence,
                    )
                if numeric_comma_applied:
                    applied = True
                    applied_match = numeric_comma_anchor or text_patch.selector.match_text
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
                        detail={
                            "action": _action_name(op.action),
                            "target": str(target),
                            "text_match": text_patch.selector.match_text,
                            "applied_match": applied_match,
                            "replacement_text": replacement,
                            "family": "text_match_recovery",
                            "source_shape": "numeric_list_trailing_comma_before_conjunction",
                            "blocking": False,
                            "strict_disposition": "record",
                            "quirks_disposition": "record",
                            "prior_same_target_text_patch_op_ids": tuple(
                                self._applied_text_patch_targets.get(str(target), ())
                            ),
                            "prior_same_target_text_patch_count": len(
                                self._applied_text_patch_targets.get(str(target), ())
                            ),
                        },
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
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "applied_match": rotated_match,
                                "replacement_text": replacement,
                                "family": "text_match_recovery",
                                "source_shape": "trailing_comma_rotated_before_phrase",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "record",
                            },
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
                            detail={
                                "action": _action_name(op.action),
                                "target": str(target),
                                "text_match": text_patch.selector.match_text,
                                "replacement_text": replacement,
                                "applied_match": alt_match,
                                "applied_replacement": alt_replacement,
                                "family": "text_rewrite_recovery",
                                "source_shape": "fragment_substitution_provenance_tag",
                                "blocking": False,
                                "strict_disposition": "record",
                                "quirks_disposition": "record",
                            },
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
                already_rewritten = (
                    text_patch.kind is TextPatchKindEnum.REPLACE
                    and bool(replacement)
                    and (
                        _node_text_contains_text(node, replacement)
                        if heading_carrier is not None
                        else _subtree_contains_text(node, replacement)
                    )
                )
                if already_rewritten:
                    kind = "uk_replay_text_match_already_rewritten"
                    message = (
                        "UK replay skipped text-based op: text_match missing but "
                        "replacement text is already present in target subtree."
                    )
                elif (
                    text_patch.kind is TextPatchKindEnum.REPLACE
                    and bool(replacement)
                    and _normalized_replacement_text_present(
                        replacement,
                        heading_carrier if heading_carrier is not None else node,
                    )
                ):
                    kind = "uk_replay_text_match_replacement_normalized_present"
                    message = (
                        "UK replay skipped text-based op: text_match missing but "
                        "the normalized replacement text is already present in target subtree."
                    )
                elif (
                    text_patch.selector.match_text.startswith("TEXT_DEFINITION_ENTRY_")
                    and text_patch.kind is TextPatchKindEnum.DELETE
                    and _definition_entry_term_absent(text_patch.selector.match_text, node)
                ):
                    kind = "uk_replay_definition_entry_already_absent_observed"
                    message = (
                        "UK replay observed a definition-entry repeal whose named "
                        "definition term is already absent from the target subtree."
                    )
                elif text_patch.selector.match_text.startswith("TEXT_DEFINITION_ENTRY_"):
                    kind = "uk_replay_definition_entry_shape_gap"
                    message = (
                        "UK replay skipped definition-entry text op: definition entry "
                        "could not be uniquely bounded in the target subtree."
                    )
                elif text_patch.selector.match_text.startswith("TEXT_DEFINITION_CHILD_"):
                    kind = "uk_replay_definition_child_shape_gap"
                    message = (
                        "UK replay skipped definition-child text op: definition child "
                        "could not be uniquely bounded in the target subtree."
                    )
                elif (
                    target.special is FacetKind.HEADING
                    and heading_carrier is not None
                    and _UK_RESPECTIVELY_ALL_OCCURRENCES_TEXT_REWRITE_RULE_ID
                    in _text_rewrite_rule_ids_for_op(op)
                ):
                    kind = "uk_replay_heading_respectively_all_occurrences_absent_observed"
                    message = (
                        "UK replay observed a respectively paired heading-facet rewrite "
                        "whose quoted preimage is absent from this heading carrier; the "
                        "source instruction applies wherever that expression occurs."
                    )
                elif target.special is FacetKind.HEADING and heading_carrier is not None:
                    kind = "uk_replay_heading_text_preimage_gap"
                    message = (
                        "UK replay skipped heading-facet text op: heading carrier exists "
                        "but lacks the source text preimage."
                    )
                elif str(target) in self._applied_text_patch_targets:
                    prior_count = len(self._applied_text_patch_targets.get(str(target), ()))
                    if prior_count > 1:
                        kind = "uk_replay_text_patch_preimage_drift_multi_prior_same_target"
                    else:
                        kind = "uk_replay_text_patch_preimage_drift"
                    message = (
                        "UK replay skipped text-based op: text_match missing after "
                        "an earlier same-target text patch changed the replay preimage."
                    )
                elif uk_broad_schedule_table_shape_gap(target, node):
                    if str(_addr_leaf_kind(target) or "").lower() == "part":
                        kind = "uk_replay_broad_schedule_part_table_shape_gap"
                    else:
                        kind = "uk_replay_broad_schedule_table_shape_gap"
                    message = (
                        "UK replay skipped text-based op: broad schedule target has no "
                        "table or provision structure carrying the text patch preimage."
                    )
                elif not _normalized_replay_subtree_text(node):
                    kind = "uk_replay_text_target_empty_surface_gap"
                    message = (
                        "UK replay skipped text-based op: target subtree has no "
                        "replay-visible text carrying the text patch preimage."
                    )
                elif _synthetic_text_selector(text_patch.selector.match_text):
                    kind = "uk_replay_text_match_synthetic_selector_gap"
                    message = (
                        "UK replay skipped text-based op: synthetic text selector "
                        "could not be resolved in the target subtree."
                    )
                elif _non_substantive_text_selector(text_patch.selector.match_text):
                    kind = "uk_replay_text_match_non_substantive_selector_gap"
                    message = (
                        "UK replay skipped text-based op: non-substantive selector "
                        "could not be resolved in the target subtree."
                    )
                elif _multi_fragment_text_selector(text_patch.selector.match_text):
                    kind = "uk_replay_text_match_multi_fragment_selector_gap"
                    message = (
                        "UK replay skipped text-based op: text_match appears to "
                        "combine multiple separated source fragments into one selector."
                    )
                elif _normalized_text_match_present(text_patch.selector.match_text, node):
                    kind = "uk_replay_text_match_normalized_preimage_present_gap"
                    message = (
                        "UK replay skipped text-based op: exact text_match was missing "
                        "but an alphanumeric-normalized preimage is present in the target subtree."
                    )
                elif _citation_stripped_text_match_present(text_patch.selector.match_text, node):
                    kind = "uk_replay_text_match_citation_tail_surface_gap"
                    message = (
                        "UK replay skipped text-based op: exact text_match was missing "
                        "but the target subtree appears to omit citation year/chapter tail text."
                    )
                elif _citation_connector_elided_text_match_present(text_patch.selector.match_text, node):
                    kind = "uk_replay_text_match_citation_connector_surface_gap"
                    message = (
                        "UK replay skipped citation-list text op: exact text_match was missing "
                        "but the target subtree appears to elide connector words between citations."
                    )
                elif _article_phrase_content_word_present(text_patch.selector.match_text, node):
                    kind = "uk_replay_text_match_article_phrase_surface_gap"
                    message = (
                        "UK replay skipped article-prefixed text op: exact text_match was missing "
                        "but the target subtree contains the selector's content word in a different phrase shape."
                    )
                elif _monetary_amount_text_selector(text_patch.selector.match_text):
                    kind = "uk_replay_text_monetary_amount_preimage_gap"
                    message = (
                        "UK replay skipped monetary-amount text op: quoted amount preimage "
                        "is absent from the target subtree."
                    )
                elif (
                    text_patch.kind is TextPatchKindEnum.DELETE
                    and _parenthetical_omission_text_selector(text_patch.selector.match_text)
                ):
                    kind = "uk_replay_text_parenthetical_omission_preimage_gap"
                    message = (
                        "UK replay skipped parenthetical omission text op: quoted parenthetical "
                        "preimage is absent from the target subtree."
                    )
                elif (
                    text_patch.kind is TextPatchKindEnum.REPLACE
                    and _text_patch_replacement_preserves_anchor(text_patch.selector.match_text, replacement)
                ):
                    kind = "uk_replay_text_insert_anchor_preimage_gap"
                    message = (
                        "UK replay skipped insertion-style text op: the replacement preserves "
                        "the source anchor, but that anchor is absent from the target subtree."
                    )
                else:
                    kind = "uk_replay_text_match_missing"
                    message = (
                        "UK replay skipped text-based op: text_match not found in target subtree."
                    )
                self._log(
                    f"  EXECUTOR: WARN text_replace target found but text_match not in subtree: {text_patch.selector.match_text!r} in {node.kind} {node.label}"
                )
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=kind,
                    message=message,
                    op=op,
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "text_match": text_patch.selector.match_text,
                        "replacement_text": replacement,
                        "blocking": kind
                        in {
                            "uk_replay_broad_schedule_table_shape_gap",
                            "uk_replay_broad_schedule_part_table_shape_gap",
                            "uk_replay_table_shape_gap",
                            "uk_replay_definition_entry_shape_gap",
                            "uk_replay_heading_text_preimage_gap",
                            "uk_replay_text_target_empty_surface_gap",
                            "uk_replay_text_match_missing",
                            "uk_replay_text_insert_anchor_preimage_gap",
                            "uk_replay_text_monetary_amount_preimage_gap",
                            "uk_replay_text_parenthetical_omission_preimage_gap",
                            "uk_replay_text_match_article_phrase_surface_gap",
                            "uk_replay_text_patch_preimage_drift",
                            "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
                            "uk_replay_text_match_synthetic_selector_gap",
                            "uk_replay_text_match_normalized_preimage_present_gap",
                            "uk_replay_text_match_non_substantive_selector_gap",
                            "uk_replay_text_match_multi_fragment_selector_gap",
                            "uk_replay_text_match_citation_tail_surface_gap",
                            "uk_replay_text_match_citation_connector_surface_gap",
                        },
                        "strict_disposition": (
                            "block"
                            if kind
                            in {
                                "uk_replay_broad_schedule_table_shape_gap",
                                "uk_replay_broad_schedule_part_table_shape_gap",
                                "uk_replay_table_shape_gap",
                                "uk_replay_definition_entry_shape_gap",
                                "uk_replay_heading_text_preimage_gap",
                                "uk_replay_text_target_empty_surface_gap",
                                "uk_replay_text_match_missing",
                                "uk_replay_text_insert_anchor_preimage_gap",
                                "uk_replay_text_monetary_amount_preimage_gap",
                                "uk_replay_text_parenthetical_omission_preimage_gap",
                                "uk_replay_text_match_article_phrase_surface_gap",
                                "uk_replay_text_patch_preimage_drift",
                                "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
                                "uk_replay_text_match_synthetic_selector_gap",
                                "uk_replay_text_match_normalized_preimage_present_gap",
                                "uk_replay_text_match_non_substantive_selector_gap",
                                "uk_replay_text_match_multi_fragment_selector_gap",
                                "uk_replay_text_match_citation_tail_surface_gap",
                                "uk_replay_text_match_citation_connector_surface_gap",
                            }
                            else "record"
                        ),
                        "quirks_disposition": "record",
                        "prior_same_target_text_patch_op_ids": tuple(
                            self._applied_text_patch_targets.get(str(target), ())
                        ),
                        "prior_same_target_text_patch_count": len(
                            self._applied_text_patch_targets.get(str(target), ())
                        ),
                        "target_container": _addr_container(target),
                        "target_granularity": _addr_leaf_kind(target) or "",
                        "source_shape": (
                            "broad_schedule_without_table_or_provision_structure"
                            if kind
                            in {
                                "uk_replay_broad_schedule_table_shape_gap",
                                "uk_replay_broad_schedule_part_table_shape_gap",
                            }
                            else "target_subtree_without_text_surface"
                            if kind == "uk_replay_text_target_empty_surface_gap"
                            else "heading_preimage_absent"
                            if kind == "uk_replay_heading_text_preimage_gap"
                            else "respectively_all_occurrences_heading_preimage_absent"
                            if kind == "uk_replay_heading_respectively_all_occurrences_absent_observed"
                            else "definition_entry_already_absent"
                            if kind == "uk_replay_definition_entry_already_absent_observed"
                            else "insert_anchor_preimage_absent"
                            if kind == "uk_replay_text_insert_anchor_preimage_gap"
                            else "monetary_amount_preimage_absent"
                            if kind == "uk_replay_text_monetary_amount_preimage_gap"
                            else "parenthetical_omission_preimage_absent"
                            if kind == "uk_replay_text_parenthetical_omission_preimage_gap"
                            else "article_phrase_content_word_surface_gap"
                            if kind == "uk_replay_text_match_article_phrase_surface_gap"
                            else "normalized_preimage_present"
                            if kind == "uk_replay_text_match_normalized_preimage_present_gap"
                            else "replacement_normalized_present"
                            if kind == "uk_replay_text_match_replacement_normalized_present"
                            else "multi_fragment_text_selector"
                            if kind == "uk_replay_text_match_multi_fragment_selector_gap"
                            else "citation_tail_surface_gap"
                            if kind == "uk_replay_text_match_citation_tail_surface_gap"
                            else "citation_connector_surface_gap"
                            if kind == "uk_replay_text_match_citation_connector_surface_gap"
                            else ""
                        ),
                        "target_text_preview": _replay_subtree_text_preview(node),
                        "target_text_normalized_preview": _normalized_replay_subtree_text(node)[:240],
                    },
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
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._empty_descendant_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_empty_descendant_shape_gap",
                    message="UK replay skipped text-based op: parent target exists but has no descendant structural shape.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._target_under_repealed_prefix(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_repealed_target_gap",
                    message="UK replay skipped text-based op: target path was already repealed earlier in the chain.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._doubled_alpha_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent doubled-alpha sibling range under the parent path.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._missing_sibling_range_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent sibling range under the parent path.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._annex_schedule_mismatch_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_annex_schedule_reference_gap",
                    message="UK replay skipped text-based op: Annex reference was lowered to a missing schedule root target.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._container_text_target_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_schedule_container_text_target_gap",
                    message="UK replay skipped text-based op: lowered target points at a missing schedule container instead of the textual descendant.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._subsection_alpha_text_target_gap(op):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_subsection_descendant_target_collapse_gap",
                    message="UK replay skipped text-based op: lowered target collapsed a numeric subsection and alphabetic descendant into one subsection label.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._malformed_target_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._malformed_target_gap_kind(target),
                    message="UK replay skipped text-based op: lowered target path is malformed.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._missing_schedule_branch_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_schedule_branch_gap",
                    message="UK replay skipped text-based op: schedule root branch is absent.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._missing_parent_shape_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._missing_parent_shape_gap_kind(target),
                    message="UK replay skipped text-based op: immediate parent target path is structurally absent.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._schedule_paragraph_carrier_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=self._schedule_paragraph_carrier_gap_kind(target),
                    message="UK replay skipped text-based op: schedule paragraph carrier is structurally absent or wrapped.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
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
                    detail={
                        "action": _action_name(op.action),
                        "target": str(target),
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                )
            elif self._leading_blank_subparagraph_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_absent_sibling_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent leading numeric subparagraph gap under blank schedule placeholders.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif self._missing_sectionlike_gap(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_missing_sectionlike_range_gap",
                    message="UK replay skipped text-based op: target falls inside an absent sectionlike range gap.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            elif prior_kind := self._prior_same_target_gap_kind(target):
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind=prior_kind,
                    message="UK replay skipped text-based op: target already exhibited the same structural gap earlier in the chain.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
            else:
                _append_uk_replay_adjudication(
                    self.adjudications_out,
                    kind="uk_replay_target_not_found",
                    message="UK replay skipped text-based op: target not found.",
                    op=op,
                    detail={"action": _action_name(op.action), "target": str(target)},
                )
