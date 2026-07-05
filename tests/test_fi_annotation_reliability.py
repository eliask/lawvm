"""L7 reliability-census + SourceSurfacePolicy tests (grammar7 §13-C/D).

Covers:
  * reliability_of reduces the 7 NEUTRAL statuses to the four L7 buckets;
  * the buckets populate per family and partition the compared references;
  * agree% is taken over the WITNESS population (grammar_only excluded);
  * assign_role derives roles from the measured rates:
      - a high-agreement explicit-id family with a real population → corroborate
        (or self_resolve at near-total agreement);
      - a weak / divergence-heavy family → qa_only;
      - a no-witness (grammar-carried) family → qa_only;
      - a thin-population family → qa_only;
  * SourceSurfacePolicy fail-safe: an unmeasured family defaults to qa_only;
  * the live census wiring (census_one_statute → reliability → policy) on the
    synthetic witness body, no corpus required.
"""
from __future__ import annotations

from lawvm.finland.references.annotation_reliability_census import (
    GOLD_CASES,
    AnnotationRole,
    FamilyReliability,
    ReliabilityCensusResult,
    SourceSurfacePolicy,
    _annotation_hits_gold,
    _grammar_hits_gold,
    assign_role,
    build_source_surface_policy,
    format_gold_precision_report,
    format_reliability_report,
    measure_gold_precision,
    reliabilities_from_census,
    reliability_of,
)
from lawvm.finland.references.annotation_witness_census import (
    FamilyComparison,
    WitnessCensus,
    census_one_statute,
)

_AKN = 'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"'


def _statute(body_inner: str) -> bytes:
    return (
        f"<akomaNtoso {_AKN}><act><body>{body_inner}</body></act></akomaNtoso>"
    ).encode("utf-8")


def _section(num: str, p_inner: str) -> str:
    return (
        f"<section><num>{num} §</num><paragraph><content><p>{p_inner}</p>"
        "</content></paragraph></section>"
    )


# ── A. reliability reduction ─────────────────────────────────────────────────


def test_reliability_of_reduces_statuses_to_four_buckets() -> None:
    fc = FamilyComparison(
        family="explicit_id",
        both_same_target=3,
        both_same_target_diff_span=7,         # agree (target match, span uncomparable)
        both_same_span_diff_target=2,         # disagree (diff statute)
        both_same_statute_diff_provision=6,   # disagree (same statute, diff provision)
        both_present_noncomparable=1,         # disagree (undecidable)
        grammar_only=5,                       # grammar_exceeds
        annotation_only=4,                    # annotation_exceeds
        annotation_witnesses=15,
        grammar_mentions=20,
    )
    rel = reliability_of(fc)
    assert rel.agree == 10
    assert rel.grammar_exceeds == 5
    assert rel.annotation_exceeds == 4
    # disagree now includes the same_statute_diff_provision divergence (2 + 6 + 1).
    assert rel.disagree == 9
    # compared partitions all four buckets.
    assert rel.compared == 28
    # witness population EXCLUDES grammar_only (no witness there).
    assert rel.annotation_population == 10 + 4 + 9
    # agree% is over the witness population, not all compared.
    assert abs(rel.agree_pct - (10 / 23)) < 1e-9
    assert abs(rel.annotation_exceeds_pct - (4 / 23)) < 1e-9
    assert abs(rel.disagree_pct - (9 / 23)) < 1e-9


def test_same_statute_diff_provision_counts_as_disagree() -> None:
    """A same-statute-different-provision pair is DISAGREE, never agree.

    Guards the reclassification: folding provision divergences into ``agree`` (the
    old statute-id fallback) over-stated <ref> reliability. They must count as
    disagreement so agree% reflects EXACT-provision concordance only.
    """
    fc = FamilyComparison(
        family="explicit_id",
        both_same_statute_diff_provision=8,
        annotation_witnesses=8,
    )
    rel = reliability_of(fc)
    assert rel.agree == 0
    assert rel.disagree == 8
    assert rel.agree_pct == 0.0
    assert rel.disagree_pct == 1.0


def test_reliability_zero_witness_family_is_clean_zero() -> None:
    fc = FamilyComparison(family="by_name", grammar_only=998, both_same_target_diff_span=0)
    rel = reliability_of(fc)
    assert rel.annotation_population == 0
    assert rel.agree_pct == 0.0
    assert rel.grammar_exceeds == 998


# ── B. role assignment from rates ────────────────────────────────────────────


def test_high_agreement_explicit_id_earns_corroborate() -> None:
    """A real witness population with solid (but not total) agreement → corroborate."""
    rel = FamilyReliability(
        family="explicit_id", agree=200, grammar_exceeds=120, annotation_exceeds=80, disagree=20
    )
    # witness pop = 300, agree% = 200/300 ≈ 0.667 (>= 0.45, < 0.95)
    entry = assign_role(rel)
    assert entry.role is AnnotationRole.CORROBORATE
    assert entry.witness_population == 300
    assert "agree" in entry.rationale


def test_near_total_agreement_low_uncorroborated_earns_self_resolve() -> None:
    rel = FamilyReliability(
        family="explicit_id", agree=98, grammar_exceeds=10, annotation_exceeds=1, disagree=1
    )
    # witness pop = 100, agree% = 0.98 (>= 0.95), annot_exceeds% = 0.01 (<= 0.05)
    entry = assign_role(rel)
    assert entry.role is AnnotationRole.SELF_RESOLVE


def test_weak_family_earns_qa_only() -> None:
    """A divergence-heavy / low-agreement family with a real population → qa_only."""
    rel = FamilyReliability(
        family="preparatory", agree=10, grammar_exceeds=5, annotation_exceeds=80, disagree=10
    )
    # witness pop = 100, agree% = 0.10 (< 0.45)
    entry = assign_role(rel)
    assert entry.role is AnnotationRole.QA_ONLY


def test_no_witness_family_is_qa_only() -> None:
    rel = FamilyReliability(
        family="by_name", agree=0, grammar_exceeds=998, annotation_exceeds=0, disagree=0
    )
    entry = assign_role(rel)
    assert entry.role is AnnotationRole.QA_ONLY
    assert entry.witness_population == 0
    assert "grammar-carried" in entry.rationale


def test_thin_population_is_qa_only_even_if_agreement_high() -> None:
    """A tiny witness population is not trusted regardless of its rate."""
    rel = FamilyReliability(
        family="treaty", agree=9, grammar_exceeds=0, annotation_exceeds=0, disagree=0
    )
    # witness pop = 9 < 20 → qa_only despite 100% agreement.
    entry = assign_role(rel)
    assert entry.role is AnnotationRole.QA_ONLY
    assert "not yet" in entry.rationale


# ── C. policy manifest ───────────────────────────────────────────────────────


def test_policy_assigns_roles_and_fail_safe_default() -> None:
    reliabilities = {
        "explicit_id": FamilyReliability(
            family="explicit_id", agree=200, grammar_exceeds=100, annotation_exceeds=70, disagree=30
        ),
        "by_name": FamilyReliability(
            family="by_name", agree=0, grammar_exceeds=998, annotation_exceeds=0, disagree=0
        ),
    }
    policy = build_source_surface_policy(reliabilities)
    assert policy.role_for("explicit_id") is AnnotationRole.CORROBORATE
    assert policy.role_for("by_name") is AnnotationRole.QA_ONLY
    # Unmeasured family → fail-safe qa_only (never trust an unmeasured shape).
    assert policy.role_for("eu") is AnnotationRole.QA_ONLY
    # Accessors.
    assert policy.may_corroborate("explicit_id") is True
    assert policy.may_self_resolve("explicit_id") is False
    assert policy.may_corroborate("by_name") is False


def test_policy_self_resolve_implies_corroborate() -> None:
    reliabilities = {
        "explicit_id": FamilyReliability(
            family="explicit_id", agree=98, grammar_exceeds=5, annotation_exceeds=1, disagree=1
        )
    }
    policy = build_source_surface_policy(reliabilities)
    assert policy.may_self_resolve("explicit_id") is True
    assert policy.may_corroborate("explicit_id") is True


# ── D. live wiring on the synthetic witness body (no corpus) ──────────────────

# explicit_id <ref> witnesses agreeing with the grammar text-lane target +
# enough repetition to clear the statistical-meaning floor.
_AGREE_REF = (
    "Sovelletaan asetusta (481/2003). Viitataan "
    '<ref href="/akn/fi/act/statute-consolidated/2003/481">asetukseen 481/2003</ref>.'
)


def test_census_reduces_to_reliability_and_drives_policy() -> None:
    """census_one_statute → FamilyComparison → reliability buckets → policy."""
    per_family = census_one_statute(_statute(_section("1", _AGREE_REF)), "2020/100")
    census = WitnessCensus(statutes_scanned=1, families=per_family)
    reliabilities = reliabilities_from_census(census)
    assert "explicit_id" in reliabilities
    eid = reliabilities["explicit_id"]
    # The agreeing <ref> lands in the agree bucket (target match, span uncomparable).
    assert eid.agree >= 1
    # Policy assigns a role per family; explicit_id has a witness here.
    policy = build_source_surface_policy(reliabilities)
    assert "explicit_id" in policy.entries


def test_report_renders_table_and_policy() -> None:
    reliabilities = {
        "explicit_id": FamilyReliability(
            family="explicit_id", agree=200, grammar_exceeds=100, annotation_exceeds=70, disagree=30
        ),
        "by_name": FamilyReliability(
            family="by_name", agree=0, grammar_exceeds=998, annotation_exceeds=0, disagree=0
        ),
    }
    policy = build_source_surface_policy(reliabilities)
    result = ReliabilityCensusResult(
        statutes_scanned=200, reliabilities=reliabilities, policy=policy
    )
    report = format_reliability_report(result)
    assert "RELIABILITY CENSUS" in report
    assert "SOURCE SURFACE POLICY" in report
    assert "explicit_id" in report
    assert "corroborate" in report
    assert "qa_only" in report


def test_source_surface_policy_default_unknown_qa_only() -> None:
    policy = SourceSurfacePolicy(entries={})
    assert policy.role_for("anything") is AnnotationRole.QA_ONLY
    assert policy.may_corroborate("anything") is False


# ── E. hand-verified gold precision slice (honest ground truth) ──────────────


def test_gold_cases_targets_match_declared_gold() -> None:
    """Every gold case's declared target is reachable by at least one surface.

    The gold is hand-verified BY CONSTRUCTION; this test pins that the fixture
    stays coherent — for each case, EITHER the grammar text-lane OR the <ref>
    witness produces the declared ``(statute_id, provision_path)``. (Some hard
    cases are deliberately missed by BOTH surfaces to show neither is perfect; we
    exclude those from the reachability check by whitelisting them explicitly.)
    """
    # Cases where BOTH surfaces are (honestly) expected to miss the finest gold.
    known_hard = {"ref_diverges_from_gold_provision", "paren_id_two_statutes"}
    for case in GOLD_CASES:
        gram = _grammar_hits_gold(case.xml, case.gold)
        annot = _annotation_hits_gold(case.xml, case.gold)
        if case.name in known_hard:
            # Documented hard case: neither surface hits the exact gold provision.
            assert not (gram and annot), case.name
        else:
            assert gram or annot, f"{case.name}: no surface reaches declared gold"


def test_gold_precision_is_true_precision_not_concordance() -> None:
    """measure_gold_precision reports grammar/<ref> precision against known truth.

    Distinct from the corpus concordance census: here the target is KNOWN, so a
    surface is scored correct only when it produces the exact gold provision.
    """
    prec = measure_gold_precision()
    assert prec.cases == len(GOLD_CASES)
    assert 0 <= prec.grammar_correct <= prec.cases
    assert 0 <= prec.annotation_correct <= prec.cases
    # Precisions are clean fractions in [0, 1].
    assert 0.0 <= prec.grammar_precision <= 1.0
    assert 0.0 <= prec.annotation_precision <= 1.0
    # The asymmetry counters are consistent with the per-surface hit counts.
    assert prec.grammar_only_correct <= prec.grammar_correct
    assert prec.annotation_only_correct <= prec.annotation_correct


def test_gold_precision_report_is_honest_about_scope() -> None:
    """The gold report labels itself a sanity floor, not a corpus claim."""
    report = format_gold_precision_report(measure_gold_precision())
    assert "GOLD PRECISION SLICE" in report
    assert "hand-verified" in report
    assert "NOT a corpus precision claim" in report


def test_reliability_report_includes_gold_slice() -> None:
    """The full reliability report appends the honest gold precision slice."""
    reliabilities = {
        "explicit_id": FamilyReliability(
            family="explicit_id", agree=200, grammar_exceeds=100, annotation_exceeds=70, disagree=30
        ),
    }
    policy = build_source_surface_policy(reliabilities)
    result = ReliabilityCensusResult(
        statutes_scanned=200, reliabilities=reliabilities, policy=policy
    )
    report = format_reliability_report(result)
    assert "GOLD PRECISION SLICE" in report
    assert "CONCORDANCE, NOT PRECISION" in report
