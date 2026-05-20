"""Commencement-aware matching helpers for UK replay."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional, Sequence

from lawvm.core.ir import IRNode
from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.canonicalize import uk_is_transparent_wrapper_kind

if TYPE_CHECKING:
    from lawvm.uk_legislation.effects import UKEffectRecord


_UK_COMMENCEMENT_UNDATED_EFFECTS_RULE_ID = (
    "uk_commencement_undated_effects_block_self_commencement"
)
_UK_COMMENCEMENT_UNNUMBERED_SINGLE_SCHEDULE_RULE_ID = (
    "uk_commencement_unnumbered_single_schedule_target_resolved"
)

# Kind aliases used in LegalAddress paths that map to IR node kinds.
_ADDR_KIND_ALIASES: dict[str, set[str]] = {
    "section": {"section", "article", "rule", "regulation", "p1group"},
    "schedule": {"schedule"},
    "paragraph": {"paragraph", "p1", "p2", "p3", "subparagraph"},
    "subsection": {"subsection", "paragraph"},
    "part": {"part"},
    "chapter": {"chapter"},
}


def _normalize_commencement_match_label(kind: str, label: str) -> str:
    """Normalize UK source labels for commencement address matching only."""
    text = str(label or "").strip().replace("\u00a0", " ").lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("()")
    if kind == "schedule":
        text = re.sub(r"^schedule\s+", "", text)
    elif kind == "part":
        text = re.sub(r"^part\s+", "", text)
    elif kind == "chapter":
        text = re.sub(r"^chapter\s+", "", text)
    elif kind in {"section", "article", "rule", "regulation", "p1group"}:
        text = re.sub(r"^(?:section|article|rule|regulation|s\.)\s*", "", text)
    elif kind in {"paragraph", "p1", "p2", "p3", "subparagraph"}:
        text = re.sub(r"^(?:paragraph|para\.)\s*", "", text)
    text = text.strip("() ")
    return text.lstrip("0") or text


def _uk_commencement_container_descends_without_consuming(kind: str) -> bool:
    return kind in {"part", "chapter", "wrapper", "hcontainer"} or uk_is_transparent_wrapper_kind(kind)


def _collect_all_eids(node: IRNode) -> set[str]:
    """Recursively collect all eId/id attrs from a node and its descendants."""
    result: set[str] = set()
    eid = node.attrs.get("eId") or node.attrs.get("id")
    if eid:
        result.add(eid)
    for child in node.children:
        result.update(_collect_all_eids(child))
    return result


def _nodes_matching_address(
    nodes: Sequence[IRNode],
    path: tuple[tuple[str, str], ...],
    depth: int = 0,
    *,
    observations_out: Optional[list[dict[str, Any]]] = None,
    effect: Optional["UKEffectRecord"] = None,
    source_ref: str = "",
) -> list[IRNode]:
    """Walk an IR node list and return nodes that match the LegalAddress path.

    The path is a sequence of (kind, label) pairs from LegalAddress.path.
    Matching is hierarchical: each step drills into children of matched nodes.
    An empty path means "match all nodes at this level."

    Transparent kinds (part, chapter, crossheading, p1group, etc.) are
    descended into without consuming a path component, so "s. 1" matches
    a section nested arbitrarily deep under structural containers.
    """
    if depth >= len(path):
        # Consumed the whole path: return all nodes at this level.
        return list(nodes)

    addr_kind, addr_label = path[depth]
    accepted_ir_kinds = _ADDR_KIND_ALIASES.get(addr_kind, {addr_kind})
    # Remove transparent kinds from accepted set: a p1group with label=None
    # should never match an addr_label like '1'.
    non_transparent_accepted = {
        kind
        for kind in accepted_ir_kinds
        if kind not in {"part", "chapter", "wrapper", "hcontainer"} and not uk_is_transparent_wrapper_kind(kind)
    }
    unique_unnumbered_schedule: Optional[IRNode] = None
    if addr_kind == "schedule" and not str(addr_label or "").strip():
        schedule_candidates = [
            node
            for node in nodes
            if _uk_kind_value(node.kind) in non_transparent_accepted
        ]
        if len(schedule_candidates) == 1:
            unique_unnumbered_schedule = schedule_candidates[0]
            if observations_out is not None:
                observations_out.append(
                    {
                        "rule_id": _UK_COMMENCEMENT_UNNUMBERED_SINGLE_SCHEDULE_RULE_ID,
                        "family": "target_resolution_recovery",
                        "phase": "commencement_filter",
                        "effect_id": effect.effect_id if effect is not None else "",
                        "affecting_act_id": effect.affecting_act_id if effect is not None else "",
                        "affected_provisions": effect.affected_provisions if effect is not None else source_ref,
                        "affecting_provisions": effect.affecting_provisions if effect is not None else "",
                        "effect_type": effect.effect_type if effect is not None else "",
                        "reason": (
                            "UK commencement metadata named an unnumbered schedule target; "
                            "the enacted source has exactly one schedule root."
                        ),
                        "source_ref": source_ref,
                        "blocking": False,
                        "strict_disposition": "record",
                        "quirks_disposition": "record",
                    }
                )

    matched: list[IRNode] = []
    for node in nodes:
        node_kind = _uk_kind_value(node.kind)

        if node_kind not in non_transparent_accepted:
            # Commencement addresses often name sections beneath parts,
            # chapters, and crossheadings. Those containers are structural
            # context, not a reason to drop the named section.
            if _uk_commencement_container_descends_without_consuming(node_kind):
                matched.extend(
                    _nodes_matching_address(
                        node.children,
                        path,
                        depth,
                        observations_out=observations_out,
                        effect=effect,
                        source_ref=source_ref,
                    )
                )
            continue

        node_label = _normalize_commencement_match_label(node_kind, node.label or "")
        addr_lbl_norm = _normalize_commencement_match_label(node_kind, addr_label)

        node_matches = (
            node is unique_unnumbered_schedule
            if unique_unnumbered_schedule is not None
            else node_label == addr_lbl_norm
        )
        if not node_matches:
            if _uk_commencement_container_descends_without_consuming(node_kind):
                matched.extend(
                    _nodes_matching_address(
                        node.children,
                        path,
                        depth,
                        observations_out=observations_out,
                        effect=effect,
                        source_ref=source_ref,
                    )
                )
            continue

        # This node matches this path component: descend for the rest.
        if depth + 1 >= len(path):
            matched.append(node)
        else:
            sub = _nodes_matching_address(
                node.children,
                path,
                depth + 1,
                observations_out=observations_out,
                effect=effect,
                source_ref=source_ref,
            )
            if sub:
                matched.extend(sub)
            else:
                # Path extends beyond what this node has: still include the
                # parent node (commencing s. 1(2) when only s. 1 exists is fine).
                matched.append(node)

    return matched
