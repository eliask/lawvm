"""Replay and audit helpers for Open Law XML operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

from lawvm.core.comparison_normalization import ComparisonNormalizationRule, project_ir_comparison_text
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.mutation_boundary import TreePath, TreePaths, TreePathStep, build_mutation_boundary_report
from lawvm.core.tree_ops import insert_sorted_required, replace_at_required, resolve_required
from lawvm.open_law.models import (
    OpenLawAction,
    OpenLawAnnotationLane,
    OpenLawFinding,
    OpenLawLifecycleTombstone,
    OpenLawOperation,
)


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
    tombstones: Tuple[OpenLawLifecycleTombstone, ...] = ()


@dataclass(frozen=True)
class OpenLawSnapshotAuditResult:
    """Replay-vs-publication audit over one before/after snapshot pair."""

    replay: OpenLawReplayResult
    snapshot_matches_replay: bool
    changed_paths: TreePaths
    unexplained_paths: TreePaths
    findings: Tuple[OpenLawFinding, ...]


def failed_codification_findings(
    findings: Sequence[OpenLawFinding],
) -> Tuple[OpenLawFinding, ...]:
    """Umbrella ``open_law_failed_codification_source_bug`` findings.

    Open Law is a cooperative structured-source regime: the publisher receives a
    compile-time error when a codification instruction fails, so a failed
    instruction that survives into published data is a SOURCE bug, never a
    replay-side recovery target (regime contract §3). Every blocking
    ``source_pathology`` finding is a distinct, already-typed failure witness
    (missing/ambiguous target, payload/target identity mismatch, missing or
    multiple/unsupported payload, unplannable locator). This function attaches a
    single named umbrella finding per such failure so the source-bug lane is
    directly queryable by rule id, without dropping the specific witness and
    without ever broadening a target to "rescue" the instruction.
    """

    out: list[OpenLawFinding] = []
    for finding in findings:
        if finding.kind == "open_law_failed_codification_source_bug":
            continue
        if finding.source_pathology and finding.blocking:
            out.append(
                OpenLawFinding(
                    kind="open_law_failed_codification_source_bug",
                    message=(
                        "Open Law codification instruction failed to apply "
                        f"({finding.kind}); in this cooperative regime a failed "
                        "codification is a source bug in the published artifact, "
                        "not a replay-side recovery target."
                    ),
                    op_id=finding.op_id,
                    path=finding.path,
                    blocking=finding.blocking,
                    source_pathology=True,
                )
            )
    return tuple(out)


def replay_open_law_ops(tree: IRNode, ops: Sequence[OpenLawOperation], *, strict: bool = False) -> OpenLawReplayResult:
    """Replay supported Open Law operations and emit audit findings for the rest."""

    current = tree
    mutations: list[OpenLawAppliedMutation] = []
    findings: list[OpenLawFinding] = []
    tombstones: list[OpenLawLifecycleTombstone] = []
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
        if op.action is OpenLawAction.EXPIRE:
            tombstone, finding = execute_open_law_expiry(op)
            tombstones.append(tombstone)
            findings.append(finding)
            continue
        if op.action is OpenLawAction.UNSUPPORTED:
            findings.append(
                OpenLawFinding(
                    kind="open_law_unknown_codify_action",
                    message=(
                        "Open Law codify:* is a stable operation language; "
                        f"action {op.raw_action!r} is not a recognized codify verb and is treated as a finding, not a silent skip."
                    ),
                    op_id=op.op_id,
                    path=op.path,
                    blocking=strict,
                )
            )
            continue
        findings.append(
            OpenLawFinding(
                kind="open_law_unsupported_codify_action",
                message=f"Open Law codify action is recognized but not yet replayed by this frontend layer: {op.raw_action}",
                op_id=op.op_id,
                path=op.path,
                blocking=strict,
            )
        )
    return OpenLawReplayResult(
        tree=current,
        mutations=tuple(mutations),
        findings=tuple(findings),
        tombstones=tuple(tombstones),
    )


def execute_open_law_expiry(op: OpenLawOperation) -> Tuple[OpenLawLifecycleTombstone, OpenLawFinding]:
    """Execute a ``codify:expire`` op into a typed jurisdiction tombstone.

    Expiry is a lifecycle operation whose deletion semantics are
    jurisdiction-dependent (regime contract §5.1). Rather than leaving it as an
    unexecuted ``open_law_expire_lifecycle_not_replayed`` gap, this produces an
    owned ``OpenLawLifecycleTombstone`` marking the declared target expired at
    ``op.expire_date`` and a non-blocking ``open_law_expire_tombstoned`` finding
    recording the replayed lifecycle result. The Maryland tombstone is a
    standalone lifecycle marker (the expire targets are Register
    emergency/proposed-regulation identifiers, not persistent COMAR chapter
    nodes); it never silently deletes unrelated tree state.
    """

    tombstone = OpenLawLifecycleTombstone(
        op_id=op.op_id,
        doc=op.doc,
        open_law_path=op.path,
        expire_date=op.expire_date,
        history=op.history,
    )
    finding = OpenLawFinding(
        kind="open_law_expire_tombstoned",
        message=(
            "Open Law codify:expire replayed as a jurisdiction tombstone; target "
            f"{'|'.join(op.path) or '-'} marked expired at {op.expire_date or '-'}."
        ),
        op_id=op.op_id,
        path=op.path,
        blocking=False,
    )
    return tombstone, finding


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
                source_pathology=True,
            )
        )
        return current
    resolved = resolve_open_law_path(current, op.path)
    if resolved.path_status != "resolved":
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
                source_pathology=True,
            )
        )
        return current
    target = resolve_open_law_path(current, op.path)
    if target.path_status == "resolved":
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
    if target.path_status == "ambiguous":
        findings.append(_target_finding(op, target))
        return current
    parent = resolve_open_law_path(current, op.path[:-1])
    if parent.path_status != "resolved":
        findings.append(
            OpenLawFinding(
                kind=f"open_law_parent_{parent.path_status}",
                message=parent.message,
                op_id=op.op_id,
                path=op.path,
                blocking=True,
                source_pathology=True,
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
        source_pathology=True,
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
        source_pathology=True,
    )


def _target_finding(op: OpenLawOperation, resolved: "OpenLawResolvedPath") -> OpenLawFinding:
    return OpenLawFinding(
        kind=f"open_law_target_{resolved.path_status}",
        message=resolved.message,
        op_id=op.op_id,
        path=op.path,
        blocking=True,
        source_pathology=True,
    )


def audit_open_law_snapshot(
    before: IRNode,
    after: IRNode,
    ops: Sequence[OpenLawOperation],
    *,
    strict: bool = False,
    annotation_lane: OpenLawAnnotationLane | None = None,
) -> OpenLawSnapshotAuditResult:
    """Verify that a publication snapshot follows from declared Open Law ops.

    ``annotation_lane`` is the per-jurisdiction annotation policy. When it is
    ``PUBLICATION_METADATA`` annotations are projected out of the legal-text
    comparison. When it is ``OFFICIAL_CODE`` annotations are compared as legal
    text. When unset (``None``) the conservative default applies: annotations
    are compared as potentially-authoritative text and a finding records that
    the jurisdiction policy is unset.
    """

    replay = replay_open_law_ops(before, ops, strict=strict)
    findings = list(replay.findings)
    project_annotations = annotation_lane is OpenLawAnnotationLane.PUBLICATION_METADATA
    if project_annotations:
        annotation_projected_before = _project_annotations_for_snapshot_compare(before)
        annotation_projected_after = _project_annotations_for_snapshot_compare(after)
        annotation_projected_replay = _project_annotations_for_snapshot_compare(replay.tree)
    else:
        annotation_projected_before = before
        annotation_projected_after = after
        annotation_projected_replay = replay.tree
    if annotation_lane is None and (
        _tree_has_annotations(before) or _tree_has_annotations(after) or _tree_has_annotations(replay.tree)
    ):
        findings.append(
            OpenLawFinding(
                kind="open_law_annotation_lane_policy_unset",
                message=(
                    "Open Law annotation lane policy is unset; annotations are jurisdiction-dependent "
                    "(official code vs publication metadata). Conservatively comparing annotations as "
                    "potentially-authoritative legal text instead of discarding them."
                ),
                blocking=strict,
            )
        )
    projected_before = _project_typography_for_snapshot_compare(annotation_projected_before)
    projected_after = _project_typography_for_snapshot_compare(annotation_projected_after)
    projected_replay = _project_typography_for_snapshot_compare(annotation_projected_replay)
    allowed_prefixes = tuple(mutation.tree_path for mutation in replay.mutations)
    boundary = build_mutation_boundary_report(projected_before, projected_after, allowed_prefixes)
    changed_paths = boundary.changed_paths
    unexplained_paths = boundary.unexplained_changed_paths
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


_TYPOGRAPHY_COMPARISON_RULES = (
    ComparisonNormalizationRule(
        name="open_law_quote_typography",
        rule_class="presentation_cleanup",
        kind="translation",
        description="Normalize curly and straight quotation marks for Open Law snapshot comparison.",
        translation=str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
            }
        ),
    ),
)


def _project_typography_for_snapshot_compare(node: IRNode) -> IRNode:
    return project_ir_comparison_text(node, _TYPOGRAPHY_COMPARISON_RULES)


def _is_annotations_node(node: IRNode) -> bool:
    return _kind_str(node.kind) == "hcontainer" and node.label == "annos"


def _tree_has_annotations(node: IRNode) -> bool:
    if _is_annotations_node(node):
        return True
    return any(_tree_has_annotations(child) for child in node.children)


@dataclass(frozen=True)
class OpenLawResolvedPath:
    """Result of resolving an Open Law pipe-delimited path against an IR tree."""

    path_status: str
    tree_path: TreePath = ()
    message: str = ""


def resolve_open_law_path(tree: IRNode, open_law_path: Sequence[str]) -> OpenLawResolvedPath:
    """Resolve ``10|41|02|.04``-style Open Law paths by direct child labels.

    This deliberately does not broaden search across the tree. If a path segment
    is absent or ambiguous at its current parent, the caller receives a finding
    instead of a guessed target.
    """

    current = tree
    tree_path: list[TreePathStep] = []
    for segment in open_law_path:
        matches = _segment_matches(current, segment)
        if not matches:
            return OpenLawResolvedPath(
                path_status="missing",
                message=f"Open Law path segment {segment!r} was not found under {tuple(open_law_path)!r}.",
            )
        if len(matches) > 1:
            return OpenLawResolvedPath(
                path_status="ambiguous",
                message=f"Open Law path segment {segment!r} matched {len(matches)} siblings under {tuple(open_law_path)!r}.",
            )
        child = matches[0]
        tree_path.append((_kind_str(child.kind), child.label or ""))
        current = child
    return OpenLawResolvedPath(path_status="resolved", tree_path=tuple(tree_path))


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
