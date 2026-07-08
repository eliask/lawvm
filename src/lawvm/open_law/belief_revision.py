"""Cross-branch belief-revision coherence check for Open Law publication branches.

Open Law's temporal model has two axes: *legal time* (when the law is/was in
force) and *observer time* (when someone rendered a belief about that legal
time). "What was the law at time X" is not answerable on its own; the best that
can be said is "what did an observer at time Y believe the law at time X was."

Open Law operationalizes this with **publication branches**. A branch
``publication/D`` publishes a belief about a legal slice ``D``. When history is
later revised, a paired belief-revision branch ``publication/D.D'`` re-publishes
a belief about the *same* legal slice ``D`` as revised by an observer at
observer-time ``D'``. Both branches are full point-in-time snapshots of the same
codified corpus; they can, and do, disagree about the past law.

The existing corpus auditor (``corpus_audit.py``) checks **sequential forward
transitions** driven by *newly included editorial actions*: it never compares
two beliefs about the *same* legal slice, and belief-revision branches carry no
new editorial actions at all, so they are invisible to it. That auditor is a
cooperative self-attestation check: the codified lane grades its own homework.

This module builds the genuinely independent, adversarial check the
self-attesting regime cannot perform on itself:

    For each pair of branches publishing the same legal slice, and for each
    codified document they both carry, diff the two beliefs about that
    document, and ADJUDICATE every divergence against the *source* lane
    (``law-xml`` editorial actions).

A divergence is EXPLAINED iff a declared editorial action introduced in the
source lane *between the two branches' source commits* accounts for it -- an
editorial-action ``codify:*`` targeting the same document, a retroactive
``applicability`` date, or an ``expire``. If no declared source-lane cause is
found -- in particular when both branches were built from the *same* source
commit, so no editorial action could possibly differ -- the later branch has
silently changed its account of past law. That is a first-class typed finding,
``open_law_cross_branch_silent_revision``: the exact thing a self-attesting
compiled lane cannot catch about itself.

Discipline: the codified/compiled lane is never authority over the source lane.
A cross-branch delta we cannot explain from the source lane is a *finding*, not
a reason to prefer either belief. Findings are self-evidencing: they carry the
document, the legal slice, both belief hashes, and the searched-for-and-absent
declared cause.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Tuple

from lawvm.core.ir import IRNode
from lawvm.open_law.audit import (
    _project_annotations_for_snapshot_compare,
    _project_typography_for_snapshot_compare,
)
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.corpus_audit import _project_generated_metadata_history
from lawvm.open_law.local_git import MarylandLocalRepos
from lawvm.open_law.maryland import MarylandPublicationMetadata, build_maryland_inventory
from lawvm.open_law.models import OpenLawAction, OpenLawOperation
from lawvm.open_law.xml import parse_open_law_xml

_COMAR_PREFIX = "us/md/exec/comar/"
_MARYLAND_COMAR_DOC = "Code of Maryland Regulations"


@dataclass(frozen=True)
class DeclaredEditorialCause:
    """A source-lane editorial action that could explain a codified belief delta."""

    source_id: str
    action: str
    codify_path: Tuple[str, ...]
    applicability: str
    expire_date: str
    effective: str


@dataclass(frozen=True)
class CrossBranchDocumentFinding:
    """One adjudicated cross-branch belief delta for one codified document.

    ``explained`` is ``True`` iff a declared source-lane editorial cause accounts
    for the delta; otherwise this is a silent revision -- the later branch
    changed its account of the same legal slice with no declared cause. Both
    outcomes carry the full self-evidencing payload (document, legal slice, both
    belief hashes) so a finding can be verified without re-running the tool.
    """

    kind: str
    xml_path: str
    comar_locator: Tuple[str, ...]
    publication_slice: str
    earlier_branch: str
    later_branch: str
    earlier_source_commit: str
    later_source_commit: str
    earlier_belief_sha256: str
    later_belief_sha256: str
    explained: bool
    declared_causes: Tuple[DeclaredEditorialCause, ...]
    message: str
    blocking: bool


@dataclass(frozen=True)
class CrossBranchPairReport:
    """Belief-revision audit for one same-legal-slice branch pair."""

    publication_slice: str
    earlier_branch: str
    later_branch: str
    earlier_source_commit: str
    later_source_commit: str
    same_source_commit: bool
    documents_compared: int
    documents_diverged: int
    findings: Tuple[CrossBranchDocumentFinding, ...]


@dataclass(frozen=True)
class CrossBranchBeliefReport:
    """Full cross-branch belief-revision report over all same-slice pairs."""

    pair_reports: Tuple[CrossBranchPairReport, ...]
    summary: dict[str, int]


def audit_maryland_belief_revisions(
    *,
    repos: MarylandLocalRepos,
    limit: int | None = None,
    strict: bool = False,
) -> CrossBranchBeliefReport:
    """Audit every pair of branches that publish the same legal slice.

    ``limit`` caps the number of *documents diverged* audited across all pairs
    (a bound on work, not on truth): once reached the report is returned as-is.
    ``strict`` marks silent-revision findings as blocking.
    """

    inventory = build_maryland_inventory(repos)
    pair_reports: list[CrossBranchPairReport] = []
    diverged_budget = limit
    for earlier, later in _same_slice_pairs(inventory.publication_branches):
        report = _audit_pair(repos, earlier, later, strict=strict, diverged_budget=diverged_budget)
        pair_reports.append(report)
        if diverged_budget is not None:
            diverged_budget -= report.documents_diverged
            if diverged_budget <= 0:
                break
    return _report(tuple(pair_reports))


def _same_slice_pairs(
    branches: Tuple[MarylandPublicationMetadata, ...],
) -> Tuple[Tuple[MarylandPublicationMetadata, MarylandPublicationMetadata], ...]:
    """Group branches by published legal slice and order each pair by observer time.

    Two branches "cover overlapping legal time" when they publish the same
    ``<publication>`` legal slice. The earlier/later ordering is by observer
    time, taken from the branch's revision suffix (``D.D'`` observed at ``D'``)
    with the base ``D`` treated as the earliest observation of slice ``D``.
    """

    by_slice: dict[str, list[MarylandPublicationMetadata]] = {}
    for item in branches:
        slice_key = item.publication or item.branch
        by_slice.setdefault(slice_key, []).append(item)
    pairs: list[Tuple[MarylandPublicationMetadata, MarylandPublicationMetadata]] = []
    for members in by_slice.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_observer_sort_key)
        for earlier, later in combinations(ordered, 2):
            pairs.append((earlier, later))
    return tuple(pairs)


def _observer_sort_key(item: MarylandPublicationMetadata) -> tuple[str, str, str]:
    # Observer time is the branch's revision suffix (D' in publication/D.D');
    # branches without a suffix are the earliest observation of the slice. Fall
    # back to build-date then branch name for a total, deterministic order.
    suffix = item.branch.split(".", 1)[1] if "." in item.branch.split("/", 1)[-1] else ""
    return (suffix, item.build_date, item.branch)


def _audit_pair(
    repos: MarylandLocalRepos,
    earlier: MarylandPublicationMetadata,
    later: MarylandPublicationMetadata,
    *,
    strict: bool,
    diverged_budget: int | None,
) -> CrossBranchPairReport:
    same_source = earlier.source_commit == later.source_commit and bool(earlier.source_commit)
    causes_index = _declared_editorial_causes(repos, earlier.source_commit, later.source_commit)
    changed_docs = _changed_comar_documents(repos, earlier.branch, later.branch)
    findings: list[CrossBranchDocumentFinding] = []
    diverged = 0
    for xml_path in changed_docs:
        finding = _adjudicate_document(
            repos,
            earlier,
            later,
            xml_path,
            same_source=same_source,
            causes_index=causes_index,
            strict=strict,
        )
        if finding is None:
            continue
        diverged += 1
        findings.append(finding)
        if diverged_budget is not None and diverged >= diverged_budget:
            break
    return CrossBranchPairReport(
        publication_slice=earlier.publication or earlier.branch,
        earlier_branch=earlier.branch,
        later_branch=later.branch,
        earlier_source_commit=earlier.source_commit,
        later_source_commit=later.source_commit,
        same_source_commit=same_source,
        documents_compared=len(changed_docs),
        documents_diverged=diverged,
        findings=tuple(findings),
    )


def _adjudicate_document(
    repos: MarylandLocalRepos,
    earlier: MarylandPublicationMetadata,
    later: MarylandPublicationMetadata,
    xml_path: str,
    *,
    same_source: bool,
    causes_index: dict[Tuple[str, ...], Tuple[DeclaredEditorialCause, ...]],
    strict: bool,
) -> CrossBranchDocumentFinding | None:
    earlier_belief = _render_belief(repos, earlier.branch, xml_path)
    later_belief = _render_belief(repos, later.branch, xml_path)
    if earlier_belief == later_belief:
        # The raw git blobs differed (that is why this document is a candidate)
        # but the beliefs are equal once annotations, generated hidden history,
        # and quote typography are projected out for legal-text comparison.
        return None
    locator = _comar_locator(xml_path)
    causes = _causes_for_locator(causes_index, locator)
    earlier_sha = _belief_sha256(earlier_belief)
    later_sha = _belief_sha256(later_belief)
    if causes:
        return CrossBranchDocumentFinding(
            kind="open_law_cross_branch_belief_revision_explained",
            xml_path=xml_path,
            comar_locator=locator,
            publication_slice=earlier.publication or earlier.branch,
            earlier_branch=earlier.branch,
            later_branch=later.branch,
            earlier_source_commit=earlier.source_commit,
            later_source_commit=later.source_commit,
            earlier_belief_sha256=earlier_sha,
            later_belief_sha256=later_sha,
            explained=True,
            declared_causes=causes,
            message=(
                f"Later belief branch {later.branch!r} revised its account of legal slice "
                f"{earlier.publication or earlier.branch!r} for {xml_path!r}; the revision is EXPLAINED by "
                f"{len(causes)} declared source-lane editorial action(s) introduced between source commits "
                f"{_short(earlier.source_commit)}..{_short(later.source_commit)}."
            ),
            blocking=False,
        )
    reason = (
        "both branches were built from the same source commit, so no editorial action can differ between them"
        if same_source
        else "no declared editorial action introduced between the two source commits targets this document"
    )
    return CrossBranchDocumentFinding(
        kind="open_law_cross_branch_silent_revision",
        xml_path=xml_path,
        comar_locator=locator,
        publication_slice=earlier.publication or earlier.branch,
        earlier_branch=earlier.branch,
        later_branch=later.branch,
        earlier_source_commit=earlier.source_commit,
        later_source_commit=later.source_commit,
        earlier_belief_sha256=earlier_sha,
        later_belief_sha256=later_sha,
        explained=False,
        declared_causes=(),
        message=(
            f"Later belief branch {later.branch!r} silently changed its account of legal slice "
            f"{earlier.publication or earlier.branch!r} for {xml_path!r}: the two beliefs diverge "
            f"(earlier belief {earlier_sha[:12]}, later belief {later_sha[:12]}) but {reason}. A self-attesting "
            "compiled lane cannot catch this revision about itself."
        ),
        blocking=strict,
    )


def _render_belief(repos: MarylandLocalRepos, branch: str, xml_path: str) -> IRNode:
    """Render one branch's point-in-time belief about one document as comparison IR.

    Reuses the same legal-text projection lanes the snapshot auditor uses, so a
    cross-branch delta means a *legal-text* delta, not a publication-metadata,
    generated-hidden-history, or quote-typography artifact.
    """

    tree = parse_open_law_xml(repos.codified.read_text(branch, xml_path))
    tree = _project_generated_metadata_history(tree)
    tree = _project_annotations_for_snapshot_compare(tree)
    tree = _project_typography_for_snapshot_compare(tree)
    return tree


def _belief_sha256(node: IRNode) -> str:
    payload = json.dumps(node.to_jsonable_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _changed_comar_documents(repos: MarylandLocalRepos, earlier_branch: str, later_branch: str) -> Tuple[str, ...]:
    """COMAR documents whose git blob differs between the two branches.

    Uses the read-only ``list_tree`` blob SHAs, so identical content -> identical
    SHA -> skipped: only genuinely-differing blobs are rendered and adjudicated.
    Documents present in only one branch are not belief *revisions* of a shared
    document and are out of scope for this check.
    """

    earlier_blobs = {
        entry.path: entry.sha
        for entry in repos.codified.list_tree(earlier_branch)
        if _is_comar_document(entry.path)
    }
    later_blobs = {
        entry.path: entry.sha
        for entry in repos.codified.list_tree(later_branch)
        if _is_comar_document(entry.path)
    }
    return tuple(
        sorted(
            path
            for path, sha in earlier_blobs.items()
            if path in later_blobs and later_blobs[path] != sha
        )
    )


def _is_comar_document(path: str) -> bool:
    return path.startswith(_COMAR_PREFIX) and path.endswith(".xml")


def _declared_editorial_causes(
    repos: MarylandLocalRepos,
    earlier_source_commit: str,
    later_source_commit: str,
) -> dict[Tuple[str, ...], Tuple[DeclaredEditorialCause, ...]]:
    """Index source-lane editorial actions *introduced* between the two commits.

    An editorial action can explain a belief revision only if it is present in
    the later branch's source tree but absent (or changed) in the earlier one.
    When the two source commits are identical the set is empty by construction:
    no declared cause can differ, so every belief delta is a silent revision.
    """

    if not earlier_source_commit or not later_source_commit:
        return {}
    if earlier_source_commit == later_source_commit:
        return {}
    earlier_actions = _editorial_action_blobs(repos, earlier_source_commit)
    later_actions = _editorial_action_blobs(repos, later_source_commit)
    index: dict[Tuple[str, ...], list[DeclaredEditorialCause]] = {}
    for path, sha in later_actions.items():
        if earlier_actions.get(path) == sha:
            continue  # unchanged action: already reflected in the earlier belief
        xml_text = repos.source.read_text(later_source_commit, path)
        for op in parse_open_law_codify_ops(xml_text, source_id=path):
            if op.doc and op.doc != _MARYLAND_COMAR_DOC:
                continue
            locator = _codify_chapter_locator(op)
            if locator is None:
                continue
            index.setdefault(locator, []).append(
                DeclaredEditorialCause(
                    source_id=op.source_id or path,
                    action=_action_name(op),
                    codify_path=op.path,
                    applicability=op.applicability,
                    expire_date=op.expire_date,
                    effective=op.effective,
                )
            )
    return {locator: tuple(causes) for locator, causes in index.items()}


def _editorial_action_blobs(repos: MarylandLocalRepos, source_commit: str) -> dict[str, str]:
    return {
        entry.path: entry.sha
        for entry in repos.source.list_tree(source_commit)
        if entry.type == "blob" and entry.path.startswith("editorial-actions/") and entry.path.endswith(".xml")
    }


def _codify_chapter_locator(op: OpenLawOperation) -> Tuple[str, ...] | None:
    # An editorial action targets `title|subtitle|chapter|...`; the codified
    # document is one chapter file, so we adjudicate at chapter granularity. A
    # `title|heading` or `title|subtitle|heading` action targets an index file
    # and is keyed by its title/subtitle prefix.
    if len(op.path) >= 3 and op.path[2] != "heading":
        return tuple(op.path[:3])
    if len(op.path) == 2 and op.path[1] == "heading":
        return (op.path[0],)
    if len(op.path) == 3 and op.path[2] == "heading":
        return tuple(op.path[:2])
    return None


def _causes_for_locator(
    causes_index: dict[Tuple[str, ...], Tuple[DeclaredEditorialCause, ...]],
    locator: Tuple[str, ...],
) -> Tuple[DeclaredEditorialCause, ...]:
    matched: list[DeclaredEditorialCause] = []
    for cause_locator, causes in causes_index.items():
        # A chapter-file delta is explained by an action on that chapter, or by
        # a broader index/heading action on the same title/subtitle prefix.
        if _locator_covers(cause_locator, locator) or _locator_covers(locator, cause_locator):
            matched.extend(causes)
    return tuple(matched)


def _locator_covers(prefix: Tuple[str, ...], candidate: Tuple[str, ...]) -> bool:
    return len(prefix) <= len(candidate) and candidate[: len(prefix)] == prefix


def _comar_locator(xml_path: str) -> Tuple[str, ...]:
    remainder = xml_path[len(_COMAR_PREFIX):].removesuffix(".xml")
    return tuple(part for part in remainder.split("/") if part)


def _action_name(op: OpenLawOperation) -> str:
    if op.action is OpenLawAction.UNSUPPORTED:
        return op.raw_action or "unsupported"
    return op.action.value


def _short(commit: str) -> str:
    return commit[:10] if commit else "-"


def _report(pair_reports: Tuple[CrossBranchPairReport, ...]) -> CrossBranchBeliefReport:
    silent = sum(
        1 for report in pair_reports for finding in report.findings if not finding.explained
    )
    explained = sum(
        1 for report in pair_reports for finding in report.findings if finding.explained
    )
    summary = {
        "pairs_audited": len(pair_reports),
        "pairs_same_source_commit": sum(1 for report in pair_reports if report.same_source_commit),
        "documents_compared": sum(report.documents_compared for report in pair_reports),
        "documents_diverged": sum(report.documents_diverged for report in pair_reports),
        "silent_revisions": silent,
        "explained_revisions": explained,
    }
    return CrossBranchBeliefReport(pair_reports=pair_reports, summary=summary)


def belief_report_to_jsonable(report: CrossBranchBeliefReport) -> dict[str, object]:
    return {
        "summary": report.summary,
        "pairs": [_pair_to_jsonable(pair) for pair in report.pair_reports],
    }


def _pair_to_jsonable(pair: CrossBranchPairReport) -> dict[str, object]:
    return {
        "publication_slice": pair.publication_slice,
        "earlier_branch": pair.earlier_branch,
        "later_branch": pair.later_branch,
        "earlier_source_commit": pair.earlier_source_commit,
        "later_source_commit": pair.later_source_commit,
        "same_source_commit": pair.same_source_commit,
        "documents_compared": pair.documents_compared,
        "documents_diverged": pair.documents_diverged,
        "findings": [_finding_to_jsonable(finding) for finding in pair.findings],
    }


def _finding_to_jsonable(finding: CrossBranchDocumentFinding) -> dict[str, object]:
    return {
        "kind": finding.kind,
        "xml_path": finding.xml_path,
        "comar_locator": list(finding.comar_locator),
        "publication_slice": finding.publication_slice,
        "earlier_branch": finding.earlier_branch,
        "later_branch": finding.later_branch,
        "earlier_source_commit": finding.earlier_source_commit,
        "later_source_commit": finding.later_source_commit,
        "earlier_belief_sha256": finding.earlier_belief_sha256,
        "later_belief_sha256": finding.later_belief_sha256,
        "explained": finding.explained,
        "blocking": finding.blocking,
        "declared_causes": [
            {
                "source_id": cause.source_id,
                "action": cause.action,
                "codify_path": list(cause.codify_path),
                "applicability": cause.applicability,
                "expire_date": cause.expire_date,
                "effective": cause.effective,
            }
            for cause in finding.declared_causes
        ],
        "message": finding.message,
    }


def write_belief_revision_report(report: CrossBranchBeliefReport, out_dir) -> None:
    """Write JSON/JSONL belief-revision artifacts (mirrors the corpus-audit writer)."""

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "belief_revision_summary.json").write_text(
        json.dumps(report.summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (directory / "belief_revisions.jsonl").open("w", encoding="utf-8") as handle:
        for pair in report.pair_reports:
            for finding in pair.findings:
                handle.write(json.dumps(_finding_to_jsonable(finding), ensure_ascii=False) + "\n")
