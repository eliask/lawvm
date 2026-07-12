"""False-graduation canary (fi_verification_canary) — hermetic, deterministic.

Pins the per-error-class false-graduation numbers the seeded-error suite measures by
driving the REAL shipped gates (op-equivalence quotient, vision consensus reconcile,
MinerU table verify) with stubbed seeded witnesses — no :8080, no MinerU subprocess, no
network. The suite is the missing error bar on "verified": each assertion here is a fact
about the code that runs in production, not a paraphrase.

The three load-bearing findings this file locks:
  * the op-equivalence quotient is empirically NON-MASKING on every seeded visible-glyph
    error (0/N false-graduated) — even when a fold visibly fires;
  * the vision consensus gate FALSELY corroborates BOTH a single-witness overwrite (Gate A)
    and a same-lineage correlated-consensus (Gate B) — the fable-identified shipped bug;
  * the MinerU verify gate is BLIND to an omitted cell (it graduates self-verified) while
    correctly typing a wrong cell.
"""
from __future__ import annotations

import json

from lawvm.tools import fi_verification_canary as vc


def _class(report: vc.CanaryReport, name: str) -> vc.ErrorClassReport:
    matches = [c for c in report.classes if c.error_class == name]
    assert len(matches) == 1, f"missing/dup error class {name!r}"
    return matches[0]


def _seed(cls: vc.ErrorClassReport, name: str) -> vc.SeedOutcome:
    matches = [s for s in cls.seeds if s.name == name]
    assert len(matches) == 1, f"missing/dup seed {name!r}"
    return matches[0]


# --------------------------------------------------------------------------- #
# Determinism — the suite is a pure function of the seeds (no backend).         #
# --------------------------------------------------------------------------- #


def test_suite_is_deterministic() -> None:
    a = vc.render_json(vc.run_canary_suite())
    b = vc.render_json(vc.run_canary_suite())
    assert a == b


def test_report_has_the_three_gate_classes() -> None:
    report = vc.run_canary_suite()
    names = [c.error_class for c in report.classes]
    assert names == [
        vc.CLASS_FOLD_QUOTIENT,
        vc.CLASS_VISION_CONSENSUS,
        vc.CLASS_MINERU_TABLE,
    ]


# --------------------------------------------------------------------------- #
# Class 1 — op-equivalence fold quotient: NON-MASKING on visible-glyph errors.  #
# --------------------------------------------------------------------------- #


def test_fold_quotient_masks_no_seeded_error() -> None:
    cls = _class(vc.run_canary_suite(), vc.CLASS_FOLD_QUOTIENT)
    # Every seeded genuine-content difference SURVIVES as a typed residual — the quotient
    # is the designed-for non-masking, confirmed empirically (0.0 error bar), not assumed.
    assert cls.n_seeds == 5
    assert cls.n_false_graduated == 0
    assert cls.false_graduation_rate == 0.0
    for s in cls.seeds:
        assert not s.false_graduated
        assert s.gate_verdict == "residual"


def test_fold_can_fire_without_masking() -> None:
    # The diagnostic separation: a fold materially FIRING is not the same as it MASKING a
    # difference. WHITESPACE_PUNCT fires on this pair, yet the 5,9→5,8 numeric change still
    # survives as a residual — proving "fold fired" is not evidence of a false graduation.
    cls = _class(vc.run_canary_suite(), vc.CLASS_FOLD_QUOTIENT)
    s = _seed(cls, "digit_payload_substitution_under_active_fold")
    assert "whitespace_punct" in s.detail
    assert not s.false_graduated


def test_fold_diacritic_ring_umlaut_survives() -> None:
    # Å→Ä (the exact MinerU glyph error) is diacritic-sensitive and must NOT fold to equal.
    cls = _class(vc.run_canary_suite(), vc.CLASS_FOLD_QUOTIENT)
    assert not _seed(cls, "diacritic_flip_ring_to_umlaut").false_graduated


# --------------------------------------------------------------------------- #
# Class 2 — vision consensus gate: the false-corroboration rate (the shipped bug).#
# --------------------------------------------------------------------------- #


def test_vision_consensus_false_graduation_rate() -> None:
    cls = _class(vc.run_canary_suite(), vc.CLASS_VISION_CONSENSUS)
    assert cls.n_seeds == 4
    # Two false graduations (one per gate); the two correct-repair controls graduate right.
    assert cls.n_false_graduated == 2
    assert cls.false_graduation_rate == 0.5


def test_gate_a_single_witness_overwrites_correct_geom() -> None:
    # Gate A trusts a LONE vision witness: a correct geom token is overwritten by the
    # witness's single-letter misread → false graduation (single witness = candidate, not
    # an independent verdict).
    cls = _class(vc.run_canary_suite(), vc.CLASS_VISION_CONSENSUS)
    s = _seed(cls, "gateA_single_witness_overwrites_correct_geom")
    assert s.false_graduated
    assert "osalta->osolta" in s.detail


def test_gate_b_correlated_witnesses_false_corroboration() -> None:
    # THE fable-identified shipped bug: Gate B's "two INDEPENDENT reads agree" safeguard is
    # defeated by two SAME-LINEAGE correlated misreads. Both witnesses misread a CORRECT
    # geom token the same way and Gate B adopts the wrong consensus → false corroboration.
    cls = _class(vc.run_canary_suite(), vc.CLASS_VISION_CONSENSUS)
    s = _seed(cls, "gateB_correlated_witnesses_false_consensus")
    assert s.false_graduated
    assert "työttömyyskassalta->työttömyyskassaha" in s.detail


def test_vision_controls_graduate_correctly() -> None:
    # The discriminating controls: when the witnesses genuinely read the truth (geom
    # corrupt), the reconciliation graduates CORRECTLY — NOT a false graduation. This proves
    # the metric measures wrongness, not merely "a substitution happened".
    cls = _class(vc.run_canary_suite(), vc.CLASS_VISION_CONSENSUS)
    assert not _seed(cls, "gateA_correct_repair_control").false_graduated
    assert not _seed(cls, "gateB_correct_repair_control").false_graduated


# --------------------------------------------------------------------------- #
# Class 3 — MinerU table verify gate: wrong cell typed; OMITTED cell blind.      #
# --------------------------------------------------------------------------- #


def test_mineru_wrong_cell_is_typed_not_graduated() -> None:
    cls = _class(vc.run_canary_suite(), vc.CLASS_MINERU_TABLE)
    s = _seed(cls, "wrong_cell_text_layer_disagrees")
    assert not s.false_graduated
    assert s.gate_verdict == "typed_divergence"
    assert "n_divergences=1" in s.detail


def test_mineru_omitted_cell_is_the_census_blind_spot() -> None:
    # The blind-spot: a produced grid that DROPPED a true cell yields NO divergence and NO
    # pending — the per-cell gate can only witness the cells it was given, so the omission
    # graduates self-verified. This is the quantified census blind-spot (100% for omissions).
    cls = _class(vc.run_canary_suite(), vc.CLASS_MINERU_TABLE)
    s = _seed(cls, "omitted_cell_dropped_from_grid")
    assert s.false_graduated
    assert s.gate_verdict == "exact"
    assert "n_divergences=0" in s.detail


def test_mineru_class_rate() -> None:
    cls = _class(vc.run_canary_suite(), vc.CLASS_MINERU_TABLE)
    assert cls.n_seeds == 2
    assert cls.n_false_graduated == 1
    assert cls.false_graduation_rate == 0.5


# --------------------------------------------------------------------------- #
# Rendering — text table + JSON mirror.                                         #
# --------------------------------------------------------------------------- #


def test_render_text_carries_every_class_and_seed() -> None:
    text = vc.render_text(vc.run_canary_suite())
    for cls in vc.run_canary_suite().classes:
        assert cls.error_class in text
        for s in cls.seeds:
            assert s.name in text
    assert "FALSE-GRADUATED" in text


def test_render_json_is_valid_and_mirrors_rates() -> None:
    report = vc.run_canary_suite()
    payload = json.loads(vc.render_json(report))
    by_name = {c["error_class"]: c for c in payload["classes"]}
    assert by_name[vc.CLASS_FOLD_QUOTIENT]["false_graduation_rate"] == 0.0
    assert by_name[vc.CLASS_VISION_CONSENSUS]["n_false_graduated"] == 2
    assert by_name[vc.CLASS_MINERU_TABLE]["n_seeds"] == 2
