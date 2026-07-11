"""Phase-3 vision-adjudicator FALSE-GRADUATION validation harness.

This module builds the durable *no-false-exact* safety artifact for the phase-3 appendix
vision adjudicator: it bounds the rate at which the gate GRADUATES a wrong cell to
``exact``. The exactness invariant (``notes`` phase-3 headline: "exactness, not slop")
forbids a FALSE GRADUATION — marking two genuinely-different witnesses EXACT. Every other
outcome (a typed divergence, an abstain/escalate to the vision tail) is SAFE; only a
graduation of a content-altered pair is a safety failure. This harness manufactures free
ground truth to measure exactly that failure and to FREEZE it as a CI ratchet.

The adjudicator under test
--------------------------
The graduation decision lives in two importable, read-only surfaces:

  * :func:`lawvm.finland.op_equivalence.text_equivalence` — the exactness quotient (the
    primitive: two texts are EXACT iff equal modulo a closed set of legally-inert folds);
  * :func:`lawvm.tools.fi_appendix_structure.verify_tables_vision` — the vision third-
    witness tie-break that GRADUATES a routed cell iff an independent vision read reproduces
    the pdfium witness under that quotient, *and* the sparse/scanned guard (born-digital +
    non-empty witness) holds.

Both take an INJECTABLE witness/reader, so this harness drives the real production
graduation path hermetically — no model, no PDF, no rendering in CI. A live-backend reader
(``fi_appendix_structure.make_vision_region_reader``) can be threaded in for the full eval;
the CI canary never touches it.

Free ground truth (the mutation gold-set)
-----------------------------------------
Start from pairs the gate ALREADY self-verified EXACT (``text_equivalence(A, B).equal`` —
Docling text ≡ pdfium witness, high-confidence correct). Then synthetically CORRUPT one
witness by ONE mutation class. Each mutation class carries a DESIGNER-ASSIGNED ground-truth
label — assigned from typographic semantics, NOT from the adjudicator, so the test is not
circular:

  * MUST_KILL — the mutation changes a VISIBLE, legally-load-bearing glyph (a digit, a
    diacritic, a superscript, a decimal separator, a thousands separator, an edge letter).
    The mutated pair is genuinely DIFFERENT; the gate MUST NOT graduate it. A graduation is
    a counted FALSE GRADUATION (the safety numerator). These are the seven classes the
    design consult enumerated.
  * MUST_ABSORB — the mutation swaps/inserts an INVISIBLE, inert glyph (a Zs-space variant,
    a zero-width Cf char) that the quotient is DESIGNED to fold. The gate SHOULD graduate it;
    a divergence there is over-sensitivity (a precision/cost signal), never a safety failure.
    This is the complementary axis that keeps the quotient honest in BOTH directions.

The metric
----------
Reported per (mutation-class × stratum × render-DPI):

  * FALSE-GRADUATION COUNT — MUST_KILL mutants the gate marked EXACT. Escalate/abstain/
    diverge NEVER enter this numerator (an escalation of a mutant is a CORRECT, safe kill).
  * a CLOPPER–PEARSON one-sided UPPER BOUND on the true false-graduation probability
    (exact, not normal-approx — the regime is near-zero events);
  * the mutant KILL-RATE (discriminator sensitivity) and the ESCALATION-RATE (also
    ratcheted — a collapsing escalation rate is a red flag for silent quotient loosening,
    an exploding one is a visible cost signal);
  * for MUST_ABSORB: the ABSORPTION-RATE (the gate still folds true noise).

Honest sample size: a strict 1e-3 upper bound at 95% needs N≈3000 mutants PER (kill) class
(CP: ``1 - 0.05**(1/N) ≤ 1e-3``); N≈1000 buys only ~3e-3 (rule-of-three). See
:func:`sample_size_for_upper_bound`. A 50-mutant probe proves nothing about the 1e-3 regime.

The canary ratchet
-------------------
A small, curated mutant set frozen as a content-addressed fixture (keyed by a sha256
fingerprint over the case texts + labels). The CI test re-runs the REAL gate over it and
asserts (a) zero false graduations / kill-rate at ceiling, (b) MUST_ABSORB still graduates,
(c) the escalation rate stays inside a frozen band. This is the defense that catches
model-version drift, prompt rot, or a silently-loosened quotient between baselines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from lawvm.finland.op_equivalence import text_equivalence
from lawvm.tools.fi_appendix_structure import (
    StructuredCell,
    StructuredTable,
    TableCellDivergence,
    TableVerification,
    verify_tables_vision,
)

#: A page-region reader: ``(page_num, bbox) -> text``. The vision witness seam. Injected so
#: the harness is hermetic; the live wiring is ``fi_appendix_structure.make_vision_region_reader``.
RegionReader = Callable[[int, Tuple[float, float, float, float]], str]

# Fixed hermetic scaffolding for driving the real ``verify_tables_vision`` graduation path
# with one synthetic routed cell. The Docling text is a SENTINEL no candidate can equal, so a
# non-graduation always lands in ``open_divergences`` (never a spurious witness_disagreement).
_SENTINEL_DOCLING = "\x00__lawvm_vision_eval_docling_sentinel__\x00"
_CANARY_BBOX: Tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)

#: Committed frozen canary fixture (content-addressed by ``fingerprint``).
CANARY_FIXTURE_PATH = "tests/data/fi_appendix_vision_canary.json"


# --------------------------------------------------------------------------- #
# Typed vocabulary (FW-09: public symbols typed; VOCAB-02: no bare status).     #
# --------------------------------------------------------------------------- #


class AdjudicationOutcome(StrEnum):
    """The gate's verdict on one (witness, candidate) pair, mapped to the safety trichotomy.

    ``GRADUATED_EXACT`` is the ONLY outcome eligible to be a false graduation; ``DIVERGED``
    (quotient residual) and ``ESCALATED`` (sparse/abstain guard) are both SAFE non-exact
    outcomes. Named ``*_outcome`` field, never a bare ``status`` (VOCAB-02).
    """

    GRADUATED_EXACT = "graduated_exact"
    DIVERGED = "diverged"
    ESCALATED = "escalated"


class GroundTruth(StrEnum):
    """Designer-assigned truth for a mutation class — the anti-circularity anchor.

    Assigned from typographic semantics, NOT from the adjudicator: a MUST_KILL mutation
    alters a visible load-bearing glyph (genuinely different → gate must not graduate); a
    MUST_ABSORB mutation swaps an inert invisible glyph (equivalent → gate should graduate).
    """

    MUST_KILL = "must_kill"
    MUST_ABSORB = "must_absorb"


class MutationClass(StrEnum):
    """The corruption classes. The first seven are MUST_KILL (the design-consult set); the
    last two are MUST_ABSORB inert probes that keep the quotient honest the other way."""

    DIGIT_SUBSTITUTION = "digit_substitution"
    DIGIT_TRANSPOSITION = "digit_transposition"
    DIACRITIC_DROP = "diacritic_drop"
    SUPERSCRIPT_DELETE = "superscript_delete"
    DECIMAL_COMMA_FLIP = "decimal_comma_flip"
    EDGE_CHAR_DELETION = "edge_char_deletion"
    THIN_SPACE_REMOVAL = "thin_space_removal"
    ZS_SPACE_SWAP = "zs_space_swap"  # MUST_ABSORB: normal space -> thin space (inert Zs fold)
    CF_ZERO_WIDTH_INSERT = "cf_zero_width_insert"  # MUST_ABSORB: insert U+200B (inert Cf delete)


class Stratum(StrEnum):
    """The PDF stratum a base pair came from. Determines ``born_digital`` for the guard."""

    BORN_DIGITAL_CLEAN = "born_digital_clean"
    CORRUPT_FONT = "corrupt_font"
    SCANNED = "scanned"


class HumanLabel(StrEnum):
    """Ground truth for a HUMAN-adjudicated real-routed cell (the drop-in slice)."""

    GENUINE_DIFFERENCE = "genuine_difference"  # must NOT graduate
    INERT_EQUAL = "inert_equal"  # may graduate


_GROUND_TRUTH: Dict[MutationClass, GroundTruth] = {
    MutationClass.DIGIT_SUBSTITUTION: GroundTruth.MUST_KILL,
    MutationClass.DIGIT_TRANSPOSITION: GroundTruth.MUST_KILL,
    MutationClass.DIACRITIC_DROP: GroundTruth.MUST_KILL,
    MutationClass.SUPERSCRIPT_DELETE: GroundTruth.MUST_KILL,
    MutationClass.DECIMAL_COMMA_FLIP: GroundTruth.MUST_KILL,
    MutationClass.EDGE_CHAR_DELETION: GroundTruth.MUST_KILL,
    MutationClass.THIN_SPACE_REMOVAL: GroundTruth.MUST_KILL,
    MutationClass.ZS_SPACE_SWAP: GroundTruth.MUST_ABSORB,
    MutationClass.CF_ZERO_WIDTH_INSERT: GroundTruth.MUST_ABSORB,
}


def ground_truth_of(mutation_class: MutationClass) -> GroundTruth:
    """The designer-assigned ground truth for a mutation class (total over the enum)."""
    return _GROUND_TRUTH[mutation_class]


def born_digital_of(stratum: Stratum) -> bool:
    """The scanned stratum is NOT born-digital → the sparse guard forces ESCALATED there."""
    return stratum is not Stratum.SCANNED


# --------------------------------------------------------------------------- #
# The mutation operators (pure string ops; deterministic; no regex).            #
# --------------------------------------------------------------------------- #
#
# Each returns ``(mutated_text, applied)``. ``applied`` is False when the class has no site
# in the input (e.g. no digit to substitute) — a non-applied mutant is EXCLUDED from the
# denominator, never miscounted as a spuriously-easy kill. Every operator is deterministic
# (first applicable site), so the gold-set and the frozen fingerprint are reproducible.

_SUPERSCRIPTS: Dict[str, str] = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}
_THIN_SPACES = (" ", " ")  # thin space, narrow no-break space (both Zs → fold to " ")
_ZERO_WIDTH = "​"  # zero-width space (Cf → deleted by the quotient)


def _mutate_digit_substitution(text: str) -> Tuple[str, bool]:
    for i, ch in enumerate(text):
        if ch.isascii() and ch.isdigit():
            new = str((int(ch) + 1) % 10)  # always a DIFFERENT digit
            return text[:i] + new + text[i + 1 :], True
    return text, False


def _mutate_digit_transposition(text: str) -> Tuple[str, bool]:
    for i in range(len(text) - 1):
        a, b = text[i], text[i + 1]
        if a.isascii() and a.isdigit() and b.isascii() and b.isdigit() and a != b:
            return text[:i] + b + a + text[i + 2 :], True
    return text, False


def _strip_diacritic(ch: str) -> str:
    decomposed = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    return base or ch


def _mutate_diacritic_drop(text: str) -> Tuple[str, bool]:
    for i, ch in enumerate(text):
        base = _strip_diacritic(ch)
        if base != ch and base.isalpha():
            return text[:i] + base + text[i + 1 :], True
    return text, False


def _mutate_superscript_delete(text: str) -> Tuple[str, bool]:
    for i, ch in enumerate(text):
        if ch in _SUPERSCRIPTS:
            return text[:i] + _SUPERSCRIPTS[ch] + text[i + 1 :], True
    return text, False


def _mutate_decimal_comma_flip(text: str) -> Tuple[str, bool]:
    for i in range(1, len(text) - 1):
        ch = text[i]
        if ch in (",", ".") and text[i - 1].isdigit() and text[i + 1].isdigit():
            flip = "." if ch == "," else ","
            return text[:i] + flip + text[i + 1 :], True
    return text, False


def _mutate_edge_char_deletion(text: str) -> Tuple[str, bool]:
    # Delete the LAST alphanumeric (content) char — never a trailing space/punct the quotient
    # might legitimately fold, so the deletion is guaranteed content-bearing.
    if len(text) <= 1:
        return text, False
    for i in range(len(text) - 1, -1, -1):
        if text[i].isalnum():
            return text[:i] + text[i + 1 :], True
    return text, False


def _mutate_thin_space_removal(text: str) -> Tuple[str, bool]:
    for sp in _THIN_SPACES:
        idx = text.find(sp)
        if idx != -1:
            return text[:idx] + text[idx + 1 :], True
    return text, False


def _mutate_zs_space_swap(text: str) -> Tuple[str, bool]:
    idx = text.find(" ")
    if idx != -1:
        return text[:idx] + _THIN_SPACES[0] + text[idx + 1 :], True
    return text, False


def _mutate_cf_zero_width_insert(text: str) -> Tuple[str, bool]:
    if not text:
        return text, False
    return text[:1] + _ZERO_WIDTH + text[1:], True


_MUTATORS: Dict[MutationClass, Callable[[str], Tuple[str, bool]]] = {
    MutationClass.DIGIT_SUBSTITUTION: _mutate_digit_substitution,
    MutationClass.DIGIT_TRANSPOSITION: _mutate_digit_transposition,
    MutationClass.DIACRITIC_DROP: _mutate_diacritic_drop,
    MutationClass.SUPERSCRIPT_DELETE: _mutate_superscript_delete,
    MutationClass.DECIMAL_COMMA_FLIP: _mutate_decimal_comma_flip,
    MutationClass.EDGE_CHAR_DELETION: _mutate_edge_char_deletion,
    MutationClass.THIN_SPACE_REMOVAL: _mutate_thin_space_removal,
    MutationClass.ZS_SPACE_SWAP: _mutate_zs_space_swap,
    MutationClass.CF_ZERO_WIDTH_INSERT: _mutate_cf_zero_width_insert,
}


def apply_mutation(text: str, mutation_class: MutationClass) -> Tuple[str, bool]:
    """Apply one mutation class to ``text``; ``(mutated, applied)``. See operators above."""
    return _MUTATORS[mutation_class](text)


# --------------------------------------------------------------------------- #
# The adjudicator surface (drives the REAL production graduation path).         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AdjudicationVerdict:
    """One gate verdict: the safety-trichotomy ``outcome`` + a free-text ``descriptor``.

    ``descriptor`` records the fold trail (on a graduation) or the surviving residual / guard
    reason (on a non-graduation) — the addendum's free-text adjudicator descriptor.
    """

    outcome: AdjudicationOutcome
    descriptor: str


def _hermetic_reader(text: str) -> RegionReader:
    """A constant fake vision reader returning ``text`` for any region (the CI witness seam)."""

    def _read(_page_num: int, _bbox: Tuple[float, float, float, float]) -> str:
        return text

    return _read


def adjudicate(
    witness_text: str,
    candidate_text: str,
    *,
    born_digital: bool,
    region_reader: Optional[RegionReader] = None,
) -> AdjudicationVerdict:
    """Drive the REAL phase-3 vision graduation path for one (witness, candidate) pair.

    Builds a single-cell routed table whose pdfium witness is ``witness_text`` and whose
    Docling text is a sentinel, injects ``candidate_text`` as the vision read (or uses
    ``region_reader`` for the live backend), and reads the production verdict from
    :func:`verify_tables_vision`:

      * graduated       → GRADUATED_EXACT (the ONLY false-graduation-eligible outcome);
      * not graduated   → DIVERGED (the quotient itself separated them) or ESCALATED (the
        sparse/scanned guard — ``born_digital`` False or empty witness — abstained). Both SAFE.

    The DIVERGED/ESCALATED split is re-derived from the quotient + guard so the kill-MODE and
    the escalation signal are visible; it does not affect the (safety) false-graduation count.
    """
    reader = region_reader if region_reader is not None else _hermetic_reader(candidate_text)
    cell = StructuredCell(0, 0, _SENTINEL_DOCLING, is_header=False, bbox=_CANARY_BBOX)
    table = StructuredTable(
        locator="vision-eval://mutant",
        page_num=1,
        table_index=0,
        n_rows=1,
        n_cols=1,
        caption="",
        cells=(cell,),
    )
    det = TableVerification(
        locator=table.locator,
        page_num=1,
        table_index=0,
        n_cells=1,
        n_exact=0,
        n_no_witness=0,
        divergences=(TableCellDivergence(0, 0, _SENTINEL_DOCLING, witness_text),),
    )
    (verification,) = verify_tables_vision([table], [det], reader, born_digital=born_digital)
    eq = text_equivalence(witness_text, candidate_text)
    if verification.n_graduated == 1:
        folds = ",".join(f.value for f in eq.folds) or "none"
        return AdjudicationVerdict(AdjudicationOutcome.GRADUATED_EXACT, descriptor=f"folds:{folds}")
    if born_digital and witness_text.strip() and not eq.equal:
        return AdjudicationVerdict(
            AdjudicationOutcome.DIVERGED,
            descriptor=f"residual:{eq.left_canon!r}!={eq.right_canon!r}",
        )
    return AdjudicationVerdict(
        AdjudicationOutcome.ESCALATED, descriptor="sparse_or_empty_witness_guard"
    )


# --------------------------------------------------------------------------- #
# Gold-set + mutant model.                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GoldPair:
    """A high-confidence-correct base pair: witness ≡ candidate under the quotient.

    ``make_gold_pair`` asserts self-verification at construction time so the gold-set is
    genuinely drawn from cells the gate ALREADY graduated — free ground truth.
    """

    pair_id: str
    witness_text: str
    candidate_text: str
    stratum: Stratum
    dpi: int


def make_gold_pair(
    pair_id: str, witness_text: str, candidate_text: str, stratum: Stratum, dpi: int
) -> GoldPair:
    """Construct a gold pair, asserting it self-verifies (witness ≡ candidate under quotient)."""
    if not text_equivalence(witness_text, candidate_text).equal:
        raise ValueError(
            f"gold pair {pair_id!r} does not self-verify: "
            f"{witness_text!r} !≡ {candidate_text!r} under the inert quotient"
        )
    return GoldPair(pair_id, witness_text, candidate_text, stratum, dpi)


@dataclass(frozen=True, slots=True)
class Mutant:
    """One mutation applied to a gold pair's candidate witness (the corrupted second witness)."""

    pair_id: str
    mutation_class: MutationClass
    ground_truth: GroundTruth
    witness_text: str
    mutated_text: str
    stratum: Stratum
    dpi: int


def mutants_from_pair(pair: GoldPair, classes: Sequence[MutationClass]) -> Tuple[Mutant, ...]:
    """Generate the applicable mutants of one gold pair (non-applied classes are skipped)."""
    out: List[Mutant] = []
    for mc in classes:
        mutated, applied = apply_mutation(pair.candidate_text, mc)
        if not applied or mutated == pair.candidate_text:
            continue
        out.append(
            Mutant(
                pair_id=pair.pair_id,
                mutation_class=mc,
                ground_truth=ground_truth_of(mc),
                witness_text=pair.witness_text,
                mutated_text=mutated,
                stratum=pair.stratum,
                dpi=pair.dpi,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# The metric: false-graduation count, Clopper–Pearson bound, kill/escalation.   #
# --------------------------------------------------------------------------- #


def _betacf(a: float, b: float, x: float) -> float:
    """Lentz continued fraction for the regularized incomplete beta (Numerical Recipes)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """``I_x(a, b)`` — the regularized incomplete beta function (pure stdlib, monotone in x)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1.0 - x) * b - ln_beta) / a
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x)
    return 1.0 - front * _betacf(b, a, 1.0 - x) * a / b


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """Exact one-sided Clopper–Pearson UPPER bound on a binomial rate (``k`` of ``n``).

    Returns the largest ``p`` consistent with ``k`` observed events at confidence ``1-alpha``
    (the exact Beta-quantile form ``BetaInv(1-alpha; k+1, n-k)``; for ``k=0`` this reduces to
    ``1 - alpha**(1/n)``). Solved by bisecting the monotone regularized incomplete beta —
    pure stdlib, no scipy — because the near-zero false-graduation regime is exactly where the
    normal approximation is invalid. ``n == 0`` → 1.0 (no evidence); ``k >= n`` → 1.0.
    """
    if n <= 0 or k >= n:
        return 1.0
    a, b = float(k + 1), float(n - k)
    target = 1.0 - alpha
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _regularized_incomplete_beta(a, b, mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def sample_size_for_upper_bound(
    target_upper: float, alpha: float = 0.05, observed_failures: int = 0
) -> int:
    """Smallest ``n`` whose CP upper bound (with ``observed_failures`` events) is ≤ ``target``.

    Honest sizing for the headline: for a 1e-3 upper bound at 95% with zero observed false
    graduations this returns ≈3000 (``1 - 0.05**(1/n) ≤ 1e-3``); a 3e-3 bound needs ≈1000
    (rule-of-three). Monotone search, capped so a hopeless target terminates.
    """
    if not 0.0 < target_upper < 1.0:
        raise ValueError("target_upper must be in (0, 1)")
    cap = 10_000_000
    lo = max(observed_failures + 1, 1)
    if clopper_pearson_upper(observed_failures, lo, alpha) <= target_upper:
        return lo
    hi = lo
    while hi < cap and clopper_pearson_upper(observed_failures, hi, alpha) > target_upper:
        hi *= 2
    if clopper_pearson_upper(observed_failures, hi, alpha) > target_upper:
        return cap
    # Bisect for the EXACT smallest n in (lo, hi] whose CP upper bound meets the target.
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if clopper_pearson_upper(observed_failures, mid, alpha) <= target_upper:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True, slots=True)
class MetricCell:
    """Aggregated verdicts for one (mutation_class × stratum × dpi) bucket."""

    mutation_class: MutationClass
    ground_truth: GroundTruth
    stratum: Stratum
    dpi: int
    n_applied: int
    n_graduated_exact: int
    n_diverged: int
    n_escalated: int

    @property
    def false_graduation_count(self) -> int:
        """MUST_KILL mutants the gate marked EXACT (0 for a MUST_ABSORB bucket — a graduation
        there is CORRECT absorption, never a safety failure)."""
        if self.ground_truth is GroundTruth.MUST_KILL:
            return self.n_graduated_exact
        return 0

    @property
    def n_killed(self) -> int:
        """MUST_KILL mutants the gate did NOT graduate (diverged OR escalated — both safe)."""
        return self.n_diverged + self.n_escalated

    @property
    def kill_rate(self) -> float:
        if self.n_applied == 0:
            return 1.0
        return self.n_killed / self.n_applied

    @property
    def escalation_rate(self) -> float:
        if self.n_applied == 0:
            return 0.0
        return self.n_escalated / self.n_applied

    @property
    def absorption_rate(self) -> float:
        """MUST_ABSORB: fraction correctly folded to EXACT (over-sensitivity = 1 - this)."""
        if self.n_applied == 0:
            return 1.0
        return self.n_graduated_exact / self.n_applied

    def false_graduation_upper(self, alpha: float = 0.05) -> float:
        return clopper_pearson_upper(self.false_graduation_count, self.n_applied, alpha)

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "mutation_class": self.mutation_class.value,
            "ground_truth": self.ground_truth.value,
            "stratum": self.stratum.value,
            "dpi": self.dpi,
            "n_applied": self.n_applied,
            "n_graduated_exact": self.n_graduated_exact,
            "n_diverged": self.n_diverged,
            "n_escalated": self.n_escalated,
            "false_graduation_count": self.false_graduation_count,
            "kill_rate": round(self.kill_rate, 6),
            "escalation_rate": round(self.escalation_rate, 6),
            "absorption_rate": round(self.absorption_rate, 6),
            "false_graduation_upper_95": round(self.false_graduation_upper(), 8),
        }


@dataclass(frozen=True, slots=True)
class EvalReport:
    """The full harness result: per-bucket cells + headline safety aggregates."""

    cells: Tuple[MetricCell, ...]

    @property
    def total_must_kill(self) -> int:
        return sum(c.n_applied for c in self.cells if c.ground_truth is GroundTruth.MUST_KILL)

    @property
    def total_false_graduations(self) -> int:
        return sum(c.false_graduation_count for c in self.cells)

    @property
    def total_escalated(self) -> int:
        return sum(c.n_escalated for c in self.cells)

    @property
    def total_applied(self) -> int:
        return sum(c.n_applied for c in self.cells)

    @property
    def overall_kill_rate(self) -> float:
        n = self.total_must_kill
        if n == 0:
            return 1.0
        killed = sum(
            c.n_killed for c in self.cells if c.ground_truth is GroundTruth.MUST_KILL
        )
        return killed / n

    @property
    def overall_escalation_rate(self) -> float:
        if self.total_applied == 0:
            return 0.0
        return self.total_escalated / self.total_applied

    def overall_false_graduation_upper(self, alpha: float = 0.05) -> float:
        return clopper_pearson_upper(
            self.total_false_graduations, self.total_must_kill, alpha
        )

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "cells": [c.to_jsonable() for c in self.cells],
            "total_must_kill": self.total_must_kill,
            "total_false_graduations": self.total_false_graduations,
            "overall_kill_rate": round(self.overall_kill_rate, 6),
            "overall_escalation_rate": round(self.overall_escalation_rate, 6),
            "overall_false_graduation_upper_95": round(
                self.overall_false_graduation_upper(), 8
            ),
        }


def run_mutation_eval(
    pairs: Sequence[GoldPair],
    classes: Sequence[MutationClass],
    *,
    region_reader_for: Optional[Callable[[Mutant], RegionReader]] = None,
) -> EvalReport:
    """Generate every applicable mutant, adjudicate it, aggregate into per-bucket metric cells.

    ``region_reader_for`` returns a live vision reader for a mutant (the full-eval backend); by
    default the hermetic reader returns the mutant's corrupted text (CI). Pure otherwise.
    """
    buckets: Dict[Tuple[MutationClass, Stratum, int], List[AdjudicationOutcome]] = {}
    for pair in pairs:
        for mut in mutants_from_pair(pair, classes):
            reader = region_reader_for(mut) if region_reader_for is not None else None
            verdict = adjudicate(
                mut.witness_text,
                mut.mutated_text,
                born_digital=born_digital_of(mut.stratum),
                region_reader=reader,
            )
            buckets.setdefault((mut.mutation_class, mut.stratum, mut.dpi), []).append(
                verdict.outcome
            )
    cells: List[MetricCell] = []
    for (mc, stratum, dpi), outcomes in sorted(
        buckets.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value, kv[0][2])
    ):
        cells.append(
            MetricCell(
                mutation_class=mc,
                ground_truth=ground_truth_of(mc),
                stratum=stratum,
                dpi=dpi,
                n_applied=len(outcomes),
                n_graduated_exact=outcomes.count(AdjudicationOutcome.GRADUATED_EXACT),
                n_diverged=outcomes.count(AdjudicationOutcome.DIVERGED),
                n_escalated=outcomes.count(AdjudicationOutcome.ESCALATED),
            )
        )
    return EvalReport(cells=tuple(cells))


# --------------------------------------------------------------------------- #
# The frozen canary (content-addressed CI ratchet).                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CanaryCase:
    """One frozen canary mutant: a witness, its corrupted candidate, and the class/labels."""

    case_id: str
    mutation_class: MutationClass
    ground_truth: GroundTruth
    witness_text: str
    mutated_text: str
    stratum: Stratum
    dpi: int

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "case_id": self.case_id,
            "mutation_class": self.mutation_class.value,
            "ground_truth": self.ground_truth.value,
            "witness_text": self.witness_text,
            "mutated_text": self.mutated_text,
            "stratum": self.stratum.value,
            "dpi": self.dpi,
        }

    @staticmethod
    def from_jsonable(obj: Dict[str, object]) -> "CanaryCase":
        return CanaryCase(
            case_id=str(obj["case_id"]),
            mutation_class=MutationClass(str(obj["mutation_class"])),
            ground_truth=GroundTruth(str(obj["ground_truth"])),
            witness_text=str(obj["witness_text"]),
            mutated_text=str(obj["mutated_text"]),
            stratum=Stratum(str(obj["stratum"])),
            dpi=int(str(obj["dpi"])),
        )


# The curated canary GOLD. Each base pair (witness ≡ candidate) is chosen so the named class
# has an applicable site; the mutation is applied deterministically at build time. Strata span
# born-digital-clean + corrupt-font (graduation POSSIBLE → real discrimination) and scanned
# (guard forces ESCALATED → kill by abstention). This is the fixed set frozen by fingerprint.
_CANARY_SEED: Tuple[Tuple[str, str, MutationClass, Stratum, int], ...] = (
    # (case_id, base witness/candidate text, mutation class, stratum, dpi)
    ("digit_sub_bd", "2 500 mk/kg", MutationClass.DIGIT_SUBSTITUTION, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("digit_sub_cf", "veroluokka 4", MutationClass.DIGIT_SUBSTITUTION, Stratum.CORRUPT_FONT, 300),
    ("digit_trans_bd", "vuonna 1990", MutationClass.DIGIT_TRANSPOSITION, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("diacritic_bd", "Sähkön kulutus", MutationClass.DIACRITIC_DROP, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("diacritic_cf", "Räjähdysaine", MutationClass.DIACRITIC_DROP, Stratum.CORRUPT_FONT, 300),
    ("superscript_bd", "pinta-ala 12 m²", MutationClass.SUPERSCRIPT_DELETE, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("decimal_bd", "korko 1,5 %", MutationClass.DECIMAL_COMMA_FLIP, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("decimal_cf", "3,14 euroa", MutationClass.DECIMAL_COMMA_FLIP, Stratum.CORRUPT_FONT, 300),
    ("edge_del_bd", "markkaa", MutationClass.EDGE_CHAR_DELETION, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("thin_space_bd", "2 500 kpl", MutationClass.THIN_SPACE_REMOVAL, Stratum.BORN_DIGITAL_CLEAN, 300),
    # Scanned stratum: the sparse/scanned guard must ESCALATE (never graduate) even a content
    # mutation — a kill by ABSTENTION. Present so the escalation-rate signal is exercised.
    ("digit_sub_scan", "1 200 kg", MutationClass.DIGIT_SUBSTITUTION, Stratum.SCANNED, 200),
    ("diacritic_scan", "Öljy", MutationClass.DIACRITIC_DROP, Stratum.SCANNED, 200),
    # MUST_ABSORB inert probes: the gate SHOULD still graduate these (folds fire).
    ("zs_swap_bd", "3 000 euroa", MutationClass.ZS_SPACE_SWAP, Stratum.BORN_DIGITAL_CLEAN, 300),
    ("cf_zw_bd", "osakeyhtiö", MutationClass.CF_ZERO_WIDTH_INSERT, Stratum.BORN_DIGITAL_CLEAN, 300),
)


def build_canary_cases() -> Tuple[CanaryCase, ...]:
    """Materialize the curated canary cases (deterministic; asserts each base self-verifies).

    The base text is used as BOTH witness and candidate (so it trivially self-verifies — a
    genuine gold pair), then the mutation is applied to the candidate to form the frozen
    mutant. Raises if a base does not self-verify or a mutation fails to apply (a broken
    canary must fail loudly at build, never silently degrade coverage).
    """
    cases: List[CanaryCase] = []
    for case_id, base, mc, stratum, dpi in _CANARY_SEED:
        pair = make_gold_pair(case_id, base, base, stratum, dpi)
        mutated, applied = apply_mutation(pair.candidate_text, mc)
        if not applied or mutated == pair.candidate_text:
            raise ValueError(
                f"canary case {case_id!r}: mutation {mc.value} did not apply to {base!r}"
            )
        cases.append(
            CanaryCase(
                case_id=case_id,
                mutation_class=mc,
                ground_truth=ground_truth_of(mc),
                witness_text=pair.witness_text,
                mutated_text=mutated,
                stratum=stratum,
                dpi=dpi,
            )
        )
    return tuple(cases)


def canary_fingerprint(cases: Sequence[CanaryCase]) -> str:
    """Content-addressed sha256 over the canary cases (the anchor that freezes the gold).

    Any change to a case's text/class/labels changes the fingerprint, forcing a conscious
    re-emit — the repo's content-addressed-anchor discipline applied to the safety canary.
    """
    payload = json.dumps([c.to_jsonable() for c in cases], sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def adjudicate_canary_case(
    case: CanaryCase, *, region_reader: Optional[RegionReader] = None
) -> AdjudicationVerdict:
    """Run one frozen canary case through the REAL gate (hermetic by default)."""
    return adjudicate(
        case.witness_text,
        case.mutated_text,
        born_digital=born_digital_of(case.stratum),
        region_reader=region_reader,
    )


def canary_escalation_rate(cases: Sequence[CanaryCase]) -> float:
    """Fraction of canary cases the gate ESCALATES (the ratcheted abstention/cost signal)."""
    if not cases:
        return 0.0
    n_esc = sum(
        1 for c in cases if adjudicate_canary_case(c).outcome is AdjudicationOutcome.ESCALATED
    )
    return n_esc / len(cases)


def build_canary_fixture() -> Dict[str, object]:
    """Assemble the committed fixture dict: fingerprint + escalation band + frozen cases.

    The escalation-rate BAND is a true interval around the observed rate (±0.15, clamped) so
    the CI ratchet trips on BOTH a COLLAPSE (silent quotient loosening / a scanned case
    graduating) and an EXPLOSION (runaway abstention cost) — the addendum's two-sided guard.
    """
    cases = build_canary_cases()
    rate = canary_escalation_rate(cases)
    lo = max(0.0, round(rate - 0.15, 4))
    hi = min(1.0, round(rate + 0.15, 4))
    return {
        "_comment": (
            "Frozen phase-3 vision false-graduation canary. Regenerate with "
            "`uv run python -m lawvm.tools.fi_appendix_vision_eval emit-canary`. "
            "MUST_KILL cases must never graduate (0 false graduations); MUST_ABSORB cases "
            "must graduate; the escalation rate must stay in escalation_rate_band."
        ),
        "fingerprint": canary_fingerprint(cases),
        "escalation_rate_observed": round(rate, 6),
        "escalation_rate_band": [lo, hi],
        "n_cases": len(cases),
        "cases": [c.to_jsonable() for c in cases],
    }


def load_canary_fixture(path: Path) -> Dict[str, object]:
    """Load and shallow-validate the committed canary fixture JSON."""
    obj = json.loads(path.read_text(encoding="utf-8"))
    for key in ("fingerprint", "escalation_rate_band", "cases"):
        if key not in obj:
            raise ValueError(f"canary fixture {path} missing key {key!r}")
    return obj


def canary_cases_of(fixture: Dict[str, object]) -> Tuple[CanaryCase, ...]:
    """Decode the frozen ``cases`` list of a loaded fixture into typed :class:`CanaryCase`s."""
    raw = fixture["cases"]
    if not isinstance(raw, list):
        raise ValueError("canary fixture 'cases' is not a list")
    out: List[CanaryCase] = []
    for o in raw:
        if not isinstance(o, dict):
            raise ValueError("canary fixture 'cases' entry is not an object")
        out.append(CanaryCase.from_jsonable({str(k): v for k, v in o.items()}))
    return tuple(out)


def canary_escalation_band_of(fixture: Dict[str, object]) -> Tuple[float, float]:
    """The frozen two-sided escalation-rate band ``(lo, hi)`` of a loaded fixture."""
    band = fixture["escalation_rate_band"]
    if not (isinstance(band, list) and len(band) == 2):
        raise ValueError("canary fixture 'escalation_rate_band' is not a [lo, hi] pair")
    lo, hi = band
    if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
        raise ValueError("canary fixture 'escalation_rate_band' bounds are not numbers")
    return float(lo), float(hi)


# --------------------------------------------------------------------------- #
# Human-labeled real-routed slice (drop-in loader + metric; labeling external). #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HumanLabeledCell:
    """One human-adjudicated genuinely-routed cell (ground truth from a person, not the gate)."""

    cell_id: str
    witness_text: str
    candidate_text: str
    human_label: HumanLabel
    stratum: Stratum
    dpi: int


@dataclass(frozen=True, slots=True)
class HumanSliceReport:
    """False-graduation metric over the human-labeled slice (the eventual gold standard)."""

    n_cells: int
    n_genuine_difference: int
    n_false_graduation: int  # GENUINE_DIFFERENCE cells the gate GRADUATED

    @property
    def kill_rate(self) -> float:
        if self.n_genuine_difference == 0:
            return 1.0
        return 1.0 - self.n_false_graduation / self.n_genuine_difference

    def false_graduation_upper(self, alpha: float = 0.05) -> float:
        return clopper_pearson_upper(self.n_false_graduation, self.n_genuine_difference, alpha)

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "n_cells": self.n_cells,
            "n_genuine_difference": self.n_genuine_difference,
            "n_false_graduation": self.n_false_graduation,
            "kill_rate": round(self.kill_rate, 6),
            "false_graduation_upper_95": round(self.false_graduation_upper(), 8),
        }


def load_human_labeled_slice(path: Path) -> Tuple[HumanLabeledCell, ...]:
    """Load a JSONL slice of human-adjudicated cells; returns () if the file is absent (stub).

    Each line: ``{"cell_id","witness_text","candidate_text","human_label","stratum","dpi"}``.
    The human labeling is external (a few-hundred-cell effort); this is only the loader so the
    slice can be dropped in and scored with the identical metric.
    """
    if not path.exists():
        return ()
    cells: List[HumanLabeledCell] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cells.append(
            HumanLabeledCell(
                cell_id=str(obj["cell_id"]),
                witness_text=str(obj["witness_text"]),
                candidate_text=str(obj["candidate_text"]),
                human_label=HumanLabel(str(obj["human_label"])),
                stratum=Stratum(str(obj["stratum"])),
                dpi=int(str(obj["dpi"])),
            )
        )
    return tuple(cells)


def score_human_labeled_slice(
    cells: Sequence[HumanLabeledCell],
    *,
    region_reader_for: Optional[Callable[[HumanLabeledCell], RegionReader]] = None,
) -> HumanSliceReport:
    """Score the gate over the human slice: a GENUINE_DIFFERENCE graduation is a false graduation."""
    n_genuine = 0
    n_false = 0
    for c in cells:
        if c.human_label is not HumanLabel.GENUINE_DIFFERENCE:
            continue
        n_genuine += 1
        reader = region_reader_for(c) if region_reader_for is not None else None
        verdict = adjudicate(
            c.witness_text,
            c.candidate_text,
            born_digital=born_digital_of(c.stratum),
            region_reader=reader,
        )
        if verdict.outcome is AdjudicationOutcome.GRADUATED_EXACT:
            n_false += 1
    return HumanSliceReport(
        n_cells=len(cells), n_genuine_difference=n_genuine, n_false_graduation=n_false
    )


# --------------------------------------------------------------------------- #
# CLI.                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_emit_canary(args: argparse.Namespace) -> int:
    fixture = build_canary_fixture()
    text = json.dumps(fixture, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    out = Path(args.out) if args.out else Path(args.repo_root) / CANARY_FIXTURE_PATH
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} (fingerprint {fixture['fingerprint']}, n={fixture['n_cases']})")
    return 0


def _cmd_verify_canary(args: argparse.Namespace) -> int:
    path = Path(args.repo_root) / CANARY_FIXTURE_PATH
    fixture = load_canary_fixture(path)
    cases = canary_cases_of(fixture)
    false_grads: List[str] = []
    absorb_fail: List[str] = []
    n_esc = 0
    for c in cases:
        outcome = adjudicate_canary_case(c).outcome
        if outcome is AdjudicationOutcome.ESCALATED:
            n_esc += 1
        if c.ground_truth is GroundTruth.MUST_KILL and outcome is AdjudicationOutcome.GRADUATED_EXACT:
            false_grads.append(c.case_id)
        if c.ground_truth is GroundTruth.MUST_ABSORB and outcome is not AdjudicationOutcome.GRADUATED_EXACT:
            absorb_fail.append(c.case_id)
    rate = n_esc / len(cases) if cases else 0.0
    fp_now = canary_fingerprint(cases)
    fp_ok = fp_now == fixture["fingerprint"]
    print(f"fingerprint committed={fixture['fingerprint']} recomputed={fp_now} match={fp_ok}")
    print(
        f"false_graduations={false_grads} absorb_failures={absorb_fail} "
        f"escalation_rate={rate:.4f} band={fixture['escalation_rate_band']}"
    )
    return 0 if (not false_grads and not absorb_fail and fp_ok) else 1


def _cmd_sample_size(args: argparse.Namespace) -> int:
    for target in (3e-3, 1e-3, 1e-4):
        n = sample_size_for_upper_bound(target, alpha=args.alpha, observed_failures=0)
        print(f"target_upper={target:g} alpha={args.alpha} zero-failure N>= {n}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fi_appendix_vision_eval",
        description="Phase-3 vision-adjudicator false-graduation validation harness.",
    )
    parser.add_argument("--repo-root", default=".", help="repo root (for the canary fixture path)")
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit-canary", help="(re)generate the frozen canary fixture")
    emit.add_argument("--out", default="", help="output path (default: the committed fixture)")
    emit.set_defaults(func=_cmd_emit_canary)

    verify = sub.add_parser("verify-canary", help="re-run the gate over the frozen canary")
    verify.set_defaults(func=_cmd_verify_canary)

    size = sub.add_parser("sample-size", help="honest N for a target CP upper bound")
    size.add_argument("--alpha", type=float, default=0.05)
    size.set_defaults(func=_cmd_sample_size)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
