"""Canonical core stage-result types — the StageResult endgame keystone.

Program spine: ``notes_internal/STAGERESULT_ENDGAME.md`` (the goal, the target
contract, the §LEDGER, the wave plan). Doctrine: ``pro_on_architectural_coherence.md``
§2 (the stage contract) and §7 (a SMALL set of typed waists, NOT one universal
object).

WHAT THIS MODULE IS
===================
The target contract every LawVM pipeline waist eventually converges to::

    StageResult[T] = (value, evidence, residuals, findings, coverage, authority)
    PartitionResult[T] = (accepted, rejected, pending, residuals, findings, coverage)  # filters

This module DEFINES that shape by COMPOSING the already-canonical building
blocks — it does NOT reinvent them and (Pro §7) it is NOT a universal ``Thing``.
Each composed type keeps its narrow job:

  * findings    -> :class:`lawvm.core.phase_result.Finding` (canonical, registry-checked)
  * evidence    -> :class:`lawvm.core.source_witness.SourceWitness` /
                   :class:`~lawvm.core.source_witness.DigestWitness` (typed witnesses)
  * authority   -> :class:`lawvm.core.execution_authorization.ExecutionAuthorization`
                   + :class:`lawvm.core.source_acquisition.SourceBundleAdmission`
  * filter lane -> :class:`lawvm.core.filter_result.FilterResult` /
                   :class:`~lawvm.core.filter_result.RejectedItem`

WHAT THIS WAVE DOES *NOT* DO
============================
Wave 0 (FOUNDATION) adds NEW types only. NO producer is migrated onto them yet
(later waves do that, highest-risk-first, bench 0-delta). There is therefore
ZERO behavior change: nothing imports these types into a live pipeline path here.

TOTAL ACCOUNTING (the identity defaults)
========================================
A stage with nothing to report must still construct a clean StageResult
trivially. Empty ``residuals`` = "fully owned"; the empty :class:`CoverageCertificate`
is the totality-satisfied account; the neutral :class:`AuthoritySurface` is
non-authoritative by construction (``replay_authorized`` is False because there is
no authorization at all). This is the authority firewall in the type defaults
(Pro §8): a surface/evidence object is non-authoritative unless an
:class:`ExecutionAuthorization` is explicitly attached.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lawvm.core.execution_authorization import ExecutionAuthorization
from lawvm.core.filter_result import FilterResult, PendingItem, RejectedItem
from lawvm.core.phase_result import Finding
from lawvm.core.source_acquisition import SourceBundleAdmission
from lawvm.core.source_witness import DigestWitness, SourceWitness

__all__ = [
    "Residual",
    "EvidenceBundle",
    "CoverageCertificate",
    "AuthoritySurface",
    "StageResult",
    "PartitionResult",
    "RejectedItem",
    "PendingItem",
]

# A typed witness — the narrow evidence-footing waists (Pro §7). Kept as a union
# of the existing witness types rather than a new base class, so witnesses keep
# their own validation. New witness kinds extend this alias as they land.
TypedWitness = SourceWitness | DigestWitness


# ---------------------------------------------------------------------------
# Residual — the canonical typed unresolved/incomplete item.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Residual:
    """A typed unresolved/incomplete item a stage could not fully own.

    The canonical core ``Residual`` the waists converge on (Pro §7's ``Residual``
    waist). It is deliberately the GENERAL shape; the existing residual notions
    are its specializations and feed into it rather than being duplicated:

      * :class:`lawvm.core.filter_result.RejectedItem` — a filter's rejected lane
        (carries the rejected payload + reason + ``blocking``); a ``PartitionResult``
        keeps rejections in that typed form and may ALSO surface coarser
        ``Residual`` spans.
      * the FI per-family residual dataclasses (e.g.
        ``finland.legal_surface.sentence_parse.Residual``,
        ``definition_parse.Residual``, …) — char-span typed-residue records; they
        map onto this core ``Residual`` via ``kind``/``reason``/``scope`` +
        ``(source_unit_id, char_start, char_end)``.
      * the forest ``residual_span`` nodes / ``UnownedViolationToken`` —
        self-evidencing unowned spans; ``kind="unowned_violation"`` here.

    A ``Residual`` is NOT a finding (a finding is a registry-checked
    classification with a role); it is the "what was left unaccounted" half of
    total-accounting. ``blocking`` says whether its presence should forbid a clean
    claim (the §LEDGER's "incompleteness can block a clean claim" requirement).

    Attributes:
        kind:           Closed-vocabulary residual class (e.g. ``"out_of_scope"``,
                        ``"typed_residual"``, ``"unowned_violation"``,
                        ``"benign_uninterpreted"``). Required.
        reason:         Human-readable, self-evidencing reason. Required.
        scope:          What the residual is scoped to (statute/unit/family id, or
                        a phase name). May be empty for stage-global residue.
        source_unit_id: The source unit the span (if any) is into.
        char_start:     0-based inclusive offset into the unit body, or ``None``.
        char_end:       0-based exclusive offset, or ``None``.
        text:           Verbatim offending span text (self-evidencing), if any.
        blocking:       Whether this residual forbids a clean claim. Default True
                        — unowned residue is blocking unless explicitly benign.
    """

    kind: str
    reason: str
    scope: str = ""
    source_unit_id: str = ""
    char_start: int | None = None
    char_end: int | None = None
    text: str = ""
    blocking: bool = True

    def __post_init__(self) -> None:
        if not str(self.kind or "").strip():
            raise ValueError("Residual.kind must be non-empty")
        if not str(self.reason or "").strip():
            raise ValueError("Residual.reason must be non-empty")
        if not isinstance(self.blocking, bool):
            raise ValueError("Residual.blocking must be a boolean")


# ---------------------------------------------------------------------------
# EvidenceBundle — a typed bundle of witnesses (NOT authority).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """A typed bundle of source/digest witnesses backing a stage's output.

    Composes the existing witness types (:class:`SourceWitness`,
    :class:`DigestWitness`) — it does not reinvent them. "Evidence is not
    authority" (Pro §8): an ``EvidenceBundle`` NEVER carries replay authority. The
    default is empty (a stage may have produced its value without footing it on a
    persisted source witness).

    Attributes:
        witnesses: The typed witnesses (any mix of SourceWitness/DigestWitness).
    """

    witnesses: tuple[TypedWitness, ...] = ()

    def __post_init__(self) -> None:
        witnesses = tuple(self.witnesses)
        for witness in witnesses:
            if not isinstance(witness, (SourceWitness, DigestWitness)):
                raise ValueError(
                    "EvidenceBundle.witnesses must contain SourceWitness/DigestWitness records"
                )
        object.__setattr__(self, "witnesses", witnesses)

    @property
    def is_empty(self) -> bool:
        return not self.witnesses


#: The identity (empty) evidence bundle — total-accounting "no witness footing".
EMPTY_EVIDENCE: EvidenceBundle = EvidenceBundle()


# ---------------------------------------------------------------------------
# CoverageCertificate — the canonical typed coverage account.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverageCertificate:
    """A typed coverage account: the owned/benign/residual/violation partition.

    This is the CANONICAL CORE coverage certificate — a four-class partition of
    whatever a stage was accounting for (tokens, spans, candidate items, …) plus a
    totality flag. It is the abstract account the target contract's ``coverage``
    field carries.

    RELATIONSHIP TO ``finland.legal_surface.token_partition_coverage`` (note the
    name clash — they are DIFFERENT things, both kept):

      * ``token_partition_coverage.TokenPartitionCoverage`` is the FI forest's
        token-level realization of THIS account (``owned`` / ``benign_uninterpreted``
        / ``typed_residual`` / ``unowned_violation`` over tokens). It maps DIRECTLY
        onto this core ``CoverageCertificate`` field-for-field (a later wave adds
        the adapter; this wave only relates them in prose, per the 0-delta rule).
      * ``token_partition_coverage.CoverageCertificate`` is a DIFFERENT type with
        the same name living in the FI subpackage: it is the lens-vs-forest
        cross-CHECK RESULT (``nodes_checked`` / ``nodes_skipped`` / ``violations``),
        not a partition account. The two are not unified by this wave; this core
        type is the partition account, that one is a verifier result. Import the
        intended one by module path to avoid the clash.

    The fields are deliberately unit-agnostic counts so any waist (tokens, spans,
    rows, candidate operations) can fill them. ``total`` is the denominator the
    four classes must sum to for ``is_partition`` to hold.

    Attributes:
        unit:     What is being counted (e.g. ``"tokens"``, ``"spans"``, ``"rows"``,
                  ``"candidates"``). Empty for the identity certificate.
        total:    The total count being partitioned.
        owned:    Fully accounted-for / claimed.
        benign:   Unowned but carrying no actionable signal (benign uninterpreted).
        residual: Unowned but inside an explicit typed-residual scope.
        violation: Unowned, non-benign, signal-bearing — the FAILURE class
                  (Pro D2 ``unowned_violation``). Target 0; never an accepted bucket.
        totality_claimed: Whether the producer asserts the four classes are
                  exhaustive over ``total`` (a TOTALITY claim, checkable via
                  :meth:`is_partition`). Default True — the identity certificate
                  (all-zero) trivially totalizes.
    """

    unit: str = ""
    total: int = 0
    owned: int = 0
    benign: int = 0
    residual: int = 0
    violation: int = 0
    totality_claimed: bool = True

    def __post_init__(self) -> None:
        for name in ("total", "owned", "benign", "residual", "violation"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"CoverageCertificate.{name} must be a non-negative int")
        if not isinstance(self.totality_claimed, bool):
            raise ValueError("CoverageCertificate.totality_claimed must be a boolean")

    @property
    def partition_total(self) -> int:
        return self.owned + self.benign + self.residual + self.violation

    def is_partition(self) -> bool:
        """The four classes sum to ``total`` (no leak) — the totality check.

        Only meaningful when ``totality_claimed``; a producer that does not claim
        totality leaves the account open and this returns False unless it happens
        to balance.
        """
        return self.totality_claimed and self.partition_total == self.total

    @property
    def is_clean(self) -> bool:
        """The target invariant: no ``violation`` (unowned signal-bearing) items."""
        return self.violation == 0


#: The identity (empty) coverage certificate — total-accounting "nothing to
#: account for, trivially total and clean".
EMPTY_COVERAGE: CoverageCertificate = CoverageCertificate()


# ---------------------------------------------------------------------------
# AuthoritySurface — composes the authority half (firewall by default).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthoritySurface:
    """The authority half of a stage result — composed, firewalled by default.

    Composes the existing authority carriers
    (:class:`ExecutionAuthorization` — whether output may mutate legal state;
    :class:`SourceBundleAdmission` — whether the source bundle is admitted) WITHOUT
    reinventing them. Both are optional, and BOTH ABSENT is the default neutral
    surface: a surface/evidence object is NON-AUTHORITATIVE by construction (Pro §8
    authority firewall) — ``replay_authorized`` is False precisely because there is
    no ``ExecutionAuthorization`` granting it. Authority must be EXPLICITLY attached
    (an author may not set ``replay_authorized=True`` by default).

    Attributes:
        authorization: The execution authorization, or ``None`` (neutral = no
                       authority granted).
        source_admission: The source-bundle admission, or ``None``.
    """

    authorization: ExecutionAuthorization | None = None
    source_admission: SourceBundleAdmission | None = None

    def __post_init__(self) -> None:
        if self.authorization is not None and not isinstance(
            self.authorization, ExecutionAuthorization
        ):
            raise ValueError(
                "AuthoritySurface.authorization must be an ExecutionAuthorization or None"
            )
        if self.source_admission is not None and not isinstance(
            self.source_admission, SourceBundleAdmission
        ):
            raise ValueError(
                "AuthoritySurface.source_admission must be a SourceBundleAdmission or None"
            )

    @property
    def replay_authorized(self) -> bool:
        """The firewall: replay authority is granted ONLY by an explicit, granting
        :class:`ExecutionAuthorization`. The neutral surface is False."""
        return self.authorization is not None and self.authorization.replay_authorized

    @property
    def is_neutral(self) -> bool:
        return self.authorization is None and self.source_admission is None


#: The identity (neutral) authority surface — non-authoritative by construction.
NEUTRAL_AUTHORITY: AuthoritySurface = AuthoritySurface()


# ---------------------------------------------------------------------------
# StageResult[T] — the target contract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageResult[T]:
    """The canonical stage contract (Pro §2): value + the four accounts + authority.

    The shape every serious LawVM waist eventually converges to. A stage with
    nothing to report constructs one trivially from the identity defaults
    (``StageResult(value=...)``): empty residuals (= fully owned), empty evidence,
    the totality-satisfied empty coverage, and the neutral (non-authoritative)
    authority surface.

    Attributes:
        value:     The stage's primary output.
        evidence:  Typed witness footing (NOT authority). Default empty.
        residuals: Typed unaccounted/incomplete items. Empty = fully owned.
        findings:  Registry-checked findings (the classification half). Default ().
        coverage:  The partition account. Default the totality-satisfied empty one.
        authority: The authority surface. Default neutral (replay not authorized).
    """

    value: T
    evidence: EvidenceBundle = EMPTY_EVIDENCE
    residuals: tuple[Residual, ...] = ()
    findings: tuple[Finding, ...] = ()
    coverage: CoverageCertificate = EMPTY_COVERAGE
    authority: AuthoritySurface = NEUTRAL_AUTHORITY

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, EvidenceBundle):
            raise ValueError("StageResult.evidence must be an EvidenceBundle")
        residuals = tuple(self.residuals)
        if not all(isinstance(item, Residual) for item in residuals):
            raise ValueError("StageResult.residuals must contain Residual records")
        object.__setattr__(self, "residuals", residuals)
        findings = tuple(self.findings)
        if not all(isinstance(item, Finding) for item in findings):
            raise ValueError("StageResult.findings must contain Finding records")
        object.__setattr__(self, "findings", findings)
        if not isinstance(self.coverage, CoverageCertificate):
            raise ValueError("StageResult.coverage must be a CoverageCertificate")
        if not isinstance(self.authority, AuthoritySurface):
            raise ValueError("StageResult.authority must be an AuthoritySurface")

    @property
    def has_blocking_residual(self) -> bool:
        """Any residual that should forbid a clean claim (the §LEDGER requirement)."""
        return any(residual.blocking for residual in self.residuals)


# ---------------------------------------------------------------------------
# PartitionResult[T] — the filter-shaped stage result.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionResult[T]:
    """The filter-shaped stage result (Pro §2): a ``FilterResult`` + the accounts.

    DECISION (PartitionResult vs FilterResult — for diff review): NOT an alias.
    ``PartitionResult`` COMPOSES the existing :class:`FilterResult` (it holds one
    in ``filter_result``) and adds ``residuals`` / ``findings`` / ``coverage`` on
    top. Rationale: ``FilterResult[T]`` is already the canonical accepted/rejected
    lossless lane and the Audit-C conversions (vts/body_coverage/amendment_selection/
    interlink/process_structural_prepare) will adopt ``FilterResult`` directly for
    the accept/reject split; ``PartitionResult`` is then the thin wrapper that lets
    those filters ALSO surface coverage + residuals + findings when they have them,
    WITHOUT changing ``FilterResult``'s narrow job (Pro §7) and WITHOUT forcing
    every filter to immediately produce a coverage account. Aliasing
    ``PartitionResult = FilterResult`` was rejected because it would either bloat
    ``FilterResult`` with coverage/residuals (violating §7) or leave
    ``PartitionResult`` unable to carry them.

    The ``accepted`` / ``rejected`` / ``pending`` accessors delegate to the wrapped
    ``FilterResult`` so a consumer reads the same shape Pro §2 specifies, extended
    with the §6.3 temporally-deferred pending lane
    (``accepted, rejected, pending, residuals, findings, coverage``).

    Attributes:
        filter_result: The accepted/rejected lossless lane (canonical FilterResult).
        residuals:     Typed unaccounted items beyond the rejected lane. Default ().
        findings:      Registry-checked findings. Default ().
        coverage:      The partition account. Default the empty (totality) one.
    """

    filter_result: FilterResult[T] = field(default_factory=FilterResult)
    residuals: tuple[Residual, ...] = ()
    findings: tuple[Finding, ...] = ()
    coverage: CoverageCertificate = EMPTY_COVERAGE

    def __post_init__(self) -> None:
        if not isinstance(self.filter_result, FilterResult):
            raise ValueError("PartitionResult.filter_result must be a FilterResult")
        residuals = tuple(self.residuals)
        if not all(isinstance(item, Residual) for item in residuals):
            raise ValueError("PartitionResult.residuals must contain Residual records")
        object.__setattr__(self, "residuals", residuals)
        findings = tuple(self.findings)
        if not all(isinstance(item, Finding) for item in findings):
            raise ValueError("PartitionResult.findings must contain Finding records")
        object.__setattr__(self, "findings", findings)
        if not isinstance(self.coverage, CoverageCertificate):
            raise ValueError("PartitionResult.coverage must be a CoverageCertificate")

    @property
    def accepted(self) -> tuple[T, ...]:
        return self.filter_result.accepted_items

    @property
    def rejected(self) -> tuple[RejectedItem[T], ...]:
        return self.filter_result.rejected_items

    @property
    def pending(self) -> tuple[PendingItem[T], ...]:
        return self.filter_result.pending_items

    @property
    def has_blocking_residual(self) -> bool:
        return any(residual.blocking for residual in self.residuals)
