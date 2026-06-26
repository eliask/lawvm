"""Evidence-pack writer for the Open Law Maryland frontend."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
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
    manifest_path: Path
    summary_json_path: Path
    operation_audits_path: Path
    findings_path: Path
    summary_path: Path
    exemplars_path: Path
    artifact_manifest_path: Path


class EvidenceRowSummary(TypedDict):
    transition: str
    action_path: str
    op_id: str
    action: str
    codify_path: str
    xml_path: str
    audit_status: str
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
    generator = _lawvm_generator_identity()

    exemplars = _pick_exemplars(report.operation_rows)
    exemplars_path = out_dir / "exemplars.json"
    exemplars_path.write_text(json.dumps(exemplars, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary_path = out_dir / "summary.md"
    summary_path.write_text(
        _summary_markdown(manifest, report, exemplars, generator=generator, strict=strict),
        encoding="utf-8",
    )
    artifact_manifest_path = _write_artifact_manifest(
        out_dir,
        (
            "manifest.json",
            "summary.json",
            "operation_audits.jsonl",
            "findings.jsonl",
            "exemplars.json",
            "summary.md",
        ),
        generator=generator,
    )
    return OpenLawEvidencePack(
        out_dir=out_dir,
        report=report,
        manifest_path=out_dir / "manifest.json",
        summary_json_path=out_dir / "summary.json",
        operation_audits_path=out_dir / "operation_audits.jsonl",
        findings_path=out_dir / "findings.jsonl",
        summary_path=summary_path,
        exemplars_path=exemplars_path,
        artifact_manifest_path=artifact_manifest_path,
    )


def _pick_exemplars(rows: Tuple[OpenLawOperationAuditRow, ...]) -> dict[str, EvidenceRowSummary]:
    exemplars: dict[str, EvidenceRowSummary] = {}
    wanted = (
        ("clean_replace", lambda row: row.audit_status == "matched" and row.action == "replace"),
        ("replace_or_insert", lambda row: row.audit_status == "matched" and row.action == "replace-or-insert"),
        ("metadata_lane", lambda row: row.audit_status == "metadata_matched"),
        ("lifecycle_lane", lambda row: row.audit_status == "lifecycle_unsupported"),
        ("divergence", lambda row: row.audit_status == "diverged"),
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
        "audit_status": row.audit_status,
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
    generator: dict[str, object],
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
    lines.extend(_generator_identity_lines(generator))
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
                f"- status: `{row['audit_status']}`",
                f"- findings: `{', '.join(row['findings']) or '-'}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Files",
            "",
            "- `manifest.json`: local clone inventory",
            "- `evidence_pack_manifest.json`: generated artifact checksums",
            "- `summary.json`: machine-readable corpus summary counts",
            "- `operation_audits.jsonl`: one row per audited operation",
            "- `findings.jsonl`: one row per emitted finding",
            "- `exemplars.json`: selected demo rows",
            "- `summary.md`: this summary",
            "",
        ]
    )
    return "\n".join(lines)


def _write_artifact_manifest(out_dir: Path, file_names: Tuple[str, ...], *, generator: dict[str, object]) -> Path:
    """Write checksums for generated evidence-pack artifacts."""

    files: list[dict[str, object]] = []
    for name in file_names:
        path = out_dir / name
        data = path.read_bytes()
        files.append(
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest_path = out_dir / "evidence_pack_manifest.json"
    manifest_path.write_text(
        json.dumps({"generator": generator, "files": files}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _lawvm_generator_identity() -> dict[str, object]:
    """Return local LawVM code identity for generated evidence packs."""

    repo_root = Path(__file__).resolve().parents[3]
    inside = subprocess.run(
        ("git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {
            "tool": "lawvm open-law evidence-pack",
            "repository": repo_root.name,
            "git_commit": "",
            "git_dirty": None,
        }
    commit = subprocess.check_output(("git", "-C", str(repo_root), "rev-parse", "HEAD"), text=True).strip()
    status = subprocess.check_output(("git", "-C", str(repo_root), "status", "--short"), text=True)
    return {
        "tool": "lawvm open-law evidence-pack",
        "repository": _lawvm_repository_label(repo_root),
        "git_commit": commit,
        "git_dirty": bool(status.strip()),
    }


def _lawvm_repository_label(repo_root: Path) -> str:
    """Return a shareable repository identity without leaking local paths.

    Recognized shared-remote shapes: ``git@github.com:owner/repo[.git]``,
    ``https://...`` / ``http://...`` / ``ssh://...`` / ``git://...`` URIs, and
    ``github.com/owner/repo`` HTTPS-without-scheme. A local-path remote
    (an absolute Unix path like ``/srv/git/repo``, a ``./`` / ``../`` relative
    reference, or ``file://`` URI) is NOT shareable — fall back to the repo
    root's leaf name so a developer-local checkout (e.g. one whose
    ``remote.origin.url`` points at a sibling local clone) does not leak its
    on-disk location into the evidence-pack manifest. Pinned by
    ``tests/test_open_law_frontend.py``'s release-hygiene leak guard
    (AGENTS §1.10 — the diagnostic must be a typed refusal of the leak, never
    silent).
    """

    remote = subprocess.run(
        ("git", "-C", str(repo_root), "config", "--get", "remote.origin.url"),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    remote_url = remote.stdout.strip()
    if not remote_url:
        return repo_root.name
    return _shareable_git_remote_url(remote_url, fallback_leaf=repo_root.name)


# URI schemes that are unambiguously shareable (not a developer-local path).
_SHARED_REMOTE_SCHEMES = ("http://", "https://", "ssh://", "git://", "file://")
# Bare-host remote shapes that are also shareable (e.g. "github.com/owner/repo").
_SHARED_REMOTE_HOST_PREFIXES = (
    "github.com/",
    "gitlab.com/",
    "bitbucket.org/",
)


def _is_local_path_remote(remote_url: str) -> bool:
    """A remote URL that resolves to a developer-local filesystem path.

    Absolute Unix paths (e.g. ``/srv/git/repo``), Windows drive-letters
    (``C:\\...`` / ``C:/...``), and ``./`` / ``../`` relative references are all
    local-path remotes that MUST NOT be returned from a shareable-identity
    function. The leak guard in ``verify_pack`` enforces this contract on the
    written manifest; this predicate catches it at emission instead.
    """
    if not remote_url:
        return False
    if remote_url.startswith(("/", "./", "../")):
        return True
    # Windows drive-letter form: "C:\..." or "C:/...".
    if len(remote_url) >= 3 and remote_url[1:3] == ":\\" and remote_url[0].isalpha():
        return True
    if len(remote_url) >= 3 and remote_url[1:3] == ":/" and remote_url[0].isalpha():
        return True
    return False


def _shareable_git_remote_url(
    remote_url: str, *, fallback_leaf: str | None = None
) -> str:
    """Normalize a ``git`` remote URL into a shareable identity.

    GitHub SSH remotes are normalized to HTTPS. Recognized shareable-scheme URIs
    (http(s) / ssh / git) and bare github.com-style host shapes are returned
    verbatim after GitHub-SSH normalization. A local-path remote falls back to
    ``fallback_leaf`` (the repo root's leaf directory name) so a developer-local
    checkout does not leak its on-disk path into a serialized evidence-pack
    artifact. If ``fallback_leaf`` is None and the remote is a local path, the
    verbatim URL is returned (preserves the prior behaviour for any non-library
    caller and surfaces the path as-is — which the verify-pack leak guard will
    flag as a typed issue, never silent).
    """
    match = re.fullmatch(r"git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?", remote_url)
    if match is not None:
        return f"https://github.com/{match.group('owner')}/{match.group('repo')}.git"
    if remote_url.startswith(_SHARED_REMOTE_SCHEMES):
        # Strip a file:// URI down to a leaf-name fallback — file:// remotes are
        # local-path remotes dressed as URIs; they leak filesystem paths.
        if remote_url.startswith("file://"):
            return fallback_leaf if fallback_leaf is not None else remote_url
        return remote_url
    if remote_url.startswith(_SHARED_REMOTE_HOST_PREFIXES):
        return remote_url
    if _is_local_path_remote(remote_url):
        return fallback_leaf if fallback_leaf is not None else remote_url
    return remote_url


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


def _generator_identity_lines(generator: dict[str, object]) -> list[str]:
    commit = generator.get("git_commit")
    dirty = generator.get("git_dirty")
    repository = generator.get("repository")
    lines = [
        f"- LawVM generator commit: `{commit}`",
        f"- LawVM generator dirty: `{dirty}`",
    ]
    if isinstance(repository, str) and repository:
        lines.append(f"- LawVM generator repository: `{repository}`")
    return lines
