"""D1 coverage metric + quality-detector tests for ``lawvm.core.source_document``.

Pins the total+disjoint partition metric (``coverage_report``) and the owned-
content fidelity detectors (``detect_quality_issues``). The e2e case runs the
real Finnish PDF fixture and asserts the metric surfaces low-fidelity output
(e.g. a stray single-token body block) as a typed ``QualityIssue`` rather than
hiding it — the D1 compass made concrete (AGENTS.md §0 total accounting; §1.11
findings, not authority).

See the approved plan at ``.claude/plans/calm-kindling-wand.md`` (D1).
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import pytest

from lawvm.core.source_document import (
    AssuranceTier,
    CoverageReport,
    QualityIssueFamily,
    RegionOwnership,
    Residual,
    ResidualFamily,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
    SourceManifestation,
    coverage_report,
    detect_quality_issues,
)
from lawvm.finland.source_document import ingest_pdf_manifestation

_DIGEST = "a" * 64
_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "finland" / "oikaisu_fi.pdf"


def _para(text: str, page: int = 1) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.PARAGRAPH,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator=f"page={page}", page_num=page),
        text=text,
    )


def _root(children: tuple[SourceDocumentNode, ...]) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="manifestation"),
        children=children,
    )


# ---------------------------------------------------------------------------
# detect_quality_issues
# ---------------------------------------------------------------------------


def test_quality_detectors_flag_low_fidelity_body_artifacts() -> None:
    root = _root(
        (
            _para("1"),                       # stray token -> SUSPECT_SHORT_BODY
            _para("Tämä on oikea kappale."),  # clean -> no issue
            _para("yhdistel-"),               # trailing hyphen -> HYPHENATION_ARTIFACT
            _para("12)"),                     # footnote-marker leak -> FOOTNOTE_MARKER_IN_BODY
        )
    )
    issues = detect_quality_issues(root)
    families = {i.family for i in issues}
    assert QualityIssueFamily.SUSPECT_SHORT_BODY in families
    assert QualityIssueFamily.HYPHENATION_ARTIFACT in families
    assert QualityIssueFamily.FOOTNOTE_MARKER_IN_BODY in families
    # Clean paragraphs are not flagged.
    snippets = {i.snippet for i in issues}
    assert "Tämä on oikea kappale." not in snippets
    # Each issue embeds its snippet (§1.10 — no re-running extraction to triage).
    assert all(i.snippet for i in issues)


def test_quality_detectors_skip_non_body_nodes() -> None:
    # Short text in a table cell or footnote is legitimate, not an artifact.
    cell = SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE_CELL,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=1;table=0;row=0;col=0", page_num=1),
        text="1",
    )
    assert detect_quality_issues(_root((cell,))) == ()


# ---------------------------------------------------------------------------
# coverage_report
# ---------------------------------------------------------------------------


def test_coverage_report_partition_and_ratio() -> None:
    root = _root((_para("x", page=1), _para("y", page=2)))
    residuals = (
        Residual(
            family=ResidualFamily.PDF_TEXT_LAYER_EMPTY,
            ownership=RegionOwnership.RESIDUAL,
            anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=3", page_num=3),
        ),
        Residual(
            family=ResidualFamily.PDF_TEXT_LAYER_EMPTY,
            ownership=RegionOwnership.BLOCKED,
            anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=4", page_num=4),
        ),
    )
    report: CoverageReport = coverage_report(root, residuals, page_count=4)
    assert report.owned_pages == (1, 2)
    assert report.residual_pages == (3,)
    assert report.blocked_pages == (4,)
    assert report.page_coverage_ratio == 0.5
    assert report.residual_count_by_family == {"pdf.text_layer_empty": 2}
    # WORK_ROOT + 2 paragraphs = 3 nodes.
    assert report.owned_node_count == 3


def test_coverage_report_zero_scope_is_safe() -> None:
    empty = _root(())
    report = coverage_report(empty, residuals=(), page_count=0)
    assert report.page_coverage_ratio == 0.0
    assert report.owned_pages == ()


# ---------------------------------------------------------------------------
# e2e: the metric surfaces real fidelity gaps on the Finnish fixture
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FIXTURE.exists(), reason="oikaisu fixture not present")
def test_real_pdf_coverage_metric_surfaces_stray_tokens() -> None:
    bytes_ = _FIXTURE.read_bytes()
    manifestation = SourceManifestation(
        artifact_digest=hashlib.sha256(bytes_).hexdigest(),
        source_bytes=bytes_,
        locator="finland/oikaisu_fi.pdf",
        source_role="corrigendum",
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )
    result = ingest_pdf_manifestation(manifestation, max_pages=2)
    issues = detect_quality_issues(result.root)
    report = coverage_report(result.root, result.residuals, result.page_count, issues)

    # Both in-scope pages owned by deterministic extraction.
    assert report.page_coverage_ratio == 1.0
    # The deterministic baseline is NOT flawless: stray short bodies leak through
    # and the D1 compass surfaces them as typed findings, not hidden garbage.
    assert report.quality_issue_count >= 1
    assert any(i.family is QualityIssueFamily.SUSPECT_SHORT_BODY for i in issues)
