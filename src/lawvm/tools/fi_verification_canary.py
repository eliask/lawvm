"""``lawvm fi-verification-canary`` — the missing ERROR BAR on "verified".

Every LawVM fidelity gate emits a verdict — ``equal`` / ``corroborated`` /
``cell_exact`` / ``self_verified``. What none of them ships is the error bar on
that verdict: *when the truth is actually WRONG, what fraction does the gate still
wave through as verified?* This harness measures exactly that. It SEEDS known
errors (the truth is deliberately corrupted) and drives the ACTUAL shipped gate
code with those seeded inputs, then counts what fraction FALSELY GRADUATES
(accepted / corroborated / exact when the truth is wrong) versus what is correctly
TYPED as a divergence.

This is QA/measurement SCAFFOLDING, not a production-path primitive. It calls NO
model and touches NO network (:8080 / MinerU subprocess never run): the witnesses
are STUBBED — seeded strings fed straight into the pure gate functions — so the
whole suite is deterministic and CI-testable. It REUSES the shipped gates rather
than reimplementing them, so a false-graduation number here is a fact about the
code that actually runs in production, not about a paraphrase of it.

The three gates it drives (one seeded-error class each)
-------------------------------------------------------
1. **Op-equivalence fold quotient** — :func:`lawvm.core.op_equivalence.text_equivalence`
   over the ``EncodingFold`` set. Seeds a single-glyph payload substitution, a
   diacritic flip (``Å``→``Ä``), and a citation confusability (``l``/``1``,
   ``O``/``0``). For each, a fold either MASKS the difference (``equal=True`` →
   FALSE GRADUATION — a genuine content change hidden inside a fold bucket) or the
   difference SURVIVES as a typed residual (``equal=False`` — correctly typed).
   The quotient is designed to be non-masking on visible-glyph differences, so a
   0/N here is the empirically-confirmed error bar, not an assumption.

2. **Vision consensus gate** — :func:`lawvm.ingest.text_layer_repair.reconcile_vision_tokens`,
   Gate A (single witness) and Gate B (two-witness consensus). The pointed seed is
   the same-lineage correlated misread: TWO agreeing vision witnesses that BOTH
   differ from truth the SAME way. Gate B's non-masking argument rests on "two
   INDEPENDENT reads agreeing" — so when the two reads are NOT independent (both
   Qwen-lineage, a shared semantic prior), a coincident wrong consensus FALSELY
   corroborates. This harness quantifies that shipped false-corroboration rate,
   and the single-witness Gate A blind-trust rate alongside it.

3. **MinerU table verify gate** — :func:`lawvm.ingest.llm_backends.mineru_producer.verify_mineru_table_textlayer`.
   Two seeds: a WRONG cell the text layer disagrees with (→ typed divergence,
   good) and an OMITTED cell dropped entirely from the produced grid. The verify
   gate iterates the cells it WAS given, so a dropped cell is never witnessed — it
   produces no divergence and no pending. This harness quantifies that census
   blind-spot (an omission graduates ``self_verified`` at 100%).

Every seed carries its own GROUND TRUTH, so "false graduation" is decidable
locally: the gate output is compared against the seeded truth, never against
another gate. The report is a per-error-class table of
``n_false_graduated / n_seeds`` plus the per-seed detail, emitted as text or JSON.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from lawvm.core.op_equivalence import text_equivalence
from lawvm.ingest.llm_backends.mineru_producer import (
    MineruCell,
    MineruTable,
    verify_mineru_table_textlayer,
)
from lawvm.ingest.text_layer_repair import reconcile_vision_tokens

# The three seeded-error classes, named once so the report and the tests share a vocab.
CLASS_FOLD_QUOTIENT = "op_equivalence_fold_quotient"
CLASS_VISION_CONSENSUS = "vision_consensus_gate"
CLASS_MINERU_TABLE = "mineru_table_verify_gate"


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    """One seeded known-error run against a real gate, with its false-graduation verdict.

    ``false_graduated`` is the whole point: True iff the gate accepted / corroborated /
    graduated the seeded input as verified even though its ground truth is WRONG. When
    False the gate correctly TYPED the seeded error as a divergence (the honest outcome).
    ``detail`` carries the gate-specific evidence (which folds fired, which token the gate
    substituted, the routing verdict) so the number is auditable, never a bare bit.
    """

    name: str
    false_graduated: bool
    gate_verdict: str
    detail: str

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "false_graduated": self.false_graduated,
            "gate_verdict": self.gate_verdict,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ErrorClassReport:
    """Per-error-class false-graduation rate over its seeded known errors."""

    error_class: str
    seeds: Tuple[SeedOutcome, ...]

    @property
    def n_seeds(self) -> int:
        return len(self.seeds)

    @property
    def n_false_graduated(self) -> int:
        return sum(1 for s in self.seeds if s.false_graduated)

    @property
    def false_graduation_rate(self) -> float:
        """Fraction of seeded known errors the gate FALSELY graduated (0.0 = non-masking)."""
        return self.n_false_graduated / self.n_seeds if self.n_seeds else 0.0

    def to_jsonable(self) -> Dict[str, object]:
        return {
            "error_class": self.error_class,
            "n_seeds": self.n_seeds,
            "n_false_graduated": self.n_false_graduated,
            "false_graduation_rate": round(self.false_graduation_rate, 4),
            "seeds": [s.to_jsonable() for s in self.seeds],
        }


@dataclass(frozen=True, slots=True)
class CanaryReport:
    """The full seeded-error suite: one :class:`ErrorClassReport` per gate."""

    classes: Tuple[ErrorClassReport, ...]

    def to_jsonable(self) -> Dict[str, object]:
        return {"classes": [c.to_jsonable() for c in self.classes]}


# --------------------------------------------------------------------------- #
# Class 1 — op-equivalence fold quotient (does a fold MASK a real difference?) #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _FoldSeed:
    """A genuine CONTENT difference (truth vs a corrupted witness) fed to the quotient."""

    name: str
    truth: str
    corrupted: str


# Each pair differs by a VISIBLE, legally-meaningful change — the exact classes the
# quotient's docstring promises stay substantive. A fold MASKS the error iff the pair
# folds to equal (false graduation); the designed-for outcome is that every one SURVIVES
# as a residual (equal=False), which this harness confirms empirically rather than assumes.
_FOLD_SEEDS: Tuple[_FoldSeed, ...] = (
    _FoldSeed(
        "single_glyph_payload_substitution",
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2015",
        "Tämä laki tulee voimaan 1 päivänä tammakuuta 2015",  # tammi→tamma (one letter)
    ),
    _FoldSeed(
        "diacritic_flip_ring_to_umlaut",
        "INGARSKILAÅN",
        "INGARSKILAÄN",  # Å→Ä (the exact MinerU glyph error)
    ),
    _FoldSeed(
        "citation_confusability_l_for_one",
        "annettu asetus (1505/1992)",
        "annettu asetus (l505/l992)",  # 1→l in a statute citation
    ),
    _FoldSeed(
        "citation_confusability_O_for_zero",
        "viitataan kohtaan 20",
        "viitataan kohtaan 2O",  # 0→O
    ),
    _FoldSeed(
        "digit_payload_substitution_under_active_fold",
        "korotetaan 5,9 prosenttiin .",  # trailing space-before-period → WHITESPACE_PUNCT fires
        "korotetaan 5,8 prosenttiin.",  # a genuine numeric change UNDER an active fold
    ),
)


def run_fold_quotient_suite() -> ErrorClassReport:
    """Drive :func:`text_equivalence` on seeded genuine-content differences.

    For each seed the gate is the REAL op-equivalence quotient. ``equal=True`` means a
    fold collapsed a genuine content difference to equal — a FALSE GRADUATION; ``False``
    means the difference correctly survived as a typed residual. The folds that fired are
    recorded so a 0/N is auditable (folds may fire and still not mask the difference).
    """
    outcomes: List[SeedOutcome] = []
    for seed in _FOLD_SEEDS:
        verdict = text_equivalence(seed.truth, seed.corrupted)
        fired = ",".join(f.value for f in verdict.folds) or "none"
        outcomes.append(
            SeedOutcome(
                name=seed.name,
                false_graduated=verdict.equal,
                gate_verdict="equal" if verdict.equal else "residual",
                detail=f"folds_fired={fired}",
            )
        )
    return ErrorClassReport(CLASS_FOLD_QUOTIENT, tuple(outcomes))


# --------------------------------------------------------------------------- #
# Class 2 — vision consensus gate (Gate A single witness; Gate B consensus)    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _VisionSeed:
    """A geom read + one/two vision witness read(s), each carrying its own ground truth.

    ``truth`` is what the region ACTUALLY says. ``geom`` is the deterministic text-layer
    read; ``vision_1`` / ``vision_2`` are the (stubbed) independent vision reads. The gate
    output is compared against ``truth``: any repaired body that differs from truth is a
    false graduation (a correct token overwritten, or a wrong consensus adopted).
    """

    name: str
    gate: str  # "A" or "B" — which gate the seed probes
    truth: str
    geom: str
    vision_1: str
    vision_2: Optional[str]


# Gate A (single witness): a single-letter disagreement where the GEOM read was already
# correct. Gate A substitutes toward the lone vision witness (single witness = candidate,
# not an independent verdict), so a correct token is overwritten → false graduation.
# Gate B (two witnesses): the SAME-LINEAGE correlated misread — two witnesses that BOTH
# misread a correct geom token the SAME way. Gate B's consensus rule is meant to be the
# corroboration safeguard, but coincident non-independent reads defeat it → false
# corroboration. Each class also carries a CORRECT-repair control (geom genuinely corrupt,
# witnesses read the truth) to prove the harness distinguishes a real graduation.
_VISION_SEEDS: Tuple[_VisionSeed, ...] = (
    _VisionSeed(
        "gateA_single_witness_overwrites_correct_geom",
        "A",
        truth="Korvausoikeuden osalta noudatetaan",
        geom="Korvausoikeuden osalta noudatetaan",  # geom is CORRECT
        vision_1="Korvausoikeuden osolta noudatetaan",  # vision misreads osalta→osolta
        vision_2=None,
    ),
    _VisionSeed(
        "gateA_correct_repair_control",
        "A",
        truth="tietojen luovuttaminen",
        geom="tietosen luovuttaminen",  # geom corrupt (j→s, a garble)
        vision_1="tietojen luovuttaminen",  # vision reads the truth
        vision_2=None,
    ),
    _VisionSeed(
        "gateB_correlated_witnesses_false_consensus",
        "B",
        truth="työttömyyskassalta haettava etuus",
        geom="työttömyyskassalta haettava etuus",  # geom is CORRECT
        vision_1="työttömyyskassaha haettava etuus",  # correlated misread lta→ha
        vision_2="työttömyyskassaha haettava etuus",  # SAME misread (same lineage)
    ),
    _VisionSeed(
        "gateB_correct_repair_control",
        "B",
        truth="perimisestä säädetään erikseen",
        geom="periruisestä säädetään erikseen",  # geom corrupt (multi-char CMap garble)
        vision_1="perimisestä säädetään erikseen",  # both witnesses read the truth
        vision_2="perimisestä säädetään erikseen",
    ),
)


def run_vision_consensus_suite() -> ErrorClassReport:
    """Drive :func:`reconcile_vision_tokens` on seeded geom/vision reads.

    ``false_graduated`` iff the reconciled body differs from the seed's ground truth: a
    correct geom token overwritten by a lone/consensus vision misread. A control seed
    (geom genuinely corrupt, witnesses read truth) graduates CORRECTLY — the reconciled
    body equals truth — so it is NOT a false graduation, proving the metric discriminates.
    """
    outcomes: List[SeedOutcome] = []
    for seed in _VISION_SEEDS:
        result = reconcile_vision_tokens(
            seed.geom, seed.vision_1, vision_text_2=seed.vision_2
        )
        # Truth-referenced: a repaired body that is not the truth is a false graduation.
        false_grad = result.repaired_text != seed.truth
        subs = (
            ";".join(f"{s.geom_token}->{s.vision_token}" for s in result.substitutions)
            or "none"
        )
        outcomes.append(
            SeedOutcome(
                name=seed.name,
                false_graduated=false_grad,
                gate_verdict="repaired" if result.changed else "unchanged",
                detail=f"gate={seed.gate} subs={subs}",
            )
        )
    return ErrorClassReport(CLASS_VISION_CONSENSUS, tuple(outcomes))


# --------------------------------------------------------------------------- #
# Class 3 — MinerU table verify gate (wrong cell typed; OMITTED cell blind)    #
# --------------------------------------------------------------------------- #

#: The born-digital text layer of the seeded page region — the independent witness the
#: MinerU verify gate corroborates each cell against. It carries the TRUE content of all
#: three cells, so a produced cell is corroborated iff it faithfully reproduces the truth.
_MINERU_REGION_TEXT = "Lääni ja kunta tukkipuu 250 kuitupuu 180"


def _mineru_table(cells: Tuple[MineruCell, ...]) -> MineruTable:
    return MineruTable(
        locator="canary://mineru/seed",
        page_num=1,
        table_index=0,
        n_rows=1,
        n_cols=len(cells),
        caption="",
        cells=cells,
    )


def run_mineru_table_suite() -> ErrorClassReport:
    """Drive :func:`verify_mineru_table_textlayer` on a wrong cell and an OMITTED cell.

    * **wrong_cell** — a produced cell whose content the text layer does not carry. The
      gate emits a typed ``TableCellDivergence`` and routes ``vision_escalate`` → correctly
      typed, NOT a false graduation.
    * **omitted_cell** — a produced grid that DROPPED a true cell entirely. The gate only
      witnesses the cells it was given, so the dropped cell yields no divergence and no
      pending; the table graduates with every present cell exact → FALSE GRADUATION. This
      is the census blind-spot: an omission is structurally invisible to a per-cell gate.
    """
    outcomes: List[SeedOutcome] = []

    # Seed 1: a WRONG cell (text layer disagrees) → typed divergence, correctly not graduated.
    wrong = _mineru_table(
        (
            MineruCell(0, 0, 1, 1, "Lääni ja kunta", is_header=True),
            MineruCell(0, 1, 1, 1, "tukkipuu 250"),
            MineruCell(0, 2, 1, 1, "kuitupuu 9999"),  # 180 → 9999: a fabricated figure
        )
    )
    v_wrong = verify_mineru_table_textlayer(wrong, _MINERU_REGION_TEXT)
    outcomes.append(
        SeedOutcome(
            name="wrong_cell_text_layer_disagrees",
            false_graduated=v_wrong.exact,  # exact=True would mean the wrong cell slipped through
            gate_verdict="exact" if v_wrong.exact else "typed_divergence",
            detail=f"n_divergences={len(v_wrong.divergences)}",
        )
    )

    # Seed 2: an OMITTED cell — the true grid has three cells; the produced grid dropped one.
    # The text layer still carries all three, but the gate can only witness what it was given.
    omitted = _mineru_table(
        (
            MineruCell(0, 0, 1, 1, "Lääni ja kunta", is_header=True),
            MineruCell(0, 1, 1, 1, "tukkipuu 250"),
            # the "kuitupuu 180" cell has been DROPPED entirely
        )
    )
    v_omit = verify_mineru_table_textlayer(omitted, _MINERU_REGION_TEXT)
    outcomes.append(
        SeedOutcome(
            name="omitted_cell_dropped_from_grid",
            # No divergence and no pending is produced for the dropped cell → it graduates.
            false_graduated=v_omit.exact and not v_omit.divergences,
            gate_verdict="exact" if v_omit.exact else "typed_divergence",
            detail=(
                f"n_cells={v_omit.n_cells} n_exact={v_omit.n_exact} "
                f"n_divergences={len(v_omit.divergences)} (true grid had 3 cells)"
            ),
        )
    )
    return ErrorClassReport(CLASS_MINERU_TABLE, tuple(outcomes))


# --------------------------------------------------------------------------- #
# Suite driver + report rendering                                             #
# --------------------------------------------------------------------------- #


def run_canary_suite() -> CanaryReport:
    """Run all three seeded-error classes against the real gates (pure, deterministic)."""
    return CanaryReport(
        (
            run_fold_quotient_suite(),
            run_vision_consensus_suite(),
            run_mineru_table_suite(),
        )
    )


def render_text(report: CanaryReport) -> str:
    """A human-readable per-error-class false-graduation table + per-seed detail."""
    lines: List[str] = []
    lines.append("FALSE-GRADUATION CANARY — seeded known errors vs the real gates")
    lines.append("=" * 68)
    lines.append("")
    lines.append(f"{'error_class':<32} {'false_grad':>10} {'rate':>8}")
    lines.append("-" * 52)
    for cls in report.classes:
        lines.append(
            f"{cls.error_class:<32} "
            f"{cls.n_false_graduated:>4}/{cls.n_seeds:<5} "
            f"{cls.false_graduation_rate:>8.3f}"
        )
    lines.append("")
    for cls in report.classes:
        lines.append(f"[{cls.error_class}]")
        for s in cls.seeds:
            flag = "FALSE-GRADUATED" if s.false_graduated else "typed-divergence"
            lines.append(f"  {flag:<16} {s.name}  ({s.gate_verdict}; {s.detail})")
        lines.append("")
    return "\n".join(lines)


def render_json(report: CanaryReport) -> str:
    return json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2, sort_keys=True)


def main(args: argparse.Namespace) -> None:
    """CLI handler for ``lawvm fi-verification-canary``."""
    report = run_canary_suite()
    out = render_json(report) if args.json else render_text(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out + ("\n" if not out.endswith("\n") else ""))
        rates = ";".join(
            f"{c.error_class}={c.n_false_graduated}/{c.n_seeds}" for c in report.classes
        )
        print(f"fi-verification-canary → {args.out} ({rates})")
    else:
        print(out)


__all__ = [
    "CLASS_FOLD_QUOTIENT",
    "CLASS_MINERU_TABLE",
    "CLASS_VISION_CONSENSUS",
    "CanaryReport",
    "ErrorClassReport",
    "SeedOutcome",
    "main",
    "render_json",
    "render_text",
    "run_canary_suite",
    "run_fold_quotient_suite",
    "run_mineru_table_suite",
    "run_vision_consensus_suite",
]
