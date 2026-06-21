"""Point-in-time projection helpers for CLI comparison/read surfaces."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from typing import Any, Literal, Mapping

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline_selection import content_is_repeal_placeholder, select_active_version_ex


def _node_path_key(node: IRNode) -> tuple[str, str] | None:
    if not node.label:
        return None
    kind = node.kind.value if isinstance(node.kind, IRNodeKind) else str(node.kind)
    return (kind, node.label)


def _projection_as_of(master: Any, explicit_as_of: str = "") -> str:
    if explicit_as_of:
        return explicit_as_of
    products = getattr(master, "products", None)
    spec = getattr(products, "materialization_spec", None)
    as_of = getattr(spec, "as_of", "") if spec is not None else ""
    return str(as_of or "")


def _prune_inactive_sections(
    node: IRNode,
    *,
    timelines: Mapping[LegalAddress, ProvisionTimeline],
    as_of: str,
    query_type: Literal["governing", "in_force"],
    path: tuple[tuple[str, str], ...] = (),
) -> IRNode | None:
    path_key = _node_path_key(node)
    current_path = path + (path_key,) if path_key is not None else path
    if node.kind is IRNodeKind.SECTION and current_path:
        timeline = timelines.get(LegalAddress(path=current_path))
        if timeline is not None:
            selection = select_active_version_ex(
                timeline,
                as_of=as_of,
                query_type=query_type,
            )
            version = selection.version
            if (
                version is None
                or version.content is None
                or content_is_repeal_placeholder(version.content)
            ):
                return None

    changed = False
    children: list[IRNode] = []
    for child in node.children:
        projected = _prune_inactive_sections(
            child,
            timelines=timelines,
            as_of=as_of,
            query_type=query_type,
            path=current_path,
        )
        if projected is None:
            changed = True
            continue
        if projected is not child:
            changed = True
        children.append(projected)
    return dc_replace(node, children=tuple(children)) if changed else node


def comparison_ir_for_pit(
    master: Any,
    *,
    as_of: str = "",
    query_type: Literal["governing", "in_force"] = "in_force",
) -> IRNode:
    """Return the IR that comparison/read surfaces should expose for a PIT query.

    Replay products may carry governing/context structure in ``materialized_state``.
    CLI comparison surfaces compare the in-force text-state, so sections whose own
    timelines are inactive at the selected PIT are pruned from that projected IR.
    """

    materialized_state = getattr(master, "materialized_state", None)
    materialized_ir = getattr(materialized_state, "ir", None) if materialized_state is not None else None
    replay_ir = materialized_ir if materialized_ir is not None else master.ir
    projection_as_of = _projection_as_of(master, as_of)
    timelines = getattr(master, "timelines", None)
    if not projection_as_of or not isinstance(timelines, Mapping):
        return replay_ir
    return _prune_inactive_sections(
        replay_ir,
        timelines=timelines,
        as_of=projection_as_of,
        query_type=query_type,
    ) or replay_ir
