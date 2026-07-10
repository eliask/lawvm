"""Statute-level fixed-term validity bounds and their proof carrier.

Finnish fixed-term laws (määräaikainen laki) state a whole-law validity period
in the entry-into-force provision (voimaantulosäännös), e.g. 482/2024 §7:
"Tämä laki ... on voimassa 31 päivään joulukuuta 2026". This is a *law-level*
validity condition, not a per-provision sunset, so it is modelled here as a
statute-level fact rather than by mutating every ``ProvisionVersion``.

Ontology (see Pro sign-off, design D′):
  - One bound fact per version of the entry-into-force provision (extension acts
    text-replace that provision, so each version carries the then-current bound).
  - At query time the governing bound is the latest eligible one, and the seam
    projects an ``expired`` status past the bound.

Inclusive vs exclusive: source prose gives an INCLUSIVE ``valid_until`` (the law
is in force ON that date); the kernel's existing ``expires`` cutoff is EXCLUSIVE
(a version drops out once ``as_of >= expires``). Both are stored:
``expires_on == valid_until + 1 day``. A statute is expired at D iff
``D >= expires_on`` (equivalently ``D > valid_until``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from lawvm.core.ir import LegalAddress

# v1 supports whole-statute bounds only. Scoped (chapter/section) bounds are
# detected and diagnosed but not lifted into a bound.
ValidityScope = Literal["whole_statute"]

# "stated_expiry": the source states the validity end day itself.
# "upper_cap": the source states an open-ended validity ("toistaiseksi") with
# a hard outer cap ("ei kuitenkaan kau(v)emmin kuin ..."); the bound is the
# cap. Expiry projection is identical past the cap — there is no weaker
# "possibly expired" status — but the law may have been terminated earlier by
# a separate instrument, which ``earlier_termination_possible`` records.
# "duration_from_commencement": the source states a year/month duration from
# the law's commencement; the end day is COMPUTED under a named, pinned
# arithmetic authority (150/1930 §3), never ad hoc — the bound must carry
# ``arithmetic_authority``, ``commencement_date`` and ``duration_spec``.
BoundKind = Literal["stated_expiry", "upper_cap", "duration_from_commencement"]

# How the bound's validity end was established. "grammar_fact": parsed
# directly from an explicit date expression. "computed_under_pinned_authority":
# arithmetic under a named statutory rule (the application of that rule to
# whole-law validity carries a recorded scope caveat).
# "high_confidence_inference": a narrow doctrinal inference (e.g. elided-year
# "vuoden loppuun" resolved from the same-sentence commencement year) — never
# to be presented as a grammar fact.
EpistemicStatus = Literal[
    "grammar_fact", "computed_under_pinned_authority", "high_confidence_inference"
]

FIXED_TERM_WHOLE_STATUTE_RULE_ID = "fixed_term_whole_statute_expiry"


def expires_on_from_valid_until(valid_until: dt.date) -> dt.date:
    """Convert a source-prose inclusive last-valid day to the kernel's exclusive expires cutoff.

    The kernel-wide convention is that ``expires`` is an EXCLUSIVE cutoff: a
    version is in force on ``[effective, expires)`` and selection treats it as
    inactive ON its ``expires`` date (``eligible()`` uses
    ``expires > horizon``). Finnish prose states the INCLUSIVE last in-force
    day ("on voimassa 30 päivään kesäkuuta 2023" = in force THROUGH June 30),
    so every prose-derived expiry must pass through this helper before being
    stamped into a kernel ``expires`` field: ``expires = valid_until + 1 day``.
    This is the single conversion waist for that off-by-one.
    """
    return valid_until + dt.timedelta(days=1)


@dataclass(frozen=True, slots=True)
class FixedTermValidityProof:
    """Proof object for one governing fixed-term validity decision.

    Part of the certificate/proof surface so the bound is never an unreachable
    guard: the seam carries this whenever it projects ``expired``.
    """

    source_text: str
    source_span: Optional[Tuple[int, int]]
    source_hash: str
    rule_id: str
    valid_until: str
    expires_on: str
    governing_bound_id: str
    bound_kind: BoundKind = "stated_expiry"
    earlier_termination_possible: bool = False
    epistemic_status: EpistemicStatus = "grammar_fact"
    arithmetic_authority: Optional[str] = None
    authority_scope_caveat: Optional[str] = None
    commencement_date: Optional[str] = None
    commencement_source_kind: Optional[str] = None
    duration_spec: Optional[str] = None


@dataclass(frozen=True, slots=True)
class StatuteValidityBound:
    """One stored statute-level validity bound fact.

    A bound is extracted from a single version of the entry-into-force
    provision. ``effective`` is that provision version's effective date, which
    drives extension semantics: the governing bound at a query date is the
    latest one whose ``effective`` is at or before the query date.
    """

    statute_id: str
    scope: ValidityScope
    effective: str
    enacted: Optional[str]
    valid_until: str
    expires_on: str
    source_provision: LegalAddress
    source_version_id: str
    source_hash: str
    source_span: Optional[Tuple[int, int]]
    rule_id: str
    source_text: str
    source_sequence: int = 0
    bound_kind: BoundKind = "stated_expiry"
    # Source phrase family behind an upper_cap classification (e.g.
    # "toistaiseksi_ei_kauemmin_kuin"); None for plain stated expiry.
    source_phrase_kind: Optional[str] = None
    earlier_termination_possible: bool = False
    # Anaphoric date resolution provenance ("sanotun vuoden loppuun"): the
    # same-sentence antecedent expression that supplied the year, and its span
    # in the normalised source text. None for non-anaphoric grammar families.
    antecedent_text: Optional[str] = None
    antecedent_span: Optional[Tuple[int, int]] = None
    # Epistemic provenance: how the validity end was established (grammar
    # fact / computed under a pinned authority / narrow inference). Computed
    # and inferred bounds must never masquerade as grammar facts.
    epistemic_status: EpistemicStatus = "grammar_fact"
    # Arithmetic provenance for duration_from_commencement bounds: the named
    # authority (e.g. "fi/150/1930"), its recorded scope caveat, the concrete
    # commencement the period runs from, where that commencement was read
    # ("same_sentence" / "same_statute_commencement_clause"), and the period
    # ("P2Y", "P12M").
    arithmetic_authority: Optional[str] = None
    authority_scope_caveat: Optional[str] = None
    commencement_date: Optional[str] = None
    commencement_source_kind: Optional[str] = None
    duration_spec: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.statute_id:
            raise ValueError("StatuteValidityBound.statute_id must be non-empty")
        if self.scope != "whole_statute":
            raise ValueError(
                f"StatuteValidityBound.scope must be 'whole_statute'; got {self.scope!r}"
            )
        if self.bound_kind not in (
            "stated_expiry",
            "upper_cap",
            "duration_from_commencement",
        ):
            raise ValueError(
                f"StatuteValidityBound.bound_kind must be 'stated_expiry', "
                f"'upper_cap' or 'duration_from_commencement'; got {self.bound_kind!r}"
            )
        if self.epistemic_status not in (
            "grammar_fact",
            "computed_under_pinned_authority",
            "high_confidence_inference",
        ):
            raise ValueError(
                f"StatuteValidityBound.epistemic_status must be 'grammar_fact', "
                f"'computed_under_pinned_authority' or 'high_confidence_inference'; "
                f"got {self.epistemic_status!r}"
            )
        if self.bound_kind == "upper_cap" and not self.earlier_termination_possible:
            raise ValueError(
                "StatuteValidityBound with bound_kind='upper_cap' must set "
                "earlier_termination_possible=True (an upper cap bounds an "
                "otherwise open-ended validity)"
            )
        if self.bound_kind == "duration_from_commencement":
            # A computed bound must carry its full arithmetic provenance and
            # must not present itself as a grammar fact.
            missing = [
                name
                for name, value in (
                    ("arithmetic_authority", self.arithmetic_authority),
                    ("authority_scope_caveat", self.authority_scope_caveat),
                    ("commencement_date", self.commencement_date),
                    ("commencement_source_kind", self.commencement_source_kind),
                    ("duration_spec", self.duration_spec),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "StatuteValidityBound with bound_kind='duration_from_commencement' "
                    f"must carry arithmetic provenance; missing: {missing}"
                )
            if self.epistemic_status == "grammar_fact":
                raise ValueError(
                    "a duration-computed bound must not claim epistemic_status="
                    "'grammar_fact'; use 'computed_under_pinned_authority'"
                )
        if not self.effective:
            raise ValueError("StatuteValidityBound.effective must be a non-empty date string")
        if not self.valid_until:
            raise ValueError("StatuteValidityBound.valid_until must be a non-empty date string")
        if not self.expires_on:
            raise ValueError("StatuteValidityBound.expires_on must be a non-empty date string")
        if self.expires_on <= self.valid_until:
            raise ValueError(
                "StatuteValidityBound.expires_on must be strictly after valid_until "
                f"(valid_until={self.valid_until!r}, expires_on={self.expires_on!r})"
            )
        if not isinstance(self.source_provision, LegalAddress):
            raise TypeError("StatuteValidityBound.source_provision must be a LegalAddress")

    @property
    def bound_id(self) -> str:
        """Stable identifier for this bound: source act + effective date."""
        return f"{self.source_version_id}@{self.effective}"

    def proof(self) -> FixedTermValidityProof:
        return FixedTermValidityProof(
            source_text=self.source_text,
            source_span=self.source_span,
            source_hash=self.source_hash,
            rule_id=self.rule_id,
            valid_until=self.valid_until,
            expires_on=self.expires_on,
            governing_bound_id=self.bound_id,
            bound_kind=self.bound_kind,
            earlier_termination_possible=self.earlier_termination_possible,
            epistemic_status=self.epistemic_status,
            arithmetic_authority=self.arithmetic_authority,
            authority_scope_caveat=self.authority_scope_caveat,
            commencement_date=self.commencement_date,
            commencement_source_kind=self.commencement_source_kind,
            duration_spec=self.duration_spec,
        )


def _eligible(bound: StatuteValidityBound, as_of: str, query_type: str) -> bool:
    """A bound is usable at ``as_of`` if it has taken effect (and, for in_force
    queries, has been enacted)."""
    if bound.effective > as_of:
        return False
    if query_type == "in_force" and bound.enacted and bound.enacted > as_of:
        return False
    return True


def governing_bound(
    bounds: Tuple[StatuteValidityBound, ...] | list[StatuteValidityBound],
    *,
    as_of: str,
    query_type: str = "governing",
) -> Optional[StatuteValidityBound]:
    """Return the bound that governs at ``as_of``: the latest eligible one.

    Latest is ranked by ``(effective, source_sequence)``; ties on both are
    surfaced separately as ambiguity (see ``ambiguous_bounds``) rather than
    silently picked here.
    """
    eligible = [b for b in bounds if _eligible(b, as_of, query_type)]
    if not eligible:
        return None
    return max(eligible, key=lambda b: (b.effective, b.source_sequence))


def ambiguous_bounds(
    bounds: Tuple[StatuteValidityBound, ...] | list[StatuteValidityBound],
) -> bool:
    """True when two distinct whole-law bounds share an effective date but state
    different validity ends — an unresolved conflict the seam must block on."""
    by_effective: dict[str, set[str]] = {}
    for bound in bounds:
        by_effective.setdefault(bound.effective, set()).add(bound.valid_until)
    return any(len(valids) > 1 for valids in by_effective.values())


def is_expired_at(bound: StatuteValidityBound, as_of: str) -> bool:
    """A statute is expired at ``as_of`` iff ``as_of >= expires_on`` (the
    exclusive cutoff), i.e. ``as_of`` is strictly past the inclusive bound."""
    return as_of >= bound.expires_on


def late_extension_gap(
    bounds: Tuple[StatuteValidityBound, ...] | list[StatuteValidityBound],
    governing: StatuteValidityBound,
) -> bool:
    """True when ``governing`` took effect only after an earlier bound's term had
    already lapsed, leaving a deterministic gap-then-revival period.

    Finnish practice is to extend before expiry; this flags the degenerate case
    for review without changing the deterministic selection result.
    """
    earlier = [b for b in bounds if b.effective < governing.effective]
    if not earlier:
        return False
    latest_earlier = max(earlier, key=lambda b: (b.effective, b.source_sequence))
    return governing.effective > latest_earlier.expires_on
