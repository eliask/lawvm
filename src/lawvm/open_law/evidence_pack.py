"""Evidence-pack writer for the Open Law Maryland frontend."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple, TypedDict, cast

from lawvm.open_law.corpus_audit import (
    OpenLawCorpusAuditReport,
    OpenLawOperationAuditRow,
    audit_maryland_corpus,
    write_corpus_report,
    write_inventory,
)
from lawvm.open_law.local_git import MarylandLocalRepos
from lawvm.open_law.maryland import build_maryland_inventory, maryland_manifest_to_jsonable


@dataclass(frozen=True)
class OpenLawEvidencePack:
    """Paths and report produced by the evidence-pack writer."""

    out_dir: Path
    report: OpenLawCorpusAuditReport
    summary_path: Path
    exemplars_path: Path


class EvidenceRowSummary(TypedDict):
    transition: str
    action_path: str
    op_id: str
    action: str
    codify_path: str
    xml_path: str
    status: str
    snapshot_matches_replay: bool
    changed_path_count: int
    unexplained_path_count: int
    findings: list[str]


def write_maryland_evidence_pack(
    out_dir: Path,
    *,
    repos: MarylandLocalRepos,
    limit: int | None = None,
    strict: bool = False,
) -> OpenLawEvidencePack:
    """Write a compact evidence pack for the Maryland Open Law corpus."""

    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = build_maryland_inventory(repos)
    report = audit_maryland_corpus(repos=repos, limit=limit, strict=strict)
    write_inventory(out_dir, repos=repos)
    write_corpus_report(report, out_dir)
    manifest = maryland_manifest_to_jsonable(inventory, repos=repos)

    exemplars = _pick_exemplars(report.operation_rows)
    exemplars_path = out_dir / "exemplars.json"
    exemplars_path.write_text(json.dumps(exemplars, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary_path = out_dir / "summary.md"
    summary_path.write_text(
        _summary_markdown(manifest, report, exemplars, strict=strict),
        encoding="utf-8",
    )
    return OpenLawEvidencePack(
        out_dir=out_dir,
        report=report,
        summary_path=summary_path,
        exemplars_path=exemplars_path,
    )


def _pick_exemplars(rows: Tuple[OpenLawOperationAuditRow, ...]) -> dict[str, EvidenceRowSummary]:
    exemplars: dict[str, EvidenceRowSummary] = {}
    wanted = (
        ("clean_replace", lambda row: row.status == "matched" and row.action == "replace"),
        ("replace_or_insert", lambda row: row.status == "matched" and row.action == "replace-or-insert"),
        ("metadata_lane", lambda row: row.status == "metadata_matched"),
        ("lifecycle_lane", lambda row: row.status == "lifecycle_unsupported"),
        ("divergence", lambda row: row.status == "diverged"),
    )
    for name, predicate in wanted:
        for row in rows:
            if predicate(row):
                exemplars[name] = _row_summary(row)
                break
    return exemplars


def _row_summary(row: OpenLawOperationAuditRow) -> EvidenceRowSummary:
    return {
        "transition": f"{row.before_branch} -> {row.after_branch}",
        "action_path": row.action_path,
        "op_id": row.op_id,
        "action": row.action,
        "codify_path": "|".join(row.codify_path),
        "xml_path": row.xml_path,
        "status": row.status,
        "snapshot_matches_replay": row.snapshot_matches_replay,
        "changed_path_count": row.changed_path_count,
        "unexplained_path_count": row.unexplained_path_count,
        "findings": [finding.kind for finding in row.findings],
    }


def _summary_markdown(
    manifest: dict[str, object],
    report: OpenLawCorpusAuditReport,
    exemplars: dict[str, EvidenceRowSummary],
    *,
    strict: bool,
) -> str:
    operation_counts = manifest.get("operation_counts", {})
    branch_count = _sized_len(manifest.get("publication_branches", ()))
    action_count = _sized_len(manifest.get("source_editorial_actions", ()))
    lines = [
        "# Open Law Maryland Evidence Pack",
        "",
        "This pack audits public Maryland Open Law XML from local git clones.",
        "It does not scrape the HTML site and does not infer amendments from Maryland Register prose.",
        "",
        "## Inputs",
        "",
        f"- publication branches inventoried: {branch_count}",
        f"- source editorial action files: {action_count}",
        f"- operation counts: `{json.dumps(operation_counts, sort_keys=True)}`",
        f"- strict mode: `{strict}`",
    ]
    lines.extend(_repository_identity_lines(manifest))
    lines.extend(
        [
            "",
            "## Corpus Audit Summary",
            "",
            "| metric | count |",
            "| --- | ---: |",
        ]
    )
    for key in (
        "operation_rows",
        "matched",
        "diverged",
        "planning_failed",
        "metadata_unsupported",
        "metadata_matched",
        "metadata_diverged",
        "lifecycle_unsupported",
        "snapshot_missing",
        "findings",
        "unexplained_paths",
    ):
        lines.append(f"| {key} | {report.summary.get(key, 0)} |")
    lines.extend(
        [
            "",
            "## What LawVM Claims",
            "",
            "- Local Open Law XML can be parsed into LawVM IR without using network reads during replay.",
            "- Supported `codify:*` body operations replay over exact declared Open Law paths.",
            "- Open Law annotation metadata operations replay in a separate metadata lane.",
            "- Publication snapshots either match replay or produce explicit findings.",
            "- Unsupported or non-body lanes remain visible instead of being dropped.",
            "",
            "## What LawVM Does Not Claim",
            "",
            "- It does not independently interpret Maryland Register prose.",
            "- It does not treat Open Law annotation metadata as legal body text.",
            "- It records but does not yet apply non-COMAR emergency-register expiry semantics.",
            "- It does not treat git diffs alone as legal proof.",
            "",
            "## Exemplars",
            "",
        ]
    )
    if not exemplars:
        lines.append("No exemplar rows were selected.")
    for name, row in exemplars.items():
        lines.extend(
            [
                f"### {name}",
                "",
                f"- transition: `{row['transition']}`",
                f"- action file: `{row['action_path']}`",
                f"- action: `{row['action']}`",
                f"- codify path: `{row['codify_path']}`",
                f"- XML file: `{row['xml_path']}`",
                f"- status: `{row['status']}`",
                f"- findings: `{', '.join(row['findings']) or '-'}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Files",
            "",
            "- `manifest.json`: local clone inventory",
            "- `operation_audits.jsonl`: one row per audited operation",
            "- `findings.jsonl`: one row per emitted finding",
            "- `exemplars.json`: selected demo rows",
            "- `summary.md`: this summary",
            "",
        ]
    )
    return "\n".join(lines)


def _sized_len(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, tuple):
        return len(value)
    return 0


def _repository_identity_lines(manifest: dict[str, object]) -> list[str]:
    repos = manifest.get("local_repositories")
    if not isinstance(repos, Mapping):
        return []
    repo_map = cast("Mapping[str, object]", repos)
    lines: list[str] = []
    for key in ("source", "codified"):
        item = repo_map.get(key)
        if not isinstance(item, Mapping):
            continue
        repo_item = cast("Mapping[str, object]", item)
        head = repo_item.get("head_commit")
        branch_count = repo_item.get("branch_count")
        if isinstance(head, str) and isinstance(branch_count, int):
            lines.append(f"- {key} clone HEAD: `{head}` across {branch_count} local branches/refs")
        remotes = repo_item.get("remotes")
        if isinstance(remotes, list) and remotes:
            remote_bits: list[str] = []
            for remote in remotes:
                if isinstance(remote, Mapping):
                    remote_item = cast("Mapping[str, object]", remote)
                    remote_name = remote_item.get("name")
                    remote_url = remote_item.get("url")
                    if isinstance(remote_name, str) and isinstance(remote_url, str):
                        remote_bits.append(f"{remote_name}={remote_url}")
            if remote_bits:
                lines.append(f"- {key} clone remotes: `{', '.join(remote_bits)}`")
    return lines
