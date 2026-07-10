"""Coverage partition + typed residual taxonomy (D0).

Total accounting (AGENTS.md §0, §1.8): every source region ends up OWNED,
RESIDUAL, or BLOCKED — never silently dropped. A ``Residual`` is a first-class
typed object with a stable family (AGENTS.md §2.1 rule id) so a proposal lane
(D4 vision / human review / future OCR) can target exactly the families
deterministic extraction cannot own.

A residual is valuable BEFORE any vision lane exists: it is the honest output
of the determinism firewall — the core runs complete with every enricher off
and still produces typed residuals instead of silent holes
(``notes_internal/pro_on_unstructured_input_ingest.md`` §1.3).

Discipline (AGENTS.md §1.9, §1.10): typed frozen carrier; a residual carries
the offending snippet so triaging never requires re-running extraction.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Tuple

from typing_extensions import override

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import SourceDocumentNode, SourceDocumentNodeKind


class RegionOwnership(Enum):
    """Ownership state of one source region under total accounting."""

    OWNED = "owned"
    """Deterministic extraction owns this region (a native extraction assertion covers it)."""
    RESIDUAL = "residual"
    """Unowned / low-confidence — eligible for a proposal lane (vision / human / OCR)."""
    BLOCKED = "blocked"
    """Unreadable; no lane may propose (e.g. encrypted page). First-class, not an error."""

    @override
    def __str__(self) -> str:
        return self.value


class ResidualFamily(Enum):
    """Closed taxonomy of deterministic-extraction residuals.

    Values are jurisdiction-neutral: the ``PDF.`` / ``DOCX.`` prefix names the
    SOURCE FORMAT and ``GOVERNMENT_PROPOSAL_DRAFT.`` names a neutral SOURCE ROLE
    (see ``lawvm.core.source_document.extraction.SourceManifestation.source_role``),
    not a jurisdiction. Jurisdiction-specific residuals are carried as
    frontend-local strings at the boundary, mirroring
    ``lawvm.core.semantic_types.SourceNormalizationKind`` (shared host +
    frontend-local escape hatch). D3 wires concrete detectors onto these
    families; D1's coverage metric counts them.
    """

    PDF_PAGE_IMAGE_ONLY = "pdf.page_image_only"
    PDF_TEXT_LAYER_EMPTY = "pdf.text_layer_empty"
    PDF_TEXT_LAYER_GARBLED = "pdf.text_layer_garbled"
    PDF_TABLE_GRID_UNOWNED = "pdf.table_grid_unowned"
    PDF_FOOTNOTE_UNCOLLATED = "pdf.footnote_uncollated"
    PDF_READING_ORDER_AMBIGUOUS = "pdf.reading_order_ambiguous"
    DOCX_STRUCTURE_UNTRUSTED = "docx.structure_untrusted"
    GOVERNMENT_PROPOSAL_DRAFT_OP_SET_UNEXTRACTED = "government_proposal_draft.op_set_unextracted"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Residual:
    """A typed, source-anchored region deterministic extraction could not own.

    ``snippet`` embeds the offending source text/region (AGENTS.md §1.10) so a
    residual can be triaged without re-running extraction. ``ownership`` is
    RESIDUAL or BLOCKED — never OWNED (an owned region is not a residual).
    """

    family: ResidualFamily
    ownership: RegionOwnership
    anchor: SourceAnchor
    snippet: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.family, ResidualFamily):
            raise TypeError("Residual.family must be a ResidualFamily")
        if not isinstance(self.ownership, RegionOwnership):
            raise TypeError("Residual.ownership must be a RegionOwnership")
        if self.ownership is RegionOwnership.OWNED:
            raise ValueError(
                "Residual.ownership must be RESIDUAL or BLOCKED, not OWNED — "
                "an owned region is not a residual (AGENTS.md §1.8)"
            )
        if not isinstance(self.anchor, SourceAnchor):
            raise TypeError("Residual.anchor must be a SourceAnchor")


# ---------------------------------------------------------------------------
# Coverage metric + quality detectors (D1)
# ---------------------------------------------------------------------------


def _walk(node: SourceDocumentNode):
    """Yield a node and all its descendants."""
    yield node
    for child in node.children:
        yield from _walk(child)


class QualityIssueFamily(Enum):
    """Closed taxonomy of owned-content quality findings (D1 detectors).

    A ``QualityIssue`` flags OWNED content that looks wrong (low-fidelity
    deterministic extraction) — distinct from a ``Residual`` (an unownable
    region). These are findings / prefilters (AGENTS.md §1.11): they never
    authorize state; D5 validators consume them.
    """

    SUSPECT_SHORT_BODY = "quality.suspect_short_body"
    HYPHENATION_ARTIFACT = "quality.hyphenation_artifact"
    FOOTNOTE_MARKER_IN_BODY = "quality.footnote_marker_in_body"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class QualityIssue:
    """A typed fidelity finding on owned content (snippet embedded, §1.10)."""

    family: QualityIssueFamily
    anchor: SourceAnchor
    snippet: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.family, QualityIssueFamily):
            raise TypeError("QualityIssue.family must be a QualityIssueFamily")
        if not isinstance(self.anchor, SourceAnchor):
            raise TypeError("QualityIssue.anchor must be a SourceAnchor")


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Measurable coverage + quality summary for one ingest (the D1 compass).

    Owned / residual / blocked partition counts + page-coverage ratio +
    per-family residual counts. Persistable as a baseline so each later
    increment is a predict-then-compare event (mirrors the EU ctsf gate).
    """

    scope_pages: int
    owned_pages: Tuple[int, ...]
    residual_pages: Tuple[int, ...]
    blocked_pages: Tuple[int, ...]
    residual_count_by_family: Mapping[str, int]
    page_coverage_ratio: float
    owned_node_count: int
    quality_issue_count: int


def coverage_report(
    root: SourceDocumentNode,
    residuals: Tuple[Residual, ...],
    page_count: int,
    quality_issues: Tuple[QualityIssue, ...] = (),
) -> CoverageReport:
    """Compute the coverage + partition metric over one ingest result."""
    owned_pages = tuple(
        sorted({n.anchor.page_num for n in _walk(root) if n.anchor.page_num is not None})
    )
    residual_pages = tuple(
        sorted(
            {
                r.anchor.page_num
                for r in residuals
                if r.ownership is RegionOwnership.RESIDUAL and r.anchor.page_num is not None
            }
        )
    )
    blocked_pages = tuple(
        sorted(
            {
                r.anchor.page_num
                for r in residuals
                if r.ownership is RegionOwnership.BLOCKED and r.anchor.page_num is not None
            }
        )
    )
    by_family: dict[str, int] = {}
    for r in residuals:
        key = str(r.family)
        by_family[key] = by_family.get(key, 0) + 1
    owned_node_count = sum(1 for _ in _walk(root))
    ratio = (len(owned_pages) / page_count) if page_count > 0 else 0.0
    return CoverageReport(
        scope_pages=page_count,
        owned_pages=owned_pages,
        residual_pages=residual_pages,
        blocked_pages=blocked_pages,
        residual_count_by_family=by_family,
        page_coverage_ratio=ratio,
        owned_node_count=owned_node_count,
        quality_issue_count=len(quality_issues),
    )


_SHORT_BODY_MAX = 3


def _looks_like_footnote_marker_leak(text: str) -> bool:
    """String-based (§2.4) detection of a ``N)`` / ``Na)`` marker leaking into body."""
    t = text.strip()
    if not t or len(t) > 8 or not t.endswith(")"):
        return False
    prefix = t[:-1]
    if prefix.isdigit():
        return True
    return bool(prefix) and prefix[:-1].isdigit() and prefix[-1].isalpha()


def detect_quality_issues(root: SourceDocumentNode) -> Tuple[QualityIssue, ...]:
    """Scan owned body content for low-fidelity deterministic-extraction artifacts.

    Findings only (§1.11): never authorize state. String-based detectors (§2.4)
    — no regex. D5 validators consume these to decide promotion/repair.
    """
    issues: list[QualityIssue] = []
    for node in _walk(root):
        if node.kind is not SourceDocumentNodeKind.PARAGRAPH:
            continue
        text = node.text.strip()
        if not text:
            continue
        if _looks_like_footnote_marker_leak(text):
            issues.append(
                QualityIssue(
                    family=QualityIssueFamily.FOOTNOTE_MARKER_IN_BODY,
                    anchor=node.anchor,
                    snippet=text,
                    detail="body text is a footnote-marker shape (N)/Na)) that leaked past separation",
                )
            )
            continue
        if text.endswith("-"):
            issues.append(
                QualityIssue(
                    family=QualityIssueFamily.HYPHENATION_ARTIFACT,
                    anchor=node.anchor,
                    snippet=text,
                    detail="body text ends with '-' (possible line-wrap artifact)",
                )
            )
            continue
        if len(text) <= _SHORT_BODY_MAX:
            issues.append(
                QualityIssue(
                    family=QualityIssueFamily.SUSPECT_SHORT_BODY,
                    anchor=node.anchor,
                    snippet=text,
                    detail=f"body paragraph only {len(text)} chars (likely stray token/page artifact)",
                )
            )
    return tuple(issues)
