"""Shared coverage result types for observed-vs-claimed analysis.

The module models the common contract used by frontends and tooling when they
compare observed structural units against claimed targets and classify gaps.
It intentionally stays at the typed-result layer:

    observed units  -> CoverageUnit
    claimed targets  -> CoverageClaim
    uncovered units  -> CoverageGap
    full partition   -> CoverageReport

The shared disposition buckets are:

    - ``supplemental_candidate``: actionable uncovered unit
    - ``ignore_nonoperative``: present in the source, but not operative
    - ``covered_by_broad_scope``: absorbed by a broader claim
    - ``container_overbundle_pathology``: bundle/standalone mismatch
    - ``ambiguous_uncovered``: unresolved by the available evidence
    - ``duplicate_standalone_and_bundled``: duplicate target appears twice

Frontends decide how to populate the buckets. This module only defines the
shared carrier and the common partition helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Literal, Optional, Tuple


CoverageClaimKind = Literal[
    "explicit",
    "broad",
    "fallback",
    "supplemental",
]

CoverageDisposition = Literal[
    "ignore_nonoperative",
    "supplemental_candidate",
    "covered_by_broad_scope",
    "container_overbundle_pathology",
    "ambiguous_uncovered",
    "duplicate_standalone_and_bundled",
]

ACTIONABLE_GAP_DISPOSITIONS: FrozenSet[CoverageDisposition] = frozenset(
    {"supplemental_candidate"}
)
NON_ACTIONABLE_GAP_DISPOSITIONS: FrozenSet[CoverageDisposition] = frozenset(
    {"ignore_nonoperative", "covered_by_broad_scope"}
)
OBLIGATION_GAP_DISPOSITIONS: FrozenSet[CoverageDisposition] = frozenset(
    {
        "ambiguous_uncovered",
        "container_overbundle_pathology",
        "duplicate_standalone_and_bundled",
    }
)
_COVERAGE_CLAIM_KINDS: FrozenSet[CoverageClaimKind] = frozenset(
    {"explicit", "broad", "fallback", "supplemental"}
)
_COVERAGE_DISPOSITIONS: FrozenSet[CoverageDisposition] = (
    ACTIONABLE_GAP_DISPOSITIONS | NON_ACTIONABLE_GAP_DISPOSITIONS | OBLIGATION_GAP_DISPOSITIONS
)


def _string_tuple(values: Tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    result = tuple(values)
    if not all(isinstance(value, str) for value in result):
        raise ValueError(f"{field_name} must contain strings")
    return result


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageUnit:
    """One operative unit observed in the amendment body XML.

    Produced by ``extract_body_coverage`` before any claim collection or
    apply step.  The ``unit_id`` is unique within one amendment's analysis.

    Args:
        unit_id: Unique identifier for this unit within the amendment.
        kind: Structural kind — ``'section'``, ``'chapter'``, ``'appendix'``,
            ``'heading'``, ``'commencement'``, etc.
        observed_label: Section number, chapter number, or similar label
            extracted directly from the source XML.  ``None`` if the element
            has no label.
        parent_label: Label of the enclosing container (e.g. the chapter
            number for a section inside a chapter node).  ``None`` at top
            level.
        payload_ref: Opaque reference into the body surface — typically an
            ``IRNode`` or an XPath-style path.  Used by downstream synthesis
            to locate the payload without re-parsing.
        tags: Free-form classification tags attached during extraction.
            Common values: ``'nonoperative'``, ``'provenance'``, ``'context'``,
            ``'standalone'``, ``'bundled_in_container'``.
    """

    unit_id: str
    kind: str
    observed_label: Optional[str]
    parent_label: Optional[str]
    payload_ref: Optional[object]
    tags: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.unit_id:
            raise ValueError("CoverageUnit.unit_id must be non-empty")
        if not self.kind:
            raise ValueError("CoverageUnit.kind must be non-empty")
        tags = frozenset(self.tags)
        if not all(isinstance(tag, str) for tag in tags):
            raise ValueError("CoverageUnit.tags must contain strings")
        object.__setattr__(self, "tags", tags)


@dataclass(frozen=True)
class CoverageClaim:
    """A claim that an amendment operation covers one or more body units.

    Produced by ``collect_coverage_claims`` from already-elaborated
    PEG/fallback/supplemental ops — before any tree mutation occurs.

    Args:
        claim_kind: How the claim was established:
            ``'explicit'``     — direct PEG target with matching label,
            ``'broad'``        — whole-chapter/part replace subsumes members,
            ``'fallback'``     — fallback-path heuristic match,
            ``'supplemental'`` — synthesised by a prior coverage-gap pass.
        target: The ``LegalAddress`` (or equivalent) that the op addresses.
        covered_unit_ids: The set of ``CoverageUnit.unit_id`` values that
            this claim subsumes.
        evidence: Human-readable chain explaining how the claim was
            established (useful for audit and regression triage).
    """

    claim_kind: CoverageClaimKind
    target: object
    covered_unit_ids: FrozenSet[str]
    evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.claim_kind not in _COVERAGE_CLAIM_KINDS:
            raise ValueError(f"unsupported CoverageClaim.claim_kind: {self.claim_kind!r}")
        covered_unit_ids = frozenset(self.covered_unit_ids)
        if not all(isinstance(unit_id, str) and unit_id for unit_id in covered_unit_ids):
            raise ValueError("CoverageClaim.covered_unit_ids must contain non-empty strings")
        object.__setattr__(self, "covered_unit_ids", covered_unit_ids)
        object.__setattr__(
            self,
            "evidence",
            _string_tuple(self.evidence, field_name="CoverageClaim.evidence"),
        )


@dataclass(frozen=True)
class CoverageGap:
    """An amendment body unit not covered by any operation claim.

    Produced by ``analyze_coverage`` as ``observed - claimed``.  The
    ``disposition`` field captures the typed classifier output so downstream
    stages can route the gap without re-running heuristics.

    Args:
        unit: The unclaimed ``CoverageUnit``.
        disposition: Typed classifier result — one of ``DISPOSITION_KINDS``.
        suggested_target: A ``LegalAddress`` hint produced by heuristics
            (for example preamble regex or label inference). May be ``None``
            when no confident hint is available.
        evidence: Human-readable chain explaining the disposition decision.
    """

    unit: CoverageUnit
    disposition: CoverageDisposition
    suggested_target: Optional[object]
    evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.unit, CoverageUnit):
            raise ValueError("CoverageGap.unit must be a CoverageUnit")
        if self.disposition not in _COVERAGE_DISPOSITIONS:
            raise ValueError(f"unsupported CoverageGap.disposition: {self.disposition!r}")
        object.__setattr__(
            self,
            "evidence",
            _string_tuple(self.evidence, field_name="CoverageGap.evidence"),
        )


@dataclass(frozen=True)
class CoverageIgnoredUnit:
    """A body unit candidate that extraction intentionally ignored.

    Used when the amendment body contains structurally relevant XML that does
    not become a normal ``CoverageUnit`` because its labeling or shape is not
    usable enough for the observed-vs-claimed diff.
    """

    unit_kind: str
    reason: str
    observed_label: Optional[str] = None
    parent_label: Optional[str] = None
    payload_ref: Optional[object] = None
    evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.unit_kind:
            raise ValueError("CoverageIgnoredUnit.unit_kind must be non-empty")
        if not self.reason:
            raise ValueError("CoverageIgnoredUnit.reason must be non-empty")
        object.__setattr__(
            self,
            "evidence",
            _string_tuple(self.evidence, field_name="CoverageIgnoredUnit.evidence"),
        )


@dataclass(frozen=True)
class CoverageRejectedClaim:
    """A compiled op that coverage claim collection intentionally skipped."""

    reason: str
    target: object
    evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("CoverageRejectedClaim.reason must be non-empty")
        object.__setattr__(
            self,
            "evidence",
            _string_tuple(self.evidence, field_name="CoverageRejectedClaim.evidence"),
        )


@dataclass(frozen=True)
class CoverageReport:
    """Full coverage analysis for one amendment's body.

    Immutable container for the three-way partition of body units.
    Derived views (``uncovered_count``, ``supplemental_candidates``,
    ``obligations``) are computed properties that filter ``gaps``.

    Args:
        units: All ``CoverageUnit`` objects extracted from the body surface.
        claims: All ``CoverageClaim`` objects collected from elaborated ops.
        gaps: All ``CoverageGap`` objects (observed units without a matching
            claim), each annotated with a disposition.
    """

    units: Tuple[CoverageUnit, ...]
    claims: Tuple[CoverageClaim, ...]
    gaps: Tuple[CoverageGap, ...]
    ignored_units: Tuple[CoverageIgnoredUnit, ...] = field(default_factory=tuple)
    rejected_claims: Tuple[CoverageRejectedClaim, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", tuple(self.units))
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(self, "gaps", tuple(self.gaps))
        object.__setattr__(self, "ignored_units", tuple(self.ignored_units))
        object.__setattr__(self, "rejected_claims", tuple(self.rejected_claims))
        if not all(isinstance(unit, CoverageUnit) for unit in self.units):
            raise ValueError("CoverageReport.units must contain CoverageUnit records")
        if not all(isinstance(claim, CoverageClaim) for claim in self.claims):
            raise ValueError("CoverageReport.claims must contain CoverageClaim records")
        if not all(isinstance(gap, CoverageGap) for gap in self.gaps):
            raise ValueError("CoverageReport.gaps must contain CoverageGap records")
        if not all(isinstance(unit, CoverageIgnoredUnit) for unit in self.ignored_units):
            raise ValueError("CoverageReport.ignored_units must contain CoverageIgnoredUnit records")
        if not all(isinstance(claim, CoverageRejectedClaim) for claim in self.rejected_claims):
            raise ValueError(
                "CoverageReport.rejected_claims must contain CoverageRejectedClaim records"
            )

    @property
    def uncovered_count(self) -> int:
        """Number of actionable gaps.

        Non-actionable dispositions like ``'ignore_nonoperative'`` and
        ``'covered_by_broad_scope'`` are excluded from this count.
        """
        return sum(
            1 for g in self.gaps if g.disposition not in NON_ACTIONABLE_GAP_DISPOSITIONS
        )

    @property
    def supplemental_candidates(self) -> Tuple[CoverageGap, ...]:
        """Gaps that should produce supplemental operations.

        These are genuine operative units present in the amendment body that
        have no corresponding claim.  The ``suggested_target`` field on each
        gap provides an address hint for synthesis.
        """
        return tuple(
            g for g in self.gaps if g.disposition in ACTIONABLE_GAP_DISPOSITIONS
        )

    @property
    def obligations(self) -> Tuple[CoverageGap, ...]:
        """Gaps that cannot be resolved automatically.

        Includes ``'ambiguous_uncovered'``, ``'container_overbundle_pathology'``,
        and ``'duplicate_standalone_and_bundled'`` dispositions.  Each should
        produce an ``Obligation`` (from ``phase_result.py``) so the caller
        can surface the issue and apply a permissive ``StrictProfile`` if
        warranted.
        """
        return tuple(
            g for g in self.gaps
            if g.disposition in OBLIGATION_GAP_DISPOSITIONS
        )
