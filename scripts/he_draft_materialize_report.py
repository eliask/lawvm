#!/usr/bin/env python
"""Draft-HE materialize + cross-bill conflict report — the "trustworthy milestone".

Extends the corpus sweep (``scripts/he_corpus_sweep.py``) from "did the lowering
produce ops?" to "what would the law SAY if this bill passed, and does any other
pending bill touch the same provision?". For every draft-HE classified
``HE_BILL`` under a corpus directory this script:

1. lowers the PDF to a ``ProposalPackage`` (the deterministic ``source_document``
   pipeline, no LLM) and publishes, per branch, the target statute + each
   candidate op's ``AssuranceTier`` (the honest per-change confidence);

2. for each branch whose target statute resolves, loads the CURRENTLY ENACTED
   provision (full amendment replay via ``load_enacted_provision``) and
   materializes the counterfactual "if enacted" section IR + human diff
   (``materialize_conditional_provision``) — "§4 gains momentti 5: …". A statute
   / section not in the corpus is a TYPED skip (``SKIP_STATUTE_UNRESOLVED`` /
   ``SKIP_SECTION_NOT_IN_CORPUS``), never a crash and never a fabricated diff;

3. builds a CROSS-BILL conflict report: it groups every branch across ALL bills
   by ``(target_statute_id, section)`` and flags where TWO DIFFERENT bills touch
   the SAME provision — the collision no official consolidation surfaces.

Everything is a typed carrier. No hardcoded paths: the corpus directory comes
from ``--corpus-dir`` (default ``$LAWVM_HE_CORPUS_DIR``); ``load_enacted_provision``
needs ``$LAWVM_CANONICAL_DATA_ROOT`` pointed at the replay corpus (branches are
typed-skipped, never crashed, when it is absent).

Run::

    LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM \
    LAWVM_HE_CORPUS_DIR=/path/to/lausunnot \
    uv run --extra pdf python scripts/he_draft_materialize_report.py \
        --markdown-out notes_internal/he_draft_materialize_report.md
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from typing_extensions import override

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.core.source_document.proposal import (
    ConditionalBranch,
    ProposalPackage,
)
from lawvm.finland.source_document.he_draft import (
    HeDocKind,
    classify_he_document,
    extract_conditional_branch,
    reading_order_text_from_pdf,
)
from lawvm.finland.source_document.materialize import (
    load_enacted_provision,
    materialize_conditional_provision,
)
from lawvm.finland.source_document.pdf_profiles import ingest_pdf_manifestation


# --------------------------------------------------------------------------- #
# Typed outcome vocabulary                                                     #
# --------------------------------------------------------------------------- #
class MaterializeStatus(Enum):
    """Per-branch materialization outcome — every one is typed, never silent."""

    MATERIALIZED = "materialized"
    """The enacted provision loaded and the branch's ops applied → a diff."""
    APPLIED_NO_DIFF = "applied_no_diff"
    """Enacted provision loaded but every op produced a finding (no clean diff)."""
    SKIP_STATUTE_UNRESOLVED = "skip_statute_unresolved"
    """The branch names no well-formed target statute (nothing to load)."""
    SKIP_NO_SECTION = "skip_no_section"
    """The branch's ops name no section locator to load a provision for."""
    SKIP_STATUTE_NOT_IN_CORPUS = "skip_statute_not_in_corpus"
    """The statute did not compile from the replay corpus (absent / build error)."""
    SKIP_SECTION_NOT_IN_CORPUS = "skip_section_not_in_corpus"
    """The statute compiled but the named section was not found in it."""
    SKIP_NO_DATA_ROOT = "skip_no_data_root"
    """No LAWVM_CANONICAL_DATA_ROOT — the enacted timeline cannot be loaded."""

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class BranchMaterialization:
    """One branch's target + assurance profile + materialization outcome."""

    branch_id: str
    target_statute_id: str
    section_label: str
    n_ops: int
    assurance_tiers: Tuple[str, ...]
    status: MaterializeStatus
    diff_lines: Tuple[str, ...] = ()
    findings: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BillReport:
    """One HE_BILL document's lowering + per-branch materialization outcome."""

    rel_path: str
    proposal_id: str
    n_branches: int
    lowering_findings: Tuple[str, ...]
    branch_mats: Tuple[BranchMaterialization, ...]
    error: str = ""


# A cross-bill collision: >=2 DIFFERENT bills touch the same (statute, section).
@dataclass(frozen=True, slots=True)
class CrossBillCollision:
    """Two or more pending HEs proposing changes to the SAME provision."""

    statute_id: str
    section_label: str
    # (rel_path, branch_id, action, assurance) per touching op, ordered.
    touches: Tuple[Tuple[str, str, str, str], ...]

    @property
    def bill_paths(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for rel, _bid, _act, _tier in self.touches:
            if rel not in seen:
                seen.append(rel)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class MaterializeReport:
    """Whole-corpus report: per-bill materializations + cross-bill collisions."""

    bills: Tuple[BillReport, ...] = field(default_factory=tuple)
    collisions: Tuple[CrossBillCollision, ...] = field(default_factory=tuple)

    @property
    def status_counts(self) -> Dict[str, int]:
        c: Dict[str, int] = {s.value: 0 for s in MaterializeStatus}
        for bill in self.bills:
            for bm in bill.branch_mats:
                c[bm.status.value] += 1
        return c


# --------------------------------------------------------------------------- #
# Provision-ref → section label                                               #
# --------------------------------------------------------------------------- #
def _section_label_of(provision_ref: str) -> str:
    """Pull the section label from a ``section:4/subsection:5`` provision ref.

    ``load_enacted_provision`` addresses a section by its bare label ("4"); the
    candidate op's ``target_provision_ref`` is the rendered ``LegalAddress``
    (``section:4/subsection:5``). Returns the ``section`` label, or "" when the
    ref names no section (nothing loadable at section granularity).
    """
    for part in provision_ref.split("/"):
        kind, _, label = part.partition(":")
        if kind == "section" and label:
            return label
    return ""


def _dominant_section(branch: ConditionalBranch) -> str:
    """The section this branch's ops predominantly target (first non-empty).

    A branch may edit several sections; we materialize the FIRST resolvable
    section as the representative counterfactual (the report names the rest in
    findings). Returns "" when no op names a section.
    """
    for op in branch.candidate_ops:
        label = _section_label_of(op.target_provision_ref)
        if label:
            return label
    return ""


def _branch_statute_id(branch: ConditionalBranch) -> str:
    """The (single) well-formed target statute id a branch amends, or ""."""
    for op in branch.candidate_ops:
        if op.target_statute_id:
            return op.target_statute_id
    return ""


# --------------------------------------------------------------------------- #
# Per-branch materialization                                                   #
# --------------------------------------------------------------------------- #
async def _materialize_branch(
    branch: ConditionalBranch,
    *,
    have_data_root: bool,
) -> BranchMaterialization:
    """Load the enacted provision + apply the branch → a typed outcome.

    Never raises for a missing statute/section: a load that returns ``None`` or
    a build error is a typed skip, not a crash and not a fabricated diff.
    """
    statute_id = _branch_statute_id(branch)
    section_label = _dominant_section(branch)
    tiers = tuple(str(op.assurance_tier) for op in branch.candidate_ops)

    def _out(
        status: MaterializeStatus,
        *,
        diff_lines: Tuple[str, ...] = (),
        findings: Tuple[str, ...] = (),
    ) -> BranchMaterialization:
        return BranchMaterialization(
            branch_id=branch.branch_id,
            target_statute_id=statute_id,
            section_label=section_label,
            n_ops=len(branch.candidate_ops),
            assurance_tiers=tiers,
            status=status,
            diff_lines=diff_lines,
            findings=findings,
        )

    if not statute_id:
        return _out(MaterializeStatus.SKIP_STATUTE_UNRESOLVED)
    if not section_label:
        return _out(MaterializeStatus.SKIP_NO_SECTION)
    if not have_data_root:
        return _out(MaterializeStatus.SKIP_NO_DATA_ROOT)

    try:
        enacted = await load_enacted_provision(statute_id, section_label)
    except Exception as exc:  # noqa: BLE001 — an unbuildable statute is a typed skip
        return _out(
            MaterializeStatus.SKIP_STATUTE_NOT_IN_CORPUS,
            findings=(f"statute {statute_id} did not compile: {type(exc).__name__}: {exc}",),
        )
    if enacted is None:
        return _out(
            MaterializeStatus.SKIP_SECTION_NOT_IN_CORPUS,
            findings=(f"§{section_label} not found in enacted {statute_id}",),
        )

    mat = materialize_conditional_provision(
        enacted,
        branch,
        statute_id=statute_id,
        provision_ref=f"section:{section_label}",
    )
    status = (
        MaterializeStatus.MATERIALIZED if mat.diff_lines else MaterializeStatus.APPLIED_NO_DIFF
    )
    return _out(status, diff_lines=mat.diff_lines, findings=mat.findings)


def _lower_bill(pdf_path: Path, corpus_dir: Path) -> Tuple[Optional[ProposalPackage], str, str]:
    """Lower one PDF to a ProposalPackage. Returns (package-or-None, rel, error)."""
    rel = pdf_path.relative_to(corpus_dir).as_posix()
    proposal_id = f"fi:he:{rel}"
    try:
        pdf_bytes = pdf_path.read_bytes()
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        manifestation = SourceManifestation(
            artifact_digest=digest,
            source_bytes=pdf_bytes,
            locator=rel,
            source_role="he_draft",
            fetched_at=datetime.now(tz=timezone.utc),
            media_type="application/pdf",
        )
        result = ingest_pdf_manifestation(manifestation)
        if classify_he_document(result.root) is not HeDocKind.HE_BILL:
            return None, rel, ""  # not a bill — the sweep already types these
        reading_order_text = reading_order_text_from_pdf(pdf_bytes)
        package = extract_conditional_branch(
            result.root,
            proposal_id,
            reading_order_text=reading_order_text,
            source_manifestation_digests=(digest,),
        )
        return package, rel, ""
    except Exception as exc:  # noqa: BLE001 — a live sweep must not abort on one PDF
        return None, rel, "".join(traceback.format_exception(exc))


async def _report_one_bill(
    package: ProposalPackage, rel: str, *, have_data_root: bool
) -> BillReport:
    branch_mats = tuple(
        [
            await _materialize_branch(b, have_data_root=have_data_root)
            for b in package.branches
        ]
    )
    return BillReport(
        rel_path=rel,
        proposal_id=package.proposal_id,
        n_branches=len(package.branches),
        lowering_findings=package.findings,
        branch_mats=branch_mats,
    )


# --------------------------------------------------------------------------- #
# Cross-bill conflict grouping                                                 #
# --------------------------------------------------------------------------- #
def cross_bill_collisions(
    bill_branches: Tuple[Tuple[str, ProposalPackage], ...],
) -> Tuple[CrossBillCollision, ...]:
    """Group every op across ALL bills by (statute, section); flag multi-bill hits.

    A collision is a ``(statute_id, section)`` touched by ops from TWO OR MORE
    DIFFERENT bills (by ``rel_path``). Two ops from the SAME bill on one provision
    are an intra-bill duplicate (``diagnose_branch_conflicts`` owns that); this
    layer surfaces only the cross-bill case — the thing no official consolidation
    offers. Deterministic order: sorted by (statute, section).
    """
    # (statute, section) -> ordered list of (rel_path, branch_id, action, tier)
    index: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = {}
    for rel_path, package in bill_branches:
        for branch in package.branches:
            for op in branch.candidate_ops:
                if not op.target_statute_id:
                    continue
                section = _section_label_of(op.target_provision_ref)
                if not section:
                    continue
                key = (op.target_statute_id, section)
                index.setdefault(key, []).append(
                    (rel_path, branch.branch_id, op.action, str(op.assurance_tier))
                )

    collisions: List[CrossBillCollision] = []
    for (statute_id, section), touches in sorted(index.items()):
        distinct_bills = {t[0] for t in touches}
        if len(distinct_bills) < 2:
            continue
        collisions.append(
            CrossBillCollision(
                statute_id=statute_id,
                section_label=section,
                touches=tuple(touches),
            )
        )
    return tuple(collisions)


# --------------------------------------------------------------------------- #
# Driver                                                                       #
# --------------------------------------------------------------------------- #
def _find_pdfs(corpus_dir: Path) -> List[Path]:
    """Every draft-HE PDF under ``**/he/`` and ``**/sources/`` (sorted, deduped)."""
    seen: set[Path] = set()
    out: List[Path] = []
    for pattern in ("**/he/*.pdf", "**/sources/*.pdf"):
        for p in sorted(corpus_dir.glob(pattern)):
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                out.append(p)
    return out


async def run_report(corpus_dir: Path, *, have_data_root: bool) -> MaterializeReport:
    """Lower every HE_BILL, materialize its branches, group cross-bill collisions."""
    bills: List[BillReport] = []
    bill_packages: List[Tuple[str, ProposalPackage]] = []
    for pdf_path in _find_pdfs(corpus_dir):
        package, rel, error = _lower_bill(pdf_path, corpus_dir)
        if error:
            bills.append(
                BillReport(
                    rel_path=rel,
                    proposal_id=f"fi:he:{rel}",
                    n_branches=0,
                    lowering_findings=(f"lowering exception: {error.splitlines()[-1]}",),
                    branch_mats=(),
                    error=error,
                )
            )
            continue
        if package is None:
            continue  # not an HE_BILL (already typed by the sweep)
        bill_packages.append((rel, package))
        bills.append(await _report_one_bill(package, rel, have_data_root=have_data_root))

    collisions = cross_bill_collisions(tuple(bill_packages))
    return MaterializeReport(bills=tuple(bills), collisions=collisions)


def _write_markdown(report: MaterializeReport, out_path: Path) -> None:
    counts = report.status_counts
    lines: List[str] = []
    lines.append("# Draft-HE materialize + cross-bill conflict report\n")
    lines.append(f"HE_BILLs: {len(report.bills)}\n")
    lines.append("## Per-branch materialization status counts\n")
    for status, n in counts.items():
        if n:
            lines.append(f"- {status}: {n}")
    lines.append("")

    lines.append("## Cross-bill collisions (>=2 DIFFERENT bills touch one provision)\n")
    if not report.collisions:
        lines.append("_none_\n")
    for col in report.collisions:
        lines.append(f"### {col.statute_id} §{col.section_label}")
        lines.append(f"Bills: {', '.join(col.bill_paths)}\n")
        for rel, bid, action, tier in col.touches:
            lines.append(f"- `{rel}` [{bid}] proposes **{action}** (assurance {tier})")
        lines.append("")

    lines.append("## Per-bill materialization\n")
    for bill in report.bills:
        lines.append(f"### {bill.rel_path}")
        lines.append(f"branches={bill.n_branches}")
        for f in bill.lowering_findings:
            lines.append(f"- lowering finding: {f}")
        for bm in bill.branch_mats:
            lines.append(
                f"- [{bm.status}] {bm.target_statute_id or '-'} §{bm.section_label or '-'} "
                f"ops={bm.n_ops} tiers={list(dict.fromkeys(bm.assurance_tiers))}"
            )
            for d in bm.diff_lines:
                lines.append(f"    - diff: {d}")
            for f in bm.findings:
                lines.append(f"    - finding: {f}")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _print_report(report: MaterializeReport) -> None:
    counts = report.status_counts
    print(f"# Draft-HE materialize report — {len(report.bills)} HE_BILLs")
    print("branch-materialization status:")
    for status, n in counts.items():
        if n:
            print(f"  {status}: {n}")
    print()
    print(f"# Cross-bill collisions: {len(report.collisions)}")
    for col in report.collisions:
        print(f"  {col.statute_id} §{col.section_label} — bills: {', '.join(col.bill_paths)}")
        for rel, bid, action, tier in col.touches:
            print(f"    - {rel} [{bid}] {action} ({tier})")
    print()
    materialized = [
        (bill.rel_path, bm)
        for bill in report.bills
        for bm in bill.branch_mats
        if bm.status is MaterializeStatus.MATERIALIZED
    ]
    print(f"# Materialized branches (with a concrete diff): {len(materialized)}")
    for rel, bm in materialized:
        print(f"  {rel} — {bm.target_statute_id} §{bm.section_label}")
        for d in bm.diff_lines:
            print(f"    {d}")


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir",
        default=os.environ.get("LAWVM_HE_CORPUS_DIR", ""),
        help="Root directory of the draft-HE corpus (default: $LAWVM_HE_CORPUS_DIR).",
    )
    parser.add_argument(
        "--markdown-out",
        default="",
        help="Optional path to write the full Markdown report (e.g. notes_internal/…).",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    if not args.corpus_dir:
        print("error: --corpus-dir (or $LAWVM_HE_CORPUS_DIR) is required", file=sys.stderr)
        return 2
    corpus_dir = Path(args.corpus_dir).expanduser()
    if not corpus_dir.is_dir():
        print(f"error: corpus dir does not exist: {corpus_dir}", file=sys.stderr)
        return 2
    have_data_root = bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))
    if not have_data_root:
        print(
            "warning: LAWVM_CANONICAL_DATA_ROOT unset — branches will be typed-skipped "
            "(SKIP_NO_DATA_ROOT), not materialized",
            file=sys.stderr,
        )
    report = asyncio.run(run_report(corpus_dir, have_data_root=have_data_root))
    _print_report(report)
    if args.markdown_out:
        _write_markdown(report, Path(args.markdown_out).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
