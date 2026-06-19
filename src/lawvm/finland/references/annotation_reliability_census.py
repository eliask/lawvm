"""Family-stratified ``<ref>`` reliability census + ``SourceSurfacePolicy`` (L7).

grammar7 §4 / §13-C / §13-D. The annotation-witness machinery already compares
the GRAMMAR parse against the ``<ref>`` annotation surface per family (the SEVEN
NEUTRAL statuses of :mod:`annotation_witness_census`). This module turns that raw
status tally into the two L7 artifacts:

  1. a FAMILY-STRATIFIED RELIABILITY TABLE — for each reference family, the four
     reliability buckets the L7 question asks for:

       agree              — grammar and ``<ref>`` AGREE on the target
                            (both_same_target + both_same_target_diff_span);
       grammar_exceeds    — a grammar mention with NO overlapping ``<ref>``
                            (grammar_only) — the annotation-independent superset,
                            the recall the markup lacks;
       annotation_exceeds — a ``<ref>`` witness the grammar does NOT cover
                            (annotation_only) — what the markup adds;
       disagree           — both present but the targets DIVERGE
                            (both_same_span_diff_target) OR the pair cannot be
                            decided (both_present_noncomparable).

     This is the empirical answer to "how reliable is ``<ref>`` per family" — the
     quantification of grammar7's claim that ``<ref>`` is reliable ONLY for
     explicit paren-ids and weak elsewhere.

  2. a ``SourceSurfacePolicy`` MANIFEST (grammar7 §13-D) — a typed, auditable
     declaration of the role each annotation shape (family) is PERMITTED to play:

       self_resolve  — the annotation may be trusted to resolve a target on its
                       own (high agreement AND grammar corroborates broadly);
       corroborate   — the annotation may CONFIRM a grammar parse but may not
                       stand alone (moderate agreement / present-but-partial);
       qa_only       — the annotation is used only for QA / divergence flagging,
                       NEVER for resolution (weak / unverified families).

     The role is DERIVED from the reliability buckets — high agreement on a family
     with a real witness population earns ``corroborate``/``self_resolve``; a
     family whose ``<ref>`` is absent, or present only as an unverified one-sided
     surface, earns ``qa_only``. The manifest makes the annotation-dependence
     policy EXPLICIT instead of buried in the extractor.

DISCIPLINE (this lane): MEASUREMENT + POLICY only. It reads the existing census
output; it does NOT change the parser, the witness lens, or any resolution
behaviour. Surface-only, off the replay/apply path. The reliability table reports
the ACTUAL rates from the corpus — if a family's ``<ref>`` is more (or less)
reliable than grammar7 assumed, that IS the finding, not an error to suppress.

NEUTRALITY (grammar7 §14): the four buckets stay NEUTRAL. ``grammar_exceeds`` is
NOT an "annotation bug"; ``annotation_exceeds`` is NOT a "parser miss". The role
assignment is a POLICY judgement about how much to TRUST the annotation, derived
from the measured rates — never a per-case adjudication of which side is right.

Run via ``python -m lawvm.finland.references.annotation_reliability_census
--limit N`` (requires the canonical corpus). Drives the grammar side with
``ignore_annotations=True`` (grammar-primary; annotations as witness only).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.finland.references.annotation_independence_census import FAMILIES
from lawvm.finland.references.annotation_witness_census import (
    FamilyComparison,
    WitnessCensus,
    run_annotation_witness_census,
)

# ---------------------------------------------------------------------------
# L7 reliability buckets — the four-way reduction of the seven NEUTRAL statuses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FamilyReliability:
    """The four L7 reliability buckets for ONE reference family.

    Derived purely from a :class:`FamilyComparison` (the 7-status tally). The
    four buckets partition every COMPARED reference (matched pair or one-sided
    leftover) into the L7 reliability view. Rates are taken as fractions of
    ``compared`` (the total of the four buckets) so they sum to 1.0.
    """

    family: str
    #: Grammar and <ref> agree on the target (the reliable core).
    agree: int
    #: grammar_only — a grammar mention no <ref> covers (annotation-independent
    #: superset; the recall the markup lacks).
    grammar_exceeds: int
    #: annotation_only — a <ref> the grammar does not cover (what the markup adds).
    annotation_exceeds: int
    #: Genuine target divergence OR an undecidable pair (the unreliable residue).
    disagree: int

    @property
    def compared(self) -> int:
        return self.agree + self.grammar_exceeds + self.annotation_exceeds + self.disagree

    @property
    def annotation_population(self) -> int:
        """References for which a <ref> witness exists (agree+annot_exceeds+disagree).

        grammar_exceeds is EXCLUDED — those have no witness, so they say nothing
        about <ref> reliability, only about grammar recall the markup lacks.
        """
        return self.agree + self.annotation_exceeds + self.disagree

    @property
    def agree_pct(self) -> float:
        """Agreement as a fraction of the WITNESS population (<ref>-reliability).

        High = whenever a <ref> is present in this family, grammar agrees on its
        target. This is the direct ``<ref>``-reliability signal grammar7 §13-C
        asks for. 0.0 when the family has no witnesses at all.
        """
        denom = self.annotation_population
        return self.agree / denom if denom else 0.0

    @property
    def disagree_pct(self) -> float:
        """Divergence/undecidable as a fraction of the witness population."""
        denom = self.annotation_population
        return self.disagree / denom if denom else 0.0

    @property
    def annotation_exceeds_pct(self) -> float:
        """Annotation-only as a fraction of the witness population.

        High = a large slice of this family's ``<ref>`` witnesses sit on spans the
        grammar never recovers — the annotation is UNCORROBORATED there, so it
        cannot be trusted to self-resolve on the strength of this census.
        """
        denom = self.annotation_population
        return self.annotation_exceeds / denom if denom else 0.0


def reliability_of(fc: FamilyComparison) -> FamilyReliability:
    """Reduce a 7-status :class:`FamilyComparison` to the four L7 buckets.

      agree              = both_same_target + both_same_target_diff_span
      grammar_exceeds    = grammar_only
      annotation_exceeds = annotation_only
      disagree           = both_same_span_diff_target + both_present_noncomparable
    """
    return FamilyReliability(
        family=fc.family,
        agree=fc.both_same_target + fc.both_same_target_diff_span,
        grammar_exceeds=fc.grammar_only,
        annotation_exceeds=fc.annotation_only,
        disagree=fc.both_same_span_diff_target + fc.both_present_noncomparable,
    )


# ---------------------------------------------------------------------------
# SourceSurfacePolicy — the typed annotation-role manifest (grammar7 §13-D)
# ---------------------------------------------------------------------------


class AnnotationRole(str, Enum):
    """The permitted role an annotation shape may play in resolution.

    Ordered most-trusted → least-trusted. ``str`` mix-in so the role serialises
    to its name in reports / JSON without a custom encoder.
    """

    #: The annotation may be trusted to resolve a target on its own.
    SELF_RESOLVE = "self_resolve"
    #: The annotation may CONFIRM a grammar parse but may not stand alone.
    CORROBORATE = "corroborate"
    #: The annotation is used only for QA / divergence flagging, never resolution.
    QA_ONLY = "qa_only"


#: Decision thresholds for role assignment. Tuned to the grammar7 stance:
#: explicit-paren-id is the only family <ref> covers densely AND agrees on, so it
#: clears CORROBORATE; everything weak/absent stays QA_ONLY. self_resolve is
#: reserved — it requires near-total agreement AND a low uncorroborated share,
#: which no real family reaches today (so the manifest does not over-trust).
_MIN_WITNESS_POP = 20          # below this the rate is statistically meaningless
_CORROBORATE_AGREE = 0.45      # >= this agree% on a real population → corroborate
_SELF_RESOLVE_AGREE = 0.95     # >= this agree% AND low annot-exceeds → self_resolve
_SELF_RESOLVE_MAX_ANNOT_EXCEEDS = 0.05


@dataclass(frozen=True, slots=True)
class AnnotationPolicyEntry:
    """One family's policy row in the :class:`SourceSurfacePolicy` manifest."""

    family: str
    role: AnnotationRole
    #: Witness population the role was derived from (0 → no <ref>, forced qa_only).
    witness_population: int
    agree_pct: float
    annotation_exceeds_pct: float
    disagree_pct: float
    #: Human-readable derivation of WHY this role (auditability, §13-D).
    rationale: str


def assign_role(rel: FamilyReliability) -> AnnotationPolicyEntry:
    """Derive the permitted annotation role for one family from its rates.

    Policy (grammar7 §13-D), most → least trusting:

      * NO witnesses (annotation_population == 0): the family's references are
        grammar-carried; ``<ref>`` says nothing → ``qa_only`` (nothing to trust).
      * tiny witness population (< _MIN_WITNESS_POP): the rate is not yet
        statistically meaningful → ``qa_only`` (don't trust on thin evidence).
      * near-total agreement AND a low uncorroborated (annotation_exceeds) share
        → ``self_resolve`` (the annotation tracks the grammar so tightly it may
        stand alone).
      * solid agreement (>= _CORROBORATE_AGREE) → ``corroborate`` (the annotation
        may confirm a grammar parse but not resolve alone).
      * otherwise (weak agreement / divergence-heavy) → ``qa_only``.
    """
    pop = rel.annotation_population
    if pop == 0:
        return AnnotationPolicyEntry(
            family=rel.family,
            role=AnnotationRole.QA_ONLY,
            witness_population=0,
            agree_pct=0.0,
            annotation_exceeds_pct=0.0,
            disagree_pct=0.0,
            rationale="no <ref> witnesses — family is grammar-carried, annotation says nothing",
        )
    agree = rel.agree_pct
    annot_exceeds = rel.annotation_exceeds_pct
    disagree = rel.disagree_pct
    if pop < _MIN_WITNESS_POP:
        role = AnnotationRole.QA_ONLY
        rationale = (
            f"witness population {pop} < {_MIN_WITNESS_POP}: rate not yet "
            "statistically meaningful — QA only"
        )
    elif agree >= _SELF_RESOLVE_AGREE and annot_exceeds <= _SELF_RESOLVE_MAX_ANNOT_EXCEEDS:
        role = AnnotationRole.SELF_RESOLVE
        rationale = (
            f"agree {agree:.0%} >= {_SELF_RESOLVE_AGREE:.0%} and uncorroborated "
            f"{annot_exceeds:.0%} <= {_SELF_RESOLVE_MAX_ANNOT_EXCEEDS:.0%} — "
            "annotation tracks grammar tightly enough to stand alone"
        )
    elif agree >= _CORROBORATE_AGREE:
        role = AnnotationRole.CORROBORATE
        rationale = (
            f"agree {agree:.0%} >= {_CORROBORATE_AGREE:.0%} but uncorroborated "
            f"share {annot_exceeds:.0%} too high to stand alone — may confirm a "
            "grammar parse only"
        )
    else:
        role = AnnotationRole.QA_ONLY
        rationale = (
            f"weak agreement {agree:.0%} (< {_CORROBORATE_AGREE:.0%}) / "
            f"divergence {disagree:.0%} — annotation used only for QA flagging"
        )
    return AnnotationPolicyEntry(
        family=rel.family,
        role=role,
        witness_population=pop,
        agree_pct=agree,
        annotation_exceeds_pct=annot_exceeds,
        disagree_pct=disagree,
        rationale=rationale,
    )


@dataclass(frozen=True, slots=True)
class SourceSurfacePolicy:
    """The typed per-family annotation-role manifest (grammar7 §13-D).

    Built from a reliability census. ``entries`` is keyed by family; ``role_for``
    is the safe accessor — an UNKNOWN family defaults to ``qa_only`` (fail-safe:
    never trust an unmeasured annotation shape to resolve).
    """

    entries: dict[str, AnnotationPolicyEntry]

    def role_for(self, family: str) -> AnnotationRole:
        entry = self.entries.get(family)
        return entry.role if entry is not None else AnnotationRole.QA_ONLY

    def may_self_resolve(self, family: str) -> bool:
        return self.role_for(family) is AnnotationRole.SELF_RESOLVE

    def may_corroborate(self, family: str) -> bool:
        # corroborate OR the stronger self_resolve both permit corroboration.
        return self.role_for(family) in (
            AnnotationRole.CORROBORATE,
            AnnotationRole.SELF_RESOLVE,
        )


def build_source_surface_policy(
    reliabilities: dict[str, FamilyReliability],
) -> SourceSurfacePolicy:
    """Assemble the manifest from per-family reliabilities (role per family)."""
    return SourceSurfacePolicy(
        entries={fam: assign_role(rel) for fam, rel in reliabilities.items()}
    )


# ---------------------------------------------------------------------------
# Census → reliability table → policy
# ---------------------------------------------------------------------------


def reliabilities_from_census(census: WitnessCensus) -> dict[str, FamilyReliability]:
    """Reduce every family in a witness census to its L7 reliability buckets."""
    return {fam: reliability_of(fc) for fam, fc in census.families.items()}


@dataclass
class ReliabilityCensusResult:
    """The L7 artifacts: the reliability table + the derived policy manifest."""

    statutes_scanned: int
    reliabilities: dict[str, FamilyReliability]
    policy: SourceSurfacePolicy


def run_reliability_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    cheap_signal: bool = False,
) -> ReliabilityCensusResult:
    """Run the witness census (grammar-primary) and derive the L7 artifacts.

    ``cheap_signal`` defaults OFF here — the cheap-signal proxy is a recall probe,
    not part of the grammar-vs-annotation reliability comparison, so it is skipped
    by default to keep the corpus run light (another lane runs heavy builds).
    """
    census = run_annotation_witness_census(
        limit=limit, min_year=min_year, cheap_signal=cheap_signal
    )
    reliabilities = reliabilities_from_census(census)
    policy = build_source_surface_policy(reliabilities)
    return ReliabilityCensusResult(
        statutes_scanned=census.statutes_scanned,
        reliabilities=reliabilities,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

_ORDER = FAMILIES


def format_reliability_report(result: ReliabilityCensusResult) -> str:
    """Render the L7 family-stratified reliability table + the policy manifest."""
    rel = result.reliabilities
    lines: list[str] = []
    lines.append("=" * 110)
    lines.append(
        "FAMILY-STRATIFIED <ref> RELIABILITY CENSUS (L7, grammar7 §13-C)  "
        f"(statutes scanned: {result.statutes_scanned})"
    )
    lines.append("=" * 110)
    header = (
        f"  {'family':<13} {'agree':>7} {'gram_exc':>9} {'annot_exc':>10} "
        f"{'disagree':>9} {'wit_pop':>8} {'agree%':>8} {'role':>13}"
    )
    lines.append(header)
    lines.append("-" * 110)
    order = [f for f in _ORDER if f in rel] + [f for f in rel if f not in _ORDER]
    for fam in order:
        r = rel[fam]
        role = result.policy.role_for(fam).value
        lines.append(
            f"  {fam:<13} {r.agree:>7} {r.grammar_exceeds:>9} "
            f"{r.annotation_exceeds:>10} {r.disagree:>9} "
            f"{r.annotation_population:>8} {100 * r.agree_pct:>7.1f}% {role:>13}"
        )
    lines.append("-" * 110)
    lines.append("")
    lines.append("  RELIABILITY BUCKETS (NEUTRAL, grammar7 §14):")
    lines.append(
        "    agree      = grammar and <ref> agree on target "
        "(both_same_target + both_same_target_diff_span)"
    )
    lines.append("    gram_exc   = grammar_only — annotation-independent superset (markup lacks it)")
    lines.append("    annot_exc  = annotation_only — what the markup adds (grammar misses)")
    lines.append("    disagree   = target divergence OR undecidable pair")
    lines.append(
        "    agree%     = agree / witness-population (agree + annot_exc + disagree); "
        "the direct <ref>-reliability signal"
    )
    lines.append("")
    lines.append("  SOURCE SURFACE POLICY (grammar7 §13-D) — derived annotation role per family:")
    for fam in order:
        entry = result.policy.entries[fam]
        lines.append(f"    {fam:<13} {entry.role.value:<13} — {entry.rationale}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Family-stratified <ref> reliability census + SourceSurfacePolicy (L7)."
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--min-year", type=int, default=0)
    parser.add_argument("--cheap-signal", action="store_true")
    args = parser.parse_args(argv)

    result = run_reliability_census(
        limit=args.limit, min_year=args.min_year, cheap_signal=args.cheap_signal
    )
    print(format_reliability_report(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
