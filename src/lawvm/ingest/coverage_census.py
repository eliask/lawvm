"""Deterministic OMISSION census — the coverage-ledger verifier (NEW mechanism).

Every existing ingest mechanism verifies the units it EXTRACTED (garble suspects,
table fallbacks, de-facsimile ledger gates, vision corroboration). NOTHING audits
what was DROPPED: a missing page / column / annex / op emits zero pendings and is
therefore INVISIBLE — a silent hole. Phase A's XML↔PDF reference catches this as
``op_missing``, but phases B/C (born-digital structure, appendix tables) have no
reference. This module is that missing reference, done the cheap deterministic way.

Mechanism (two complementary deterministic checks over ONE page/document):

  * **Ink coverage** — build a ledger of every source INK region (born-digital:
    one region per text-layer line, with its geometry) and verify each is CLAIMED
    by some emitted unit's span (bbox coverage). An unclaimed non-furniture region
    is a silent hole → typed ``pdf.omission_suspect``. Running headers / footers /
    page numbers are distinguished as FURNITURE by GEOMETRY (margin band + a bare
    page number OR cross-page recurrence — the SAME affordances Level-1 uses), not
    guessed away by content; a furniture region left unclaimed is not a false alarm,
    everything else is FLAGGED (a false omission-flag is a typed row to review; a
    missed omission is a silent hole — we err toward flagging).

  * **Sequence continuity** — a gap in the page-number or ``§``/Article ordinal
    sequence (an ordinal present up to N, absent at N+1, present again at N+2) is a
    dropped page/section → typed ``pdf.sequence_gap``. Deterministic over the
    ordinals DERIVED from the source (label DERIVED from the pdf for an off-path
    verify is allowed; the label is never CONSUMED back into extraction).

The interface is PRODUCER-NEUTRAL over WHERE ink comes from: ``InkRegion`` carries
only ``(page, bbox, text, band, is_furniture)``, so a born-digital text-layer
projection and a future SCAN ink-projection (one region per connected component,
``text=""``) satisfy the same contract and the coverage check downstream is
identical. Only ``ink_regions_from_page_elements`` is born-digital-specific.

Discipline: PURE + deterministic (no model, no network); ADDITIVE and OPT-IN — this
is a SEPARATE verify pass that CONSUMES an already-produced simulacrum, so wiring it
in changes NO producer output (the no-ledger path is byte-identical). Findings are
emitted into the EXISTING typed-residual channel
(``lawvm.core.source_document.coverage.Residual`` / ``ResidualFamily``), never a
bespoke stream.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.coverage import (
    RegionOwnership,
    Residual,
    ResidualFamily,
)
from lawvm.core.source_document.ir import SourceDocumentNode
from lawvm.ingest.page_elements import (
    PageElements,
    line_is_bare_page_number,
    line_section_number,
)
from lawvm.ingest.page_level import _is_furniture_candidate, _recurrence_key
from lawvm.ingest.simulacrum import PageSimulacrum

# A source ink region is CLAIMED when this fraction of its area lies inside some
# emitted unit's bbox. A born-digital unit's bbox is the axis-aligned UNION of its
# owned lines, so a genuinely-owned line sits ~fully inside it; 0.5 tolerates minor
# rounding / a marginally-wider neighbour without masking a dropped unit.
_MIN_COVERAGE = 0.5

# A sequence gap wider than this is treated as a STRUCTURAL boundary (a new chapter
# restarting numbering, a large legitimate jump), NOT a dropped unit — flagging
# thousands of "missing" ordinals across such a jump would be noise. A dropped unit
# leaves a gap of exactly 2 (N, N+2), well inside this bound.
_MAX_GAP_SPAN = 4


# --------------------------------------------------------------------------- #
# Producer-neutral ink-region interface.                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class InkRegion:
    """One source ink/content region the census must see ACCOUNTED for.

    Producer-neutral: a born-digital page projects one region per text-layer line
    (``text`` populated); a future scan page projects one region per connected ink
    component (``text=""``). ``is_furniture`` is the DETERMINISTIC geometry verdict
    (margin band + bare-page-number / recurrence) — an unclaimed furniture region is
    not an omission alarm, an unclaimed content region is.
    """

    page_num: int
    bbox: BBox
    text: str = ""
    band: Optional[str] = None
    is_furniture: bool = False


@dataclass(frozen=True, slots=True)
class PageCensus:
    """Per-page coverage accounting (the compass; residuals carry the holes)."""

    page_num: int
    ink_total: int
    ink_furniture: int
    claimed: int
    unclaimed_content: int
    coverage_ratio: float
    residuals: Tuple[Residual, ...]


@dataclass(frozen=True, slots=True)
class CensusLedger:
    """Whole-document census: per-page coverage + document-level sequence gaps."""

    pages: Tuple[PageCensus, ...]
    sequence_residuals: Tuple[Residual, ...]

    @property
    def residuals(self) -> Tuple[Residual, ...]:
        """All typed omission findings (per-page unclaimed ink + sequence gaps)."""
        out: List[Residual] = []
        for pc in self.pages:
            out.extend(pc.residuals)
        out.extend(self.sequence_residuals)
        return tuple(out)

    @property
    def omission_count(self) -> int:
        return sum(pc.unclaimed_content for pc in self.pages)


# --------------------------------------------------------------------------- #
# Born-digital ink projection (the one producer-specific adapter).              #
# --------------------------------------------------------------------------- #


def ink_regions_from_page_elements(
    page_elements: PageElements,
    *,
    recurrence: Optional[Mapping[str, int]] = None,
    page_count: int = 1,
) -> Tuple[InkRegion, ...]:
    """Project a born-digital page's text-layer lines into ``InkRegion``s.

    One region per line that carries geometry (a line without a bbox cannot be
    covered-checked, so it is skipped — the census is a GEOMETRY verifier). Furniture
    is the deterministic Level-1 verdict (``_is_furniture_candidate``): a bare page
    number OR a line recurring at the same margin band across pages.
    """
    rec = recurrence if recurrence is not None else {}
    out: List[InkRegion] = []
    for line in page_elements.page_lines:
        if line.bbox is None or not line.text.strip():
            continue
        rk = _recurrence_key(line.text, line)
        band_count = rec.get(rk) if rk is not None else None
        furniture = _is_furniture_candidate(line.text, line, band_count, page_count)
        out.append(
            InkRegion(
                page_num=page_elements.page_num,
                bbox=line.bbox,
                text=line.text,
                band=line.band,
                is_furniture=furniture,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# Claimed spans (from an already-produced simulacrum).                          #
# --------------------------------------------------------------------------- #


def _walk_nodes(nodes: Iterable[SourceDocumentNode]) -> Iterable[SourceDocumentNode]:
    for n in nodes:
        yield n
        yield from _walk_nodes(n.children)


def claimed_boxes(simulacrum: PageSimulacrum) -> Tuple[BBox, ...]:
    """Every emitted unit's claimed bbox on this page (walks nested children).

    A node without geometry (``anchor.bbox is None``) claims no region — it is
    silently absent from the ledger of claims, so any ink it should have covered
    stays unclaimed and is FLAGGED (err toward flagging, never toward assuming).
    """
    boxes: List[BBox] = []
    for node in _walk_nodes(simulacrum.nodes):
        if node.anchor.bbox is not None:
            boxes.append(node.anchor.bbox)
    return tuple(boxes)


# --------------------------------------------------------------------------- #
# Coverage geometry.                                                            #
# --------------------------------------------------------------------------- #


def _coverage_fraction(ink: BBox, claim: BBox) -> float:
    """Fraction of ``ink``'s area contained inside ``claim`` (axis-aligned)."""
    ix0 = max(ink.x0, claim.x0)
    iy0 = max(ink.y0, claim.y0)
    ix1 = min(ink.x1, claim.x1)
    iy1 = min(ink.y1, claim.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area = (ink.x1 - ink.x0) * (ink.y1 - ink.y0)
    if area <= 0.0:
        return 0.0
    return inter / area


def _is_claimed(ink: BBox, claims: Sequence[BBox], min_coverage: float) -> bool:
    return any(_coverage_fraction(ink, c) >= min_coverage for c in claims)


def page_census(
    ink_regions: Sequence[InkRegion],
    claims: Sequence[BBox],
    *,
    artifact_digest: str,
    page_num: int,
    min_coverage: float = _MIN_COVERAGE,
) -> PageCensus:
    """Account for every ink region against the claimed spans on one page.

    An unclaimed non-furniture region → a ``pdf.omission_suspect`` residual carrying
    the offending snippet + its geometry. Furniture left unclaimed is recorded but
    NOT flagged (distinguished by geometry, never dropped silently). Coverage ratio
    counts furniture as content-neutral (ratio over the CONTENT regions).
    """
    residuals: List[Residual] = []
    ink_total = len(ink_regions)
    ink_furniture = sum(1 for r in ink_regions if r.is_furniture)
    claimed = 0
    unclaimed_content = 0
    for region in ink_regions:
        if _is_claimed(region.bbox, claims, min_coverage):
            claimed += 1
            continue
        if region.is_furniture:
            # Distinguished as furniture by GEOMETRY (band + bare-page/recurrence);
            # an unclaimed running header is not a dropped op. Recorded, not flagged.
            continue
        unclaimed_content += 1
        b = region.bbox
        anchor = SourceAnchor(
            artifact_digest=artifact_digest,
            locator=f"page={page_num};bbox={b.x0},{b.y0},{b.x1},{b.y1}",
            page_num=page_num,
            bbox=b,
        )
        residuals.append(
            Residual(
                family=ResidualFamily.PDF_OMISSION_SUSPECT,
                ownership=RegionOwnership.RESIDUAL,
                anchor=anchor,
                snippet=region.text,
                detail=(
                    "source ink region is not claimed by any emitted unit "
                    "(silent-drop suspect)"
                ),
            )
        )
    # Coverage ratio is over CONTENT regions (furniture is content-neutral): a
    # claimed furniture header should neither help nor hurt the content-coverage
    # score, so subtract claimed furniture from ``claimed`` before the ratio.
    content_total = ink_total - ink_furniture
    claimed_content = claimed - sum(
        1
        for r in ink_regions
        if r.is_furniture and _is_claimed(r.bbox, claims, min_coverage)
    )
    ratio = claimed_content / content_total if content_total > 0 else 1.0
    return PageCensus(
        page_num=page_num,
        ink_total=ink_total,
        ink_furniture=ink_furniture,
        claimed=claimed,
        unclaimed_content=unclaimed_content,
        coverage_ratio=ratio,
        residuals=tuple(residuals),
    )


# --------------------------------------------------------------------------- #
# Sequence continuity.                                                          #
# --------------------------------------------------------------------------- #


def _leading_int(label: str) -> Optional[int]:
    """First maximal digit run in a label (``"4 §"``→4, ``"§ 5"``→5, ``"Art. 5"``→5)."""
    digits = ""
    for ch in label:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else None


def section_ordinals(
    simulacra: Sequence[PageSimulacrum],
) -> Tuple[Tuple[int, int], ...]:
    """``(ordinal, page_num)`` for every emitted unit whose head is a ``§``/Article label.

    Derived from the EMITTED units (the claimed stream), in document order. A dropped
    section leaves a hole in this ordinal run that ``sequence_gap_residuals`` reports.
    """
    out: List[Tuple[int, int]] = []
    for sim in simulacra:
        for node in _walk_nodes(sim.nodes):
            head = node.text.split("\n", 1)[0] if node.text else ""
            label = line_section_number(head)
            if label is None:
                continue
            val = _leading_int(label)
            if val is not None:
                out.append((val, sim.page_num))
    return tuple(out)


def page_ordinals(
    page_elements: Sequence[PageElements],
) -> Tuple[Tuple[int, int], ...]:
    """``(printed_page_number, page_index)`` from bare-page-number furniture lines.

    A gap in the PRINTED page-number run (10, 11, 13) is a physically dropped page —
    a source-completeness omission, detected on the ink itself.
    """
    out: List[Tuple[int, int]] = []
    for pe in page_elements:
        for line in pe.page_lines:
            if line_is_bare_page_number(line.text):
                val = _leading_int(line.text)
                if val is not None:
                    out.append((val, pe.page_num))
                    break  # one page number per physical page
    return tuple(out)


def sequence_gap_residuals(
    ordinals: Sequence[Tuple[int, int]],
    *,
    artifact_digest: str,
    kind: str,
    max_gap_span: int = _MAX_GAP_SPAN,
) -> Tuple[Residual, ...]:
    """Typed ``pdf.sequence_gap`` residuals for holes in an ascending ordinal run.

    ``ordinals`` is ``(value, page_num)``. For each pair of consecutive DISTINCT
    present values ``a < b`` with ``1 < b - a <= max_gap_span``, every missing integer
    in ``(a, b)`` is a dropped page/section. A gap wider than ``max_gap_span`` is a
    structural boundary (chapter renumber), not a drop, and is left unflagged.
    """
    present = sorted({v for v, _ in ordinals})
    page_of = {v: p for v, p in ordinals}
    residuals: List[Residual] = []
    for a, b in zip(present, present[1:], strict=False):
        span = b - a
        if span <= 1 or span > max_gap_span:
            continue
        for missing in range(a + 1, b):
            page = page_of.get(a, 0)
            anchor = SourceAnchor(
                artifact_digest=artifact_digest,
                locator=f"page={page};seq_gap={kind}={missing}",
                page_num=page if page > 0 else None,
            )
            residuals.append(
                Residual(
                    family=ResidualFamily.PDF_SEQUENCE_GAP,
                    ownership=RegionOwnership.RESIDUAL,
                    anchor=anchor,
                    snippet=str(missing),
                    detail=(
                        f"{kind} ordinal {missing} is missing between {a} and {b} "
                        "(dropped page/section suspect)"
                    ),
                )
            )
    return tuple(residuals)


# --------------------------------------------------------------------------- #
# Whole-document convenience.                                                   #
# --------------------------------------------------------------------------- #


def run_census(
    page_elements: Sequence[PageElements],
    simulacra: Sequence[Optional[PageSimulacrum]],
    *,
    artifact_digest: str,
    recurrence: Optional[Mapping[str, int]] = None,
    min_coverage: float = _MIN_COVERAGE,
    max_gap_span: int = _MAX_GAP_SPAN,
) -> CensusLedger:
    """Run the full coverage census over a document's pages + produced simulacra.

    ``simulacra[i]`` is the emitted page tree for ``page_elements[i]`` (``None`` when
    that page was not born-digital / not produced — its ink is then fully unclaimed
    and every content region is flagged, the honest degrade). Pure + deterministic.
    """
    from lawvm.ingest.page_level import band_recurrence_map

    rec = recurrence if recurrence is not None else band_recurrence_map(page_elements)
    pages: List[PageCensus] = []
    produced: List[PageSimulacrum] = []
    for i, pe in enumerate(page_elements):
        sim = simulacra[i] if i < len(simulacra) else None
        claims = claimed_boxes(sim) if sim is not None else ()
        if sim is not None:
            produced.append(sim)
        ink = ink_regions_from_page_elements(
            pe, recurrence=rec, page_count=len(page_elements)
        )
        pages.append(
            page_census(
                ink,
                claims,
                artifact_digest=artifact_digest,
                page_num=pe.page_num,
                min_coverage=min_coverage,
            )
        )
    seq: List[Residual] = []
    seq.extend(
        sequence_gap_residuals(
            section_ordinals(produced),
            artifact_digest=artifact_digest,
            kind="section",
            max_gap_span=max_gap_span,
        )
    )
    seq.extend(
        sequence_gap_residuals(
            page_ordinals(page_elements),
            artifact_digest=artifact_digest,
            kind="page",
            max_gap_span=max_gap_span,
        )
    )
    return CensusLedger(pages=tuple(pages), sequence_residuals=tuple(seq))


__all__ = [
    "CensusLedger",
    "InkRegion",
    "PageCensus",
    "claimed_boxes",
    "ink_regions_from_page_elements",
    "page_census",
    "page_ordinals",
    "run_census",
    "section_ordinals",
    "sequence_gap_residuals",
]
