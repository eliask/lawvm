from __future__ import annotations

import json
from typing import Any

from lawvm.semantic.align import align_semantic_trees
from lawvm.semantic.diff import semantic_diff, semantic_diff_events
from lawvm.semantic.normalize_structure import normalize_structure_for_viewer


SEMANTIC_CONTRACT_VERSION = "semantic-v1"


_ORACLE_ANNOTATABLE_KINDS = frozenset({
    "unit_missing_right",
    "unit_missing_left",
    "wording_text_changed",
    "heading_text_changed",
    "intro_text_changed",
    "facet_added",
    "facet_removed",
})


def _annotate_events_with_oracle_diagnosis(
    events: tuple,
    diagnosis: str,
) -> tuple:
    """Stamp oracle_diagnosis onto events where it adds classification value."""
    from lawvm.semantic.model import SemanticDiffEvent
    return tuple(
        SemanticDiffEvent(
            kind=e.kind,
            semantic_path=e.semantic_path,
            match_basis=e.match_basis,
            unit_kind=e.unit_kind,
            unit_label=e.unit_label,
            facet_kind=e.facet_kind,
            left_text=e.left_text,
            right_text=e.right_text,
            left_badge=e.left_badge,
            right_badge=e.right_badge,
            oracle_diagnosis=diagnosis,
        )
        if e.kind in _ORACLE_ANNOTATABLE_KINDS and not e.oracle_diagnosis
        else e
        for e in events
    )


def build_semantic_support(
    replay_semantic: Any,
    oracle_semantic: Any,
    *,
    section_oracle_diagnosis: str = "",
) -> dict[str, Any]:
    item: dict[str, Any] = {"semantic_contract_version": SEMANTIC_CONTRACT_VERSION}
    replay_structure = replay_semantic.to_dict() if replay_semantic is not None else None
    oracle_structure = oracle_semantic.to_dict() if oracle_semantic is not None else None
    if replay_structure is not None:
        item["replay"] = replay_structure
    if oracle_structure is not None:
        item["oracle"] = oracle_structure
    if replay_semantic is not None or oracle_semantic is not None:
        aligned = align_semantic_trees(replay_semantic, oracle_semantic)
        diff = semantic_diff(replay_semantic, oracle_semantic)
        events = semantic_diff_events(replay_semantic, oracle_semantic)
        if section_oracle_diagnosis:
            events = _annotate_events_with_oracle_diagnosis(events, section_oracle_diagnosis)
        if aligned is not None:
            item["aligned"] = aligned.to_dict()
        item["semantic_diff"] = {
            "kind": diff.kind,
            "summary": diff.summary,
            "structural": diff.stats.structural,
            "label": diff.stats.label,
            "text": diff.stats.text,
            "editorial": diff.stats.editorial,
            "events": [event.to_dict() for event in events],
        }
    return item


def semantic_support_projection(support: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "semantic_contract_version": support.get("semantic_contract_version"),
        "oracle_structure": (
            json.dumps(
                normalize_structure_for_viewer(support["oracle"]),
                ensure_ascii=False,
            )
            if "oracle" in support
            else None
        ),
        "replay_structure": (
            json.dumps(
                normalize_structure_for_viewer(support["replay"]),
                ensure_ascii=False,
            )
            if "replay" in support
            else None
        ),
        "aligned_structure": (
            json.dumps(support["aligned"], ensure_ascii=False)
            if "aligned" in support
            else None
        ),
    }
    semantic_diff_payload = support.get("semantic_diff") if isinstance(support, dict) else None
    if isinstance(semantic_diff_payload, dict):
        projection["structure_diff_kind"] = semantic_diff_payload.get("kind")
        projection["structure_diff_summary"] = semantic_diff_payload.get("summary")
        projection["structure_diff_structural"] = semantic_diff_payload.get("structural")
        projection["structure_diff_label"] = semantic_diff_payload.get("label")
        projection["structure_diff_text"] = semantic_diff_payload.get("text")
        projection["structure_diff_events"] = json.dumps(
            semantic_diff_payload.get("events", []),
            ensure_ascii=False,
        )
    return projection
