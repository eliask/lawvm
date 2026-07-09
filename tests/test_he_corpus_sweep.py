"""Hermetic unit test of the HE-corpus-sweep status vocabulary.

The live sweep (``scripts/he_corpus_sweep.py``) reads real corpus PDFs; this
test pins only the deterministic status-classification logic — the clean /
partial / failed verdict — so the honest-accuracy buckets cannot silently drift.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from lawvm.finland.source_document.he_draft import HeDocKind

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "he_corpus_sweep.py"
_spec = importlib.util.spec_from_file_location("he_corpus_sweep", _SCRIPT)
assert _spec is not None and _spec.loader is not None
sweep = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass(slots=True) can resolve the module dict
# (dataclasses looks the owning module up in sys.modules during processing).
sys.modules["he_corpus_sweep"] = sweep
_spec.loader.exec_module(sweep)


def _classify(kind: HeDocKind, ops: int, findings: tuple[str, ...], errored: bool = False) -> str:
    return sweep.classify_status(
        doc_kind=kind, n_candidate_ops=ops, findings=findings, errored=errored
    )


def test_clean_is_bill_with_ops_and_no_findings() -> None:
    assert _classify(HeDocKind.HE_BILL, ops=3, findings=()) == sweep.STATUS_CLEAN


def test_bill_with_ops_but_findings_is_partial() -> None:
    assert (
        _classify(HeDocKind.HE_BILL, ops=1, findings=("law 2 unresolved",))
        == sweep.STATUS_PARTIAL
    )


def test_bill_with_zero_ops_is_failed() -> None:
    # A real bill that produced nothing is a failure, not an honest empty set.
    assert _classify(HeDocKind.HE_BILL, ops=0, findings=()) == sweep.STATUS_FAILED


def test_maarays_with_zero_ops_is_partial_not_failed() -> None:
    # A regulatory order legitimately carries no bill text — reasoning-only.
    assert (
        _classify(HeDocKind.MAARAYS, ops=0, findings=("reasoning-only",))
        == sweep.STATUS_PARTIAL
    )


def test_muistio_with_zero_ops_is_partial_not_failed() -> None:
    assert (
        _classify(HeDocKind.MUISTIO, ops=0, findings=("reasoning-only",))
        == sweep.STATUS_PARTIAL
    )


def test_exception_is_always_failed() -> None:
    # Even a määräys is 'failed' when the pipeline threw (e.g. missing producer).
    assert (
        _classify(HeDocKind.MAARAYS, ops=0, findings=("exception",), errored=True)
        == sweep.STATUS_FAILED
    )


def test_report_counts_bucket_rows() -> None:
    rows = (
        sweep.DocResult(
            filename="a.pdf",
            rel_path="x/he/a.pdf",
            doc_kind="he_bill",
            n_branches=1,
            n_candidate_ops=2,
            target_statute_ids=("603/2006",),
            findings=(),
            status=sweep.STATUS_CLEAN,
        ),
        sweep.DocResult(
            filename="b.pdf",
            rel_path="x/he/b.pdf",
            doc_kind="he_bill",
            n_branches=0,
            n_candidate_ops=0,
            target_statute_ids=(),
            findings=("no johtolause",),
            status=sweep.STATUS_FAILED,
        ),
        sweep.DocResult(
            filename="c.pdf",
            rel_path="x/he/c.pdf",
            doc_kind="maarays",
            n_branches=0,
            n_candidate_ops=0,
            target_statute_ids=(),
            findings=("reasoning-only",),
            status=sweep.STATUS_PARTIAL,
        ),
    )
    report = sweep.SweepReport(rows=rows)
    counts = report.counts
    assert counts[sweep.STATUS_CLEAN] == 1
    assert counts[sweep.STATUS_PARTIAL] == 1
    assert counts[sweep.STATUS_FAILED] == 1
