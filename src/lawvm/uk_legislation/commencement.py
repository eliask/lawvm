"""Commencement-aware matching helpers for UK replay."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional, Sequence

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import IRNode, IRStatute
from lawvm.uk_legislation.addressing import _uk_kind_value
from lawvm.uk_legislation.canonicalize import (
    uk_is_transparent_wrapper_kind,
    uk_should_bubble_structural_commencement,
)
from lawvm.uk_legislation.effects import _COMMENCEMENT_EFFECT_TYPES
from lawvm.uk_legislation.target_parser import (
    _parse_affected_target,
    _split_metadata_provisions,
)

if TYPE_CHECKING:
    from lawvm.uk_legislation.effects import UKEffectRecord


_UK_COMMENCEMENT_UNDATED_EFFECTS_RULE_ID = (
    "uk_commencement_undated_effects_block_self_commencement"
)
_UK_COMMENCEMENT_UNNUMBERED_SINGLE_SCHEDULE_RULE_ID = (
    "uk_commencement_unnumbered_single_schedule_target_resolved"
)


def _uk_commencement_diagnostic(
    *,
    rule_id: str,
    family: str,
    reason: str,
    **detail: Any,
) -> dict[str, Any]:
    return diagnostic_detail(
        rule_id=rule_id,
        family=family,
        phase="commencement_filter",
        reason=reason,
        blocking=False,
        detail=detail,
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
                    _uk_commencement_diagnostic(
                        rule_id=_UK_COMMENCEMENT_UNNUMBERED_SINGLE_SCHEDULE_RULE_ID,
                        family="target_resolution_recovery",
                        reason=(
                            "UK commencement metadata named an unnumbered schedule target; "
                            "the enacted source has exactly one schedule root."
                        ),
                        effect_id=effect.effect_id if effect is not None else "",
                        affecting_act_id=effect.affecting_act_id if effect is not None else "",
                        affected_provisions=effect.affected_provisions if effect is not None else source_ref,
                        affecting_provisions=effect.affecting_provisions if effect is not None else "",
                        effect_type=effect.effect_type if effect is not None else "",
                        source_ref=source_ref,
                    )
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


def commencement_eid_set(
    effects: list["UKEffectRecord"],
    statute_ir: IRStatute,
    *,
    applicability_mode: str = "effective_date_plus_feed_applied",
    observations_out: Optional[list[dict[str, Any]]] = None,
) -> set[str]:
    """Return the set of EIDs that have been brought into force.

    Parses "coming into force" effects, maps their provision references to
    nodes in *statute_ir*, and returns the union of EIDs for those nodes,
    their descendants, AND any structural ancestor nodes (part, chapter,
    crossheading) that contain at least one commenced provision.

    An EID is "commenced" when at least one replay-applicable commencement-like
    effect with a non-empty effective date covers the provision (or any
    ancestor).

    If no commencement effects are found at all, returns the full set of EIDs
    from the statute (treat all provisions as in force: self-commencement).
    If commencement-like rows exist but none has a replay-applicable date,
    returns an empty set and emits an observation instead of guessing.
    """
    commencement_like_effects = [
        e
        for e in effects
        if e.effect_type.lower() in _COMMENCEMENT_EFFECT_TYPES
    ]
    comm_effects = [
        e
        for e in commencement_like_effects
        if e.effective_date  # must have a real date
        and e.is_applicable_for_replay(applicability_mode=applicability_mode)
    ]

    all_ir_nodes: list[IRNode] = list(statute_ir.body.children)
    for sched in statute_ir.supplements:
        all_ir_nodes.append(sched)

    if not comm_effects:
        if commencement_like_effects:
            if observations_out is not None:
                observations_out.append(
                    _uk_commencement_diagnostic(
                        rule_id=_UK_COMMENCEMENT_UNDATED_EFFECTS_RULE_ID,
                        family="temporal_recovery",
                        reason=(
                            "UK source has commencement-style effect rows, but none "
                            "has a replay-applicable effective date; LawVM will not "
                            "silently treat the whole instrument as commenced."
                        ),
                        effect_count=len(commencement_like_effects),
                        effect_types=sorted(
                            {
                                (effect.effect_type or "").strip()
                                for effect in commencement_like_effects
                                if (effect.effect_type or "").strip()
                            }
                        ),
                    )
                )
            return set()
        # No commencement orders found: treat all provisions as in force.
        all_eids: set[str] = set()
        for node in all_ir_nodes:
            all_eids.update(_collect_all_eids(node))
        return all_eids

    # Collect directly commenced EIDs: section/schedule/paragraph nodes and descendants.
    commenced: set[str] = set()

    for effect in comm_effects:
        prov_str = effect.affected_provisions.strip()
        if not prov_str:
            continue

        prov_parts = _split_metadata_provisions(prov_str)
        for part in prov_parts:
            part = part.strip()
            if not part:
                continue

            addr = _parse_affected_target(part)

            if str(addr.special or "") == "whole_act":
                for node in all_ir_nodes:
                    commenced.update(_collect_all_eids(node))
                return commenced

            if not addr.path:
                continue

            matching = _nodes_matching_address(
                all_ir_nodes,
                addr.path,
                observations_out=observations_out,
                effect=effect,
                source_ref=part,
            )
            for node in matching:
                commenced.update(_collect_all_eids(node))

    # Bubble-up pass: add structural ancestors whose subtrees contain at least
    # one commenced EID. These structural EIDs appear in the oracle but are
    # never named in commencement orders.
    def _add_structural_ancestors(nodes: Sequence[IRNode]) -> bool:
        """Return True if any descendant is commenced. Side-effect: adds structural EIDs."""
        any_child_commenced = False
        for node in nodes:
            eid = node.attrs.get("eId") or node.attrs.get("id")
            if eid and eid in commenced:
                any_child_commenced = True
                continue
            sub_commenced = _add_structural_ancestors(node.children)
            if sub_commenced:
                any_child_commenced = True
                if uk_should_bubble_structural_commencement(node) and eid:
                    commenced.add(eid)
        return any_child_commenced

    _add_structural_ancestors(all_ir_nodes)

    return commenced
