"""Shared lowered-operation assembly helpers for UK effects."""

from __future__ import annotations

import json
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.heading_facets import _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE
from lawvm.uk_legislation.provenance_notes import (
    NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR as _NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR,
    NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION as _NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION,
    NOTE_TABLE_CELL_SELECTOR as _NOTE_TABLE_CELL_SELECTOR,
)
from lawvm.uk_legislation.source_parent_payloads import (
    UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
)
from lawvm.uk_legislation.source_payload_helpers import (
    UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID as _UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID,
)
from lawvm.uk_legislation.substitution_metadata import (
    UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID as _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
    UKSourceLabelChangingSubstitution,
)
from lawvm.uk_legislation.witness_sidecars import _uk_lowered_op_provenance_tags
from lawvm.uk_legislation.witnesses import UKLoweredOperationWitness


def build_lowered_operation_provenance(
    *,
    lowered_witness: UKLoweredOperationWitness,
    table_cell_selector: Optional[dict[str, Any]],
    crossheading_group_repeal_selector: Optional[dict[str, Any]],
    curr_action: str,
    target: LegalAddress,
    label_changing_substitution: Optional[UKSourceLabelChangingSubstitution],
    flat_p1para_schedule_insert_lowered: bool,
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
    source_parent_at_end_added_payload: Optional[dict[str, Any]],
    target_index: int,
) -> tuple[tuple[str, ...], Optional[str]]:
    provenance_tags = _uk_lowered_op_provenance_tags(lowered_witness)
    if table_cell_selector is not None:
        provenance_tags = (
            *provenance_tags,
            f"{_NOTE_TABLE_CELL_SELECTOR}{json.dumps(table_cell_selector, ensure_ascii=False)}",
        )
    op_witness_rule_id = None
    if crossheading_group_repeal_selector is not None and curr_action == "repeal":
        op_witness_rule_id = _CROSSHEADING_AND_STRUCTURAL_REPEAL_RULE
        provenance_tags = (
            *provenance_tags,
            (
                f"{_NOTE_CROSSHEADING_GROUP_REPEAL_SELECTOR}"
                f"{json.dumps(crossheading_group_repeal_selector, ensure_ascii=False)}"
            ),
        )
    if (
        label_changing_substitution is not None
        and curr_action == "replace"
        and tuple(target.path) == tuple(label_changing_substitution.source_target.path)
    ):
        op_witness_rule_id = _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID
        label_change_note = {
            "rule_id": _UK_SOURCE_LABEL_CHANGING_SUBSTITUTION_RULE_ID,
            "source_target": str(label_changing_substitution.source_target),
            "replacement_target": str(label_changing_substitution.replacement_target),
            "source_ref": label_changing_substitution.source_ref,
            "replacement_ref": label_changing_substitution.replacement_ref,
        }
        provenance_tags = (
            *provenance_tags,
            (
                f"{_NOTE_SOURCE_LABEL_CHANGE_SUBSTITUTION}"
                f"{json.dumps(label_change_note, ensure_ascii=False)}"
            ),
        )
    if flat_p1para_schedule_insert_lowered and curr_action == "insert":
        op_witness_rule_id = _UK_FLAT_P1PARA_SCHEDULE_PARAGRAPH_INSERT_RULE_ID
    if (
        source_parent_substitution_range_payload is not None
        and curr_action == "replace"
        and target_index < len(source_parent_substitution_range_payload["payload_labels"])
    ):
        op_witness_rule_id = _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
    if source_parent_at_end_added_payload is not None and curr_action == "insert":
        op_witness_rule_id = _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID
    return provenance_tags, op_witness_rule_id
