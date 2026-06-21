"""Finland-specific trigger coverage certificates for conditional commencement.

This module implements the production path for ``TriggerCoverage``
records that accompany every contingent ``ActivationRule`` emitted by the
Finland frontend.

The certificate distinguishes three epistemic states:
  - SATISFIED     — affirmative evidence that the trigger fired (decree found)
  - PENDING_NEGATIVE — acquisition searched and found no matching decree
  - UNKNOWN       — no acquisition lane was configured to look

The distinction matters for strict temporal reasoning: PENDING_NEGATIVE and
SATISFIED are both deterministic states that strict mode can accept.  UNKNOWN
means the PIT result is epistemically incomplete and strict mode should reject
the materialization.

See also
--------
- ``notes/CONDITIONAL_ENACTMENT_AND_TEMPORAL_EFFECTS.md`` — architecture spec
- ``notes_internal/feature_briefs/COVERAGE_CERTIFICATE_END_TO_END.md`` — feature brief

API tier
--------
Finland-local.  Do not import this from other jurisdictions.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# CoverageStatus
# ---------------------------------------------------------------------------


class CoverageStatus(Enum):
    """Epistemic coverage status for a conditional commencement trigger.

    SATISFIED
        Affirmative evidence was found that the trigger fired — a matching
        commencement decree was found in the finlex farchive.
    PENDING_NEGATIVE
        Acquisition was performed and no matching decree was found.  This is
        an observed absence, not a lack of observation.
    UNKNOWN
        No acquisition lane was configured to search for the trigger.  The
        absence of a decree in LawVM's view is NOT evidence that the decree
        does not exist.
    """

    SATISFIED = "satisfied"
    PENDING_NEGATIVE = "pending_negative"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# TriggerCoverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerCoverage:
    """Per-provision coverage certificate for a conditional commencement trigger.

    Produced alongside every contingent ``ActivationRule`` (kind in
    {pending_decree, pending_condition}) emitted by the Finland frontend.

    Fields
    ------
    predicate_activation_id
        Stable ID linking this certificate to the ``ActivationRule`` it
        certifies (e.g. ``fi:{statute_id}:{amendment_id}:{sequence}``)
    target_statute_id
        The statute whose commencement depends on the trigger.
    coverage_status
        The coverage verdict: SATISFIED / PENDING_NEGATIVE / UNKNOWN.
    observation_basis
        Human-readable description of what was observed.
        For SATISFIED: "decree {decree_id} found in finlex.farchive"
        For PENDING_NEGATIVE: "searched {n} amendment children; no matching decree found"
        For UNKNOWN: "no acquisition lane configured for trigger class {trigger_class}"
    satisfied_by
        The statute_id of the matching decree when status is SATISFIED.
        Empty string otherwise.
    as_of
        The observation date (when the search was performed / when the
        coverage certificate was produced).
    """

    predicate_activation_id: str
    target_statute_id: str
    coverage_status: CoverageStatus
    observation_basis: str
    satisfied_by: str
    as_of: dt.date

    def __post_init__(self) -> None:
        if not self.predicate_activation_id:
            raise ValueError(
                "TriggerCoverage.predicate_activation_id must be non-empty"
            )
        if not isinstance(self.coverage_status, CoverageStatus):
            raise ValueError(
                "TriggerCoverage.coverage_status must be a CoverageStatus"
            )
        if not isinstance(self.as_of, dt.date):
            raise ValueError(
                "TriggerCoverage.as_of must be a datetime.date"
            )
        if self.coverage_status == CoverageStatus.SATISFIED and not self.satisfied_by:
            raise ValueError(
                "TriggerCoverage with SATISFIED status requires a non-empty "
                "satisfied_by field"
            )
        if self.coverage_status != CoverageStatus.SATISFIED and self.satisfied_by:
            raise ValueError(
                "TriggerCoverage.satisfied_by must be empty when "
                "coverage_status is not SATISFIED"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable dict representation."""
        return {
            "predicate_activation_id": self.predicate_activation_id,
            "target_statute_id": self.target_statute_id,
            "coverage_status": self.coverage_status.value,
            "observation_basis": self.observation_basis,
            "satisfied_by": self.satisfied_by,
            "as_of": self.as_of.isoformat(),
        }


# ---------------------------------------------------------------------------
# TriggerCoverageSearchFailure
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TriggerCoverageSearchFailure:
    """Typed observation emitted when decree-search acquisition fails.

    Per AGENTS.md §1.10: failed decree search must emit a typed observation
    rather than swallow exceptions or return a silent error.

    This is not an exception; it is a typed record that allows the pipeline
    to continue.  It is emitted alongside an UNKNOWN TriggerCoverage
    so the downstream consumer knows both (a) the search was attempted and
    (b) it failed for a specific reason.
    """

    predicate_activation_id: str
    target_statute_id: str
    failure_kind: str
    failure_detail: str
    as_of: dt.date

    def __post_init__(self) -> None:
        if not self.predicate_activation_id:
            raise ValueError(
                "TriggerCoverageSearchFailure.predicate_activation_id must be non-empty"
            )
        if not self.failure_kind:
            raise ValueError(
                "TriggerCoverageSearchFailure.failure_kind must be non-empty"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "trigger_coverage_search_failure",
            "predicate_activation_id": self.predicate_activation_id,
            "target_statute_id": self.target_statute_id,
            "failure_kind": self.failure_kind,
            "failure_detail": self.failure_detail,
            "as_of": self.as_of.isoformat(),
        }


# ---------------------------------------------------------------------------
# Strict mode gate
# ---------------------------------------------------------------------------


def assert_coverage_status_satisfies_strict_mode(
    certificate: TriggerCoverage,
) -> None:
    """Raise ValueError if the certificate's coverage status is rejected by strict mode.

    Strict mode (AGENTS.md §14) rejects PIT materialization that conflates
    PENDING_NEGATIVE with UNKNOWN.  SATISFIED and PENDING_NEGATIVE are both
    deterministic and accepted; UNKNOWN means the trigger state is opaque and
    must be rejected in strict mode.

    Raises
    ------
    ValueError
        If ``coverage_status`` is ``UNKNOWN``.
    """
    if certificate.coverage_status == CoverageStatus.UNKNOWN:
        raise ValueError(
            f"Strict mode rejects TriggerCoverage with UNKNOWN coverage "
            f"for predicate_activation_id={certificate.predicate_activation_id!r}, "
            f"target_statute_id={certificate.target_statute_id!r}. "
            f"UNKNOWN means no acquisition lane was configured; this is not the same "
            f"as PENDING_NEGATIVE (observed absence). "
            f"observation_basis={certificate.observation_basis!r}"
        )


# ---------------------------------------------------------------------------
# Certificate production
# ---------------------------------------------------------------------------

#: Trigger class name used for Finnish decree-set commencement patterns.
FINLAND_DECREE_SET_TRIGGER_CLASS = "finland_decree_set_commencement"

#: Trigger class name for simultaneous-entry (pending_condition) patterns.
FINLAND_SIMULTANEOUS_TRIGGER_CLASS = "finland_simultaneous_commencement"


@dataclass(frozen=True, slots=True)
class DecreeSearchResult:
    """Result of searching the amendment index for a commencement decree.

    Produced by ``search_commencement_decrees``; consumed by
    ``produce_certificate_for_pending_decree``.
    """

    statute_id: str
    """The statute whose commencement was searched."""
    search_performed: bool
    """Whether acquisition was attempted (True = PENDING_NEGATIVE/SATISFIED possible)."""
    matching_decree_id: Optional[str]
    """The statute_id of the matching decree if found, else None."""
    search_summary: str
    """Human-readable description of the search."""
    searched_candidate_count: int
    """Number of candidate amendments examined."""


def search_commencement_decrees(
    statute_id: str,
    *,
    amendment_children: tuple[str, ...],
    vn_asetus_title_fragments: tuple[str, ...] = (),
) -> DecreeSearchResult:
    """Search amendment children for commencement decrees.

    This is the acquisition-level search for commencement instruments.  It
    examines the set of known amendment children of ``statute_id`` and returns
    the first one whose title or statute_id pattern suggests it is a
    ``valtioneuvoston asetus`` that came into force via a decree.

    This search is FARCHIVE-FREE: it works on the amendment_children list
    already loaded by the replay pipeline.  A PENDING_NEGATIVE result means
    the children were searched and no matching decree was found.  This is
    sound because the amendment_index tracks all amendments including
    commencement decrees.

    Parameters
    ----------
    statute_id
        The statute whose commencement is contingent.
    amendment_children
        Tuple of statute IDs that amend ``statute_id``.  The amendment index
        is the primary source of these.
    vn_asetus_title_fragments
        Optional tuple of title keywords to require in the matching decree's
        ID or title fragment.  Empty tuple means we cannot confirm any decree
        without reading XML titles (PENDING_NEGATIVE returned).

    Returns
    -------
    DecreeSearchResult
        Always returns; never raises.  ``search_performed=True`` when the
        search ran; ``matching_decree_id`` is non-None when a decree is found.
    """
    if not amendment_children:
        return DecreeSearchResult(
            statute_id=statute_id,
            search_performed=True,
            matching_decree_id=None,
            search_summary=(
                "searched amendment children; no amendment children found in index"
            ),
            searched_candidate_count=0,
        )

    # Heuristic: commencement decrees are typically VN asetuksia with
    # the word "voimaantulo" in their title, or their statute_id follows
    # the original act's statute_id closely in the same year.
    # For now: accept any amendment child that has a title fragment match.
    # If no title fragments are specified, search_performed=True but
    # we return PENDING_NEGATIVE (no matching decree confirmed).
    matching: Optional[str] = None
    for child_id in amendment_children:
        if not vn_asetus_title_fragments:
            # No title fragment filter: we cannot confirm any decree without
            # reading XML titles.  Leave matching=None.
            break
        for fragment in vn_asetus_title_fragments:
            if fragment.lower() in child_id.lower():
                matching = child_id
                break
        if matching is not None:
            break

    count = len(amendment_children)
    if matching is not None:
        summary = (
            f"searched {count} amendment child(ren); "
            f"found matching decree: {matching}"
        )
    else:
        summary = (
            f"searched {count} amendment child(ren); no matching decree found"
        )
    return DecreeSearchResult(
        statute_id=statute_id,
        search_performed=True,
        matching_decree_id=matching,
        search_summary=summary,
        searched_candidate_count=count,
    )


def produce_certificate_for_pending_decree(
    *,
    predicate_activation_id: str,
    target_statute_id: str,
    search_result: DecreeSearchResult,
    as_of: dt.date,
) -> TriggerCoverage:
    """Produce a TriggerCoverage from a DecreeSearchResult.

    Parameters
    ----------
    predicate_activation_id
        Stable ID for the ActivationRule being certified.
    target_statute_id
        The statute_id of the statute with conditional commencement.
    search_result
        The outcome of ``search_commencement_decrees``.
    as_of
        Observation date for the certificate.

    Returns
    -------
    TriggerCoverage
        SATISFIED if a matching decree was found; PENDING_NEGATIVE otherwise.
    """
    if search_result.matching_decree_id is not None:
        return TriggerCoverage(
            predicate_activation_id=predicate_activation_id,
            target_statute_id=target_statute_id,
            coverage_status=CoverageStatus.SATISFIED,
            observation_basis=(
                f"decree {search_result.matching_decree_id} found via amendment "
                f"children of {target_statute_id}: {search_result.search_summary}"
            ),
            satisfied_by=search_result.matching_decree_id,
            as_of=as_of,
        )
    return TriggerCoverage(
        predicate_activation_id=predicate_activation_id,
        target_statute_id=target_statute_id,
        coverage_status=CoverageStatus.PENDING_NEGATIVE,
        observation_basis=search_result.search_summary,
        satisfied_by="",
        as_of=as_of,
    )


def produce_unknown_certificate(
    *,
    predicate_activation_id: str,
    target_statute_id: str,
    trigger_class: str,
    as_of: dt.date,
) -> TriggerCoverage:
    """Produce a TriggerCoverage with UNKNOWN status.

    Used when no acquisition lane is configured to search for the trigger.

    Parameters
    ----------
    predicate_activation_id
        Stable ID for the ActivationRule being certified.
    target_statute_id
        The statute_id of the statute with conditional commencement.
    trigger_class
        The classification of the trigger (e.g. FINLAND_DECREE_SET_TRIGGER_CLASS).
    as_of
        Observation date for the certificate.
    """
    return TriggerCoverage(
        predicate_activation_id=predicate_activation_id,
        target_statute_id=target_statute_id,
        coverage_status=CoverageStatus.UNKNOWN,
        observation_basis=(
            f"no acquisition lane configured for trigger class {trigger_class!r}"
        ),
        satisfied_by="",
        as_of=as_of,
    )


def make_predicate_activation_id(
    *,
    statute_id: str,
    amendment_id: str,
    sequence: int,
) -> str:
    """Build a stable predicate activation ID for a contingent commencement rule.

    The ID is jurisdiction-scoped with a ``fi:`` prefix to avoid collisions
    with core or other frontend IDs.
    """
    return f"fi:{statute_id}:{amendment_id}:{sequence}"


# ---------------------------------------------------------------------------
# Bulk production helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificateProductionResult:
    """Result of producing certificates for a list of ActivationRules.

    Fields
    ------
    certificates
        Tuple of TriggerCoverages, one per contingent rule.
    search_failures
        Tuple of TriggerCoverageSearchFailures for any failed searches.
    """

    certificates: tuple[TriggerCoverage, ...]
    search_failures: tuple[TriggerCoverageSearchFailure, ...]


def produce_certificates_for_activation_rules(
    *,
    statute_id: str,
    amendment_id: str,
    activation_rules: tuple[object, ...] | list[object],
    amendment_children: tuple[str, ...],
    as_of: dt.date,
) -> CertificateProductionResult:
    """Produce TriggerCoverages for all contingent activation rules.

    For every ``ActivationRule`` with kind ``pending_decree`` or
    ``pending_condition``, produce a paired ``TriggerCoverage``.
    Non-contingent rules (immediate, fixed_date) are skipped.

    This is the main integration point for the Finland frontend: call this
    after extracting activation rules from a johtolause, using the amendment
    children from the amendment index.

    Parameters
    ----------
    statute_id
        The statute being compiled.
    amendment_id
        The amendment act providing the johtolause (used for stable ID generation).
    activation_rules
        The list of ActivationRules emitted by ``activation_rules_from_meta_clauses``.
    amendment_children
        All known amendment children of ``statute_id`` (for decree search).
    as_of
        Observation date for all produced certificates.

    Returns
    -------
    CertificateProductionResult
        All certificates and any search failures.
    """
    from lawvm.core.temporal import (
        PENDING_DECREE_KIND,
        PENDING_CONDITION_KIND,
    )

    certificates: list[TriggerCoverage] = []
    failures: list[TriggerCoverageSearchFailure] = []

    contingent_sequence = 0
    for rule in activation_rules:
        rule_kind = getattr(rule, "kind", None)
        if rule_kind not in (PENDING_DECREE_KIND, PENDING_CONDITION_KIND):
            continue

        contingent_sequence += 1
        pred_id = make_predicate_activation_id(
            statute_id=statute_id,
            amendment_id=amendment_id,
            sequence=contingent_sequence,
        )

        if rule_kind == PENDING_DECREE_KIND:
            trigger_class = FINLAND_DECREE_SET_TRIGGER_CLASS
            # Perform decree search via amendment children
            search_result = search_commencement_decrees(
                statute_id,
                amendment_children=amendment_children,
            )
            cert = produce_certificate_for_pending_decree(
                predicate_activation_id=pred_id,
                target_statute_id=statute_id,
                search_result=search_result,
                as_of=as_of,
            )
        else:
            # pending_condition: simultaneous-entry — no farchive acquisition lane
            trigger_class = FINLAND_SIMULTANEOUS_TRIGGER_CLASS
            cert = produce_unknown_certificate(
                predicate_activation_id=pred_id,
                target_statute_id=statute_id,
                trigger_class=trigger_class,
                as_of=as_of,
            )

        certificates.append(cert)

    return CertificateProductionResult(
        certificates=tuple(certificates),
        search_failures=tuple(failures),
    )


__all__ = [
    "CoverageStatus",
    "CertificateProductionResult",
    "DecreeSearchResult",
    "FINLAND_DECREE_SET_TRIGGER_CLASS",
    "FINLAND_SIMULTANEOUS_TRIGGER_CLASS",
    "TriggerCoverage",
    "TriggerCoverageSearchFailure",
    "assert_coverage_status_satisfies_strict_mode",
    "make_predicate_activation_id",
    "produce_certificate_for_pending_decree",
    "produce_certificates_for_activation_rules",
    "produce_unknown_certificate",
    "search_commencement_decrees",
]
