"""Developer CLI for the Open Law Library frontend."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import Any

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.open_law.audit import audit_open_law_snapshot, replay_open_law_ops
from lawvm.open_law.corpus_audit import audit_maryland_corpus, audit_maryland_transition, write_corpus_report, write_inventory
from lawvm.open_law.evidence_pack import write_maryland_evidence_pack
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.local_git import MarylandLocalRepos, make_maryland_repos
from lawvm.open_law.models import OpenLawFinding, OpenLawOperation
from lawvm.open_law.xml import parse_open_law_xml, wrap_open_law_body_with_prefix


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
    raise SystemExit("open-law requires a subcommand: ops, replay, audit, inventory, corpus-audit, or evidence-pack")


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
                    "summary_path": str(pack.summary_path),
                    "exemplars_path": str(pack.exemplars_path),
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
                f"planning_failed={pack.report.summary['planning_failed']}",
                f"unexplained_paths={pack.report.summary['unexplained_paths']}",
            )
        )
    )
    print(f"wrote {pack.summary_path}")


def _op_json(op: OpenLawOperation) -> dict[str, Any]:
    return {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": op.action.value,
        "doc": op.doc,
        "path": list(op.path),
        "source_id": op.source_id,
        "effective": op.effective,
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


def _read_open_law_tree(path: str, path_prefix: str) -> IRNode:
    tree = parse_open_law_xml(_read_text(path))
    prefix = tuple(part.strip() for part in path_prefix.split("|") if part.strip())
    if not prefix:
        return tree
    return wrap_open_law_body_with_prefix(tree, prefix)


def _maryland_repos(args: Namespace) -> MarylandLocalRepos:
    return make_maryland_repos(args.source_repo, args.codified_repo)
