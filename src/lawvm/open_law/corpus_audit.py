"""Corpus replay audit for public Open Law Maryland XML."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow, CorpusOperationEvidenceRow, CorpusRowStatus
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.tree_ops import resolve_required
from lawvm.open_law.audit import (
    OpenLawSnapshotAuditResult,
    TreePath,
    audit_open_law_snapshot,
    diff_ir_paths,
    replay_open_law_ops,
    resolve_open_law_path,
)
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.maryland import (
    build_maryland_inventory,
    maryland_manifest_to_jsonable,
    plan_maryland_publication_transitions,
)
from lawvm.open_law.local_git import MarylandLocalRepos
from lawvm.open_law.models import OpenLawAction, OpenLawFinding, OpenLawOperation
from lawvm.open_law.planner import OpenLawFilePlan, plan_maryland_comar_operation
from lawvm.open_law.xml import parse_open_law_xml, wrap_open_law_body_with_prefix


@dataclass(frozen=True)
class OpenLawOperationAuditRow:
    """Audit result for one Open Law operation in one publication transition."""

    before_branch: str
    after_branch: str
    action_path: str
    op_id: str
    action: str
    codify_path: Tuple[str, ...]
    xml_path: str
    status: str
    expire_date: str = ""
    snapshot_matches_replay: bool = False
    changed_path_count: int = 0
    unexplained_path_count: int = 0
    findings: Tuple[OpenLawFinding, ...] = ()


@dataclass(frozen=True)
class OpenLawCorpusAuditReport:
    """Full corpus audit report."""

    operation_rows: Tuple[OpenLawOperationAuditRow, ...]
    summary: dict[str, int]


def audit_maryland_transition(
    before_branch: str,
    after_branch: str,
    *,
    repos: MarylandLocalRepos,
    limit: int | None = None,
    strict: bool = False,
) -> OpenLawCorpusAuditReport:
    """Audit one Maryland publication transition by after-branch included actions."""

    inventory = build_maryland_inventory(repos)
    metadata_by_branch = {item.branch: item for item in inventory.publication_branches}
    before_metadata = metadata_by_branch[before_branch]
    after_metadata = metadata_by_branch[after_branch]
    rows: list[OpenLawOperationAuditRow] = []
    before_actions = set(before_metadata.included_editorial_actions)
    transition_actions = tuple(path for path in after_metadata.included_editorial_actions if path not in before_actions)
    for action_path in transition_actions:
        action_xml = repos.codified.read_text(after_branch, action_path)
        ops = parse_open_law_codify_ops(action_xml, source_id=action_path)
        rows.extend(_audit_action_operations(repos, before_branch, after_branch, action_path, ops, strict=strict))
        if limit is not None and len(rows) >= limit:
            return _report(tuple(rows[:limit]))
    return _report(tuple(rows))


def audit_maryland_corpus(
    *,
    repos: MarylandLocalRepos,
    limit: int | None = None,
    strict: bool = False,
) -> OpenLawCorpusAuditReport:
    """Audit adjacent publication transitions that introduce new actions."""

    inventory = build_maryland_inventory(repos)
    rows: list[OpenLawOperationAuditRow] = []
    for transition in plan_maryland_publication_transitions(inventory):
        report = audit_maryland_transition(
            transition.before_branch,
            transition.after_branch,
            repos=repos,
            limit=None,
            strict=strict,
        )
        rows.extend(report.operation_rows)
        if limit is not None and len(rows) >= limit:
            return _report(tuple(rows[:limit]))
    return _report(tuple(rows))


def write_corpus_report(report: OpenLawCorpusAuditReport, out_dir: Path) -> None:
    """Write JSON/JSONL corpus audit artifacts."""

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(report.summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_dir / "operation_audits.jsonl").open("w", encoding="utf-8") as handle:
        for row in report.operation_rows:
            handle.write(json.dumps(_row_jsonable(row), ensure_ascii=False) + "\n")
    with (out_dir / "findings.jsonl").open("w", encoding="utf-8") as handle:
        for row in report.operation_rows:
            for finding in row.findings:
                handle.write(
                    json.dumps(
                        {
                            "before_branch": row.before_branch,
                            "after_branch": row.after_branch,
                            "action_path": row.action_path,
                            "op_id": row.op_id,
                            "kind": finding.kind,
                            "message": finding.message,
                            "path": list(finding.path),
                            "blocking": finding.blocking,
                            "evidence_row": _finding_evidence_row(row, finding).to_dict(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )


def write_inventory(out_dir: Path, *, repos: MarylandLocalRepos) -> None:
    inventory = build_maryland_inventory(repos)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(maryland_manifest_to_jsonable(inventory, repos=repos), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _audit_one_operation(
    repos: MarylandLocalRepos,
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
    *,
    strict: bool,
) -> OpenLawOperationAuditRow:
    plan = plan_maryland_comar_operation(op)
    if op.action is OpenLawAction.EXPIRE:
        return _lifecycle_lane_row(before_branch, after_branch, action_path, op)
    if plan.status != "planned":
        return _finding_row(before_branch, after_branch, action_path, op, plan)
    if op.path and op.path[-1] == "annos":
        return _audit_metadata_operation(repos, before_branch, after_branch, action_path, op, plan, strict=strict)
    before_xml = _read_snapshot_xml(repos, before_branch, plan.xml_path, before_branch, after_branch, action_path, op)
    if isinstance(before_xml, OpenLawOperationAuditRow):
        return before_xml
    after_xml = _read_snapshot_xml(repos, after_branch, plan.xml_path, before_branch, after_branch, action_path, op)
    if isinstance(after_xml, OpenLawOperationAuditRow):
        return after_xml
    result = _audit_snapshots(before_xml, after_xml, (op,), plan, strict=strict)
    return _audited_row(before_branch, after_branch, action_path, op, plan, result)


def _audit_action_operations(
    repos: MarylandLocalRepos,
    before_branch: str,
    after_branch: str,
    action_path: str,
    ops: Tuple[OpenLawOperation, ...],
    *,
    strict: bool,
) -> Tuple[OpenLawOperationAuditRow, ...]:
    rows: list[OpenLawOperationAuditRow] = []
    grouped_ops: dict[tuple[str, Tuple[str, ...]], list[tuple[OpenLawOperation, OpenLawFilePlan]]] = {}
    for op in ops:
        if op.action is OpenLawAction.EXPIRE:
            rows.append(_lifecycle_lane_row(before_branch, after_branch, action_path, op))
            continue
        plan = plan_maryland_comar_operation(op)
        if plan.status != "planned":
            rows.append(_finding_row(before_branch, after_branch, action_path, op, plan))
            continue
        grouped_ops.setdefault((plan.xml_path, plan.path_prefix), []).append((op, plan))

    for (xml_path, _path_prefix), planned_ops in grouped_ops.items():
        representative_op, representative_plan = planned_ops[0]
        before_xml = _read_snapshot_xml(repos, before_branch, xml_path, before_branch, after_branch, action_path, representative_op)
        if isinstance(before_xml, OpenLawOperationAuditRow):
            rows.extend(_snapshot_failure_rows(before_xml, planned_ops))
            continue
        after_xml = _read_snapshot_xml(repos, after_branch, xml_path, before_branch, after_branch, action_path, representative_op)
        if isinstance(after_xml, OpenLawOperationAuditRow):
            rows.extend(_snapshot_failure_rows(after_xml, planned_ops))
            continue
        body_planned_ops = tuple((op, plan) for op, plan in planned_ops if not (op.path and op.path[-1] == "annos"))
        metadata_planned_ops = tuple((op, plan) for op, plan in planned_ops if op.path and op.path[-1] == "annos")
        if body_planned_ops:
            result = _audit_snapshots(
                before_xml,
                after_xml,
                tuple(op for op, _plan in body_planned_ops),
                representative_plan,
                strict=strict,
            )
        for op, plan in body_planned_ops:
            rows.append(_audited_row(before_branch, after_branch, action_path, op, plan, result))
        companion_ops = tuple(op for op, _plan in planned_ops)
        for op, plan in metadata_planned_ops:
            rows.append(
                _audit_metadata_operation(
                    repos,
                    before_branch,
                    after_branch,
                    action_path,
                    op,
                    plan,
                    companion_ops=companion_ops,
                    strict=strict,
                )
            )
    return tuple(rows)


def _read_snapshot_xml(
    repos: MarylandLocalRepos,
    branch: str,
    xml_path: str,
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
) -> str | OpenLawOperationAuditRow:
    try:
        return repos.codified.read_text(branch, xml_path)
    except subprocess.CalledProcessError as exc:
        finding = OpenLawFinding(
            kind="open_law_snapshot_file_missing",
            message=f"Could not read {xml_path!r} from {branch!r}: git exited {exc.returncode}.",
            op_id=op.op_id,
            path=op.path,
            blocking=True,
        )
        return OpenLawOperationAuditRow(
            before_branch=before_branch,
            after_branch=after_branch,
            action_path=action_path,
            op_id=op.op_id,
            action=op.action.value,
            codify_path=op.path,
            xml_path=xml_path,
            status="snapshot_missing",
            findings=(finding,),
        )


def _audit_snapshots(
    before_xml: str,
    after_xml: str,
    ops: Tuple[OpenLawOperation, ...],
    plan: OpenLawFilePlan,
    *,
    strict: bool,
) -> OpenLawSnapshotAuditResult:
    before = wrap_open_law_body_with_prefix(parse_open_law_xml(before_xml), plan.path_prefix)
    after = wrap_open_law_body_with_prefix(parse_open_law_xml(after_xml), plan.path_prefix)
    return audit_open_law_snapshot(before, after, ops, strict=strict)


def _audit_metadata_operation(
    repos: MarylandLocalRepos,
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
    plan: OpenLawFilePlan,
    *,
    companion_ops: Tuple[OpenLawOperation, ...] | None = None,
    strict: bool,
) -> OpenLawOperationAuditRow:
    before_xml = _read_snapshot_xml(repos, before_branch, plan.xml_path, before_branch, after_branch, action_path, op)
    if isinstance(before_xml, OpenLawOperationAuditRow):
        return before_xml
    after_xml = _read_snapshot_xml(repos, after_branch, plan.xml_path, before_branch, after_branch, action_path, op)
    if isinstance(after_xml, OpenLawOperationAuditRow):
        return after_xml
    before = wrap_open_law_body_with_prefix(parse_open_law_xml(before_xml), plan.path_prefix)
    after = wrap_open_law_body_with_prefix(parse_open_law_xml(after_xml), plan.path_prefix)
    replay_ops = companion_ops if companion_ops is not None else (op,)
    replay = replay_open_law_ops(before, replay_ops, strict=strict)
    projected_before = _project_generated_metadata_history(before)
    projected_after = _project_generated_metadata_history(after)
    projected_replay = _project_generated_metadata_history(replay.tree)
    resolved = resolve_open_law_path(after, op.path)
    findings = list(replay.findings)
    if projected_before != before or projected_after != after or projected_replay != replay.tree:
        findings.append(
            OpenLawFinding(
                kind="open_law_metadata_generated_history_projection",
                message="Generated hidden Open Law history annotations were projected out for declared metadata replay comparison.",
                op_id=op.op_id,
                path=op.path,
                blocking=strict,
            )
        )
    if resolved.status != "resolved":
        findings.append(
            OpenLawFinding(
                kind=f"open_law_metadata_target_{resolved.status}",
                message=resolved.message,
                op_id=op.op_id,
                path=op.path,
                blocking=True,
            )
        )
        return OpenLawOperationAuditRow(
            before_branch=before_branch,
            after_branch=after_branch,
            action_path=action_path,
            op_id=op.op_id,
            action=op.action.value,
            codify_path=op.path,
            xml_path=plan.xml_path,
            status="metadata_diverged",
            findings=tuple(findings),
        )
    changed_paths = diff_ir_paths(projected_before, projected_after)
    allowed_prefixes = tuple(mutation.tree_path for mutation in replay.mutations)
    unexplained_paths = tuple(path for path in changed_paths if not any(_path_has_prefix(path, prefix) for prefix in allowed_prefixes))
    replay_resolved = resolve_open_law_path(projected_replay, op.path)
    if replay_resolved.status != "resolved":
        findings.append(
            OpenLawFinding(
                kind=f"open_law_metadata_replay_target_{replay_resolved.status}",
                message=replay_resolved.message,
                op_id=op.op_id,
                path=op.path,
                blocking=True,
            )
        )
        return OpenLawOperationAuditRow(
            before_branch=before_branch,
            after_branch=after_branch,
            action_path=action_path,
            op_id=op.op_id,
            action=op.action.value,
            codify_path=op.path,
            xml_path=plan.xml_path,
            status="metadata_diverged",
            changed_path_count=len(changed_paths),
            unexplained_path_count=len(unexplained_paths),
            findings=tuple(findings),
        )
    replay_node = resolve_required(projected_replay, replay_resolved.tree_path)
    after_node = resolve_required(projected_after, resolved.tree_path)
    if replay_node == after_node and not unexplained_paths:
        findings.append(
            OpenLawFinding(
                kind="open_law_metadata_target_replayed",
                message="Open Law annotations metadata target matched replay of the declared codify operation.",
                op_id=op.op_id,
                path=op.path,
                blocking=False,
            )
        )
        status = "metadata_matched"
        snapshot_matches_replay = True
    else:
        if replay_node != after_node:
            findings.append(
                OpenLawFinding(
                    kind="open_law_metadata_snapshot_mismatch",
                    message="Open Law annotations metadata target does not match replay of the declared codify operation.",
                    op_id=op.op_id,
                    path=op.path,
                    blocking=True,
                )
            )
        if unexplained_paths:
            findings.append(
                OpenLawFinding(
                    kind="open_law_metadata_unexplained_body_mutation",
                    message="Metadata operation coincided with changes outside the declared annotations target.",
                    op_id=op.op_id,
                    path=op.path,
                    blocking=True,
                )
            )
        status = "metadata_diverged"
        snapshot_matches_replay = False
    return OpenLawOperationAuditRow(
        before_branch=before_branch,
        after_branch=after_branch,
        action_path=action_path,
        op_id=op.op_id,
        action=op.action.value,
        codify_path=op.path,
        xml_path=plan.xml_path,
        status=status,
        snapshot_matches_replay=snapshot_matches_replay,
        changed_path_count=len(changed_paths),
        unexplained_path_count=len(unexplained_paths),
        findings=tuple(findings),
    )


def _audited_row(
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
    plan: OpenLawFilePlan,
    result: OpenLawSnapshotAuditResult,
) -> OpenLawOperationAuditRow:
    status = "matched" if result.snapshot_matches_replay and not result.unexplained_paths else "diverged"
    return OpenLawOperationAuditRow(
        before_branch=before_branch,
        after_branch=after_branch,
        action_path=action_path,
        op_id=op.op_id,
        action=op.action.value,
        codify_path=op.path,
        xml_path=plan.xml_path,
        status=status,
        snapshot_matches_replay=result.snapshot_matches_replay,
        changed_path_count=len(result.changed_paths),
        unexplained_path_count=len(result.unexplained_paths),
        findings=result.findings,
    )


def _snapshot_failure_rows(
    failure: OpenLawOperationAuditRow,
    planned_ops: list[tuple[OpenLawOperation, OpenLawFilePlan]],
) -> Tuple[OpenLawOperationAuditRow, ...]:
    return tuple(
        OpenLawOperationAuditRow(
            before_branch=failure.before_branch,
            after_branch=failure.after_branch,
            action_path=failure.action_path,
            op_id=op.op_id,
            action=op.action.value,
            codify_path=op.path,
            xml_path=plan.xml_path,
            status=failure.status,
            findings=failure.findings,
        )
        for op, plan in planned_ops
    )


def _finding_row(
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
    plan: OpenLawFilePlan,
) -> OpenLawOperationAuditRow:
    findings = (plan.finding,) if plan.finding is not None else ()
    return OpenLawOperationAuditRow(
        before_branch=before_branch,
        after_branch=after_branch,
        action_path=action_path,
        op_id=op.op_id,
        action=op.action.value,
        codify_path=op.path,
        xml_path=plan.xml_path,
        status="planning_failed",
        findings=findings,
    )


def _lifecycle_lane_row(
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
) -> OpenLawOperationAuditRow:
    finding = OpenLawFinding(
        kind="open_law_expire_lifecycle_not_replayed",
        message=(
            "Open Law codify:expire is a lifecycle operation; this frontend records it "
            f"with expire_date={op.expire_date or '-'} but does not replay expiry semantics yet."
        ),
        op_id=op.op_id,
        path=op.path,
        blocking=True,
    )
    return OpenLawOperationAuditRow(
        before_branch=before_branch,
        after_branch=after_branch,
        action_path=action_path,
        op_id=op.op_id,
        action=op.action.value,
        codify_path=op.path,
        xml_path="",
        status="lifecycle_unsupported",
        expire_date=op.expire_date,
        findings=(finding,),
    )


def _path_has_prefix(path: TreePath, prefix: TreePath) -> bool:
    return len(path) >= len(prefix) and path[: len(prefix)] == prefix


def _project_generated_metadata_history(node: IRNode) -> IRNode:
    children = tuple(
        projected
        for child in node.children
        for projected in (_project_generated_metadata_child(child),)
        if projected is not None
    )
    if children == node.children:
        return node
    return IRNode(kind=node.kind, label=node.label, text=node.text, attrs=dict(node.attrs), children=children)


def _project_generated_metadata_child(node: IRNode) -> IRNode | None:
    if _is_generated_hidden_history_annotation(node):
        return None
    return _project_generated_metadata_history(node)


def _is_generated_hidden_history_annotation(node: IRNode) -> bool:
    if _kind_str(node.kind) != "content":
        return False
    if node.text.strip():
        return False
    return (
        node.attrs.get("open_law_attr_type") == "History"
        and node.attrs.get("open_law_attr_display") == "false"
        and bool(node.attrs.get("open_law_attr_doc"))
        and "open_law_attr_path" in node.attrs
        and bool(node.attrs.get("open_law_attr_eff") or node.attrs.get("open_law_attr_effective"))
    )


def _report(rows: Tuple[OpenLawOperationAuditRow, ...]) -> OpenLawCorpusAuditReport:
    summary = {
        "operation_rows": len(rows),
        "matched": sum(1 for row in rows if row.status == "matched"),
        "diverged": sum(1 for row in rows if row.status == "diverged"),
        "planning_failed": sum(1 for row in rows if row.status == "planning_failed"),
        "metadata_unsupported": sum(1 for row in rows if row.status == "metadata_unsupported"),
        "metadata_matched": sum(1 for row in rows if row.status == "metadata_matched"),
        "metadata_diverged": sum(1 for row in rows if row.status == "metadata_diverged"),
        "lifecycle_unsupported": sum(1 for row in rows if row.status == "lifecycle_unsupported"),
        "snapshot_missing": sum(1 for row in rows if row.status == "snapshot_missing"),
        "findings": sum(len(row.findings) for row in rows),
        "unexplained_paths": sum(row.unexplained_path_count for row in rows),
    }
    return OpenLawCorpusAuditReport(operation_rows=rows, summary=summary)


def _row_jsonable(row: OpenLawOperationAuditRow) -> dict[str, object]:
    return {
        "before_branch": row.before_branch,
        "after_branch": row.after_branch,
        "action_path": row.action_path,
        "op_id": row.op_id,
        "action": row.action,
        "codify_path": list(row.codify_path),
        "xml_path": row.xml_path,
        "status": row.status,
        "expire_date": row.expire_date,
        "evidence_row": _operation_evidence_row(row).to_dict(),
        "snapshot_matches_replay": row.snapshot_matches_replay,
        "changed_path_count": row.changed_path_count,
        "unexplained_path_count": row.unexplained_path_count,
        "findings": [
            {
                "kind": finding.kind,
                "message": finding.message,
                "path": list(finding.path),
                "blocking": finding.blocking,
            }
            for finding in row.findings
        ],
    }


def _operation_evidence_row(row: OpenLawOperationAuditRow) -> CorpusOperationEvidenceRow:
    return CorpusOperationEvidenceRow(
        row_id=row.op_id,
        frontend_id="open_law_maryland",
        source_artifact_id=row.action_path,
        source_unit_id=f"{row.before_branch}->{row.after_branch}",
        source_locator="|".join(row.codify_path),
        effect_family=row.action,
        canonical_family=row.action if row.status in {"matched", "metadata_matched"} else "",
        original_target="|".join(row.codify_path),
        resolved_target=row.xml_path,
        status=_shared_status(row.status),
        blocking=any(finding.blocking for finding in row.findings),
        strict_disposition=_strict_disposition(row),
        quirks_disposition=_quirks_disposition(row),
        finding_ids=tuple(finding.kind for finding in row.findings),
        detail={
            "status": row.status,
            "expire_date": row.expire_date,
            "snapshot_matches_replay": row.snapshot_matches_replay,
            "changed_path_count": row.changed_path_count,
            "unexplained_path_count": row.unexplained_path_count,
        },
    )


def _finding_evidence_row(row: OpenLawOperationAuditRow, finding: OpenLawFinding) -> CorpusFindingEvidenceRow:
    return CorpusFindingEvidenceRow(
        finding_id=f"{row.op_id}:{finding.kind}",
        frontend_id="open_law_maryland",
        family=finding.kind,
        rule_id=finding.kind,
        phase=_finding_phase(row.status),
        message=finding.message,
        source_artifact_id=row.action_path,
        source_unit_id=f"{row.before_branch}->{row.after_branch}",
        related_row_ids=(row.op_id,),
        blocking=finding.blocking,
        strict_disposition="block" if finding.blocking else "record",
        quirks_disposition="record",
        evidence={
            "codify_path": "|".join(finding.path or row.codify_path),
            "status": row.status,
        },
    )


def _shared_status(status: str) -> CorpusRowStatus:
    if status in {"matched", "metadata_matched"}:
        return CorpusRowStatus.MATCHED
    if status in {"diverged", "metadata_diverged"}:
        return CorpusRowStatus.DIVERGED
    if status in {"lifecycle_unsupported", "metadata_unsupported"}:
        return CorpusRowStatus.UNSUPPORTED
    if status in {"planning_failed", "snapshot_missing"}:
        return CorpusRowStatus.FAILED
    return CorpusRowStatus.ACCEPTED


def _strict_disposition(row: OpenLawOperationAuditRow) -> str:
    if row.status in {"matched", "metadata_matched"}:
        return "record"
    if row.status in {"lifecycle_unsupported", "metadata_unsupported", "planning_failed", "snapshot_missing"}:
        return "block"
    if row.status in {"diverged", "metadata_diverged"}:
        return "block"
    return "record"


def _quirks_disposition(row: OpenLawOperationAuditRow) -> str:
    if row.status in {"matched", "metadata_matched"}:
        return "record"
    if row.status == "lifecycle_unsupported":
        return "record_unsupported"
    if row.status in {"planning_failed", "snapshot_missing"}:
        return "record_failure"
    if row.status in {"diverged", "metadata_diverged"}:
        return "record_divergence"
    return "record"


def _finding_phase(status: str) -> str:
    if status in {"matched", "diverged"}:
        return "audit"
    if status.startswith("metadata_"):
        return "metadata_audit"
    if status == "lifecycle_unsupported":
        return "lifecycle"
    if status == "planning_failed":
        return "planning"
    return "corpus_audit"
