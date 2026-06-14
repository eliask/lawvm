"""§23 UK cross-statute reference/delegation graph — read-only instrumentation.

These tests exercise the standalone edge-extraction surface on synthetic
``UKEffectRecord`` rows (no archive needed): relation typing across the
taxonomy, edge construction/orientation, deterministic canonical ordering
(run twice → identical), dangling-target detection against a known base
corpus, the N4 deixis unresolved-target flag, delegation-depth aggregation,
and the diffable JSON report envelope.
"""
from __future__ import annotations

import json

from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.tools.uk_cross_statute_graph import (
    RELATION_AMENDS,
    RELATION_APPLIES_BY_REFERENCE,
    RELATION_COMMENCES,
    RELATION_CONFERS_POWER,
    RELATION_MODIFIES,
    RELATION_ORDER,
    RELATION_REFERENCES,
    RELATION_REPEALS,
    CrossStatuteEdge,
    classify_uk_cross_statute_relation,
    cross_statute_graph_report_jsonable,
    cross_statute_graph_summary,
    edge_from_effect,
)


def _record(
    *,
    effect_id: str = "e",
    effect_type: str = "inserted",
    applied: bool = True,
    affected_uri: str = "",
    affected_class: str = "",
    affected_year: str = "2000",
    affected_number: str = "26",
    affected_provisions: str = "s. 1",
    affecting_uri: str = "http://www.legislation.gov.uk/id/ukpga/2007/15",
    affecting_class: str = "UnitedKingdomPublicGeneralAct",
    affecting_year: str = "2007",
    affecting_number: str = "15",
    affecting_provisions: str = "Sch. 13 para. 138",
    metadata_only: bool = False,
) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=applied,
        requires_applied=False,
        modified="2020-01-01",
        affected_uri=affected_uri,
        affected_class=affected_class,
        affected_year=affected_year,
        affected_number=affected_number,
        affected_provisions=affected_provisions,
        affecting_uri=affecting_uri,
        affecting_class=affecting_class,
        affecting_year=affecting_year,
        affecting_number=affecting_number,
        affecting_provisions=affecting_provisions,
        affecting_title="",
        metadata_only=metadata_only,
    )


class TestRelationTyping:
    def test_structural_inserts_substitutes_are_amends(self) -> None:
        for effect_type in ("inserted", "words substituted", "word added", "substituted"):
            assert classify_uk_cross_statute_relation(effect_type) == RELATION_AMENDS

    def test_repeals_and_omissions_are_repeals(self) -> None:
        for effect_type in ("repealed", "words omitted", "repealed in part", "revoked"):
            assert classify_uk_cross_statute_relation(effect_type) == RELATION_REPEALS

    def test_repealed_by_prefix_is_repeals(self) -> None:
        assert classify_uk_cross_statute_relation("repealed by 2020 c. 1") == RELATION_REPEALS

    def test_commencement_family_is_commences(self) -> None:
        for effect_type in ("coming into force", "Commencement Order", "appointed day(s)"):
            assert classify_uk_cross_statute_relation(effect_type) == RELATION_COMMENCES

    def test_applied_by_is_applies_by_reference(self) -> None:
        assert (
            classify_uk_cross_statute_relation("applied by SSI 2005/467 reg. 33(2) (as inserted)")
            == RELATION_APPLIES_BY_REFERENCE
        )

    def test_transfer_of_functions_is_confers_power(self) -> None:
        assert (
            classify_uk_cross_statute_relation("transfer of functions")
            == RELATION_CONFERS_POWER
        )

    def test_modification_family_is_modifies(self) -> None:
        for effect_type in ("modified", "applied with modifications", "extended"):
            assert classify_uk_cross_statute_relation(effect_type) == RELATION_MODIFIES

    def test_empty_type_is_amends_structural_shell(self) -> None:
        assert classify_uk_cross_statute_relation("") == RELATION_AMENDS

    def test_unclassified_falls_back_to_references(self) -> None:
        assert classify_uk_cross_statute_relation("see annotations") == RELATION_REFERENCES

    def test_classification_is_total_over_taxonomy(self) -> None:
        # Every classified relation is a member of the canonical order.
        sampled = [
            "inserted",
            "repealed",
            "coming into force",
            "applied by X",
            "transfer of functions",
            "modified",
            "see annotations",
        ]
        for effect_type in sampled:
            assert classify_uk_cross_statute_relation(effect_type) in RELATION_ORDER


class TestEdgeConstruction:
    def test_edge_orientation_source_is_affecting_target_is_affected(self) -> None:
        edge = edge_from_effect(_record(), affected_statute_id="ukpga/2000/26")
        # Source = affecting (the citing instrument); target = affected (cited).
        assert edge.source_statute == "ukpga/2007/15"
        assert edge.source_provision == "Sch. 13 para. 138"
        assert edge.target_statute == "ukpga/2000/26"
        assert edge.target_provision == "s. 1"
        assert edge.relation == RELATION_AMENDS

    def test_target_statute_prefers_affected_uri_slug(self) -> None:
        edge = edge_from_effect(
            _record(affected_uri="http://www.legislation.gov.uk/id/ukpga/1998/11"),
            affected_statute_id="ukpga/2000/26",
        )
        assert edge.target_statute == "ukpga/1998/11"

    def test_target_statute_falls_back_to_queried_statute(self) -> None:
        edge = edge_from_effect(
            _record(affected_uri="", affected_class="", affected_year="", affected_number=""),
            affected_statute_id="ukpga/2000/26",
        )
        assert edge.target_statute == "ukpga/2000/26"

    def test_metadata_only_counts_as_applied(self) -> None:
        edge = edge_from_effect(
            _record(applied=False, metadata_only=True),
            affected_statute_id="ukpga/2000/26",
        )
        assert edge.applied is True

    def test_deixis_application_target_flagged_unresolved(self) -> None:
        edge = edge_from_effect(
            _record(effect_type="applied by SSI 2005/467 reg. 33(2) (as inserted)"),
            affected_statute_id="asp/2003/13",
        )
        assert edge.relation == RELATION_APPLIES_BY_REFERENCE
        assert edge.deictic_target_unresolved is True

    def test_plain_application_not_flagged_deictic(self) -> None:
        edge = edge_from_effect(
            _record(effect_type="applied by SI 1999/1 reg. 4"),
            affected_statute_id="asp/2003/13",
        )
        assert edge.relation == RELATION_APPLIES_BY_REFERENCE
        assert edge.deictic_target_unresolved is False


class TestDanglingTargets:
    def test_target_present_in_base_is_not_dangling(self) -> None:
        edge = edge_from_effect(
            _record(),
            affected_statute_id="ukpga/2000/26",
            base_statute_ids={"ukpga/2000/26"},
        )
        assert edge.target_in_base is True

    def test_target_absent_from_base_is_dangling(self) -> None:
        edge = edge_from_effect(
            _record(affected_uri="http://www.legislation.gov.uk/id/ukpga/1970/9"),
            affected_statute_id="ukpga/2000/26",
            base_statute_ids={"ukpga/2000/26"},
        )
        assert edge.target_in_base is False

    def test_summary_reports_dangling_statutes(self) -> None:
        edges = (
            edge_from_effect(
                _record(affected_uri="http://www.legislation.gov.uk/id/ukpga/1970/9"),
                affected_statute_id="ukpga/2000/26",
                base_statute_ids={"ukpga/2000/26"},
            ),
        )
        summary = cross_statute_graph_summary(edges)
        assert summary["dangling_target"]["edge_count"] == 1
        assert summary["dangling_target"]["statutes"] == ["ukpga/1970/9"]


class TestDeterministicOrdering:
    def _mixed_edges(self) -> tuple[CrossStatuteEdge, ...]:
        records = [
            _record(effect_id="e3", effect_type="repealed", affecting_provisions="s. 9"),
            _record(effect_id="e1", effect_type="inserted", affecting_provisions="s. 2"),
            _record(effect_id="e2", effect_type="inserted", affecting_provisions="s. 1"),
            _record(effect_id="e4", effect_type="coming into force", affecting_provisions="s. 5"),
        ]
        return tuple(
            edge_from_effect(rec, affected_statute_id="ukpga/2000/26") for rec in records
        )

    def test_sort_is_stable_run_twice(self) -> None:
        edges = self._mixed_edges()
        first = sorted(edges, key=lambda e: e.sort_key)
        second = sorted(tuple(reversed(edges)), key=lambda e: e.sort_key)
        assert [e.sort_key for e in first] == [e.sort_key for e in second]

    def test_relation_is_primary_sort_key(self) -> None:
        ordered = sorted(self._mixed_edges(), key=lambda e: e.sort_key)
        relations = [e.relation for e in ordered]
        # amends < commences < repeals lexically as relation strings.
        assert relations == sorted(relations)

    def test_report_json_is_byte_identical_across_runs(self) -> None:
        edges = self._mixed_edges()
        base = {"ukpga/2000/26"}
        report_a = cross_statute_graph_report_jsonable(
            statute_ids=("ukpga/2000/26",),
            edges=edges,
            archive_path="/tmp/x.farchive",
            base_statute_ids=base,
        )
        report_b = cross_statute_graph_report_jsonable(
            statute_ids=("ukpga/2000/26",),
            edges=tuple(reversed(edges)),
            archive_path="/tmp/x.farchive",
            base_statute_ids=base,
        )
        dumped_a = json.dumps(report_a, sort_keys=True, ensure_ascii=False)
        dumped_b = json.dumps(report_b, sort_keys=True, ensure_ascii=False)
        assert dumped_a == dumped_b

    def test_report_json_is_invariant_to_statute_input_order(self) -> None:
        edges = self._mixed_edges()
        base = {"ukpga/2000/26"}
        report_a = cross_statute_graph_report_jsonable(
            statute_ids=("ukpga/2000/26", "asp/2003/13"),
            edges=edges,
            archive_path="/tmp/x.farchive",
            base_statute_ids=base,
        )
        report_b = cross_statute_graph_report_jsonable(
            statute_ids=("asp/2003/13", "ukpga/2000/26"),
            edges=edges,
            archive_path="/tmp/x.farchive",
            base_statute_ids=base,
        )
        # The statute_ids header is canonicalized (sorted/deduped) so the whole
        # artifact is byte-identical regardless of supplied order.
        assert report_a["statute_ids"] == ["asp/2003/13", "ukpga/2000/26"]
        assert json.dumps(report_a, sort_keys=True) == json.dumps(report_b, sort_keys=True)


class TestSummaryStatistics:
    def test_edge_counts_by_relation_cover_full_taxonomy(self) -> None:
        edges = (
            edge_from_effect(_record(effect_type="inserted"), affected_statute_id="ukpga/2000/26"),
            edge_from_effect(_record(effect_type="repealed"), affected_statute_id="ukpga/2000/26"),
        )
        summary = cross_statute_graph_summary(edges)
        counts = summary["edge_counts_by_relation"]
        assert set(counts) == set(RELATION_ORDER)
        assert counts[RELATION_AMENDS] == 1
        assert counts[RELATION_REPEALS] == 1
        assert counts[RELATION_COMMENCES] == 0

    def test_delegation_depth_chains_application_edges(self) -> None:
        # a --applies--> b --applies--> c : a delegation chain of depth 2.
        edges = (
            CrossStatuteEdge(
                source_statute="a/1/1",
                source_provision="s. 1",
                target_statute="b/1/1",
                target_provision="s. 1",
                relation=RELATION_APPLIES_BY_REFERENCE,
                effect_id="e1",
                effect_type="applied by b",
                applied=True,
                deictic_target_unresolved=False,
                target_in_base=True,
            ),
            CrossStatuteEdge(
                source_statute="b/1/1",
                source_provision="s. 1",
                target_statute="c/1/1",
                target_provision="s. 1",
                relation=RELATION_APPLIES_BY_REFERENCE,
                effect_id="e2",
                effect_type="applied by c",
                applied=True,
                deictic_target_unresolved=False,
                target_in_base=True,
            ),
        )
        depth = cross_statute_graph_summary(edges)["delegation_depth"]
        assert depth["max_delegation_depth"] == 2
        assert depth["delegation_nodes"] == 3
        assert depth["delegation_edge_count"] == 2

    def test_delegation_depth_ignores_pure_amend_edges(self) -> None:
        edges = (
            edge_from_effect(_record(effect_type="inserted"), affected_statute_id="ukpga/2000/26"),
        )
        depth = cross_statute_graph_summary(edges)["delegation_depth"]
        assert depth["max_delegation_depth"] == 0
        assert depth["delegation_edge_count"] == 0

    def test_delegation_depth_terminates_on_cycle(self) -> None:
        edges = (
            CrossStatuteEdge(
                source_statute="a/1/1",
                source_provision="s. 1",
                target_statute="b/1/1",
                target_provision="s. 1",
                relation=RELATION_CONFERS_POWER,
                effect_id="e1",
                effect_type="transfer of functions",
                applied=True,
                deictic_target_unresolved=False,
                target_in_base=True,
            ),
            CrossStatuteEdge(
                source_statute="b/1/1",
                source_provision="s. 1",
                target_statute="a/1/1",
                target_provision="s. 1",
                relation=RELATION_CONFERS_POWER,
                effect_id="e2",
                effect_type="transfer of functions",
                applied=True,
                deictic_target_unresolved=False,
                target_in_base=True,
            ),
        )
        depth = cross_statute_graph_summary(edges)["delegation_depth"]
        # Cycle must not blow up; longest acyclic path is 1.
        assert depth["max_delegation_depth"] == 1


class TestReportEnvelope:
    def test_report_is_read_only_evidence_surface(self) -> None:
        report = cross_statute_graph_report_jsonable(
            statute_ids=("ukpga/2000/26",),
            edges=(edge_from_effect(_record(), affected_statute_id="ukpga/2000/26"),),
            archive_path="/tmp/x.farchive",
            base_statute_ids={"ukpga/2000/26"},
        )
        assert report["jurisdiction"] == "uk"
        assert report["report_kind"] == "uk_cross_statute_graph"
        assert report["replay_claims"] is False
        assert report["canonical_effect_claims"] is False
        assert report["relation_taxonomy"] == list(RELATION_ORDER)
        assert report["summary"]["edge_count"] == 1
        assert len(report["rows"]) == 1

    def test_summary_only_report_omits_rows(self) -> None:
        report = cross_statute_graph_report_jsonable(
            statute_ids=("ukpga/2000/26",),
            edges=(edge_from_effect(_record(), affected_statute_id="ukpga/2000/26"),),
            archive_path="/tmp/x.farchive",
            base_statute_ids={"ukpga/2000/26"},
            summary_only=True,
        )
        assert report["rows"] == []
        assert report["summary"]["edge_count"] == 1
