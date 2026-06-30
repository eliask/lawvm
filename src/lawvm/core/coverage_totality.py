"""``lawvm.core.coverage_totality`` — D ``COVERAGE.UNIT_UNCLASSIFIED``.

COVERAGE TOTALITY (stream D). The net-new jurisdiction-neutral core assertion
that **nothing is silently dropped** from an amendment's coverage partition:
every source unit the frontend extracted is either *claimed by an op* (covered),
*classified as a typed gap* (the injected ``classify`` disposition —
``supplemental_candidate`` / ``ignore_nonoperative`` / ``covered_by_broad_scope``
/ an obligation disposition), or *recorded as a rejected claim*. Symmetrically,
every base-IR ``target_unit`` an op could land on is either *touched* by a claim
or *asserted untouched*. A unit that is neither covered nor classified is the
residue this audit surfaces — a unit that fell out of the partition with no
owner. It gets one typed ``COVERAGE.UNIT_UNCLASSIFIED``
:class:`~lawvm.core.phase_result.Observation`.

This is the universal half of coverage. The ``core/coverage.py`` carriers
(:class:`~lawvm.core.coverage.CoverageUnit` /
:class:`~lawvm.core.coverage.CoverageClaim` /
:class:`~lawvm.core.coverage.CoverageGap` /
:class:`~lawvm.core.coverage.CoverageReport` /
:class:`~lawvm.core.coverage.CoverageRejectedClaim`) already exist and FI already
produces them (``finland/body_coverage.py``). The *unit extraction* and the *gap
classification disposition* stay in the frontend (per
``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §3.3/§3.5 — "the totality assertion
hoists; unit extraction stays in the frontend"). The new piece here is the
totality assertion itself, plus the universal claim→unit covered-set algebra
(direct ``covered_unit_ids`` match + chapter-free label-only match) lifted out of
FI's ``analyze_coverage`` so every frontend's covered-set computation is shared.

THE PARTITION (what "owned" means). For each source unit, in priority order:

  1. **covered** — a claim's ``covered_unit_ids`` references the unit directly,
     or a *chapter-free* claim covers it by label alone (the op had no chapter
     context, so it covers any chapter's section of that label — mirrors FI's
     ``label_only`` rule which deliberately does NOT let a chapter-qualified
     claim absorb sections in other chapters).
  2. **classified** — ``classify(unit)`` returns a typed
     :class:`~lawvm.core.coverage.CoverageDisposition`; the unit becomes a typed
     :class:`~lawvm.core.coverage.CoverageGap` (owned residue, no finding).
  3. **unclassified** — ``classify(unit)`` returns ``None`` AND no claim covers
     the unit: the unit fell out of the partition with no owner. One
     ``COVERAGE.UNIT_UNCLASSIFIED`` observation; the unit is recorded as an
     ``ambiguous_uncovered`` gap in the report so the report still partitions
     totally (covered ∪ classified ∪ unclassified == input).

Rejected claims (ops coverage collection intentionally skipped) are carried
through verbatim — they are an explicit owner lane, not a silent drop.

TARGET SYMMETRY. ``target_units`` are the base-IR units the ops could land on.
Each is either *touched* (some claim references it, by id or chapter-free label)
or *asserted untouched* (no claim references it — a legitimate no-op, NOT a
finding). The audit records the touched/untouched split in the report's
diagnostics but emits no finding for an untouched target: a base unit no op
addresses is the normal case, not a gap.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). Evidence-plane audit lane: it inspects
passed coverage carriers + the op stream, returns
:class:`~lawvm.core.phase_result.Observation` tuples and a
:class:`~lawvm.core.coverage.CoverageReport`, and **never mutates legal state**,
never fabricates a claim or a disposition, never raises on shape-valid input. The
wire consumer (a future unified seam, §3.1) decides whether an observation
becomes a strict barrier or a quirks finding — this is NET-NEW core-only audit,
not wired into any apply lane. Reuses the ``core/coverage.py`` carriers verbatim
(no parallel coverage model) and mirrors the D7 (``commencement_totality_audit``)
and C (``provenance_totality_audit``) observation-role precedents.

JURISDICTION-NEUTRAL. The carriers are core; the classifier is injected. FI is
DELEGATED (this module does not import or modify ``finland/``); its tag-based
classification is the model for :func:`default_gap_classifier`. The offline
diagnostic (:mod:`lawvm.tools.coverage_totality_report`) runs the assertion over
FI's already-produced coverage carriers READ-ONLY, to show it works against a
real producer.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Optional

from lawvm.core.coverage import (
    CoverageDisposition,
    CoverageGap,
    CoverageRejectedClaim,
    CoverageReport,
    CoverageUnit,
)
from lawvm.core.coverage import CoverageClaim
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.phase_result import Observation

# Public finding code, registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
COVERAGE_UNIT_UNCLASSIFIED = "COVERAGE.UNIT_UNCLASSIFIED"

# Audit-stage / owner stamped into the emitted Observations. Mirror the registry
# row's phase/owner so the wire point and the registry agree.
_COVERAGE_AUDIT_STAGE = "coverage-totality"
_COVERAGE_AUDIT_OWNER = "coverage_totality_audit"
_COVERAGE_AUDIT_REASON = "source_unit_neither_covered_nor_classified"

# An unclassified unit is still recorded in the report (so the report partitions
# totally) under this disposition — it is, definitionally, an uncovered unit the
# classifier could not resolve. Same meaning as FI's ``ambiguous_uncovered``.
_UNCLASSIFIED_DISPOSITION: CoverageDisposition = "ambiguous_uncovered"


# A GapClassifier is the per-profile disposition function. It receives an
# UNCOVERED source unit and returns a typed disposition (the unit is an owned,
# classified gap) or ``None`` (the classifier cannot place it — the unit is
# unclassified and gets a finding). The classifier is frontend-supplied; the
# totality assertion around it is the universal part.
GapClassifier = Callable[[CoverageUnit], Optional[CoverageDisposition]]


def default_gap_classifier(unit: CoverageUnit) -> Optional[CoverageDisposition]:
    """Jurisdiction-neutral default disposition for an uncovered source unit.

    Models FI's ``analyze_coverage`` tag logic (FI is DELEGATED; this is the
    reference shape, not an import):

    * a container-only chapter (``container`` tag, ``kind == 'chapter'``) carries
      no operative whole-chapter payload of its own — its content is covered by
      its child section claims → ``covered_by_broad_scope``;
    * a ``nonoperative`` / ``provenance`` tagged unit is present in the source
      but not operative → ``ignore_nonoperative``;
    * any other uncovered unit is a genuine operative unit with no claim →
      ``supplemental_candidate``.

    Returns a typed disposition for EVERY unit (the default never returns
    ``None``): under the default classifier, no unit is ever unclassified — an
    uncovered operative unit is an actionable ``supplemental_candidate``, not a
    silent drop. A frontend that supplies a STRICTER classifier (one that returns
    ``None`` for units it refuses to place) is the path that surfaces
    ``COVERAGE.UNIT_UNCLASSIFIED``.
    """
    if "container" in unit.tags and unit.kind == "chapter":
        return "covered_by_broad_scope"
    if "nonoperative" in unit.tags or "provenance" in unit.tags:
        return "ignore_nonoperative"
    return "supplemental_candidate"


def _covered_sets(
    claims: Sequence[CoverageClaim],
) -> tuple[frozenset[str], frozenset[str]]:
    """Compute ``(directly_covered, label_only_covered)`` from claims.

    Lifted verbatim (in behaviour) from FI's ``analyze_coverage`` so the
    covered-set algebra is shared, not re-derived per frontend:

    * ``directly_covered`` — every ``covered_unit_ids`` value across all claims.
    * ``label_only_covered`` — the 2-part (``kind_label``) ids among the directly
      covered set, i.e. claims with NO chapter context. These cover a unit by
      label alone regardless of chapter. A 3-part chapter-qualified id
      (``kind_parent_label``) is deliberately NOT promoted to label-only: a
      chapter-scoped claim must not absorb sections of the same label in OTHER
      chapters (that would silently suppress recovery for new sub-chapters).
    """
    directly_covered: set[str] = set()
    for claim in claims:
        directly_covered.update(claim.covered_unit_ids)
    label_only_covered = {uid for uid in directly_covered if len(uid.split("_")) == 2}
    return frozenset(directly_covered), frozenset(label_only_covered)


def _unit_is_covered(
    unit: CoverageUnit,
    directly_covered: frozenset[str],
    label_only_covered: frozenset[str],
) -> bool:
    """True iff some claim covers ``unit`` (direct id or chapter-free label).

    Mirrors FI's matching: a direct ``unit_id`` match, or a chapter-free
    ``<kind>_<label>`` match against either the directly-covered set (a 2-part
    claim id) or the label-only set.
    """
    if unit.unit_id in directly_covered:
        return True
    label_only_id = f"{unit.kind}_{unit.observed_label}"
    return label_only_id in directly_covered or label_only_id in label_only_covered


def _format_address(target: object) -> str:
    """Best-effort human-readable target string for the observation surface.

    Claims carry an opaque ``target`` (a ``LegalAddress``, an op, or a frontend
    carrier). We stringify it for the diagnostic surface only — no fields are
    consumed from it.
    """
    if target is None:
        return ""
    if isinstance(target, LegalAddress):
        formatted = str(target)
        return formatted if formatted else repr(target)
    return str(target)


def _build_observation(unit: CoverageUnit, source_statute: str) -> Observation:
    """Build the typed ``COVERAGE.UNIT_UNCLASSIFIED`` observation.

    Detail carries the unit identity, kind, labels, and tags so a triager can
    answer "which source unit fell out of the partition with no owner" without
    re-running coverage analysis. Only load-bearing identity fields are carried
    (no XML handles, no payload bodies) — mirrors the D7/C
    load-bearing-fields-only discipline.
    """
    detail: dict[str, Any] = {
        "unit_id": unit.unit_id,
        "kind": unit.kind,
        "observed_label": unit.observed_label,
        "parent_label": unit.parent_label,
        "tags": tuple(sorted(unit.tags)),
        "reason": _COVERAGE_AUDIT_REASON,
        "owner": _COVERAGE_AUDIT_OWNER,
    }
    return Observation(
        kind=COVERAGE_UNIT_UNCLASSIFIED,
        stage=_COVERAGE_AUDIT_STAGE,
        detail=detail,
        source_statute=source_statute,
    )


def assert_coverage_totality(
    source_units: Sequence[CoverageUnit],
    ops: Sequence[LegalOperation],
    target_units: Sequence[CoverageUnit],
    ledger: Sequence[CoverageClaim],
    *,
    classify: GapClassifier = default_gap_classifier,
    rejected_claims: Sequence[CoverageRejectedClaim] = (),
    source_statute: str = "",
) -> tuple[tuple[Observation, ...], CoverageReport]:
    """Assert coverage totality over a ``(source_units, ops, target_units)`` surface.

    Every ``source_unit`` is partitioned into covered / classified / unclassified;
    every ``target_unit`` is touched-or-asserted-untouched. A source unit that is
    neither covered by a claim nor classified by ``classify`` gets one
    ``COVERAGE.UNIT_UNCLASSIFIED`` observation. Nothing is silently dropped.

    Args:
        source_units: the frontend-extracted units observed in the amendment
            body (FI's ``extract_body_coverage`` is the reference producer). A
            unit filtered out *before* extraction is invisible here; that filter
            owns its receipt accounting (AGENTS.md §1.8) — and an
            intentionally-ignored unit is the frontend's
            :class:`~lawvm.core.coverage.CoverageIgnoredUnit` lane, not a drop.
        ops: the compiled op stream the claims were collected from. Carried for
            the seam's caller symmetry (§3.1 ``coverage_delta`` accumulation);
            the covered-set is computed from ``ledger`` (the accumulated claims),
            not re-derived from ``ops`` here, so the audit and the producer agree
            on what "claimed" means.
        target_units: the base-IR units the ops could land on. Each is recorded
            as touched (a claim references it) or asserted-untouched (no claim) —
            an untouched base unit is a legitimate no-op, NOT a finding.
        ledger: the accumulated :class:`~lawvm.core.coverage.CoverageClaim`
            stream (the seam's per-op ``coverage_delta`` accumulation; FI's
            ``collect_coverage_claims_partition`` accepted claims are the
            reference shape). The existing carrier is the ledger surface — no
            parallel coverage model.
        classify: the per-profile gap-disposition function. Given an UNCOVERED
            source unit it returns a typed
            :class:`~lawvm.core.coverage.CoverageDisposition` (owned, classified
            gap) or ``None`` (cannot place — unclassified → finding). Defaults to
            :func:`default_gap_classifier` (FI's tag logic), which classifies
            every unit (never ``None``).
        rejected_claims: ops coverage collection intentionally skipped, carried
            through verbatim into the report's explicit owner lane.
        source_statute: the base statute id, carried into each observation so a
            multi-statute bench run can route the finding back to its source.

    Returns:
        ``(observations, report)``. ``observations`` is one
        ``COVERAGE.UNIT_UNCLASSIFIED`` per unclassified source unit, in
        source-unit order (deterministic — the input is a deterministically
        ordered unit stream). ``report`` is a
        :class:`~lawvm.core.coverage.CoverageReport` whose ``gaps`` carry the
        classified dispositions AND the unclassified residue (recorded as
        ``ambiguous_uncovered`` so ``covered ∪ classified ∪ unclassified`` equals
        the input — the partition is total). The caller decides whether the
        observations become findings (quirks default) or strict-mode barriers;
        this function emits observations only, never raises on shape-valid input,
        never mutates carriers, never fabricates a claim or disposition.

    Per AGENTS.md §0: a source unit that falls out of the coverage partition with
    no owner is surfaced evidence that the frontend's extraction/claim/classify
    triad did not account for it — it is reported, not absorbed. The audit never
    invents a claim or a disposition to make the gap disappear.
    """
    directly_covered, label_only_covered = _covered_sets(ledger)

    observations: list[Observation] = []
    gaps: list[CoverageGap] = []
    for unit in source_units:
        if _unit_is_covered(unit, directly_covered, label_only_covered):
            # Owned: claimed by an op.
            continue
        disposition = classify(unit)
        if disposition is not None:
            # Owned: classified gap (one of the typed dispositions).
            gaps.append(
                CoverageGap(
                    unit=unit,
                    disposition=disposition,
                    suggested_target=None,
                    evidence=(f"unit_id={unit.unit_id}", f"classified={disposition}"),
                )
            )
            continue
        # Unowned: neither covered nor classified — surface it and still record
        # it in the report so the partition stays total.
        observations.append(_build_observation(unit, source_statute))
        gaps.append(
            CoverageGap(
                unit=unit,
                disposition=_UNCLASSIFIED_DISPOSITION,
                suggested_target=None,
                evidence=(f"unit_id={unit.unit_id}", _COVERAGE_AUDIT_REASON),
            )
        )

    report = CoverageReport(
        units=tuple(source_units),
        claims=tuple(ledger),
        gaps=tuple(gaps),
        rejected_claims=tuple(rejected_claims),
    )
    return tuple(observations), report


def target_touch_partition(
    target_units: Sequence[CoverageUnit],
    ledger: Sequence[CoverageClaim],
) -> tuple[tuple[CoverageUnit, ...], tuple[CoverageUnit, ...]]:
    """Split base-IR ``target_units`` into ``(touched, asserted_untouched)``.

    The symmetric half of the totality claim: every base unit the ops could land
    on is either *touched* (some claim references it, by id or chapter-free
    label) or *asserted untouched* (no claim references it). An untouched target
    is a legitimate no-op — this returns it explicitly so it is asserted-untouched
    rather than silently dropped, but it is NOT a finding (a base unit no op
    addresses is the normal case). Deterministic: preserves input order within
    each bucket.
    """
    directly_covered, label_only_covered = _covered_sets(ledger)
    touched: list[CoverageUnit] = []
    untouched: list[CoverageUnit] = []
    for unit in target_units:
        if _unit_is_covered(unit, directly_covered, label_only_covered):
            touched.append(unit)
        else:
            untouched.append(unit)
    return tuple(touched), tuple(untouched)


__all__ = [
    "COVERAGE_UNIT_UNCLASSIFIED",
    "GapClassifier",
    "assert_coverage_totality",
    "default_gap_classifier",
    "target_touch_partition",
]
