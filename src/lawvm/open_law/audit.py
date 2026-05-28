"""Replay and audit helpers for Open Law XML operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.mutation_boundary import TreePath, diff_ir_paths, unexplained_changed_paths
from lawvm.core.tree_ops import insert_sorted_required, replace_at_required, resolve_required
from lawvm.open_law.models import OpenLawAction, OpenLawFinding, OpenLawOperation


@dataclass(frozen=True)
class OpenLawAppliedMutation:
    """A mutation LawVM applied while auditing Open Law operations."""

    op_id: str
    action: OpenLawAction
    open_law_path: Tuple[str, ...]
    tree_path: TreePath


@dataclass(frozen=True)
class OpenLawReplayResult:
    """Result of replaying Open Law operations against one IR tree."""

    tree: IRNode
    mutations: Tuple[OpenLawAppliedMutation, ...]
    findings: Tuple[OpenLawFinding, ...]


@dataclass(frozen=True)
class OpenLawSnapshotAuditResult:
    """Replay-vs-publication audit over one before/after snapshot pair."""

    replay: OpenLawReplayResult
    snapshot_matches_replay: bool
    changed_paths: Tuple[TreePath, ...]
    unexplained_paths: Tuple[TreePath, ...]
    findings: Tuple[OpenLawFinding, ...]


def replay_open_law_ops(tree: IRNode, ops: Sequence[OpenLawOperation], *, strict: bool = False) -> OpenLawReplayResult:
    """Replay supported Open Law operations and emit audit findings for the rest."""

    current = tree
    mutations: list[OpenLawAppliedMutation] = []
    findings: list[OpenLawFinding] = []
    for op in ops:
        if op.diagnostics:
            findings.extend(op.diagnostics)
            if any(finding.blocking for finding in op.diagnostics):
                continue
        if op.action is OpenLawAction.REPLACE:
            current = _apply_replace(current, op, mutations, findings)
            continue
        if op.action is OpenLawAction.REPLACE_OR_INSERT:
            current = _apply_replace_or_insert(current, op, mutations, findings)
            continue
        if op.action is not OpenLawAction.REPLACE:
            findings.append(
                OpenLawFinding(
                    kind="open_law_unsupported_codify_action",
                    message=f"Open Law codify action is not supported by this frontend layer: {op.raw_action}",
                    op_id=op.op_id,
                    path=op.path,
                    blocking=strict,
                )
            )
            continue
    return OpenLawReplayResult(tree=current, mutations=tuple(mutations), findings=tuple(findings))


def _apply_replace(
    current: IRNode,
    op: OpenLawOperation,
    mutations: list[OpenLawAppliedMutation],
    findings: list[OpenLawFinding],
) -> IRNode:
    if op.payload is None:
        findings.append(
            OpenLawFinding(
                kind="open_law_replace_missing_payload",
                message="Open Law codify:replace has no structural payload.",
                op_id=op.op_id,
                path=op.path,
                blocking=True,
            )
        )
        return current
    resolved = resolve_open_law_path(current, op.path)
    if resolved.status != "resolved":
        findings.append(_target_finding(op, resolved))
        return current
    resolve_required(current, resolved.tree_path)
    mismatch = _payload_target_mismatch_finding(op, expected_key=resolved.tree_path[-1])
    if mismatch is not None:
        findings.append(mismatch)
        return current
    updated = replace_at_required(current, resolved.tree_path, op.payload)
    mutations.append(
        OpenLawAppliedMutation(
            op_id=op.op_id,
            action=op.action,
            open_law_path=op.path,
            tree_path=resolved.tree_path,
        )
    )
    return updated


def _apply_replace_or_insert(
    current: IRNode,
    op: OpenLawOperation,
    mutations: list[OpenLawAppliedMutation],
    findings: list[OpenLawFinding],
) -> IRNode:
    if op.payload is None:
        findings.append(
            OpenLawFinding(
                kind="open_law_replace_or_insert_missing_payload",
                message="Open Law codify:replace-or-insert has no structural payload.",
                op_id=op.op_id,
                path=op.path,
                blocking=True,
            )
        )
        return current
    target = resolve_open_law_path(current, op.path)
    if target.status == "resolved":
        findings.append(
            OpenLawFinding(
                kind="open_law_replace_or_insert_replaced_existing_target",
                message="Open Law codify:replace-or-insert resolved an existing target and replayed as replace.",
                op_id=op.op_id,
                path=op.path,
                blocking=False,
            )
        )
        return _apply_replace(current, op, mutations, findings)
    if target.status == "ambiguous":
        findings.append(_target_finding(op, target))
        return current
    parent = resolve_open_law_path(current, op.path[:-1])
    if parent.status != "resolved":
        findings.append(
            OpenLawFinding(
                kind=f"open_law_parent_{parent.status}",
                message=parent.message,
                op_id=op.op_id,
                path=op.path,
                blocking=True,
            )
        )
        return current
    mismatch = _payload_insert_target_mismatch_finding(op)
    if mismatch is not None:
        findings.append(mismatch)
        return current
    inserted_path = parent.tree_path + ((_kind_str(op.payload.kind), op.payload.label or ""),)
    updated = insert_sorted_required(current, parent.tree_path, op.payload)
    findings.append(
        OpenLawFinding(
            kind="open_law_replace_or_insert_inserted_missing_target",
            message="Open Law codify:replace-or-insert target was absent and replayed as insert under the explicit parent path.",
            op_id=op.op_id,
            path=op.path,
            blocking=False,
        )
    )
    mutations.append(
        OpenLawAppliedMutation(
            op_id=op.op_id,
            action=op.action,
            open_law_path=op.path,
            tree_path=inserted_path,
        )
    )
    return updated


def _payload_key(payload: IRNode) -> tuple[str, str]:
    return (_kind_str(payload.kind), payload.label or "")


def _payload_target_mismatch_finding(
    op: OpenLawOperation,
    *,
    expected_key: tuple[str, str],
) -> OpenLawFinding | None:
    if op.payload is None:
        return None
    actual_key = _payload_key(op.payload)
    if actual_key == expected_key:
        return None
    return OpenLawFinding(
        kind="open_law_payload_target_mismatch",
        message=(
            "Open Law codify payload identity does not match the declared target; "
            f"expected {expected_key[0]}:{expected_key[1]!r}, got {actual_key[0]}:{actual_key[1]!r}."
        ),
        op_id=op.op_id,
        path=op.path,
        blocking=True,
    )


def _payload_insert_target_mismatch_finding(op: OpenLawOperation) -> OpenLawFinding | None:
    if op.payload is None or not op.path:
        return None
    final_segment = op.path[-1]
    if final_segment == "heading":
        return _payload_target_mismatch_finding(op, expected_key=("heading", ""))
    if final_segment == "annos":
        return _payload_target_mismatch_finding(op, expected_key=("hcontainer", "annos"))
    actual_key = _payload_key(op.payload)
    if actual_key[1] == final_segment:
        return None
    return OpenLawFinding(
        kind="open_law_payload_target_mismatch",
        message=(
            "Open Law codify payload label does not match the declared insert target; "
            f"expected label {final_segment!r}, got {actual_key[0]}:{actual_key[1]!r}."
        ),
        op_id=op.op_id,
        path=op.path,
        blocking=True,
    )


def _target_finding(op: OpenLawOperation, resolved: "OpenLawResolvedPath") -> OpenLawFinding:
    return OpenLawFinding(
        kind=f"open_law_target_{resolved.status}",
        message=resolved.message,
        op_id=op.op_id,
        path=op.path,
        blocking=True,
    )


def audit_open_law_snapshot(
    before: IRNode,
    after: IRNode,
    ops: Sequence[OpenLawOperation],
    *,
    strict: bool = False,
) -> OpenLawSnapshotAuditResult:
    """Verify that a publication snapshot follows from declared Open Law ops."""

    replay = replay_open_law_ops(before, ops, strict=strict)
    annotation_projected_before = _project_annotations_for_snapshot_compare(before)
    annotation_projected_after = _project_annotations_for_snapshot_compare(after)
    annotation_projected_replay = _project_annotations_for_snapshot_compare(replay.tree)
    projected_before = _project_typography_for_snapshot_compare(annotation_projected_before)
    projected_after = _project_typography_for_snapshot_compare(annotation_projected_after)
    projected_replay = _project_typography_for_snapshot_compare(annotation_projected_replay)
    changed_paths = diff_ir_paths(projected_before, projected_after)
    allowed_prefixes = tuple(mutation.tree_path for mutation in replay.mutations)
    unexplained_paths = unexplained_changed_paths(changed_paths, allowed_prefixes)
    findings = list(replay.findings)
    if (
        annotation_projected_before != before
        or annotation_projected_after != after
        or annotation_projected_replay != replay.tree
    ):
        findings.append(
            OpenLawFinding(
                kind="open_law_snapshot_annotation_projection",
                message="Open Law annotations were projected out for body-text snapshot comparison.",
                blocking=strict,
            )
        )
    if (
        projected_before != annotation_projected_before
        or projected_after != annotation_projected_after
        or projected_replay != annotation_projected_replay
    ):
        findings.append(
            OpenLawFinding(
                kind="open_law_snapshot_typography_projection",
                message="Straight and curly quotation marks were normalized for presentation-layer snapshot comparison.",
                blocking=strict,
            )
        )
    if projected_replay != projected_after:
        findings.append(
            OpenLawFinding(
                kind="open_law_publication_snapshot_mismatch",
                message="Open Law publication snapshot does not equal LawVM replay of declared codify operations.",
                blocking=True,
            )
        )
    if unexplained_paths:
        findings.append(
            OpenLawFinding(
                kind="open_law_unexplained_publication_mutation",
                message="Publication snapshot changed paths outside declared codify operation target regions.",
                blocking=True,
            )
        )
    return OpenLawSnapshotAuditResult(
        replay=replay,
        snapshot_matches_replay=projected_replay == projected_after,
        changed_paths=changed_paths,
        unexplained_paths=unexplained_paths,
        findings=tuple(findings),
    )


def _project_annotations_for_snapshot_compare(node: IRNode) -> IRNode:
    children = tuple(
        projected
        for child in node.children
        for projected in (_project_annotation_child_for_snapshot_compare(child),)
        if projected is not None
    )
    if children == node.children:
        return node
    return IRNode(kind=node.kind, label=node.label, text=node.text, attrs=dict(node.attrs), children=children)


def _project_annotation_child_for_snapshot_compare(node: IRNode) -> IRNode | None:
    if _is_annotations_node(node):
        return None
    return _project_annotations_for_snapshot_compare(node)


_TYPOGRAPHY_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def _project_typography_for_snapshot_compare(node: IRNode) -> IRNode:
    text = node.text.translate(_TYPOGRAPHY_TRANSLATION)
    children = tuple(_project_typography_for_snapshot_compare(child) for child in node.children)
    if text == node.text and children == node.children:
        return node
    return IRNode(kind=node.kind, label=node.label, text=text, attrs=dict(node.attrs), children=children)


def _is_annotations_node(node: IRNode) -> bool:
    return _kind_str(node.kind) == "hcontainer" and node.label == "annos"


@dataclass(frozen=True)
class OpenLawResolvedPath:
    """Result of resolving an Open Law pipe-delimited path against an IR tree."""

    status: str
    tree_path: TreePath = ()
    message: str = ""


def resolve_open_law_path(tree: IRNode, open_law_path: Sequence[str]) -> OpenLawResolvedPath:
    """Resolve ``10|41|02|.04``-style Open Law paths by direct child labels.

    This deliberately does not broaden search across the tree. If a path segment
    is absent or ambiguous at its current parent, the caller receives a finding
    instead of a guessed target.
    """

    current = tree
    tree_path: list[tuple[str, str]] = []
    for segment in open_law_path:
        matches = _segment_matches(current, segment)
        if not matches:
            return OpenLawResolvedPath(
                status="missing",
                message=f"Open Law path segment {segment!r} was not found under {tuple(open_law_path)!r}.",
            )
        if len(matches) > 1:
            return OpenLawResolvedPath(
                status="ambiguous",
                message=f"Open Law path segment {segment!r} matched {len(matches)} siblings under {tuple(open_law_path)!r}.",
            )
        child = matches[0]
        tree_path.append((_kind_str(child.kind), child.label or ""))
        current = child
    return OpenLawResolvedPath(status="resolved", tree_path=tuple(tree_path))


def _segment_matches(current: IRNode, segment: str) -> Tuple[IRNode, ...]:
    if segment == "heading":
        return tuple(child for child in current.children if _kind_str(child.kind) == "heading")
    if segment == "annos":
        return tuple(
            child
            for child in current.children
            if _kind_str(child.kind) == "hcontainer" and child.label == "annos"
        )
    return tuple(child for child in current.children if child.label == segment)
