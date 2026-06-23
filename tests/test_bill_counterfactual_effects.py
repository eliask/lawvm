"""Unit + witness tests for the counterfactual three-tier effects projection.

The tier-logic tests run on SYNTHETIC ``ParsedOp`` + synthetic
``LawvmInterlinkRow`` fixtures — there is NO corpus dependency.  The pure
``build_tier_*`` functions are exercised directly, and the assembled report is
checked for the architectural invariants (tiers separate, never conflated;
provenance on every tier-1/2 item; no score/magnitude keys).

One CORPUS-GATED witness test proves the capability end-to-end on a real Finnish
amendment (``2018/301`` — "Laki liikenteen palveluista annetun lain
muuttamisesta", which amends the Transport Services Act ``2017/320``): it asserts
tier 1 AND tier 2 are non-empty and correctly provenance-tagged.  It skips when
the Finland corpus is not present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.references.defined_terms import (
    BINDING_TARKOITETAAN,
    STATUS_OK,
    DefinedTermBinding,
)
from lawvm.finland.references.definition_graph import DefinitionEdge, DefinitionGraph
from lawvm.finland.references.term_use import STATUS_RESOLVED, RULE_MORPH, TermUse
from lawvm.tools import bill_counterfactual_effects as cf
from lawvm.tools.transition_graph_interlinks import LawvmInterlinkRow

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


# ---------------------------------------------------------------------------
# Synthetic fixture factories (corpus-free)
# ---------------------------------------------------------------------------


def _op(verb: str, number: str, *, chapter: str = "", momentti: int = 0, item: str = "") -> ParsedOp:
    return ParsedOp(
        verb=verb,
        kind="P",
        chapter=chapter,
        number=number,
        momentti=momentti,
        item=item,
        raw=f"{verb} P {number}",
    )


def _row(
    *,
    interlink_id: str,
    source_locator: str | None,
    target_locator: str | None,
    target_work_id: str | None,
    resolution_status: str,
    role: str = "cites",
) -> LawvmInterlinkRow:
    """Minimal synthetic interlink row carrying only the fields tier-2 reads."""
    return LawvmInterlinkRow(
        interlink_id=interlink_id,
        source_jurisdiction="fi",
        source_work_kind="normative_act",
        source_local_id="2017/320",
        source_work_id="fi:normative_act:2017/320",
        source_locator=source_locator,
        surface_text="",
        surface_kind="prose_ref",
        role=role,
        target_jurisdiction="fi",
        target_work_kind="normative_act",
        target_local_id="2017/320",
        target_work_id=target_work_id,
        target_locator=target_locator,
        target_url=None,
        candidate_work_ids=None,
        resolution_status=resolution_status,
        confidence="exact",
        resolver_id="fi.reference_mention",
        source_artifact_id=None,
        source_span_byte_offset=None,
        source_span_byte_len=None,
        rendered_statute_id=None,
        rendered_effective_date=None,
        rendered_address=None,
        rendered_segment_index=None,
        rendered_char_start=None,
        rendered_char_end=None,
        valid_at_start=None,
        valid_at_end=None,
        detail_json="{}",
    )


# ---------------------------------------------------------------------------
# Synthetic definition-graph fixtures (corpus-free) for the definition-user arm
# ---------------------------------------------------------------------------


def _binding(term: str, *, offset: int) -> DefinedTermBinding:
    """A minimal synthetic ``tarkoitetaan`` binding at a byte offset."""
    return DefinedTermBinding(
        term=term,
        target_ref=None,
        expansion="x",
        scope="statute",
        source_span=SourceSpan("fi", offset, len(term)),
        binding_kind=BINDING_TARKOITETAAN,
        status=STATUS_OK,
    )


def _use(surface: str, lemma: str, binding: DefinedTermBinding, *, offset: int) -> TermUse:
    """A minimal synthetic RESOLVED use of ``binding`` at a byte offset."""
    return TermUse(
        term_surface=surface,
        lemma=lemma,
        binding=binding,
        source_span=SourceSpan("fi", offset, len(surface)),
        status=STATUS_RESOLVED,
        rule_id=RULE_MORPH,
        bindings=(binding,),
    )


def _graph(edges: tuple[DefinitionEdge, ...]) -> DefinitionGraph:
    return DefinitionGraph(
        statute_id="2017/320",
        body_text="x",
        bindings=tuple(e.binding for e in edges),
        uses=tuple(e.use for e in edges),
        edges=edges,
    )


# ---------------------------------------------------------------------------
# TIER 1 — directly changed
# ---------------------------------------------------------------------------


def test_tier_1_projects_each_op_with_johtolause_provenance() -> None:
    ops = [_op("M", "5"), _op("K", "7"), _op("L", "9", momentti=1, item="3")]
    tier_1 = cf.build_tier_1_direct(ops)
    assert len(tier_1) == 3
    assert [d.verb_label for d in tier_1] == ["AMEND", "REPEAL", "INSERT"]
    assert [d.section for d in tier_1] == ["5", "7", "9"]
    # every tier-1 item carries johtolause provenance + the op-code as node id
    for d in tier_1:
        assert d.source == cf.SOURCE_JOHTOLAUSE_PARSE
        assert d.node_id == d.op_code
    assert tier_1[2].momentti == 1 and tier_1[2].item == "3"


# ---------------------------------------------------------------------------
# TIER 2 — changed via references (back-references)
# ---------------------------------------------------------------------------


def test_tier_2_traces_only_internal_resolved_backrefs_to_changed_sections() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5"), _op("K", "7")])
    rows = [
        # internal resolved cite -> changed §5 : a tier-2 hit
        _row(
            interlink_id="r1",
            source_locator="section:12",
            target_locator="section:5",
            target_work_id="fi:normative_act:2017/320",
            resolution_status="resolved",
        ),
        # internal cite -> §99 (NOT changed) : excluded
        _row(
            interlink_id="r2",
            source_locator="section:12",
            target_locator="section:99",
            target_work_id="fi:normative_act:2017/320",
            resolution_status="resolved",
        ),
        # cross-act cite -> §5 of a DIFFERENT act : excluded (not internal)
        _row(
            interlink_id="r3",
            source_locator="section:12",
            target_locator="section:5",
            target_work_id="fi:normative_act:1999/442",
            resolution_status="resolved",
        ),
        # untraceable status (open) -> §7 : excluded, never guessed
        _row(
            interlink_id="r4",
            source_locator="section:20",
            target_locator="section:7",
            target_work_id=None,
            resolution_status="open",
        ),
    ]
    tier_2 = cf.build_tier_2_citing_provisions(direct, "2017/320", rows)
    assert len(tier_2) == 1
    hit = tier_2[0]
    assert hit.citing_section == "12"
    assert hit.cited_section == "5"
    assert hit.resolution_status == "resolved"
    assert hit.source == cf.SOURCE_INTERLINK_GRAPH
    assert hit.node_id == "r1"


def test_tier_2_unchanged_status_is_traceable() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    rows = [
        _row(
            interlink_id="r1",
            source_locator="section:8",
            target_locator="section:5",
            target_work_id="fi:normative_act:2017/320",
            resolution_status="unchanged",
        )
    ]
    tier_2 = cf.build_tier_2_citing_provisions(direct, "2017/320", rows)
    assert len(tier_2) == 1 and tier_2[0].resolution_status == "unchanged"


def test_tier_2_empty_when_amended_act_unknown() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    rows = [
        _row(
            interlink_id="r1",
            source_locator="section:8",
            target_locator="section:5",
            target_work_id="fi:normative_act:2017/320",
            resolution_status="resolved",
        )
    ]
    # unknown parent => no scope => report nothing (declared in tier 3, not guessed)
    assert cf.build_tier_2_citing_provisions(direct, "", rows) == ()


def test_tier_2_dedupes_identical_backrefs() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    dupe = dict(
        source_locator="section:8",
        target_locator="section:5",
        target_work_id="fi:normative_act:2017/320",
        resolution_status="resolved",
    )
    rows = [_row(interlink_id="r1", **dupe), _row(interlink_id="r1", **dupe)]
    tier_2 = cf.build_tier_2_citing_provisions(direct, "2017/320", rows)
    assert len(tier_2) == 1


# ---------------------------------------------------------------------------
# TIER 2 — definition-users (a term defined in a changed § used elsewhere)
# ---------------------------------------------------------------------------


def test_definition_user_traced_when_defining_section_changed() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    # term defined at offset 10 (in §5), used at offset 100 (in §12)
    b = _binding("sivutuote", offset=10)
    u = _use("sivutuotteen", "sivutuote", b, offset=100)
    graph = _graph((DefinitionEdge(binding=b, use=u),))
    # crosswalk: §5 spans [0,50), §12 spans [50,200)
    crosswalk = [(0, 50, "5"), (50, 200, "12")]
    out = cf.build_tier_2_definition_users(direct, graph, crosswalk)
    assert len(out) == 1
    e = out[0]
    assert e.defining_section == "5"
    assert e.using_section == "12"
    assert e.term == "sivutuote"
    assert e.use_surface == "sivutuotteen"
    assert e.source == cf.SOURCE_DEFINITION_GRAPH
    assert e.node_id == "def:5:sivutuote->use:12"


def test_definition_user_excluded_when_defining_section_unchanged() -> None:
    direct = cf.build_tier_1_direct([_op("M", "9")])  # §9 changed, def is in §5
    b = _binding("sivutuote", offset=10)
    u = _use("sivutuotteen", "sivutuote", b, offset=100)
    graph = _graph((DefinitionEdge(binding=b, use=u),))
    crosswalk = [(0, 50, "5"), (50, 200, "12")]
    assert cf.build_tier_2_definition_users(direct, graph, crosswalk) == ()


def test_definition_user_excluded_when_use_in_same_section_as_definition() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    b = _binding("sivutuote", offset=10)
    # use at offset 30 is STILL inside §5 (the changed/defining section itself)
    u = _use("sivutuotteen", "sivutuote", b, offset=30)
    graph = _graph((DefinitionEdge(binding=b, use=u),))
    crosswalk = [(0, 50, "5"), (50, 200, "12")]
    assert cf.build_tier_2_definition_users(direct, graph, crosswalk) == ()


def test_definition_user_dedupes_and_sorts() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    b = _binding("sivutuote", offset=10)
    u1 = _use("sivutuotteen", "sivutuote", b, offset=100)
    u2 = _use("sivutuotteen", "sivutuote", b, offset=110)  # same section, same surface
    u3 = _use("sivutuotetta", "sivutuote", b, offset=160)  # distinct surface
    graph = _graph(
        (
            DefinitionEdge(binding=b, use=u1),
            DefinitionEdge(binding=b, use=u2),
            DefinitionEdge(binding=b, use=u3),
        )
    )
    crosswalk = [(0, 50, "5"), (50, 200, "12")]
    out = cf.build_tier_2_definition_users(direct, graph, crosswalk)
    # (5,12,sivutuote,sivutuotteen) deduped; distinct surface kept
    assert len(out) == 2
    assert [e.use_surface for e in out] == ["sivutuotetta", "sivutuotteen"]


def test_definition_user_empty_when_no_graph_inputs() -> None:
    direct = cf.build_tier_1_direct([_op("M", "5")])
    assert cf.build_tier_2_definition_users(direct, _graph(()), [(0, 50, "5")]) == ()


def test_section_label_at_returns_empty_outside_any_span() -> None:
    crosswalk = [(0, 50, "5"), (50, 200, "12")]
    assert cf._section_label_at(crosswalk, 25) == "5"
    assert cf._section_label_at(crosswalk, 100) == "12"
    assert cf._section_label_at(crosswalk, 500) == ""  # past the last span
    assert cf._section_label_at([], 10) == ""


# ---------------------------------------------------------------------------
# TIER 3 — declared boundary
# ---------------------------------------------------------------------------


def test_tier_3_declares_effect_classes_and_counts_untraceable() -> None:
    rows = [
        # an untraceable section cite -> surfaced in the resolution limits
        _row(
            interlink_id="r1",
            source_locator="section:8",
            target_locator="section:5",
            target_work_id=None,
            resolution_status="external_only",
        ),
        # a resolved one -> NOT counted as untraceable
        _row(
            interlink_id="r2",
            source_locator="section:8",
            target_locator="section:6",
            target_work_id="fi:normative_act:2017/320",
            resolution_status="resolved",
        ),
    ]
    boundary = cf.build_tier_3_boundary(rows)
    assert boundary.statement.startswith("DECLARED BOUNDARY")
    # the five mandated classes + the precision class are all declared
    joined = " ".join(boundary.effect_classes).lower()
    for needle in (
        "semantic",
        "multi-hop",
        "temporal",
        "institutional",
        "transposition",
        "granularity",
    ):
        assert needle in joined
    assert boundary.resolution_limits["untraceable_by_status"] == {"external_only": 1}
    assert boundary.resolution_limits["total_untraceable"] == 1
    # the definition-user sub-tier is now COMPUTED; tier 3 declares only its
    # NARROWER residual (transitive chains, cross-act, open/ambiguous), not a
    # blanket "deferred".
    joined_def = " ".join(boundary.deferred).lower()
    assert "definition-user residual" in joined_def
    assert "transitive" in joined_def
    assert "cross-act" in joined_def


# ---------------------------------------------------------------------------
# Assembled report — architectural invariants
# ---------------------------------------------------------------------------


def test_report_keeps_tiers_separate_and_carries_no_score() -> None:
    ops = [_op("M", "5"), _op("K", "7")]
    rows = [
        _row(
            interlink_id="r1",
            source_locator="section:12",
            target_locator="section:5",
            target_work_id="fi:normative_act:2017/320",
            resolution_status="resolved",
        )
    ]
    # definition-user input: a term defined in changed §5, used in §12
    b = _binding("sivutuote", offset=10)
    u = _use("sivutuotteen", "sivutuote", b, offset=100)
    graph = _graph((DefinitionEdge(binding=b, use=u),))
    crosswalk = [(0, 50, "5"), (50, 200, "12")]
    report = cf.build_counterfactual_report(
        "2018/301",
        "2017/320",
        ops,
        rows,
        definition_graph=graph,
        section_crosswalk=crosswalk,
    )
    d = cf.report_to_dict(report)
    # three distinct tier fields, never merged
    assert set(d) == {
        "amendment_id",
        "amended_act_id",
        "tier_1_direct",
        "tier_2_via_defs_and_refs",
        "tier_3_uncomputed_second_order",
    }
    assert len(d["tier_1_direct"]) == 2
    # the two tier-2 sub-tiers are computed AND kept structurally distinct (BRANCH-06)
    citing = d["tier_2_via_defs_and_refs"]["citing_provisions"]
    defusers = d["tier_2_via_defs_and_refs"]["definition_users"]
    assert len(citing) == 1
    assert len(defusers) == 1
    assert defusers[0]["defining_section"] == "5"
    assert defusers[0]["using_section"] == "12"
    assert defusers[0]["provenance"]["source"] == cf.SOURCE_DEFINITION_GRAPH
    # the two sub-tiers carry DIFFERENT field shapes — never merged into one list
    assert "citing_section" in citing[0] and "term" in defusers[0]
    # NO score / magnitude / severity DATA keys on any tier-1/tier-2 item (the
    # tier-3 prose legitimately disclaims "no score, no magnitude"; the discipline
    # is that items carry no scoring FIELD).
    forbidden = {"score", "magnitude", "severity", "rank", "weight"}
    items = d["tier_1_direct"] + citing + defusers
    for item in items:
        assert forbidden.isdisjoint(item.keys())
        assert forbidden.isdisjoint(item["provenance"].keys())
    # render runs and keeps the three tiers visually separate
    text = cf.render_report(report)
    assert "TIER 1 — DIRECTLY CHANGED" in text
    assert "TIER 2 — CHANGED VIA REFERENCES" in text
    assert "TIER 3 — UNCOMPUTED SECOND-ORDER" in text
    assert "uses term 'sivutuote'" in text


def test_report_definition_users_empty_without_graph_inputs() -> None:
    # backward-compatible call (no graph / crosswalk) -> empty definition-users,
    # the declared-limit behaviour for an unresolvable amended act.
    ops = [_op("M", "5")]
    report = cf.build_counterfactual_report("2018/301", "2017/320", ops, [])
    assert report.tier_2_via_defs_and_refs["definition_users"] == ()


# ---------------------------------------------------------------------------
# CORPUS-GATED witness — end-to-end proof on a real amendment
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_witness_2018_301_has_nonempty_tier_1_and_tier_2() -> None:
    """Real witness: 2018/301 amends the Transport Services Act 2017/320.

    Proves the capability end-to-end — a real multi-section amendment yields a
    non-empty tier 1 (directly-changed provisions) AND a non-empty tier 2
    (provisions in the amended act that cite a changed §), both correctly
    provenance-tagged, with the amended act resolved from the parent map.
    """
    report = cf._build_report_from_corpus("2018/301")

    assert report.amendment_id == "2018/301"
    assert report.amended_act_id == "2017/320"

    # TIER 1 non-empty + every item johtolause-provenanced
    assert len(report.tier_1_direct) > 0
    for d in report.tier_1_direct:
        assert d.source == cf.SOURCE_JOHTOLAUSE_PARSE
        assert d.node_id  # op-code present

    # TIER 2 non-empty + every item interlink-provenanced, traceable-only,
    # and citing a section that tier 1 actually changed
    citing = report.tier_2_via_defs_and_refs["citing_provisions"]
    assert len(citing) > 0
    changed = {d.section.strip() for d in report.tier_1_direct if d.section.strip()}
    for c in citing:
        assert c.source == cf.SOURCE_INTERLINK_GRAPH
        assert c.node_id
        assert c.resolution_status in {"resolved", "unchanged"}
        assert c.cited_section in changed

    # tier 3 boundary present + self-evidencing about untraceable would-be effects
    boundary = report.tier_3_uncomputed_second_order
    assert boundary.statement
    assert "total_untraceable" in boundary.resolution_limits


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_witness_2021_1244_has_definition_users_with_provenance() -> None:
    """Real witness for the definition-user arm: 2021/1244 amends 2017/320.

    2021/1244 changes §127 and §130 of the Transport Services Act. §127 DEFINES
    ``lentotoiminta-asetus`` (used in §266); §130 DEFINES ``lentomiehistöasetus``
    (used in §135 / §195 / §207).  The definition-user sub-tier must therefore be
    non-empty, with each effect provenance-tagged ``definition_graph`` and pointing
    from a tier-1-changed defining section to a DIFFERENT using section.
    """
    report = cf._build_report_from_corpus("2021/1244")
    assert report.amended_act_id == "2017/320"

    changed = {d.section.strip() for d in report.tier_1_direct if d.section.strip()}
    defusers = report.tier_2_via_defs_and_refs["definition_users"]
    assert len(defusers) >= 1
    defining_sections = set()
    for u in defusers:
        assert u.source == cf.SOURCE_DEFINITION_GRAPH
        assert u.node_id
        assert u.term
        assert u.defining_section in changed
        assert u.using_section and u.using_section != u.defining_section
        defining_sections.add(u.defining_section)
    # the §130 definer (lentomiehistöasetus) is among the witnessed effects
    assert "130" in defining_sections
    # citing and definition-user sub-tiers stay STRUCTURALLY DISTINCT (BRANCH-06)
    citing = report.tier_2_via_defs_and_refs["citing_provisions"]
    assert type(citing) is tuple and type(defusers) is tuple
    assert all(isinstance(c, cf.CitingProvisionEffect) for c in citing)
    assert all(isinstance(u, cf.DefinitionUserEffect) for u in defusers)
