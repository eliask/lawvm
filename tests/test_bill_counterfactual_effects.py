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

from lawvm.finland.johtolause.types import ParsedOp
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
    # the deferred definition-user sub-tier is declared, not silent
    assert any("definition-user" in d for d in boundary.deferred)


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
    report = cf.build_counterfactual_report("2018/301", "2017/320", ops, rows)
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
    assert len(d["tier_2_via_defs_and_refs"]["citing_provisions"]) == 1
    assert d["tier_2_via_defs_and_refs"]["definition_users"] == []  # deferred
    # NO score / magnitude / severity DATA keys on any tier-1/tier-2 item (the
    # tier-3 prose legitimately disclaims "no score, no magnitude"; the discipline
    # is that items carry no scoring FIELD).
    forbidden = {"score", "magnitude", "severity", "rank", "weight"}
    items = d["tier_1_direct"] + d["tier_2_via_defs_and_refs"]["citing_provisions"]
    for item in items:
        assert forbidden.isdisjoint(item.keys())
        assert forbidden.isdisjoint(item["provenance"].keys())
    # render runs and keeps the three tiers visually separate
    text = cf.render_report(report)
    assert "TIER 1 — DIRECTLY CHANGED" in text
    assert "TIER 2 — CHANGED VIA REFERENCES" in text
    assert "TIER 3 — UNCOMPUTED SECOND-ORDER" in text


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

    # the deferred definition-user sub-tier is empty (declared in tier 3)
    assert report.tier_2_via_defs_and_refs["definition_users"] == ()

    # tier 3 boundary present + self-evidencing about untraceable would-be effects
    boundary = report.tier_3_uncomputed_second_order
    assert boundary.statement
    assert "total_untraceable" in boundary.resolution_limits
