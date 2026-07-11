"""Phase-3 vision-adjudicator FALSE-GRADUATION canary ratchet.

The durable *no-false-exact* CI defense for the phase-3 appendix vision adjudicator. A
FALSE GRADUATION — the gate marking two genuinely-different witnesses EXACT — is the single
safety failure the exactness invariant forbids; every other outcome (a typed divergence, an
abstain/escalate to the vision tail) is SAFE. This test freezes a curated set of synthetic
mutants (each a high-confidence-correct base pair with ONE witness corrupted) and re-runs the
REAL production graduation path (``verify_tables_vision`` + the ``text_equivalence`` quotient,
driven with an injected hermetic reader — no model/PDF/render in CI) over it, asserting:

  * ZERO false graduations — every MUST_KILL mutant is killed (kill-rate at ceiling);
  * MUST_ABSORB inert probes STILL graduate (the quotient stays honest in both directions);
  * the ESCALATION RATE stays inside a frozen two-sided band (a collapse = silent quotient
    loosening / a scanned case leaking to exact; an explosion = runaway abstention cost);
  * the canary gold is content-addressed — a sha256 fingerprint over the case texts/labels
    must match the committed fixture, so the frozen gold cannot drift without a conscious
    re-emit (the repo's content-addressed-anchor discipline).

This is the ratchet that catches model-version drift, prompt rot, or a silently-loosened
graduation quotient between baselines. Regenerate the fixture (only on a CONSCIOUS change to
the canary set) with:

    uv run python -m lawvm.tools.fi_appendix_vision_eval emit-canary
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.tools.fi_appendix_vision_eval import (
    CANARY_FIXTURE_PATH,
    AdjudicationOutcome,
    CanaryCase,
    GroundTruth,
    MutationClass,
    Stratum,
    adjudicate,
    adjudicate_canary_case,
    apply_mutation,
    born_digital_of,
    build_canary_cases,
    canary_cases_of,
    canary_escalation_band_of,
    canary_fingerprint,
    clopper_pearson_upper,
    ground_truth_of,
    load_canary_fixture,
    sample_size_for_upper_bound,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PATH = _REPO_ROOT / CANARY_FIXTURE_PATH


def _load_cases() -> tuple[CanaryCase, ...]:
    return canary_cases_of(load_canary_fixture(_FIXTURE_PATH))


# --------------------------------------------------------------------------- #
# The content-addressed anchor (frozen gold cannot drift silently).             #
# --------------------------------------------------------------------------- #


class TestCanaryFixtureIntegrity:
    def test_fixture_exists(self) -> None:
        assert _FIXTURE_PATH.exists(), (
            f"Missing vision-canary fixture at {_FIXTURE_PATH}. Generate it with "
            "`uv run python -m lawvm.tools.fi_appendix_vision_eval emit-canary`."
        )

    def test_committed_fingerprint_matches_cases(self) -> None:
        fixture = load_canary_fixture(_FIXTURE_PATH)
        cases = _load_cases()
        assert canary_fingerprint(cases) == fixture["fingerprint"], (
            "Canary fixture cases do not match their committed fingerprint — the frozen gold "
            "drifted. Re-emit consciously if intended."
        )

    def test_code_seed_matches_committed_fixture(self) -> None:
        # The in-code curated seed and the committed fixture must agree byte-for-byte (by
        # fingerprint) — otherwise the fixture is stale vs the generator.
        fixture = load_canary_fixture(_FIXTURE_PATH)
        assert canary_fingerprint(build_canary_cases()) == fixture["fingerprint"], (
            "The in-code canary seed diverged from the committed fixture. Re-emit with "
            "`uv run python -m lawvm.tools.fi_appendix_vision_eval emit-canary`."
        )

    def test_canary_is_not_vacuous(self) -> None:
        cases = _load_cases()
        assert len(cases) >= 10
        kinds = {c.mutation_class for c in cases}
        # every design-consult MUST_KILL class is represented (the safety-critical set).
        for mc in (
            MutationClass.DIGIT_SUBSTITUTION,
            MutationClass.DIGIT_TRANSPOSITION,
            MutationClass.DIACRITIC_DROP,
            MutationClass.SUPERSCRIPT_DELETE,
            MutationClass.DECIMAL_COMMA_FLIP,
            MutationClass.EDGE_CHAR_DELETION,
            MutationClass.THIN_SPACE_REMOVAL,
        ):
            assert mc in kinds, f"canary lost coverage of {mc.value}"
        # both must-absorb probes and all three strata present.
        assert {GroundTruth.MUST_KILL, GroundTruth.MUST_ABSORB} <= {c.ground_truth for c in cases}
        assert {Stratum.BORN_DIGITAL_CLEAN, Stratum.CORRUPT_FONT, Stratum.SCANNED} <= {
            c.stratum for c in cases
        }


# --------------------------------------------------------------------------- #
# The safety ratchet: zero false graduations, kill-rate at ceiling.             #
# --------------------------------------------------------------------------- #


class TestNoFalseGraduation:
    def test_zero_false_graduations(self) -> None:
        false_grads = [
            c.case_id
            for c in _load_cases()
            if c.ground_truth is GroundTruth.MUST_KILL
            and adjudicate_canary_case(c).outcome is AdjudicationOutcome.GRADUATED_EXACT
        ]
        assert not false_grads, (
            "\n[VISION CANARY] FALSE GRADUATION(S) — the gate marked a content-corrupted cell "
            f"EXACT:\n  {false_grads}\nThe no-false-exact invariant is broken. This is the "
            "single safety failure the phase-3 exactness gate exists to prevent."
        )

    def test_kill_rate_at_ceiling(self) -> None:
        cases = [c for c in _load_cases() if c.ground_truth is GroundTruth.MUST_KILL]
        killed = sum(
            1
            for c in cases
            if adjudicate_canary_case(c).outcome is not AdjudicationOutcome.GRADUATED_EXACT
        )
        assert killed == len(cases), f"kill-rate below ceiling: {killed}/{len(cases)}"

    def test_must_absorb_still_graduates(self) -> None:
        # Over-tightening (refusing to fold genuinely-inert noise) would tank precision/cost.
        # A born-digital inert probe MUST still graduate to exact.
        failed = [
            c.case_id
            for c in _load_cases()
            if c.ground_truth is GroundTruth.MUST_ABSORB
            and born_digital_of(c.stratum)
            and adjudicate_canary_case(c).outcome is not AdjudicationOutcome.GRADUATED_EXACT
        ]
        assert not failed, (
            f"\n[VISION CANARY] inert MUST_ABSORB probe(s) failed to graduate: {failed}\n"
            "The quotient stopped folding legally-inert noise (over-sensitivity / cost blowup)."
        )


# --------------------------------------------------------------------------- #
# The escalation-rate ratchet (two-sided: collapse OR explosion both trip).      #
# --------------------------------------------------------------------------- #


class TestEscalationRateRatchet:
    def test_escalation_rate_within_frozen_band(self) -> None:
        fixture = load_canary_fixture(_FIXTURE_PATH)
        cases = _load_cases()
        n_esc = sum(
            1
            for c in cases
            if adjudicate_canary_case(c).outcome is AdjudicationOutcome.ESCALATED
        )
        rate = n_esc / len(cases)
        lo, hi = canary_escalation_band_of(fixture)
        assert lo <= rate <= hi, (
            f"\n[VISION CANARY] escalation rate {rate:.4f} left the frozen band [{lo}, {hi}].\n"
            "A COLLAPSE means the gate stopped abstaining where it should (silent loosening — "
            "check false graduations); an EXPLOSION means runaway abstention cost. Both are a "
            "conscious-review event, not a silent tune."
        )


# --------------------------------------------------------------------------- #
# Guard-liveness / trip-proofs: the harness is not vacuous — it CATCHES a leak. #
# --------------------------------------------------------------------------- #


class TestHarnessTripProofs:
    def test_gate_would_graduate_an_uncorrupted_pair(self) -> None:
        # Sanity that the graduation PATH is live: two inert-equal witnesses on a born-digital
        # cell DO graduate. (If this stopped graduating, the whole gate would be inert and the
        # canary vacuously "passing".)
        v = adjudicate("2 500 mk/kg", "2 500 mk/kg", born_digital=True)
        assert v.outcome is AdjudicationOutcome.GRADUATED_EXACT

    def test_harness_flags_a_planted_false_graduation(self) -> None:
        # Plant a would-be false graduation: label a genuinely-inert (hence graduating) pair as
        # MUST_KILL and confirm the classifier CONVICTS it. Proves the safety assert has teeth.
        planted = CanaryCase(
            case_id="planted_leak",
            mutation_class=MutationClass.DIGIT_SUBSTITUTION,
            ground_truth=GroundTruth.MUST_KILL,
            witness_text="3 000 euroa",
            mutated_text="3 000 euroa",  # identical -> the gate WILL graduate
            stratum=Stratum.BORN_DIGITAL_CLEAN,
            dpi=300,
        )
        outcome = adjudicate_canary_case(planted).outcome
        assert outcome is AdjudicationOutcome.GRADUATED_EXACT
        is_false_graduation = (
            planted.ground_truth is GroundTruth.MUST_KILL
            and outcome is AdjudicationOutcome.GRADUATED_EXACT
        )
        assert is_false_graduation, "the false-graduation detector failed to convict a planted leak"

    def test_scanned_guard_forces_escalation_not_graduation(self) -> None:
        # The sparse/scanned guard: even an inert-equal pair must NOT graduate when not
        # born-digital (the pdfium witness is untrustworthy there) — it escalates.
        v = adjudicate("2 500 mk/kg", "2 500 mk/kg", born_digital=False)
        assert v.outcome is AdjudicationOutcome.ESCALATED

    def test_mutations_actually_change_content(self) -> None:
        # Each MUST_KILL operator must genuinely alter its applicable base (else a "kill" is
        # trivial). Verified against one applicable base per class.
        checks = {
            MutationClass.DIGIT_SUBSTITUTION: "2 500",
            MutationClass.DIGIT_TRANSPOSITION: "1990",
            MutationClass.DIACRITIC_DROP: "Sähkö",
            MutationClass.SUPERSCRIPT_DELETE: "12 m²",
            MutationClass.DECIMAL_COMMA_FLIP: "1,5",
            MutationClass.EDGE_CHAR_DELETION: "markkaa",
            MutationClass.THIN_SPACE_REMOVAL: "2 500",
        }
        for mc, base in checks.items():
            mutated, applied = apply_mutation(base, mc)
            assert applied and mutated != base, f"{mc.value} failed to alter {base!r}"
            assert ground_truth_of(mc) is GroundTruth.MUST_KILL

    def test_non_applicable_mutation_is_skipped(self) -> None:
        # A class with no site does NOT apply (so it is excluded from the denominator, never a
        # spurious easy kill).
        _mutated, applied = apply_mutation("no digits here", MutationClass.DIGIT_SUBSTITUTION)
        assert applied is False


# --------------------------------------------------------------------------- #
# The Clopper–Pearson metric (exact, near-zero regime) + honest sample size.     #
# --------------------------------------------------------------------------- #


class TestClopperPearsonMetric:
    def test_zero_failure_closed_form(self) -> None:
        # For k=0 the CP upper bound is 1 - alpha**(1/n); check against the closed form.
        for n in (100, 1000, 3000):
            assert clopper_pearson_upper(0, n, 0.05) == pytest.approx(
                1 - 0.05 ** (1 / n), abs=1e-6
            )

    def test_bound_is_monotone_decreasing_in_n(self) -> None:
        vals = [clopper_pearson_upper(0, n, 0.05) for n in (50, 100, 1000, 3000)]
        assert all(a > b for a, b in zip(vals, vals[1:]))

    def test_honest_sample_size_for_one_in_a_thousand(self) -> None:
        # The headline honesty: a strict 1e-3 upper bound at 95% needs ~3000 mutants/class;
        # ~1000 buys only ~3e-3 (rule of three). A 50-mutant probe proves nothing here.
        assert sample_size_for_upper_bound(1e-3, 0.05) == 2995
        assert sample_size_for_upper_bound(3e-3, 0.05) == 998
        assert clopper_pearson_upper(0, 50, 0.05) > 0.05  # a 50-sample bound is ~6%, not 1e-3

    def test_more_events_raise_the_bound(self) -> None:
        assert clopper_pearson_upper(1, 1000, 0.05) > clopper_pearson_upper(0, 1000, 0.05)
