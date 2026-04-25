"""Finland body coverage extraction and analysis.

This module owns the Finland-specific source heuristics that populate the
shared coverage contract from ``lawvm.core.coverage``. It parses amendment
body XML, enumerates the operative units present, matches them against
compiled ops, and produces a ``CoverageReport`` identifying gaps.

The pipeline is:

    extract_body_coverage(muutos_tree)  → List[CoverageUnit]
    collect_coverage_claims(ops)        → List[CoverageClaim]
    analyze_coverage(units, claims)     → CoverageReport

The gap classification in ``analyze_coverage`` uses simple heuristics keyed on
the unit's ``tags``. Downstream callers use ``CoverageReport.supplemental_candidates``
to synthesise supplemental ops and ``CoverageReport.obligations`` to surface
pathologies.
"""

from __future__ import annotations

import re
from typing import List, Optional

import lxml.etree as etree

from lawvm.core.coverage import (
    CoverageUnit,
    CoverageClaim,
    CoverageGap,
    CoverageIgnoredUnit,
    CoverageRejectedClaim,
    CoverageReport,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.helpers import _norm_num_token


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _localname(el: etree._Element) -> str:
    """Return the local XML tag name, stripping any namespace prefix."""
    tag = el.tag
    if isinstance(tag, str):
        return tag.rsplit("}", 1)[-1]
    return ""


def _num_text(el: etree._Element) -> Optional[str]:
    """Return stripped text of the first <num> child, or None."""
    num_el = el.find("{*}num")
    if num_el is None:
        num_el = el.find("num")
    if num_el is None or not num_el.text:
        return None
    return num_el.text.strip()


def _normalize_section_label(raw: str) -> str:
    """Normalize a raw section <num> text to a canonical label.

    Strips § suffix and surrounding whitespace so ``"6 §"`` → ``"6"``.
    Delegates to ``_norm_num_token`` for full normalization.
    """
    cleaned = re.sub(r"\s*§.*$", "", raw).strip()
    return _norm_num_token(cleaned)


def _normalize_chapter_label(raw: str) -> str:
    """Normalize a chapter <num> text, stripping 'luku' suffix."""
    return _norm_num_token(raw).removesuffix("luku")


def _is_pseudo_chapter_marker_section(raw_num: str) -> bool:
    """Return True when a malformed section num acts as a chapter marker."""
    return _norm_num_token(raw_num).endswith("luku")


def _heading_lower(el: etree._Element) -> str:
    """Return lower-case stripped heading text for the element, or ''."""
    heading_el = el.find("{*}heading")
    if heading_el is None:
        heading_el = el.find("heading")
    if heading_el is None:
        return ""
    parts: List[str] = [str(t) for t in heading_el.itertext()]
    return " ".join("".join(parts).split()).lower()


# ---------------------------------------------------------------------------
# Tag classifiers
# ---------------------------------------------------------------------------

_NONOPERATIVE_HEADING_PREFIXES = (
    "voimaantulo",
    "siirtymä",
    "kumottavat",
    "kumoaminen",
    "soveltaminen",
    "voimassaolo",
)

_SELLAISENA_KUIN_PATTERNS = (
    "sellaisena kuin",
    "sellaisenaan",
)


def _classify_tags(el: etree._Element, kind: str) -> frozenset:
    """Return a frozenset of classification tags for a body element.

    Heuristics applied:
    - ``'nonoperative'`` — voimaantulo/siirtymä headings, or sections that
      carry transitional/commencement material by heading convention.
    - ``'provenance'`` — sellaisena-kuin blocks that record prior form.
    """
    tags = set()
    heading = _heading_lower(el)
    for prefix in _NONOPERATIVE_HEADING_PREFIXES:
        if heading.startswith(prefix):
            tags.add("nonoperative")
            break
    # Also check hcontainer name attr on direct children (some encodings)
    for child in el:
        name_attr = child.get("name", "")
        if name_attr in ("voimaantulo", "siirtymasaannos"):
            tags.add("nonoperative")
    # Sellaisena-kuin provenance: look for the phrase in the heading or in
    # any direct text content.
    all_text = " ".join(str(_t) for _t in el.itertext()).lower()
    for pat in _SELLAISENA_KUIN_PATTERNS:
        if pat in all_text:
            tags.add("provenance")
            break
    return frozenset(tags)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_body_coverage(
    muutos_tree: etree._Element,
    *,
    ignored_units_out: Optional[List[CoverageIgnoredUnit]] = None,
) -> List[CoverageUnit]:
    """Walk an amendment body XML and enumerate all operative structural units.

    Looks for ``<section>``, ``<chapter>``, and ``<article>`` elements inside
    the ``<body>`` of *muutos_tree*.  Each element becomes one ``CoverageUnit``
    with:

    - ``unit_id`` — ``"<kind>_<label>"`` (e.g. ``"section_6"``).
    - ``kind`` — ``"section"``, ``"chapter"``, or ``"article"``.
    - ``observed_label`` — normalised label extracted from ``<num>``.
    - ``parent_label`` — enclosing chapter/part label, or ``None`` at top level.
    - ``payload_ref`` — the lxml element (opaque reference for downstream use).
    - ``tags`` — classification tags (``'nonoperative'``, ``'provenance'``, …).

    The function returns an empty list when ``muutos_tree`` has no ``<body>``.
    Sections/chapters with no ``<num>`` element are skipped.
    """
    body = muutos_tree.find(".//{*}body")
    if body is None:
        if ignored_units_out is not None:
            ignored_units_out.append(
                CoverageIgnoredUnit(
                    unit_kind="body",
                    reason="missing_body",
                    payload_ref=muutos_tree,
                    evidence=("missing_body",),
                )
            )
        return []

    units: List[CoverageUnit] = []
    seen_ids: set = set()

    def _append_unit(kind: str, observed_label: str, parent_label: Optional[str], el: etree._Element) -> None:
        base_id = f"{kind}_{observed_label}"
        if parent_label:
            base_id = f"{kind}_{parent_label}_{observed_label}"
        unit_id = base_id
        counter = 1
        while unit_id in seen_ids:
            unit_id = f"{base_id}_{counter}"
            counter += 1
        seen_ids.add(unit_id)

        tags = _classify_tags(el, kind)
        units.append(
            CoverageUnit(
                unit_id=unit_id,
                kind=kind,
                observed_label=observed_label,
                parent_label=parent_label,
                payload_ref=el,
                tags=tags,
            )
        )

    def _walk_children(parent: etree._Element, active_chapter: Optional[str] = None) -> None:
        current_chapter = active_chapter
        for child in parent:
            kind = _localname(child)

            if kind == "chapter":
                raw_num = _num_text(child)
                if raw_num:
                    chapter_label = _normalize_chapter_label(raw_num)
                    if chapter_label:
                        _append_unit("chapter", chapter_label, None, child)
                        _walk_children(child, chapter_label)
                        current_chapter = active_chapter
                        continue
                    if ignored_units_out is not None:
                        ignored_units_out.append(
                            CoverageIgnoredUnit(
                                unit_kind="chapter",
                                reason="unusable_num",
                                observed_label=raw_num,
                                payload_ref=child,
                                evidence=(f"raw_num={raw_num}", "normalize_failed"),
                            )
                        )
                elif ignored_units_out is not None:
                    ignored_units_out.append(
                        CoverageIgnoredUnit(
                            unit_kind="chapter",
                            reason="missing_num",
                            payload_ref=child,
                            evidence=("missing_num",),
                        )
                    )

            if kind == "section":
                raw_num = _num_text(child)
                if raw_num:
                    if _is_pseudo_chapter_marker_section(raw_num):
                        pseudo_chapter = _normalize_chapter_label(raw_num)
                        if pseudo_chapter:
                            _append_unit("chapter", pseudo_chapter, None, child)
                            _walk_children(child, pseudo_chapter)
                            current_chapter = pseudo_chapter
                            continue
                        if ignored_units_out is not None:
                            ignored_units_out.append(
                                CoverageIgnoredUnit(
                                    unit_kind="section",
                                    reason="pseudo_chapter_marker_unusable",
                                    observed_label=raw_num,
                                    parent_label=current_chapter,
                                    payload_ref=child,
                                    evidence=(f"raw_num={raw_num}", "pseudo_chapter_marker"),
                                )
                            )

                    observed_label = _normalize_section_label(raw_num)
                    if observed_label:
                        _append_unit("section", observed_label, current_chapter, child)
                        _walk_children(child, current_chapter)
                        continue
                    if ignored_units_out is not None:
                        ignored_units_out.append(
                            CoverageIgnoredUnit(
                                unit_kind="section",
                                reason="unusable_num",
                                observed_label=raw_num,
                                parent_label=current_chapter,
                                payload_ref=child,
                                evidence=(f"raw_num={raw_num}", "normalize_failed"),
                            )
                        )
                elif ignored_units_out is not None:
                    ignored_units_out.append(
                        CoverageIgnoredUnit(
                            unit_kind="section",
                            reason="missing_num",
                            parent_label=current_chapter,
                            payload_ref=child,
                            evidence=("missing_num",),
                        )
                    )

            if kind == "article":
                raw_num = _num_text(child)
                if raw_num:
                    observed_label = _norm_num_token(raw_num)
                    if observed_label:
                        _append_unit("article", observed_label, current_chapter, child)
                    elif ignored_units_out is not None:
                        ignored_units_out.append(
                            CoverageIgnoredUnit(
                                unit_kind="article",
                                reason="unusable_num",
                                observed_label=raw_num,
                                parent_label=current_chapter,
                                payload_ref=child,
                                evidence=(f"raw_num={raw_num}", "normalize_failed"),
                            )
                        )
                elif ignored_units_out is not None:
                    ignored_units_out.append(
                        CoverageIgnoredUnit(
                            unit_kind="article",
                            reason="missing_num",
                            parent_label=current_chapter,
                            payload_ref=child,
                            evidence=("missing_num",),
                        )
                    )

            _walk_children(child, current_chapter)

    _walk_children(body)

    return units


def collect_coverage_claims(
    ops: List[AmendmentOp],
    *,
    rejected_claims_out: Optional[List[CoverageRejectedClaim]] = None,
) -> List[CoverageClaim]:
    """Build CoverageClaims from a list of compiled AmendmentOps.

    Each op that targets a section, chapter, or part produces one claim.
    The neutral ``target_unit_kind`` is the structural authority here.
    The ``claim_kind`` is:

    - ``'explicit'`` — op does not carry typed fallback provenance.
    - ``'fallback'`` — op carries typed fallback provenance, indicating
      heuristic origin.

    The ``covered_unit_ids`` is computed lazily at claim creation as a
    frozenset containing the canonical unit_id for the op's primary target.
    The label derivation mirrors ``extract_body_coverage``'s unit_id scheme
    without knowledge of whether the unit actually exists — matching is done
    later by ``analyze_coverage``.
    """
    claims: List[CoverageClaim] = []

    for op in ops:
        if not op.target_section:
            if rejected_claims_out is not None:
                rejected_claims_out.append(
                    CoverageRejectedClaim(
                        reason="missing_target_section",
                        target=op,
                        evidence=(f"op_id={op.op_id}", f"op_type={op.op_type}"),
                    )
                )
            continue

        if op.target_unit_kind == "section":
            label = _norm_num_token(op.target_section)
            kind = "section"
        elif op.target_unit_kind == "chapter":
            label = _norm_num_token(op.target_section).removesuffix("luku")
            kind = "chapter"
        elif op.target_unit_kind == "part":
            label = _norm_num_token(op.target_section)
            kind = "part"
        else:
            if rejected_claims_out is not None:
                rejected_claims_out.append(
                    CoverageRejectedClaim(
                        reason="unsupported_target_unit_kind",
                        target=op,
                        evidence=(
                            f"op_id={op.op_id}",
                            f"op_type={op.op_type}",
                            f"target_unit_kind={op.target_unit_kind}",
                        ),
                    )
                )
            continue

        # Determine claim_kind from typed provenance; `resolution_hint` is
        # historical residue only and is no longer a Finland runtime transport
        # lane.
        if op.fallback_provenance or op.body_root_replace_fallback:
            claim_kind = "fallback"
        else:
            claim_kind = "explicit"

        # Build the candidate unit_id(s) this op might cover
        chapter_label: Optional[str] = None
        if op.target_chapter:
            chapter_label = _norm_num_token(op.target_chapter).removesuffix("luku")

        if chapter_label:
            base_unit_id = f"{kind}_{chapter_label}_{label}"
        else:
            base_unit_id = f"{kind}_{label}"

        evidence_parts = [f"op_id={op.op_id}", f"op_type={op.op_type}"]
        if op.fallback_provenance:
            evidence_parts.append("fallback_provenance=true")
        if op.body_root_replace_fallback:
            evidence_parts.append("body_root_replace_fallback=true")

        claims.append(
            CoverageClaim(
                claim_kind=claim_kind,
                target=op,
                covered_unit_ids=frozenset({base_unit_id}),
                evidence=tuple(evidence_parts),
            )
        )

    return claims


def analyze_coverage(
    units: List[CoverageUnit],
    claims: List[CoverageClaim],
    *,
    ignored_units: Optional[List[CoverageIgnoredUnit]] = None,
    rejected_claims: Optional[List[CoverageRejectedClaim]] = None,
) -> CoverageReport:
    """Diff observed units against claims and produce a CoverageReport.

    Matching logic:

    1. Build a set of all ``unit_id`` values covered by all claims.
       A claim also matches a unit by label alone (ignoring parent prefix) so
       that an op without chapter context covers any section with that label.
    2. For each uncovered unit, classify its disposition:
       - ``'nonoperative'`` tag → ``'ignore_nonoperative'``
       - ``'provenance'`` tag  → ``'ignore_nonoperative'``
       - Otherwise             → ``'supplemental_candidate'``
    """
    # Build covered set: unit_ids directly referenced by claims
    directly_covered: set = set()
    for claim in claims:
        directly_covered.update(claim.covered_unit_ids)

    # Build a "label-only" match set, but ONLY for ops that lack chapter
    # context.  A chapter-qualified claim (3-part unit_id "kind_parent_label")
    # must NOT absorb sections in OTHER chapters via label-only matching —
    # that would incorrectly suppress supplemental recovery for sections that
    # live in new sub-chapters (e.g. "2a luku / 17 §") when a PEG op covers
    # the same label in an unrelated chapter (e.g. "2 luku / 17 §").
    # Format: "<kind>_<label>"  (without parent prefix) — only from chapter-free claims.
    label_only_covered: set = set()
    for unit_id in directly_covered:
        parts = unit_id.split("_")
        if len(parts) == 2:
            # kind_label — no chapter context → covers any chapter
            label_only_covered.add(unit_id)
        # 3-part (kind_parent_label): chapter-qualified — do NOT add to label_only

    gaps: List[CoverageGap] = []

    for unit in units:
        # Check direct match
        if unit.unit_id in directly_covered:
            continue
        # Check label-only match (op covered all chapters)
        label_only_id = f"{unit.kind}_{unit.observed_label}"
        if label_only_id in directly_covered or label_only_id in label_only_covered:
            continue

        # Unit is unclaimed — classify disposition
        if "nonoperative" in unit.tags or "provenance" in unit.tags:
            disposition = "ignore_nonoperative"
            evidence: tuple = ("tag:nonoperative" if "nonoperative" in unit.tags else "tag:provenance",)
        else:
            disposition = "supplemental_candidate"
            evidence = (f"unit_id={unit.unit_id}", "no_matching_claim")

        gaps.append(
            CoverageGap(
                unit=unit,
                disposition=disposition,
                suggested_target=None,
                evidence=evidence,
            )
        )

    return CoverageReport(
        units=tuple(units),
        claims=tuple(claims),
        gaps=tuple(gaps),
        ignored_units=tuple(ignored_units or ()),
        rejected_claims=tuple(rejected_claims or ()),
    )
