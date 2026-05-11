"""Corpus replay audit for public Open Law Maryland XML."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from lawvm.open_law.audit import OpenLawSnapshotAuditResult, audit_open_law_snapshot
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.maryland import (
    build_maryland_inventory,
    inventory_to_jsonable,
    plan_maryland_publication_transitions,
)
from lawvm.open_law.local_git import MarylandLocalRepos
from lawvm.open_law.models import OpenLawFinding, OpenLawOperation
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
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )


def write_inventory(out_dir: Path, *, repos: MarylandLocalRepos) -> None:
    inventory = build_maryland_inventory(repos)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(
        json.dumps(inventory_to_jsonable(inventory), indent=2, ensure_ascii=False) + "\n",
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
    if plan.status != "planned":
        return _finding_row(before_branch, after_branch, action_path, op, plan)
    if op.path and op.path[-1] == "annos":
        return _metadata_lane_row(before_branch, after_branch, action_path, op, plan)
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
        plan = plan_maryland_comar_operation(op)
        if plan.status != "planned":
            rows.append(_finding_row(before_branch, after_branch, action_path, op, plan))
            continue
        if op.path and op.path[-1] == "annos":
            rows.append(_metadata_lane_row(before_branch, after_branch, action_path, op, plan))
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
        result = _audit_snapshots(
            before_xml,
            after_xml,
            tuple(op for op, _plan in planned_ops),
            representative_plan,
            strict=strict,
        )
        for op, plan in planned_ops:
            rows.append(_audited_row(before_branch, after_branch, action_path, op, plan, result))
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


def _metadata_lane_row(
    before_branch: str,
    after_branch: str,
    action_path: str,
    op: OpenLawOperation,
    plan: OpenLawFilePlan,
) -> OpenLawOperationAuditRow:
    finding = OpenLawFinding(
        kind="open_law_metadata_target_not_body_replay",
        message="Operation targets Open Law annotations metadata; corpus body replay does not claim this lane yet.",
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
        xml_path=plan.xml_path,
        status="metadata_unsupported",
        findings=(finding,),
    )


def _report(rows: Tuple[OpenLawOperationAuditRow, ...]) -> OpenLawCorpusAuditReport:
    summary = {
        "operation_rows": len(rows),
        "matched": sum(1 for row in rows if row.status == "matched"),
        "diverged": sum(1 for row in rows if row.status == "diverged"),
        "planning_failed": sum(1 for row in rows if row.status == "planning_failed"),
        "metadata_unsupported": sum(1 for row in rows if row.status == "metadata_unsupported"),
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
