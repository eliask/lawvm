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
from dataclasses import dataclass
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
from lawvm.core.filter_result import FilterResult
from lawvm.core.stage_result import PartitionResult, Residual
from lawvm.finland.op_provenance import Recovered, RecognizerId, has_recognizer
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.helpers import _normalize_source_part_num, _normalize_source_section_num, _norm_num_token


@dataclass(frozen=True)
class CoverageClaimPartition(PartitionResult[CoverageClaim]):
    """Conserving carrier for coverage-claim collection (Audit C).

    Composes the canonical :class:`PartitionResult` (accepted claims +
    typed core ``residuals``) and ADDS ``rejected_claims`` — the rich
    domain-specific :class:`CoverageRejectedClaim` records (target op + reason +
    evidence) the production sink ``uncovered_recovery_prepare`` consumes. The
    rejected lane is NOT placed in the wrapped ``FilterResult`` because its
    payload is the rejected op, not a ``CoverageClaim``; ``rejected_claims`` is
    the typed rejected channel and ``residuals`` is the core-contract mirror.
    """

    rejected_claims: tuple[CoverageRejectedClaim, ...] = ()


@dataclass(frozen=True, slots=True)
class BodyCoveragePayloadRef:
    """Typed source-model lookup for a Finland coverage unit payload."""

    unit_id: str
    unit_kind: str
    label: str
    chapter: Optional[str] = None
    part: Optional[str] = None
    source_tag: str = ""


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
    return _normalize_source_section_num(raw)


def _normalize_chapter_label(raw: str) -> str:
    """Normalize a chapter <num> text, stripping 'luku' suffix."""
    return _norm_num_token(raw).removesuffix("luku")


def _normalize_part_label(raw: str) -> str:
    """Normalize a part label to the live-tree form used by Finland replay."""
    return _normalize_source_part_num(raw)


_PART_CROSS_HEADING_RE = re.compile(
    r"^(?P<label>(?:[IVXLCDM]{1,12}|\d{1,4}[a-z]?))\s{1,8}(?:osa|osasto)\b"
    r"(?:$|\s{1,8}[^\n]{0,200}$)",
    flags=re.I,
)


def _direct_text(el: etree._Element) -> str:
    """Return whitespace-normalized direct text content for ``el``."""
    return " ".join("".join(str(part) for part in el.itertext()).split())


def _part_label_from_cross_heading(el: etree._Element) -> str:
    """Return a normalized part label when ``el`` is a direct part marker."""
    if _localname(el) != "crossHeading":
        return ""
    # lawvm-regex: owning_parser part-marker shape over a crossHeading element's own direct XML text, structured element-text not prose
    match = _PART_CROSS_HEADING_RE.match(_direct_text(el))
    if match is None:
        return ""
    return _normalize_part_label(match.group("label"))


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


# Direct chapter children that constitute the chapter's own operative payload.
# A chapter carrying any of these (rather than just <num>/<heading> plus nested
# <section>/<chapter> wrappers) is replacing/holding whole-chapter content and
# therefore is itself an operative unit.
_CHAPTER_OPERATIVE_PAYLOAD_TAGS = frozenset(
    {
        "content",
        "subsection",
        "paragraph",
        "list",
        "blockList",
        "block",
        "p",
        "intro",
        "wrapUp",
        "hcontainer",
    }
)


def _chapter_is_container_only(el: etree._Element) -> bool:
    """Return True when a ``<chapter>`` is a pure scoping container.

    A container-only chapter wraps section (or nested chapter) edits but carries
    no operative whole-chapter payload of its own — its direct children are only
    ``<num>``/``<heading>`` metadata plus ``<section>``/``<chapter>`` units. Such
    a chapter exists solely to locate the section edits nested inside it; it is
    not a separate operative coverage unit (only a whole-chapter REPLACE/INSERT/
    REPEAL targets a chapter as a unit, and that body carries direct payload).
    """
    has_nested_unit = False
    for child in el:
        local = _localname(child)
        if local in ("section", "chapter"):
            has_nested_unit = True
            continue
        if local in ("num", "heading"):
            continue
        if local in _CHAPTER_OPERATIVE_PAYLOAD_TAGS:
            return False
    return has_nested_unit


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

_PROVENANCE_HEADING_PATTERNS = (
    "sellaisena kuin",
    "sellaisina kuin",
    "siten kuin",
)

_PROVENANCE_BLOCK_NAMES = frozenset(
    {
        "insertions-originals",
        "repeals-originals",
        "substitutions-originals",
    }
)


def _classify_tags(el: etree._Element, kind: str) -> frozenset[str]:
    """Return a frozenset of classification tags for a body element.

    Heuristics applied:
    - ``'nonoperative'`` — voimaantulo/siirtymä headings, or sections that
      carry transitional/commencement material by heading convention.
    - ``'provenance'`` — sellaisena-kuin blocks that record prior form.
    - ``'container'`` — a ``<chapter>`` that only scopes nested section edits
      and carries no operative whole-chapter payload of its own.
    """
    tags: set[str] = set()
    if kind == "chapter" and _chapter_is_container_only(el):
        tags.add("container")
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
    # Sellaisena-kuin provenance belongs to publisher/source wrappers, not to
    # arbitrary operative body text. Ordinary section content can lawfully use
    # words such as "sellaisenaan" without becoming non-operative.
    if any(pat in heading for pat in _PROVENANCE_HEADING_PATTERNS):
        tags.add("provenance")
    for child in el:
        name_attr = child.get("name", "")
        if name_attr in _PROVENANCE_BLOCK_NAMES:
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
    - ``payload_ref`` — a typed source-model lookup reference.
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
                    evidence=("missing_body",),
                )
            )
        return []

    units: List[CoverageUnit] = []
    seen_ids: set[str] = set()

    def _append_unit(
        kind: str,
        observed_label: str,
        parent_label: Optional[str],
        part_label: Optional[str],
        el: etree._Element,
    ) -> None:
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
                payload_ref=BodyCoveragePayloadRef(
                    unit_id=unit_id,
                    unit_kind=kind,
                    label=observed_label,
                    chapter=parent_label,
                    part=part_label,
                    source_tag=_localname(el),
                ),
                tags=tags,
            )
        )

    def _walk_children(
        parent: etree._Element,
        active_chapter: Optional[str] = None,
        active_part: Optional[str] = None,
    ) -> None:
        current_chapter = active_chapter
        current_part = active_part
        for child in parent:
            kind = _localname(child)

            if kind == "crossHeading":
                part_label = _part_label_from_cross_heading(child)
                if part_label:
                    current_chapter = None
                    current_part = part_label
                    continue

            if kind == "part":
                raw_num = _num_text(child)
                if raw_num:
                    part_label = _normalize_part_label(raw_num)
                    if part_label:
                        _walk_children(child, active_chapter=None, active_part=part_label)
                        current_chapter = active_chapter
                        current_part = active_part
                        continue

            if kind == "chapter":
                raw_num = _num_text(child)
                if raw_num:
                    chapter_label = _normalize_chapter_label(raw_num)
                    if chapter_label:
                        _append_unit("chapter", chapter_label, None, current_part, child)
                        _walk_children(child, chapter_label, current_part)
                        current_chapter = active_chapter
                        continue
                    if ignored_units_out is not None:
                        ignored_units_out.append(
                            CoverageIgnoredUnit(
                                unit_kind="chapter",
                                reason="unusable_num",
                                observed_label=raw_num,
                                evidence=(f"raw_num={raw_num}", "normalize_failed"),
                            )
                        )
                elif ignored_units_out is not None:
                    ignored_units_out.append(
                        CoverageIgnoredUnit(
                            unit_kind="chapter",
                            reason="missing_num",
                            evidence=("missing_num",),
                        )
                    )

            if kind == "section":
                raw_num = _num_text(child)
                if raw_num:
                    if _is_pseudo_chapter_marker_section(raw_num):
                        pseudo_chapter = _normalize_chapter_label(raw_num)
                        if pseudo_chapter:
                            _append_unit("chapter", pseudo_chapter, None, current_part, child)
                            _walk_children(child, pseudo_chapter, current_part)
                            current_chapter = pseudo_chapter
                            continue
                        if ignored_units_out is not None:
                            ignored_units_out.append(
                                CoverageIgnoredUnit(
                                    unit_kind="section",
                                    reason="pseudo_chapter_marker_unusable",
                                    observed_label=raw_num,
                                    parent_label=current_chapter,
                                    evidence=(f"raw_num={raw_num}", "pseudo_chapter_marker"),
                                )
                            )

                    observed_label = _normalize_section_label(raw_num)
                    if observed_label:
                        _append_unit("section", observed_label, current_chapter, current_part, child)
                        _walk_children(child, current_chapter, current_part)
                        continue
                    if ignored_units_out is not None:
                        ignored_units_out.append(
                            CoverageIgnoredUnit(
                                unit_kind="section",
                                reason="unusable_num",
                                observed_label=raw_num,
                                parent_label=current_chapter,
                                evidence=(f"raw_num={raw_num}", "normalize_failed"),
                            )
                        )
                elif ignored_units_out is not None:
                    ignored_units_out.append(
                        CoverageIgnoredUnit(
                            unit_kind="section",
                            reason="missing_num",
                            parent_label=current_chapter,
                            evidence=("missing_num",),
                        )
                    )

            if kind == "article":
                raw_num = _num_text(child)
                if raw_num:
                    observed_label = _norm_num_token(raw_num)
                    if observed_label:
                        _append_unit("article", observed_label, current_chapter, current_part, child)
                    elif ignored_units_out is not None:
                        ignored_units_out.append(
                            CoverageIgnoredUnit(
                                unit_kind="article",
                                reason="unusable_num",
                                observed_label=raw_num,
                                parent_label=current_chapter,
                                evidence=(f"raw_num={raw_num}", "normalize_failed"),
                            )
                        )
                elif ignored_units_out is not None:
                    ignored_units_out.append(
                        CoverageIgnoredUnit(
                            unit_kind="article",
                            reason="missing_num",
                            parent_label=current_chapter,
                            evidence=("missing_num",),
                        )
                    )

            _walk_children(child, current_chapter, current_part)

    _walk_children(body)

    return units


def collect_coverage_claims(
    ops: List[AmendmentOp],
    *,
    rejected_claims_out: Optional[List[CoverageRejectedClaim]] = None,
) -> List[CoverageClaim]:
    """Build CoverageClaims from a list of compiled AmendmentOps.

    Back-compat shim over :func:`collect_coverage_claims_partition`. The
    partition is the canonical conserving carrier; this shim drains its rejected
    lane into the legacy ``rejected_claims_out`` production sink (read by
    ``uncovered_recovery_prepare``) and returns the accepted claims. The drain is
    the single emission path — the rich ``CoverageRejectedClaim`` records are
    forwarded from the partition, never re-derived, so no double-emit occurs.
    """
    partition = collect_coverage_claims_partition(ops)
    if rejected_claims_out is not None:
        rejected_claims_out.extend(partition.rejected_claims)
    return list(partition.accepted)


def collect_coverage_claims_partition(
    ops: List[AmendmentOp],
) -> "CoverageClaimPartition":
    """Build a conserving partition of CoverageClaims from compiled ops.

    Conservation (Audit C): each op that targets a section, chapter, or part
    produces one accepted claim. Ops with no usable target are NOT silently
    skipped — they go to the rejected lane (with a typed reason) and the
    ``rejected_claims`` accessor exposes the rich ``CoverageRejectedClaim``
    records the production sink consumes. The accepted set is byte-identical to
    the previous return value.

    The neutral ``target_unit_kind`` is the structural authority here. The
    ``claim_kind`` is ``'explicit'`` unless the op carries typed fallback
    provenance, in which case it is ``'fallback'``. ``covered_unit_ids`` is the
    canonical unit_id for the op's primary target; matching is done later by
    ``analyze_coverage``.
    """
    claims: List[CoverageClaim] = []
    rejected_claims: List[CoverageRejectedClaim] = []

    for op in ops:
        # Raw-validator (W6 carry-forward): this partition REJECTS ops with an
        # empty focus label (`missing_target_section`) and ops whose focus kind is
        # not one of {section, chapter, part} (`unsupported_target_unit_kind`).
        # Both checks are sourced from the typed selector projection
        # (`target_cols`): the codec preserves an empty section focus label as
        # `""` (it lowers only chapter/part/special "" → None), so the
        # empty-label rejection survives the legacy-column deletion; the focus
        # kind is structurally one of the three supported kinds, so the
        # unsupported-kind branch remains as the defensive guard.
        cols = op.target_cols
        if not cols.target_section:
            rejected_claims.append(
                CoverageRejectedClaim(
                    reason="missing_target_section",
                    target=op,
                    evidence=(f"op_id={op.op_id}", f"op_type={op.op_type}"),
                )
            )
            continue

        if cols.target_unit_kind == "section":
            label = _norm_num_token(cols.target_section)
            kind = "section"
        elif cols.target_unit_kind == "chapter":
            label = _norm_num_token(cols.target_section).removesuffix("luku")
            kind = "chapter"
        elif cols.target_unit_kind == "part":
            label = _norm_num_token(cols.target_section)
            kind = "part"
        else:
            rejected_claims.append(
                CoverageRejectedClaim(
                    reason="unsupported_target_unit_kind",
                    target=op,
                    evidence=(
                        f"op_id={op.op_id}",
                        f"op_type={op.op_type}",
                        f"target_unit_kind={cols.target_unit_kind}",
                    ),
                )
            )
            continue

        # Determine claim_kind from typed provenance; `resolution_hint` is
        # historical residue only and is no longer a Finland runtime transport
        # lane.
        if isinstance(op.provenance, Recovered):
            claim_kind = "fallback"
        else:
            claim_kind = "explicit"

        # Build the candidate unit_id(s) this op might cover
        chapter_label: Optional[str] = None
        if cols.target_chapter:
            chapter_label = _norm_num_token(cols.target_chapter).removesuffix("luku")

        if chapter_label:
            base_unit_id = f"{kind}_{chapter_label}_{label}"
        else:
            base_unit_id = f"{kind}_{label}"

        evidence_parts = [f"op_id={op.op_id}", f"op_type={op.op_type}"]
        if isinstance(op.provenance, Recovered) and op.provenance.from_fallback_provenance:
            evidence_parts.append("fallback_provenance=true")
        if has_recognizer(op.provenance, RecognizerId.BODY_ROOT_REPLACE):
            evidence_parts.append("body_root_replace_fallback=true")

        claims.append(
            CoverageClaim(
                claim_kind=claim_kind,
                target=op,
                covered_unit_ids=frozenset({base_unit_id}),
                evidence=tuple(evidence_parts),
            )
        )

    residuals = tuple(
        Residual(
            kind="out_of_scope",
            reason=f"coverage claim rejected: {rejected.reason}",
            scope=next(
                (ev for ev in rejected.evidence if ev.startswith("op_id=")),
                "op_id=",
            ),
            blocking=False,
        )
        for rejected in rejected_claims
    )
    return CoverageClaimPartition(
        FilterResult(accepted_items=tuple(claims)),
        residuals=residuals,
        rejected_claims=tuple(rejected_claims),
    )


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
    directly_covered: set[str] = set()
    for claim in claims:
        directly_covered.update(claim.covered_unit_ids)

    # Build a "label-only" match set, but ONLY for ops that lack chapter
    # context.  A chapter-qualified claim (3-part unit_id "kind_parent_label")
    # must NOT absorb sections in OTHER chapters via label-only matching —
    # that would incorrectly suppress supplemental recovery for sections that
    # live in new sub-chapters (e.g. "2a luku / 17 §") when a PEG op covers
    # the same label in an unrelated chapter (e.g. "2 luku / 17 §").
    # Format: "<kind>_<label>"  (without parent prefix) — only from chapter-free claims.
    label_only_covered: set[str] = set()
    # A SEPARATE index: for every chapter-qualified claim, the set of distinct
    # chapters that claim each bare "<kind>_<label>". This matches *chapter-free*
    # body units only (see below) — a body section listed flat in the amendment
    # (no enclosing <chapter> wrapper) whose op nonetheless gained chapter
    # context from resolving the section against the parent tree (e.g. a flat
    # "14 §" body section vs a "3 luku 14 §" op → claim unit_id "section_3_14").
    # A chapter-free unit has no chapter of its own to disambiguate, so it is
    # covered by a chapter-qualified claim of the same label — BUT only when that
    # label is claimed in exactly ONE chapter. If two different chapters claim
    # the same bare label, the flat body unit is genuinely ambiguous and we keep
    # it uncovered (preserving the "OTHER chapter" absorption guard that
    # motivates `label_only_covered`). This never relaxes matching for
    # chapter-QUALIFIED body units.
    chapters_by_bare_claim: dict[str, set[str]] = {}
    for unit_id in directly_covered:
        parts = unit_id.split("_")
        if len(parts) == 2:
            # kind_label — no chapter context → covers any chapter
            label_only_covered.add(unit_id)
        elif len(parts) == 3:
            # kind_chapter_label — chapter-qualified: do NOT add to label_only
            # (guards sub-chapter recovery); record the chapter under the bare
            # label for chapter-free body-unit matching only.
            chapters_by_bare_claim.setdefault(f"{parts[0]}_{parts[2]}", set()).add(parts[1])

    gaps: List[CoverageGap] = []

    for unit in units:
        # Check direct match
        if unit.unit_id in directly_covered:
            continue
        # Check label-only match (op covered all chapters)
        label_only_id = f"{unit.kind}_{unit.observed_label}"
        if label_only_id in directly_covered or label_only_id in label_only_covered:
            continue
        # Chapter-free body unit covered by an unambiguous same-label,
        # chapter-qualified claim (chapter came from parent-tree resolution).
        if not unit.parent_label and len(chapters_by_bare_claim.get(label_only_id, ())) == 1:
            continue

        # Unit is unclaimed — classify disposition
        if "container" in unit.tags and unit.kind == "chapter":
            # Container-only chapter: it carries no operative whole-chapter
            # payload of its own and exists solely to scope the section edits
            # nested inside it (which are the real units). It is therefore not
            # a dropped op — its content is covered by its child section claims.
            disposition = "covered_by_broad_scope"
            evidence: tuple[str, ...] = ("tag:container",)
        elif "nonoperative" in unit.tags or "provenance" in unit.tags:
            disposition = "ignore_nonoperative"
            evidence = ("tag:nonoperative" if "nonoperative" in unit.tags else "tag:provenance",)
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
