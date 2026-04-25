from __future__ import annotations

import pytest

from lawvm.core.elaboration_context import (
    PayloadElaborationContext,
    ReplayLookups,
    TargetContext,
    _make_subsection_slot,
)
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind


def test_target_context_rejects_subsection_slots_without_live_node() -> None:
    with pytest.raises(ValueError, match="subsection_slots"):
        TargetContext(
            target_unit_kind="section",
            target_norm="2",
            target_chapter=None,
            node_path=None,
            parent_path=(),
            live_node=None,
            parent_node=IRNode(kind=IRNodeKind.BODY, children=()),
            sibling_labels=(),
            subsection_slots=(
                _make_subsection_slot(1, IRNode(kind=IRNodeKind.SUBSECTION, label="1")),
            ),
        )


def test_payload_elaboration_context_rejects_live_indexes_without_live_node() -> None:
    lookups = ReplayLookups(
        snapshot_rev=1,
        unique_section_paths={},
        chapter_members={},
        part_members={},
        all_section_labels=frozenset(),
    )
    with pytest.raises(ValueError, match="live-derived indexes"):
        PayloadElaborationContext(
            target_unit_kind="section",
            target_norm="2",
            target_chapter=None,
            live_node=None,
            parent_node=None,
            subsection_slots=(),
            live_subsections=(),
            subsection_by_label={"1": IRNode(kind=IRNodeKind.SUBSECTION, label="1")},
            item_index={},
            row_anchor_index={},
            container_member_labels=None,
            lookups=lookups,
        )
