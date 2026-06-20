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
    AnnotationRole,
    FamilyReliability,
    ReliabilityCensusResult,
    SourceSurfacePolicy,
    assign_role,
    build_source_surface_policy,
    format_reliability_report,
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


def test_reliability_of_reduces_seven_statuses_to_four_buckets() -> None:
    fc = FamilyComparison(
        family="explicit_id",
        both_same_target=3,
        both_same_target_diff_span=7,         # agree (target match, span uncomparable)
        both_same_span_diff_target=2,         # disagree
        both_present_noncomparable=1,         # disagree
        grammar_only=5,                       # grammar_exceeds
        annotation_only=4,                    # annotation_exceeds
        annotation_witnesses=15,
        grammar_mentions=20,
    )
    rel = reliability_of(fc)
    assert rel.agree == 10
    assert rel.grammar_exceeds == 5
    assert rel.annotation_exceeds == 4
    assert rel.disagree == 3
    # compared partitions all four buckets.
    assert rel.compared == 22
    # witness population EXCLUDES grammar_only (no witness there).
    assert rel.annotation_population == 10 + 4 + 3
    # agree% is over the witness population, not all compared.
    assert abs(rel.agree_pct - (10 / 17)) < 1e-9
    assert abs(rel.annotation_exceeds_pct - (4 / 17)) < 1e-9
    assert abs(rel.disagree_pct - (3 / 17)) < 1e-9


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
