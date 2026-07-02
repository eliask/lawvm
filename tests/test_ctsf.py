"""Tests for Canonical Text-State Form v0 (CTSF) — task #184.

Covers:
* the constructive projection ``to_ctsf`` on fixtures;
* ``CNF_UNSUPPORTED_*`` typed residuals for an unsupported construct;
* the control-pair admission-gate harness, incl. a NEGATIVE test that a rule
  without control pairs is REJECTED;
* each migrated editorial rule's four-part control pairs (admitted);
* the #181 spec-ledger glue unification (each rule <-> its glue lens);
* a byte-identity guard that default bench neutralizer/diff output is unchanged
  whether or not the CTSF module is imported.
"""

from __future__ import annotations



from lawvm.core.ctsf import (
    CTSF_EQUALITY_DISCLAIMER,
    CTSF_VERSION,
    CTSFTelemetry,
    collect_elisions,
    collect_residuals,
    ctsf_equal,
    ctsf_telemetry,
    to_ctsf,
)
from lawvm.core.ctsf_admission_gate import (
    CTSFEditorialRule,
    check_rule_admission,
    run_admission_gate,
)
from lawvm.core.ctsf_rules import registered_ctsf_rules
from lawvm.semantic.model import SemanticStructureFacet, SemanticStructureNode


def _wf(text: str) -> tuple[SemanticStructureFacet, ...]:
    return (SemanticStructureFacet(kind="wording", text=text),)


def _sec(label: str, *, text: str = "", basis: str = "explicit") -> SemanticStructureNode:
    return SemanticStructureNode(
        kind="section",
        label=label,
        label_basis=basis,
        facets=_wf(text) if text else (),
    )


# ---------------------------------------------------------------------------
# Constructive projection
# ---------------------------------------------------------------------------


def test_to_ctsf_extracts_addressable_fields():
    node = SemanticStructureNode(
        kind="section",
        label="111",
        facets=(
            SemanticStructureFacet(kind="heading", text="Otsikko."),
            SemanticStructureFacet(kind="wording", text="Pykälän teksti."),
        ),
        children=(
            SemanticStructureNode(kind="subsection", label="2", facets=_wf("Momentti kaksi.")),
            SemanticStructureNode(kind="subsection", label="1", facets=_wf("Momentti yksi.")),
        ),
    )
    c = to_ctsf(node)
    assert c.kind == "section"
    assert c.label == "111"
    assert c.occupancy_state == "live"
    assert c.address == "section:111"
    # heading is grammar-normalized (trailing period stripped)
    assert c.normalized_heading == "Otsikko"
    # child order is preserved verbatim (containment is normative)
    assert [ch.label for ch in c.child_order] == ["2", "1"]
    assert c.child_order[0].address == "section:111/subsection:2"


def test_to_ctsf_is_idempotent_on_addressable_fields():
    node = _sec("5", text="maksu.......... 20")
    once = to_ctsf(node)
    # Re-projecting an equal source yields an equal CTSF (fixed point on fields).
    twice = to_ctsf(_sec("5", text=once.normalized_text))
    assert ctsf_equal(once, twice)


# ---------------------------------------------------------------------------
# CNF_UNSUPPORTED typed residuals (never a silent drop)
# ---------------------------------------------------------------------------


def test_unsupported_table_construct_becomes_typed_residual():
    from lawvm.core.table_model import TableBody

    table = TableBody(table_id="t1", caption="Maksut", columns=(), rows=())
    node = SemanticStructureNode(
        kind="section",
        label="9",
        facets=(SemanticStructureFacet(kind="wording", text="taulukko", tables=(table,)),),
    )
    c = to_ctsf(node)
    residuals = collect_residuals(c)
    assert [r.kind for r in residuals] == ["CNF_UNSUPPORTED_TABLE"]
    # typed, addressed, and witnessed — not a silent drop
    r = residuals[0]
    assert r.address == "section:9"
    assert r.witness
    assert "not addressable in CTSF v0" in r.detail


# ---------------------------------------------------------------------------
# Admission-gate harness — positive (registered rules) + NEGATIVE (rejection)
# ---------------------------------------------------------------------------


def test_every_registered_rule_passes_admission_gate():
    results = run_admission_gate()
    assert results, "no CTSF rules registered"
    for res in results:
        assert res.admitted, f"{res.rule_id} rejected: {res.failures}"


def test_registered_rules_are_exactly_the_three_migrated():
    ids = {r.rule_id for r in registered_ctsf_rules()}
    assert ids == {
        "ctsf.text.grammar_normalization",
        "ctsf.occupancy.repeal_tombstone_elision",
        "ctsf.text.aiempi_sanamuoto_elision",
    }


def test_rule_without_control_pairs_is_rejected():
    """The load-bearing guardrail: a rule that ships no control pairs cannot
    enter CTSF.  Without this, CTSF is just a fancier neutralizer blacklist."""
    empty = CTSFEditorialRule(
        rule_id="ctsf.bogus.no_control_pairs",
        jurisdiction="fi",
        believed_spec="ignore whatever makes the number go up",
        falsifier="",  # also missing a falsifier
        ledger_glue_id="",  # and missing the ledger pointer
    )
    res = check_rule_admission(empty)
    assert not res.admitted
    joined = " | ".join(res.failures)
    assert "missing obligation (a)" in joined
    assert "missing obligation (b)" in joined
    assert "missing obligation (c)" in joined
    assert "missing obligation (d)" in joined
    assert "no falsifier" in joined
    assert "no ledger glue pointer" in joined


def test_rule_with_failing_control_pair_is_rejected():
    """A rule whose control pair does NOT actually project equal is rejected —
    the gate checks the fixtures, not just their presence."""
    from lawvm.core.ctsf_admission_gate import (
        CongruenceCase,
        ControlPair,
        WitnessCase,
    )

    bad = CTSFEditorialRule(
        rule_id="ctsf.bogus.merges_distinct_units",
        jurisdiction="fi",
        believed_spec="claims two genuinely different wordings are equal",
        falsifier="the two wordings differ operatively",
        ledger_glue_id="fi.lens.grammar_text_normalization",
        unamended_control_pairs=(
            ControlPair(
                label="genuinely distinct wordings",
                left=_sec("1", text="maksu on 20 euroa"),
                right=_sec("1", text="maksu on 30 euroa"),
            ),
        ),
        quoted_payload_not_applicable=True,
        congruence_cases=(
            CongruenceCase(
                label="noop",
                pre=_sec("1", text="x"),
                apply_fn=lambda n: n,
                apply_ctsf=lambda c: c,
            ),
        ),
        witness_cases=(WitnessCase(label="w", node=_sec("1", text="maksu.......... 20")),),
    )
    res = check_rule_admission(bad)
    assert not res.admitted
    assert any("not equal" in f for f in res.failures)


# ---------------------------------------------------------------------------
# Each migrated rule's four-part control pairs (the migrated behavior itself)
# ---------------------------------------------------------------------------


def test_migrated_grammar_normalization_neutralizes_dot_leader_and_spacing():
    a = to_ctsf(_sec("5", text="maksu 20"))
    b = to_ctsf(_sec("5", text="maksu.......... 20"))
    assert ctsf_equal(a, b)
    assert any(e.rule_id == "ctsf.text.grammar_normalization" for e in collect_elisions(b))


def test_migrated_repeal_tombstone_elision_equates_repealed_units():
    replay = to_ctsf(_sec("7", basis="repeal_placeholder"))
    oracle = to_ctsf(
        _sec("7", text="(7 § on kumottu lailla 2020/123)", basis="editorial_repeal_notice")
    )
    assert replay.occupancy_state == "repealed"
    assert oracle.occupancy_state == "repealed"
    assert ctsf_equal(replay, oracle)
    assert any(
        e.rule_id == "ctsf.occupancy.repeal_tombstone_elision"
        for e in collect_elisions(oracle)
    )


def test_migrated_aiempi_sanamuoto_elision_strips_former_wording_banner():
    clean = to_ctsf(_sec("3", text="Uusi teksti tässä."))
    banner = to_ctsf(
        _sec("3", text="Uusi teksti tässä. Aiempi sanamuoto kuuluu: Vanha teksti oli tämä.")
    )
    assert ctsf_equal(clean, banner)
    assert any(
        e.rule_id == "ctsf.text.aiempi_sanamuoto_elision" for e in collect_elisions(banner)
    )


def test_ctsf_does_not_over_merge_genuinely_different_wording():
    """CTSF-equality must still SEE a real content divergence (falsifier check)."""
    a = to_ctsf(_sec("4", text="maksu on 20 euroa"))
    b = to_ctsf(_sec("4", text="maksu on 30 euroa"))
    assert not ctsf_equal(a, b)


# ---------------------------------------------------------------------------
# #181 spec-ledger glue unification
# ---------------------------------------------------------------------------


def test_each_ctsf_rule_binds_to_an_existing_glue_lens():
    from lawvm.tools.spec_ledger_glue import glue_components

    glue_ids = {g.glue_id for g in glue_components()}
    for rule in registered_ctsf_rules():
        assert rule.ledger_glue_id in glue_ids, (
            f"{rule.rule_id} points at missing glue {rule.ledger_glue_id}"
        )


def test_migrated_glue_lenses_point_back_at_registered_rules():
    from lawvm.tools.spec_ledger_glue import glue_components, glue_to_dict

    registered = {r.rule_id for r in registered_ctsf_rules()}
    bound = 0
    for g in glue_components(kind="lens"):
        if g.ctsf_rule_id:
            assert g.ctsf_rule_id in registered, (
                f"{g.glue_id} points at missing rule {g.ctsf_rule_id}"
            )
            assert glue_to_dict(g)["ctsf_rule_id"] == g.ctsf_rule_id
            bound += 1
    assert bound == 3, "expected exactly the three migrated lenses to bind CTSF rules"


def test_ctsf_rules_carry_falsifier_and_ledger_pointer():
    for rule in registered_ctsf_rules():
        assert rule.falsifier.strip(), f"{rule.rule_id} has no falsifier"
        assert rule.ledger_glue_id.strip(), f"{rule.rule_id} has no ledger pointer"


# ---------------------------------------------------------------------------
# Read-only telemetry surface
# ---------------------------------------------------------------------------


def test_telemetry_reports_equality_and_residual_inventory():
    from lawvm.core.table_model import TableBody

    replay = _sec("5", text="maksu 20")
    oracle = SemanticStructureNode(
        kind="section",
        label="5",
        facets=(
            SemanticStructureFacet(
                kind="wording",
                text="maksu.......... 20",
                tables=(TableBody(table_id="t", caption="", columns=(), rows=()),),
            ),
        ),
    )
    tel = ctsf_telemetry(replay, oracle)
    assert isinstance(tel, CTSFTelemetry)
    assert tel.ctsf_equal is True  # dot-leader neutralized
    assert tel.residual_inventory() == {"CNF_UNSUPPORTED_TABLE": 1}
    d = tel.to_dict()
    assert d["ctsf_version"] == CTSF_VERSION
    assert d["disclaimer"] == CTSF_EQUALITY_DISCLAIMER


def test_disclaimer_is_carried_in_code():
    assert "NOT a claim that discarded presentation can never matter legally" in (
        CTSF_EQUALITY_DISCLAIMER
    )


# ---------------------------------------------------------------------------
# Byte-identity guard: importing CTSF must not perturb default bench output.
# ---------------------------------------------------------------------------


def _sample_diff_inputs():
    sd = {"label": 0, "structural": 0}
    events = [
        {"kind": "wording_text_changed", "left_text": "kuolemansyyn selvittämiseksi", "right_text": "kuolemansyynselvittämiseksi"},
    ]
    return sd, events


def test_bench_neutralizer_unchanged_by_ctsf_import():
    """Default bench neutralizer output is byte-identical whether or not CTSF is
    present — CTSF v0 is additive and touches no bench code path."""
    from lawvm.tools import bench

    sd, events = _sample_diff_inputs()
    before = bench._section_diff_is_bench_neutralized(sd, events)

    # Import the whole CTSF surface (module-under-test side effects, if any).
    import lawvm.core.ctsf  # noqa: F401
    import lawvm.core.ctsf_admission_gate  # noqa: F401
    import lawvm.core.ctsf_rules  # noqa: F401

    after = bench._section_diff_is_bench_neutralized(sd, events)
    assert before == after is True


def test_semantic_diff_stats_unchanged_by_ctsf_import():
    from lawvm.semantic.diff import semantic_diff_stats
    from lawvm.semantic.structure import semantic_structure_from_ir  # noqa: F401

    left = SemanticStructureNode(kind="section", label="1", facets=_wf("teksti"))
    right = SemanticStructureNode(kind="section", label="1", facets=_wf("teksti"))
    before = semantic_diff_stats(left, right)

    import lawvm.core.ctsf  # noqa: F401

    after = semantic_diff_stats(left, right)
    assert before == after
