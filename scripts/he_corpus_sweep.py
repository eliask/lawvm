#!/usr/bin/env python
"""LIVE draft-HE corpus sweep — surface real bugs in the source_document lowering.

For every draft-HE PDF under a corpus directory, this script runs the FULL
deterministic ``source_document`` lowering (no LLM):

    bytes → SourceManifestation → ingest_pdf_manifestation → reading-order text
          → extract_conditional_branch → ProposalPackage

and records, per document: filename, ``HeDocKind``, #branches, #candidate ops,
the resolved target statute ids, the findings, and a coarse status:

* ``clean``   — candidate ops > 0 and no findings;
* ``partial`` — candidate ops > 0 with findings, OR a määräys/muistio that
                produced the expected "reasoning-only" finding (the honest
                non-HE outcome, not a bug);
* ``failed``  — an HE_BILL that produced zero ops, or an exception.

No hardcoded paths: the corpus directory comes from ``--corpus-dir`` (default
``$LAWVM_HE_CORPUS_DIR``); the script errors if neither is set. It reads
``**/he/*.pdf`` and ``**/sources/*.pdf`` under that directory.

Run (LawVM canonical data root must be pointed at the book LawVM tree)::

    LAWVM_CANONICAL_DATA_ROOT=/path/to/LawVM \
    LAWVM_HE_CORPUS_DIR=/path/to/lausunnot \
    uv run python scripts/he_corpus_sweep.py
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.finland.source_document.he_draft import (
    HeDocKind,
    classify_he_document,
    extract_conditional_branch,
    reading_order_text_from_pdf,
)
from lawvm.finland.source_document.pdf_profiles import ingest_pdf_manifestation

# Status buckets (a small, testable vocabulary).
STATUS_CLEAN = "clean"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DocResult:
    """One document's sweep outcome — the row of the findings table."""

    filename: str
    rel_path: str
    doc_kind: str
    n_branches: int
    n_candidate_ops: int
    target_statute_ids: Tuple[str, ...]
    findings: Tuple[str, ...]
    status: str
    error: str = ""


def classify_status(
    *,
    doc_kind: HeDocKind,
    n_candidate_ops: int,
    findings: Tuple[str, ...],
    errored: bool,
) -> str:
    """Bucket a document into clean / partial / failed.

    * an exception → ``failed``;
    * a non-HE document (määräys / muistio) that yielded no ops → ``partial``
      (the honest reasoning-only outcome is expected, not a bug);
    * an HE_BILL with zero ops → ``failed`` (a real bill that produced nothing);
    * ops > 0 with no findings → ``clean``;
    * ops > 0 with findings → ``partial``.
    """
    if errored:
        return STATUS_FAILED
    if n_candidate_ops == 0:
        # No ops: honest only when the document is not a bill.
        if doc_kind is HeDocKind.HE_BILL:
            return STATUS_FAILED
        return STATUS_PARTIAL
    return STATUS_CLEAN if not findings else STATUS_PARTIAL


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


def sweep_one(pdf_path: Path, corpus_dir: Path) -> DocResult:
    """Run the deterministic lowering on one PDF and record its outcome."""
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
        reading_order_text = reading_order_text_from_pdf(pdf_bytes)
        package = extract_conditional_branch(
            result.root,
            proposal_id,
            reading_order_text=reading_order_text,
            source_manifestation_digests=(digest,),
        )
        # Kind is what the ingested tree classifies as (independent of the
        # reading-order producer); it is what drives the status verdict.
        doc_kind = classify_he_document(result.root)
        n_ops = sum(len(b.candidate_ops) for b in package.branches)
        target_ids = tuple(
            sorted(
                {
                    op.target_statute_id
                    for b in package.branches
                    for op in b.candidate_ops
                    if op.target_statute_id
                }
            )
        )
        status = classify_status(
            doc_kind=doc_kind,
            n_candidate_ops=n_ops,
            findings=package.findings,
            errored=False,
        )
        return DocResult(
            filename=pdf_path.name,
            rel_path=rel,
            doc_kind=str(doc_kind),
            n_branches=len(package.branches),
            n_candidate_ops=n_ops,
            target_statute_ids=target_ids,
            findings=package.findings,
            status=status,
        )
    except Exception as exc:  # noqa: BLE001 — a live sweep must not abort on one bad PDF
        return DocResult(
            filename=pdf_path.name,
            rel_path=rel,
            doc_kind="?",
            n_branches=0,
            n_candidate_ops=0,
            target_statute_ids=(),
            findings=(f"exception: {type(exc).__name__}: {exc}",),
            status=STATUS_FAILED,
            error="".join(traceback.format_exception(exc)),
        )


@dataclass(frozen=True, slots=True)
class SweepReport:
    """The whole-corpus outcome — rows + bucket counts."""

    rows: Tuple[DocResult, ...] = field(default_factory=tuple)

    @property
    def counts(self) -> dict[str, int]:
        c = {STATUS_CLEAN: 0, STATUS_PARTIAL: 0, STATUS_FAILED: 0}
        for r in self.rows:
            c[r.status] += 1
        return c


def run_sweep(corpus_dir: Path) -> SweepReport:
    pdfs = _find_pdfs(corpus_dir)
    return SweepReport(rows=tuple(sweep_one(p, corpus_dir) for p in pdfs))


def _print_report(report: SweepReport) -> None:
    counts = report.counts
    print(f"# HE corpus sweep — {len(report.rows)} PDFs")
    print(
        f"clean={counts[STATUS_CLEAN]}  "
        f"partial={counts[STATUS_PARTIAL]}  "
        f"failed={counts[STATUS_FAILED]}"
    )
    print()
    for r in report.rows:
        print(f"[{r.status.upper():7}] {r.doc_kind:8} {r.rel_path}")
        print(
            f"          branches={r.n_branches} ops={r.n_candidate_ops} "
            f"targets={list(r.target_statute_ids)}"
        )
        for f in r.findings:
            print(f"          - {f}")


def _write_markdown(report: SweepReport, out_path: Path) -> None:
    counts = report.counts
    lines: List[str] = []
    lines.append("# HE corpus sweep — raw table\n")
    lines.append(
        f"clean={counts[STATUS_CLEAN]}  partial={counts[STATUS_PARTIAL]}  "
        f"failed={counts[STATUS_FAILED]}  total={len(report.rows)}\n"
    )
    lines.append("| status | kind | branches | ops | targets | doc |")
    lines.append("|---|---|---|---|---|---|")
    for r in report.rows:
        targets = ", ".join(r.target_statute_ids) or "-"
        lines.append(
            f"| {r.status} | {r.doc_kind} | {r.n_branches} | "
            f"{r.n_candidate_ops} | {targets} | {r.rel_path} |"
        )
    lines.append("\n## Findings per document\n")
    for r in report.rows:
        if not r.findings:
            continue
        lines.append(f"### {r.rel_path} [{r.status}]")
        for f in r.findings:
            lines.append(f"- {f}")
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


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
        help="Optional path to write a Markdown table of the raw rows.",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)
    if not args.corpus_dir:
        print(
            "error: --corpus-dir (or $LAWVM_HE_CORPUS_DIR) is required",
            file=sys.stderr,
        )
        return 2
    corpus_dir = Path(args.corpus_dir).expanduser()
    if not corpus_dir.is_dir():
        print(f"error: corpus dir does not exist: {corpus_dir}", file=sys.stderr)
        return 2
    report = run_sweep(corpus_dir)
    _print_report(report)
    if args.markdown_out:
        _write_markdown(report, Path(args.markdown_out).expanduser())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
