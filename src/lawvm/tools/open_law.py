"""Developer CLI for the Open Law Library frontend."""

from __future__ import annotations

import json
import hashlib
from argparse import Namespace
from pathlib import Path
from typing import Any, Mapping

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.open_law.audit import audit_open_law_snapshot, replay_open_law_ops
from lawvm.open_law.corpus_audit import audit_maryland_corpus, audit_maryland_transition, write_corpus_report, write_inventory
from lawvm.open_law.evidence_pack import write_maryland_evidence_pack
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.local_git import MarylandLocalRepos, make_maryland_repos
from lawvm.open_law.models import OpenLawFinding, OpenLawOperation
from lawvm.open_law.xml import parse_open_law_xml, wrap_open_law_body_with_prefix
from lawvm.tools.report_query import load_report_query_records


def main(args: Namespace) -> None:
    command = args.open_law_command
    if command == "ops":
        _print_ops(args)
        return
    if command == "replay":
        _print_replay(args)
        return
    if command == "audit":
        _print_audit(args)
        return
    if command == "inventory":
        _print_inventory(args)
        return
    if command == "corpus-audit":
        _print_corpus_audit(args)
        return
    if command == "evidence-pack":
        _print_evidence_pack(args)
        return
    if command == "verify-pack":
        _print_verify_pack(args)
        return
    if command == "explain":
        _print_explain(args)
        return
    raise SystemExit(
        "open-law requires a subcommand: ops, replay, audit, inventory, corpus-audit, evidence-pack, verify-pack, or explain"
    )


def _print_ops(args: Namespace) -> None:
    ops = parse_open_law_codify_ops(_read_text(args.action_xml), source_id=args.action_xml)
    if args.json:
        print(json.dumps([_op_json(op) for op in ops], indent=2, ensure_ascii=False))
        return
    for op in ops:
        payload_kind = str(op.payload.kind) if op.payload is not None else ""
        payload_label = op.payload.label if op.payload is not None else ""
        print(
            f"{op.sequence}: action={op.action.value} path={'|'.join(op.path)} "
            f"effective={op.effective or '-'} payload={payload_kind}:{payload_label or '-'}"
        )


def _print_replay(args: Namespace) -> None:
    tree = _read_open_law_tree(args.base_xml, args.path_prefix)
    ops = parse_open_law_codify_ops(_read_text(args.action_xml), source_id=args.action_xml)
    result = replay_open_law_ops(tree, ops, strict=args.strict)
    if args.json:
        print(
            json.dumps(
                {
                    "mutations": [
                        {
                            "op_id": mutation.op_id,
                            "action": mutation.action.value,
                            "open_law_path": list(mutation.open_law_path),
                            "tree_path": [list(step) for step in mutation.tree_path],
                        }
                        for mutation in result.mutations
                    ],
                    "findings": [_finding_json(finding) for finding in result.findings],
                    "text": irnode_to_text(result.tree) if args.text else "",
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(f"mutations={len(result.mutations)} findings={len(result.findings)}")
    for mutation in result.mutations:
        print(f"  applied {mutation.action.value} {'|'.join(mutation.open_law_path)}")
    for finding in result.findings:
        print(f"  finding {finding.kind}: {finding.message}")
    if args.text:
        print(irnode_to_text(result.tree))


def _print_audit(args: Namespace) -> None:
    before = _read_open_law_tree(args.before_xml, args.path_prefix)
    after = _read_open_law_tree(args.after_xml, args.path_prefix)
    ops = parse_open_law_codify_ops(_read_text(args.action_xml), source_id=args.action_xml)
    result = audit_open_law_snapshot(before, after, ops, strict=args.strict)
    if args.json:
        print(
            json.dumps(
                {
                    "snapshot_matches_replay": result.snapshot_matches_replay,
                    "changed_paths": [[list(step) for step in path] for path in result.changed_paths],
                    "unexplained_paths": [[list(step) for step in path] for path in result.unexplained_paths],
                    "findings": [_finding_json(finding) for finding in result.findings],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(f"snapshot_matches_replay={result.snapshot_matches_replay}")
    print(f"changed_paths={len(result.changed_paths)} unexplained_paths={len(result.unexplained_paths)}")
    for finding in result.findings:
        print(f"  finding {finding.kind}: {finding.message}")


def _print_inventory(args: Namespace) -> None:
    repos = _maryland_repos(args)
    out_dir = Path(args.out)
    write_inventory(out_dir, repos=repos)
    print(f"wrote {out_dir / 'manifest.json'}")


def _print_corpus_audit(args: Namespace) -> None:
    repos = _maryland_repos(args)
    if bool(args.before_branch) != bool(args.after_branch):
        raise SystemExit("--before-branch and --after-branch must be supplied together")
    if args.before_branch and args.after_branch:
        report = audit_maryland_transition(
            args.before_branch,
            args.after_branch,
            repos=repos,
            limit=args.limit,
            strict=args.strict,
        )
    else:
        report = audit_maryland_corpus(repos=repos, limit=args.limit, strict=args.strict)
    out_dir = Path(args.out)
    write_corpus_report(report, out_dir)
    if args.json:
        print(json.dumps(report.summary, indent=2, ensure_ascii=False))
        return
    print(
        " ".join(
            (
                f"operation_rows={report.summary['operation_rows']}",
                f"matched={report.summary['matched']}",
                f"diverged={report.summary['diverged']}",
                f"planning_failed={report.summary['planning_failed']}",
                f"metadata_unsupported={report.summary['metadata_unsupported']}",
                f"metadata_matched={report.summary['metadata_matched']}",
                f"metadata_diverged={report.summary['metadata_diverged']}",
                f"lifecycle_unsupported={report.summary['lifecycle_unsupported']}",
                f"snapshot_missing={report.summary['snapshot_missing']}",
                f"findings={report.summary['findings']}",
                f"unexplained_paths={report.summary['unexplained_paths']}",
            )
        )
    )
    print(f"wrote {out_dir}")


def _print_evidence_pack(args: Namespace) -> None:
    repos = _maryland_repos(args)
    pack = write_maryland_evidence_pack(
        Path(args.out),
        repos=repos,
        limit=args.limit,
        strict=args.strict,
    )
    if args.json:
        print(
            json.dumps(
                {
                    "summary": pack.report.summary,
                    "manifest_path": str(pack.manifest_path),
                    "summary_json_path": str(pack.summary_json_path),
                    "operation_audits_path": str(pack.operation_audits_path),
                    "findings_path": str(pack.findings_path),
                    "summary_path": str(pack.summary_path),
                    "exemplars_path": str(pack.exemplars_path),
                    "artifact_manifest_path": str(pack.artifact_manifest_path),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(
        " ".join(
            (
                f"operation_rows={pack.report.summary['operation_rows']}",
                f"matched={pack.report.summary['matched']}",
                f"diverged={pack.report.summary['diverged']}",
                f"metadata_unsupported={pack.report.summary['metadata_unsupported']}",
                f"metadata_matched={pack.report.summary['metadata_matched']}",
                f"metadata_diverged={pack.report.summary['metadata_diverged']}",
                f"lifecycle_unsupported={pack.report.summary['lifecycle_unsupported']}",
                f"planning_failed={pack.report.summary['planning_failed']}",
                f"unexplained_paths={pack.report.summary['unexplained_paths']}",
            )
        )
    )
    print(f"wrote {pack.summary_path}")


def _print_verify_pack(args: Namespace) -> None:
    report_dir = Path(args.report_dir)
    result = _verify_evidence_pack(report_dir)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(
            " ".join(
                (
                    f"files={result['files']}",
                    f"operation_rows={result['operation_rows']}",
                    f"finding_rows={result['finding_rows']}",
                    f"issues={len(result['issues'])}",
                )
            )
        )
        for issue in result["issues"]:
            print(f"  issue: {issue}")
    if result["issues"]:
        raise SystemExit(1)


def _print_explain(args: Namespace) -> None:
    report_dir = Path(args.report_dir)
    rows = _read_jsonl(report_dir / "operation_audits.jsonl")
    selected = _select_explain_rows(rows, op_id=args.op_id, status=args.status, limit=args.limit)
    if args.json:
        print(json.dumps(selected, indent=2, ensure_ascii=False))
        return
    if not selected:
        print("no matching Open Law audit rows")
        return
    for row in selected:
        print(f"{row['op_id']} {row['status']} {row['action']} {'|'.join(row['codify_path'])}")
        print(f"  transition: {row['before_branch']} -> {row['after_branch']}")
        print(f"  action file: {row['action_path']}")
        if row.get("xml_path"):
            print(f"  xml: {row['xml_path']}")
        if row.get("expire_date"):
            print(f"  expire_date: {row['expire_date']}")
        print(
            "  counts: "
            f"changed={row['changed_path_count']} unexplained={row['unexplained_path_count']} "
            f"snapshot_matches_replay={row['snapshot_matches_replay']}"
        )
        evidence_row = row.get("evidence_row")
        if isinstance(evidence_row, dict):
            print(
                "  evidence: "
                f"status={evidence_row.get('status', '')} "
                f"canonical={evidence_row.get('canonical_family', '') or '-'} "
                f"strict={evidence_row.get('strict_disposition', '')} "
                f"quirks={evidence_row.get('quirks_disposition', '')}"
            )
        for finding in row["findings"]:
            print(f"  finding {finding['kind']}: {finding['message']}")


def _op_json(op: OpenLawOperation) -> dict[str, Any]:
    return {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": op.action.value,
        "doc": op.doc,
        "path": list(op.path),
        "source_id": op.source_id,
        "effective": op.effective,
        "expire_date": op.expire_date,
        "history": op.history,
        "applicability": op.applicability,
        "payload_kind": str(op.payload.kind) if op.payload is not None else "",
        "payload_label": op.payload.label if op.payload is not None else "",
        "raw_action": op.raw_action,
    }


def _finding_json(finding: OpenLawFinding) -> dict[str, Any]:
    return {
        "kind": finding.kind,
        "message": finding.message,
        "op_id": finding.op_id,
        "path": list(finding.path),
        "blocking": finding.blocking,
    }


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"missing Open Law report file: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verify_evidence_pack(report_dir: Path) -> dict[str, Any]:
    issues: list[str] = []
    artifact_manifest_path = report_dir / "evidence_pack_manifest.json"
    if not artifact_manifest_path.exists():
        issues.append(f"missing {artifact_manifest_path}")
        return {"files": 0, "operation_rows": 0, "finding_rows": 0, "issues": issues}

    manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    files_raw = manifest.get("files") if isinstance(manifest, Mapping) else None
    files = files_raw if isinstance(files_raw, list) else []
    if not files:
        issues.append("evidence_pack_manifest.json has no files list")

    verified_files = 0
    for file_record in files:
        if not isinstance(file_record, Mapping):
            issues.append("evidence_pack_manifest.json contains a non-object file record")
            continue
        path_value = file_record.get("path")
        bytes_value = file_record.get("bytes")
        sha256_value = file_record.get("sha256")
        if not isinstance(path_value, str) or not isinstance(bytes_value, int) or not isinstance(sha256_value, str):
            issues.append(f"invalid artifact manifest file record: {file_record!r}")
            continue
        if Path(path_value).is_absolute() or ".." in Path(path_value).parts:
            issues.append(f"unsafe artifact manifest path: {path_value}")
            continue
        artifact_path = report_dir / path_value
        if not artifact_path.exists():
            issues.append(f"missing artifact: {path_value}")
            continue
        data = artifact_path.read_bytes()
        if len(data) != bytes_value:
            issues.append(f"byte count mismatch for {path_value}: expected {bytes_value}, got {len(data)}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != sha256_value:
            issues.append(f"sha256 mismatch for {path_value}: expected {sha256_value}, got {digest}")
        verified_files += 1

    operation_records = _load_pack_report_rows(report_dir / "operation_audits.jsonl", issues)
    finding_records = _load_pack_report_rows(report_dir / "findings.jsonl", issues)
    for record in (*operation_records, *finding_records):
        for issue in record.validation_issues:
            issues.append(f"{record.source_path}:{record.line_no}: {issue}")
    issues.extend(_summary_consistency_issues(report_dir, operation_records, finding_records))

    return {
        "files": verified_files,
        "operation_rows": len(operation_records),
        "finding_rows": len(finding_records),
        "issues": issues,
    }


def _load_pack_report_rows(path: Path, issues: list[str]) -> tuple[Any, ...]:
    if not path.exists():
        issues.append(f"missing {path}")
        return ()
    return load_report_query_records((path,), validate=True)


def _summary_consistency_issues(report_dir: Path, operation_records: tuple[Any, ...], finding_records: tuple[Any, ...]) -> list[str]:
    summary_path = report_dir / "summary.json"
    if not summary_path.exists():
        return [f"missing {summary_path}"]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, Mapping):
        return ["summary.json is not an object"]
    expected = {
        "operation_rows": len(operation_records),
        "matched": _count_status(operation_records, "matched"),
        "diverged": _count_status(operation_records, "diverged"),
        "planning_failed": _count_status(operation_records, "planning_failed"),
        "metadata_unsupported": _count_status(operation_records, "metadata_unsupported"),
        "metadata_matched": _count_status(operation_records, "metadata_matched"),
        "metadata_diverged": _count_status(operation_records, "metadata_diverged"),
        "lifecycle_unsupported": _count_status(operation_records, "lifecycle_unsupported"),
        "snapshot_missing": _count_status(operation_records, "snapshot_missing"),
        "findings": len(finding_records),
        "unexplained_paths": sum(_int_field(record.original.get("unexplained_path_count")) for record in operation_records),
    }
    return [
        f"summary mismatch for {key}: expected {value}, got {summary.get(key)!r}"
        for key, value in expected.items()
        if summary.get(key) != value
    ]


def _count_status(records: tuple[Any, ...], status: str) -> int:
    return sum(1 for record in records if record.original.get("status") == status)


def _int_field(value: object) -> int:
    return value if isinstance(value, int) else 0


def _select_explain_rows(
    rows: list[dict[str, Any]],
    *,
    op_id: str,
    status: str,
    limit: int,
) -> list[dict[str, Any]]:
    selected = []
    for row in rows:
        if op_id and row.get("op_id") != op_id:
            continue
        if status and row.get("status") != status:
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _read_open_law_tree(path: str, path_prefix: str) -> IRNode:
    tree = parse_open_law_xml(_read_text(path))
    prefix = tuple(part.strip() for part in path_prefix.split("|") if part.strip())
    if not prefix:
        return tree
    return wrap_open_law_body_with_prefix(tree, prefix)


def _maryland_repos(args: Namespace) -> MarylandLocalRepos:
    return make_maryland_repos(args.source_repo, args.codified_repo)
