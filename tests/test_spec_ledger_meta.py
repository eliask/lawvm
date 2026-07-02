"""Coverage + schema guards for the spec-ledger enrichment (#181):
S/P ``rule_role``, per-rule ``falsifier``, the P-rule firing-density heatmap, and the
first-classed ≺ (precedence policy) / ≈ (comparison lens) glue components.

All additive / read-only: no replay-path exercise. The neutral core is tested with
synthetic ``StatuteLedgerInput``s; the UK/EU coverage guards read the adapters' merged
catalogs; the glue catalog is checked for the versioning discipline.
"""
from __future__ import annotations

from typing import Dict

from lawvm.tools.spec_ledger import (
    RuleRole,
    StatuteLedgerInput,
    build_ledger,
    render_markdown,
)
from lawvm.tools.spec_ledger_glue import (
    GlueComponent,
    glue_components,
    glue_to_dict,
    render_glue_markdown,
)


# ---------------------------------------------------------------------------
# Neutral-core schema: rule_role + falsifier + role_counts + p_rule_density
# ---------------------------------------------------------------------------

_CATALOG = {"r.s": "an S hypothesis", "r.p": "a P policy"}
_ROLES: Dict[str, RuleRole] = {"r.s": "S", "r.p": "P"}
_FALSIFIERS = {"r.s": "oracle shows r.s wrong", "r.p": "r.p contradiction rate too high"}


def _enriched_ledger(firings):
    return build_ledger(
        [StatuteLedgerInput("a/1", firings, [])],
        jurisdiction="fi",
        mode="official_consolidation",
        catalog=_CATALOG,
        roles=_ROLES,
        falsifiers=_FALSIFIERS,
    )


def test_entry_carries_role_and_falsifier():
    led = _enriched_ledger({"r.s": 3, "r.p": 5})
    assert led.rules["r.s"].rule_role == "S"
    assert led.rules["r.p"].rule_role == "P"
    assert led.rules["r.p"].is_p_rule is True
    assert led.rules["r.s"].is_p_rule is False
    assert led.rules["r.s"].falsifier == "oracle shows r.s wrong"
    d = led.rules["r.p"].to_dict()
    assert d["rule_role"] == "P"
    assert d["falsifier"] == "r.p contradiction rate too high"


def test_role_defaults_to_s_when_unannotated():
    # An entry with a believed_spec but no role sidecar reads as S (the conservative
    # law-hypothesis default); the coverage guards are what force explicit annotation.
    led = build_ledger(
        [StatuteLedgerInput("a/1", {"r.x": 1}, [])],
        jurisdiction="fi",
        mode="official_consolidation",
        catalog={"r.x": "spec"},
    )
    assert led.rules["r.x"].rule_role == "S"


def test_role_counts_partitions_s_p_uncataloged():
    led = _enriched_ledger({"r.s": 1, "r.p": 1, "r.uncat": 1})
    rc = led.role_counts()
    assert rc == {"S": 1, "P": 1, "uncataloged": 1}


def test_p_rule_density_ranks_by_firings_desc():
    roles: Dict[str, RuleRole] = {"r.p": "P", "r.p2": "P", "r.s": "S"}
    led = build_ledger(
        [
            StatuteLedgerInput(
                "a/1", {"r.p": 2, "r.p2": 9, "r.s": 100}, []
            )
        ],
        jurisdiction="fi",
        mode="official_consolidation",
        catalog={"r.p": "p", "r.p2": "p2", "r.s": "s"},
        roles=roles,
    )
    density = led.p_rule_density()
    # Only P-rules; the high-firing S-rule is excluded; ranked firings-desc.
    assert [e.rule_id for e in density] == ["r.p2", "r.p"]


def test_render_markdown_shows_role_column_and_heatmap():
    led = _enriched_ledger({"r.s": 3, "r.p": 5})
    out = render_markdown(led)
    assert "S/P" in out
    assert "undiscovered-spec heatmap" in out
    assert "P-rules (compiler-survival policy)=1" in out


def test_to_dict_carries_role_counts_and_density():
    led = _enriched_ledger({"r.s": 1, "r.p": 4})
    d = led.to_dict()
    assert d["role_counts"]["P"] == 1
    assert d["p_rule_density"] == [
        {"rule_id": "r.p", "firings": 4, "contradicted": 0}
    ]


def test_uncataloged_rule_has_no_sp_in_render():
    # An uncatalogued row's S/P sort is undefined and renders as "·", not a fake S.
    led = _enriched_ledger({"r.uncat": 1})
    out = render_markdown(led)
    line = next(ln for ln in out.splitlines() if ln.startswith("| r.uncat "))
    cells = [c.strip() for c in line.split("|")]
    # cells: ['', 'r.uncat', cat, sort, ...]
    assert cells[2] == "·"  # cat
    assert cells[3] == "·"  # S/P undefined for uncatalogued


# ---------------------------------------------------------------------------
# UK coverage guard (classifier-derived roles + templated falsifiers)
# ---------------------------------------------------------------------------

def test_uk_every_cataloged_rule_has_role_and_falsifier():
    from lawvm.uk_legislation.spec_ledger_adapter import (
        _UK_RULE_FALSIFIERS,
        _UK_RULE_ROLES,
        _UK_RULE_SPECS,
    )

    cat = set(_UK_RULE_SPECS)
    assert cat, "UK catalog is empty"
    assert not (cat - set(_UK_RULE_ROLES)), "UK rules missing a role"
    assert not (cat - set(_UK_RULE_FALSIFIERS)), "UK rules missing a falsifier"
    assert not (set(_UK_RULE_ROLES) - cat), "dead UK role entries"
    assert all(v in ("S", "P") for v in _UK_RULE_ROLES.values())
    assert all(v.strip() for v in _UK_RULE_FALSIFIERS.values())
    # Both sorts must be populated (the partition is real).
    roles = set(_UK_RULE_ROLES.values())
    assert roles == {"S", "P"}


def test_uk_manual_frontier_and_replay_are_p_rules():
    from lawvm.tools.spec_ledger_uk_catalog_meta import classify_uk_rule_role

    assert classify_uk_rule_role("uk_manual_frontier_unclassified") == "P"
    assert classify_uk_rule_role("uk_replay_schedule_list_entry_repeal_unresolved") == "P"
    assert classify_uk_rule_role("uk_oracle_retain_text_repeal_elided") == "P"
    # A plain amendment-grammar substitution is a law-hypothesis (S).
    assert classify_uk_rule_role("uk_effect_bare_quoted_substitution_text_patch") == "S"


# ---------------------------------------------------------------------------
# EU coverage guard
# ---------------------------------------------------------------------------

def test_eu_every_cataloged_rule_has_role_and_falsifier():
    from lawvm.eu.spec_ledger_adapter import (
        _EU_RULE_FALSIFIERS,
        _EU_RULE_ROLES,
        _EU_RULE_SPECS,
    )

    cat = set(_EU_RULE_SPECS)
    assert cat, "EU catalog is empty"
    assert not (cat - set(_EU_RULE_ROLES)), "EU rules missing a role"
    assert not (cat - set(_EU_RULE_FALSIFIERS)), "EU rules missing a falsifier"
    assert all(v in ("S", "P") for v in _EU_RULE_ROLES.values())
    # EU catalog is dominated by compiler/acquisition-survival diagnostics (P).
    assert _EU_RULE_ROLES["eu_amending_act_authorizes_apply"] == "S"
    assert _EU_RULE_ROLES["eu_replay_target_not_found"] == "P"


# ---------------------------------------------------------------------------
# ≺ precedence + ≈ lens glue components (first-classed, versioned)
# ---------------------------------------------------------------------------

def test_glue_has_precedence_and_lens_components():
    prec = glue_components(kind="precedence")
    lens = glue_components(kind="lens")
    assert prec and lens
    assert all(isinstance(g, GlueComponent) for g in prec + lens)
    assert {g.kind for g in prec} == {"precedence"}
    assert {g.kind for g in lens} == {"lens"}


def test_every_glue_component_is_versioned_and_falsifiable():
    for g in glue_components():
        assert g.version, f"{g.glue_id} has no version (immunizing-stratagem guard)"
        assert g.believed_spec.strip(), f"{g.glue_id} has no believed_spec"
        assert g.falsifier.strip(), f"{g.glue_id} has no falsifier"
        assert g.code_anchor.strip(), f"{g.glue_id} has no code anchor"
        assert g.changelog, f"{g.glue_id} has no changelog line"


def test_glue_ids_are_unique():
    ids = [g.glue_id for g in glue_components()]
    assert len(ids) == len(set(ids))


def test_glue_filter_by_jurisdiction():
    uk = glue_components(jurisdiction="uk")
    assert uk
    assert all(g.jurisdiction == "uk" for g in uk)


def test_glue_to_dict_and_markdown_render():
    g = glue_components(kind="lens")[0]
    d = glue_to_dict(g)
    assert d["glue_id"] == g.glue_id
    assert d["version"] == g.version
    assert isinstance(d["changelog"], list)
    md = render_glue_markdown()
    assert "≺ Precedence" in md
    assert "≈ Comparison lens" in md
    # RetainText lens (the named editorial-elision equivalence) is catalogued.
    assert "retain_text_repeal_elision" in md
