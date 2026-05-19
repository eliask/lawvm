from __future__ import annotations

import hashlib
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.timeline import ConsistencyDivergence
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.tools import cli, ee_replay, no_replay, uk_replay
from lawvm.tools.replay_payloads import build_uk_replay_payload


def test_cli_parser_accepts_generic_replay_json() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "replay",
            "-j",
            "ee",
            "193936",
            "--as-of",
            "2011-03-17",
            "--json",
        ]
    )

    assert args.command == "replay"
    assert args.jurisdiction == "ee"
    assert args.base_id == "193936"
    assert args.json is True


def test_cli_parser_accepts_generic_uk_replay_regime_flags() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "replay",
            "-j",
            "uk",
            "ukpga/1998/42",
            "--as-of",
            "2020-01-01",
            "--json",
            "--source-first-candidate",
            "--archive",
            "data/custom-uk.farchive",
            "--replay-adjudication-samples",
            "uk_replay_text_match_missing",
            "--replay-adjudication-sample-limit",
            "3",
        ]
    )

    assert args.command == "replay"
    assert args.jurisdiction == "uk"
    assert args.base_id == "ukpga/1998/42"
    assert args.uk_source_first_candidate is True
    assert args.archive == "data/custom-uk.farchive"
    assert args.replay_adjudication_samples == ["uk_replay_text_match_missing"]
    assert args.replay_adjudication_sample_limit == 3


def test_cli_parser_accepts_uk_replay_json() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-replay",
            "ukpga/1998/42",
            "--json",
        ]
    )

    assert args.command == "uk-replay"
    assert args.statute_id == "ukpga/1998/42"
    assert args.json is True


def test_generic_replay_dispatch_maps_uk_archive_and_regime_flags(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_uk_replay_main(args: Namespace) -> None:
        seen["statute_id"] = args.statute_id
        seen["pit_date"] = args.pit_date
        seen["db"] = args.db
        seen["source_first"] = args.uk_source_first_candidate

    monkeypatch.setattr("lawvm.tools.uk_replay.main", fake_uk_replay_main)
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "replay",
            "-j",
            "uk",
            "ukpga/1998/42",
            "--as-of",
            "2020-01-01",
            "--archive",
            "data/custom-uk.farchive",
            "--source-first-candidate",
        ],
    )

    cli.main()

    assert seen == {
        "statute_id": "ukpga/1998/42",
        "pit_date": "2020-01-01",
        "db": "data/custom-uk.farchive",
        "source_first": True,
    }


def test_generic_replay_rejects_uk_regime_flags_for_non_uk(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "lawvm",
            "replay",
            "-j",
            "ee",
            "193936",
            "--as-of",
            "2011-03-17",
            "--source-first-candidate",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 2
    assert "UK replay regime flags on 'replay' are only supported with -j uk" in capsys.readouterr().err


def test_cli_parser_accepts_uk_replay_regime_flags() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "uk-replay",
            "ukpga/1998/42",
            "--json",
            "--source-first-candidate",
            "--replay-adjudication-samples",
            "uk_replay_text_match_missing",
            "--replay-adjudication-sample-limit",
            "2",
        ]
    )

    assert args.command == "uk-replay"
    assert args.uk_source_first_candidate is True
    assert args.replay_adjudication_samples == ["uk_replay_text_match_missing"]
    assert args.replay_adjudication_sample_limit == 2


def test_no_replay_main_emits_normalized_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.norway.replay.replay_no_to_pit",
        lambda **kwargs: SimpleNamespace(
            base_id="no/lov/2005-05-20-28",
            as_of="2026-03-29",
            base_title="Lov om straff",
            base_source_id="base-source",
            error="",
            amendments_scanned=["a1", "a2"],
            amendments_applied=["a1"],
            amendments_skipped_future=["a2"],
            amendments_skipped_contingent=[],
            amendments_skipped_unknown_effective=[],
            n_ops=12,
            replayed=None,
            adjudications=[
                CompileAdjudication(
                    kind="no_replay_missing_amendment_source",
                    message="Norway replay skipped amendment: source bytes not found.",
                    source_statute="a2",
                    op_id="",
                    detail={
                        "rule_id": "no_replay_missing_amendment_source",
                        "phase": "acquisition",
                    },
                )
            ],
        ),
    )
    monkeypatch.setattr("lawvm.norway.index.load_no_amendment_index", lambda path: None)

    no_replay.main(
        Namespace(
            base_id="no/lov/2005-05-20-28",
            as_of="2026-03-29",
            archive=None,
            index=None,
            commencement=None,
            verbose=False,
            show_text=False,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["jurisdiction"] == "no"
    assert payload["base_id"] == "no/lov/2005-05-20-28"
    assert payload["ops_count"] == 12
    assert payload["amendment_counts"]["matched"] == 2
    assert payload["amendment_counts"]["applied"] == 1
    assert payload["oracle"]["available"] is False
    assert payload["adjudications_count"] == 1
    assert payload["adjudications"] == [
        {
            "kind": "no_replay_missing_amendment_source",
            "message": "Norway replay skipped amendment: source bytes not found.",
            "source_statute": "a2",
            "op_id": "",
            "detail": {
                "rule_id": "no_replay_missing_amendment_source",
                "phase": "acquisition",
            },
        }
    ]


def test_ee_replay_main_emits_normalized_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.estonia.replay.replay_ee_to_pit",
        lambda **kwargs: SimpleNamespace(
            base_id="193936",
            as_of="2011-03-17",
            base_title="Liiklusseadus",
            error="",
            grupi_id="grp",
            oracle_id="13336397",
            comparison_class="commensurable_delta",
            amendments_total=["a1"],
            amendments_applied=["a1"],
            amendments_skipped=[],
            amendments_failed=[],
            n_ops=217,
            oracle=object(),
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(
                        path=(("chapter", "1"), ("section", "6"), ("subsection", "2"))
                    ),
                    divergence_type="MISMATCH",
                    ops_text="replay text",
                    consolidated_text="oracle text",
                )
            ],
            n_mismatch=1,
            n_ops_missing=0,
            n_con_missing=0,
            adjudications=[
                SimpleNamespace(
                    kind="ee_replay_unsupported_action",
                    message="Replay skipped unsupported action",
                    op_id="op-1",
                    source_statute="a1",
                    detail={"rule_id": "ee_replay_unsupported_action", "phase": "replay"},
                ),
                SimpleNamespace(kind="ee_replay_unsupported_action"),
                SimpleNamespace(kind="ee_text_replace_ambiguous"),
            ],
            replayed=None,
            timelines={},
        ),
    )

    ee_replay.main(
        Namespace(
            base_id="193936",
            as_of="2011-03-17",
            archive=None,
            verbose=False,
            show_text=False,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["jurisdiction"] == "ee"
    assert payload["base_id"] == "193936"
    assert payload["oracle"]["available"] is True
    assert payload["oracle"]["comparison_class"] == "commensurable_delta"
    assert payload["consistency"]["divergence_count"] == 1
    assert payload["adjudications_count"] == 3
    assert payload["adjudication_kind_counts"] == {
        "ee_replay_unsupported_action": 2,
        "ee_text_replace_ambiguous": 1,
    }
    assert payload["adjudications"][0] == {
        "kind": "ee_replay_unsupported_action",
        "message": "Replay skipped unsupported action",
        "source_statute": "a1",
        "op_id": "op-1",
        "detail": {"rule_id": "ee_replay_unsupported_action", "phase": "replay"},
    }
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "estonia"
    assert evidence_row["rule_id"] == "ee_replay_unsupported_action"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["source_artifact_id"] == "a1"
    assert evidence_row["source_unit_id"] == "op-1"
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()
    assert payload["divergences"][0]["address"] == "chapter:1/section:6/subsection:2"


def test_build_uk_replay_payload_shape() -> None:
    payload = build_uk_replay_payload(
        statute_id="ukpga/1998/42",
        pit_date="2020-01-01",
        enacted_only=False,
        db_path="data/uk_legislation.farchive",
        n_effects=4,
        n_ops=12,
        similarity=0.75,
        comparison_class="core_delta",
        oracle_available=True,
        n_provisions=40,
        n_versions=120,
        pit_materialized_eids=38,
        timeline_mode="ops_first",
        enacted_url="https://www.legislation.gov.uk/ukpga/1998/42/enacted/data.xml",
        oracle_url="https://www.legislation.gov.uk/ukpga/1998/42/2020-01-01/data.xml",
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=456,
        oracle_source_size=7,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        base_eid_count=50,
        replayed_eid_count=55,
        oracle_eid_count=60,
        replay_compare_eid_count=54,
        oracle_compare_eid_count=59,
        common_eid_count=45,
        only_in_replayed_count=9,
        only_in_oracle_count=14,
        only_in_replayed_sample=["section-1"],
        only_in_oracle_sample=["section-2"],
        core_benchmark=True,
        adjudications=[
            SimpleNamespace(
                kind="uk_replay_payload_missing",
                message="UK replay skipped op",
                op_id="op-1",
                source_statute="ukpga/2020/1",
                detail={"rule_id": "uk_replay_payload_missing", "phase": "replay"},
            )
        ],
        lowering_rejections=[
            {
                "rule_id": "uk_effect_lowering_no_ops_rejected",
                "phase": "lowering",
                "effect_id": "eff-1",
                "blocking": True,
            }
        ],
        source_parse_rejections=[
            {
                "rule_id": "uk_container_number_inferred_from_source_uri",
                "phase": "source_parse",
                "side": "enacted",
                "blocking": False,
            }
        ],
        uk_replay_regime={
            "semantic_replay_lane": "effects_assisted_replay",
            "oracle_alignment_lane": "oracle_alignment_adapter",
            "source_purity_lane": "source_backed_with_oracle_adapter",
            "oracle_alignment_enabled": True,
        },
        uk_oracle_alignment_summary={
            "enabled": True,
            "stage": "replay_executor_inputs",
            "rule_id": "uk_oracle_eid_alignment_adapter",
            "changed_count": None,
        },
    )

    assert payload["jurisdiction"] == "uk"
    assert payload["base_id"] == "ukpga/1998/42"
    assert payload["uk_replay_regime"] == {
        "semantic_replay_lane": "effects_assisted_replay",
        "oracle_alignment_lane": "oracle_alignment_adapter",
        "source_purity_lane": "source_backed_with_oracle_adapter",
        "oracle_alignment_enabled": True,
    }
    assert payload["uk_oracle_alignment_summary"] == {
        "enabled": True,
        "stage": "replay_executor_inputs",
        "rule_id": "uk_oracle_eid_alignment_adapter",
        "changed_count": None,
    }
    assert payload["source"]["enacted_url"] == "https://www.legislation.gov.uk/ukpga/1998/42/enacted/data.xml"
    assert payload["source"]["oracle_url"] == "https://www.legislation.gov.uk/ukpga/1998/42/2020-01-01/data.xml"
    assert payload["source"]["enacted_missing"] is False
    assert payload["source"]["enacted_source_parse_failed"] is False
    assert payload["source"]["oracle_missing"] is True
    assert payload["source"]["enacted_source_status"] == "available"
    assert payload["source"]["oracle_source_status"] == "too_small"
    assert payload["source"]["enacted_source_size"] == 456
    assert payload["source"]["oracle_source_size"] == 7
    assert payload["source"]["enacted_source_sha256"] == "enacted-sha"
    assert payload["source"]["oracle_source_sha256"] == "oracle-sha"
    assert payload["oracle"]["eid_similarity"] == 0.75
    assert payload["oracle"]["core_benchmark"] is True
    assert payload["oracle"]["base_eid_count"] == 50
    assert payload["oracle"]["replayed_eid_count"] == 55
    assert payload["oracle"]["oracle_eid_count"] == 60
    assert payload["oracle"]["replay_compare_eid_count"] == 54
    assert payload["oracle"]["oracle_compare_eid_count"] == 59
    assert payload["oracle"]["common_eid_count"] == 45
    assert payload["oracle"]["only_in_replayed_count"] == 9
    assert payload["oracle"]["only_in_oracle_count"] == 14
    assert payload["oracle"]["only_in_replayed_sample"] == ["section-1"]
    assert payload["oracle"]["only_in_oracle_sample"] == ["section-2"]
    assert payload["timeline"]["mode"] == "ops_first"
    assert payload["timeline"]["versions"] == 120
    assert payload["uk_prefetch_report"] == {}
    assert payload["adjudications_count"] == 1
    assert payload["adjudication_kind_counts"] == {"uk_replay_payload_missing": 1}
    assert payload["adjudications"] == [
        {
            "kind": "uk_replay_payload_missing",
            "message": "UK replay skipped op",
            "source_statute": "ukpga/2020/1",
            "op_id": "op-1",
            "detail": {"rule_id": "uk_replay_payload_missing", "phase": "replay"},
        }
    ]
    assert payload["compile_rejection_count"] == 1
    assert payload["compile_rejection_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1
    }
    assert payload["compile_observation_count"] == 2
    assert payload["compile_observation_rule_counts"] == {
        "uk_container_number_inferred_from_source_uri": 1,
        "uk_effect_lowering_no_ops_rejected": 1
    }
    assert payload["compile_observation_lane_counts"] == {
        "source_parse": 1,
        "effect_feed_parse": 0,
        "effect_source_pathology": 0,
        "manual_compile_frontier": 0,
        "source_acquisition": 0,
        "lowering": 1,
        "authority": 0,
    }
    assert payload["blocking_compile_rejection_count"] == 1
    assert payload["blocking_compile_rejection_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1
    }
    assert payload["blocking_compile_rejection_lane_counts"] == {
        "source_parse": 0,
        "effect_feed_parse": 0,
        "effect_source_pathology": 0,
        "manual_compile_frontier": 0,
        "source_acquisition": 0,
        "lowering": 1,
        "authority": 0,
    }
    assert payload["blocking_compile_rejection_rule_counts_by_lane"] == {
        "source_parse": {},
        "effect_feed_parse": {},
        "effect_source_pathology": {},
        "manual_compile_frontier": {},
        "source_acquisition": {},
        "lowering": {"uk_effect_lowering_no_ops_rejected": 1},
        "authority": {},
    }
    assert payload["compile_rejections"]["lowering"] == [
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "phase": "lowering",
            "effect_id": "eff-1",
            "blocking": True,
        }
    ]
    assert payload["compile_observations"]["lowering"] == payload["compile_rejections"]["lowering"]
    assert payload["compile_observations"]["source_parse"] == [
        {
            "rule_id": "uk_container_number_inferred_from_source_uri",
            "phase": "source_parse",
            "side": "enacted",
            "blocking": False,
        }
    ]
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "uk"
    assert evidence_row["rule_id"] == "uk_replay_payload_missing"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["source_artifact_id"] == "ukpga/2020/1"
    assert evidence_row["source_unit_id"] == "op-1"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_build_uk_replay_payload_keeps_recorded_manual_frontier_nonblocking() -> None:
    payload = build_uk_replay_payload(
        statute_id="ukpga/1998/42",
        pit_date=None,
        enacted_only=False,
        db_path="data/uk_legislation.farchive",
        n_effects=1,
        n_ops=0,
        similarity=None,
        comparison_class=None,
        oracle_available=False,
        n_provisions=1,
        n_versions=None,
        pit_materialized_eids=None,
        timeline_mode="none",
        manual_compile_frontier_observations=[
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "strict_disposition": "record",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
            }
        ],
    )

    assert payload["compile_observation_count"] == 1
    assert payload["compile_rejection_count"] == 0
    assert payload["blocking_compile_rejection_lane_counts"]["manual_compile_frontier"] == 0
    assert payload["blocking_compile_rejection_rule_counts_by_lane"]["manual_compile_frontier"] == {}
    assert payload["compile_rejections"]["manual_compile_frontier"] == []
    assert payload["manual_compile_status_counts"] == {"manual_compile_candidate": 1}
    assert payload["manual_compile_rule_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 1
    }


def test_uk_replay_enacted_only_json_threads_effect_count_parse_rejections(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
            ),
        ),
    )

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    def fake_load_effects(statute_id, archive, *, parse_rejections_out=None):
        del archive
        assert statute_id == "ukpga/1998/42"
        assert parse_rejections_out is not None
        parse_rejections_out.append(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "effects.xml",
            }
        )
        return []

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", lambda *args, **kwargs: base_ir)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        fake_load_effects,
    )
    monkeypatch.setattr("lawvm.core.timeline_consistency.ingest_uk_snapshots", lambda *args, **kwargs: {})

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=True,
            verbose=False,
            fetch_missing=False,
            json=True,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "enacted_only"
    assert payload["compile_rejection_count"] == 1
    assert payload["compile_rejection_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["compile_rejections"]["effect_feed_parse"] == [
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "phase": "parse",
            "feed_locator": "effects.xml",
        }
    ]
    assert payload["compile_observations"]["effect_feed_parse"] == payload["compile_rejections"]["effect_feed_parse"]


def test_uk_replay_main_threads_replay_adjudications_into_json(monkeypatch, tmp_path, capsys) -> None:
    import farchive
    from lawvm.uk_legislation.uk_prefetch import UKPrefetchReport

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    prefetch_event = {
        "rule_id": "uk_prefetch_http_error",
        "phase": "acquisition",
        "blocking": True,
    }
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
            ),
        ),
    )

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(
            self,
            statute_id,
            *,
            pit_date,
            archive,
            allow_metadata_backfill,
            applicability_mode,
            authority_mode,
            allow_metadata_only_effects,
            effect_feed_parse_rejections_out,
            effect_diagnostics_out,
            lowering_rejections_out,
            authority_rejections_out,
        ):
            assert statute_id == "ukpga/1998/42"
            assert allow_metadata_backfill is True
            assert allow_metadata_only_effects is True
            assert applicability_mode == "effective_date_plus_feed_applied"
            assert authority_mode == "current_mixed"
            effect_diagnostics_out.append(
                {
                    "rule_id": "uk_manual_compile_frontier_classified",
                    "manual_compile_status": "manual_compile_candidate",
                    "manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
                    "blocking": False,
                }
            )
            effect_feed_parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "phase": "parse",
                    "feed_locator": "effects.xml",
                    "blocking": False,
                }
            )
            lowering_rejections_out.append(
                {
                    "rule_id": "uk_effect_lowering_no_ops_rejected",
                    "phase": "lowering",
                    "effect_id": "eff-1",
                    "blocking": True,
                }
            )
            authority_rejections_out.append(
                {
                    "rule_id": "uk_effect_authority_filter_rejected",
                    "phase": "lowering",
                    "effect_id": "eff-2",
                    "blocking": False,
                }
            )
            return [
                LegalOperation(
                    op_id="op-1",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=(("section", "99"),)),
                )
            ]

        def apply_ops(self, base, ops, **kwargs):
            adjudications_out = kwargs["adjudications_out"]
            adjudications_out.append(
                SimpleNamespace(
                    kind="uk_replay_target_not_found",
                    message="UK replay skipped target",
                    op_id=ops[0].op_id,
                    source_statute="ukpga/2020/1",
                    detail={"phase": "replay", "target": "section:99"},
                )
            )
            return base

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    prefetch_seen: dict[str, object] = {}

    def fake_fetch_missing_for_statute(*args, **kwargs):
        prefetch_seen["args"] = args
        prefetch_seen["kwargs"] = kwargs
        return UKPrefetchReport(0, 0, 1, (prefetch_event,))

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_prefetch.fetch_missing_for_statute",
        fake_fetch_missing_for_statute,
    )
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", lambda *args, **kwargs: base_ir)
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive", lambda *args, **kwargs: [])
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline)
    monkeypatch.setattr("lawvm.core.timeline_consistency.ingest_uk_snapshots", lambda *args, **kwargs: {})

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=False,
            verbose=False,
            fetch_missing=True,
            include_enacted_affecting=True,
            json=True,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert prefetch_seen["kwargs"]["include_enacted"] is True
    assert payload["adjudications_count"] == 1
    assert payload["uk_prefetch_report"] == {
        "enabled": True,
        "fetched_count": 0,
        "already_cached_count": 0,
        "error_count": 1,
        "event_count": 1,
        "event_rule_counts": {"uk_prefetch_http_error": 1},
        "blocking_event_count": 1,
        "blocking_event_rule_counts": {"uk_prefetch_http_error": 1},
        "events": [prefetch_event],
    }
    assert payload["adjudication_kind_counts"] == {"uk_replay_target_not_found": 1}
    assert payload["adjudications"][0]["kind"] == "uk_replay_target_not_found"
    assert payload["adjudications"][0]["source_statute"] == "ukpga/2020/1"
    assert payload["compile_rejection_count"] == 1
    assert payload["compile_rejection_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1,
    }
    assert payload["compile_observation_count"] == 4
    assert payload["compile_observation_rule_counts"] == {
        "uk_effect_authority_filter_rejected": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
        "uk_effect_lowering_no_ops_rejected": 1,
        "uk_manual_compile_frontier_classified": 1,
    }
    assert payload["manual_compile_status_counts"] == {"manual_compile_candidate": 1}
    assert payload["manual_compile_rule_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 1,
    }
    assert payload["compile_observation_lane_counts"] == {
        "source_parse": 0,
        "effect_feed_parse": 1,
        "effect_source_pathology": 0,
        "manual_compile_frontier": 1,
        "source_acquisition": 0,
        "lowering": 1,
        "authority": 1,
    }
    assert payload["blocking_compile_rejection_count"] == 1
    assert payload["blocking_compile_rejection_rule_counts"] == {
        "uk_effect_lowering_no_ops_rejected": 1,
    }
    assert payload["blocking_compile_rejection_lane_counts"] == {
        "source_parse": 0,
        "effect_feed_parse": 0,
        "effect_source_pathology": 0,
        "manual_compile_frontier": 0,
        "source_acquisition": 0,
        "lowering": 1,
        "authority": 0,
    }
    assert payload["blocking_compile_rejection_rule_counts_by_lane"] == {
        "source_parse": {},
        "effect_feed_parse": {},
        "effect_source_pathology": {},
        "manual_compile_frontier": {},
        "source_acquisition": {},
        "lowering": {"uk_effect_lowering_no_ops_rejected": 1},
        "authority": {},
    }
    assert payload["compile_observations"]["effect_feed_parse"][0]["feed_locator"] == "effects.xml"
    assert payload["compile_observations"]["manual_compile_frontier"][0][
        "manual_compile_rule_id"
    ] == "uk_manual_frontier_heading_facet_candidate"
    assert payload["compile_observations"]["lowering"][0]["effect_id"] == "eff-1"
    assert payload["compile_observations"]["authority"][0]["effect_id"] == "eff-2"
    assert payload["compile_rejections"]["effect_feed_parse"] == []
    assert payload["compile_rejections"]["lowering"][0]["effect_id"] == "eff-1"
    assert payload["compile_rejections"]["authority"] == []
    assert payload["source"]["enacted_url"].endswith("/ukpga/1998/42/enacted/data.xml")
    assert payload["source"]["oracle_url"].endswith("/ukpga/1998/42/data.xml")
    assert payload["source"]["enacted_missing"] is False
    assert payload["source"]["oracle_missing"] is True
    assert payload["source"]["enacted_source_status"] == "available"
    assert payload["source"]["oracle_source_status"] == "absent"
    assert payload["source"]["enacted_source_size"] > 100
    assert payload["source"]["oracle_source_size"] == 0
    assert payload["oracle"]["base_eid_count"] == 1
    assert payload["oracle"]["replayed_eid_count"] == 1
    assert payload["oracle"]["oracle_eid_count"] is None
    assert payload["oracle"]["only_in_replayed_count"] is None
    assert payload["oracle"]["only_in_oracle_sample"] == []
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "uk"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["source_unit_id"] == "op-1"
    assert evidence_row["evidence"]["as_of"] == "latest"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_uk_replay_main_text_reports_evidence_summary(monkeypatch, tmp_path, capsys) -> None:
    import farchive
    from lawvm.uk_legislation.uk_prefetch import UKPrefetchReport

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
            ),
        ),
    )

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return b"<short/>"

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(
            self,
            statute_id,
            *,
            pit_date,
            archive,
            allow_metadata_backfill,
            applicability_mode,
            authority_mode,
            allow_metadata_only_effects,
            effect_feed_parse_rejections_out,
            effect_diagnostics_out,
            lowering_rejections_out,
            authority_rejections_out,
        ):
            del pit_date, archive, allow_metadata_backfill, applicability_mode, authority_mode
            del allow_metadata_only_effects, effect_diagnostics_out
            assert statute_id == "ukpga/1998/42"
            effect_feed_parse_rejections_out.append(
                {"rule_id": "uk_effect_feed_xml_parse_rejected", "blocking": False}
            )
            lowering_rejections_out.append(
                {"rule_id": "uk_effect_lowering_no_ops_rejected", "blocking": True}
            )
            authority_rejections_out.append(
                {"rule_id": "uk_effect_authority_filter_rejected", "blocking": False}
            )
            return []

        def apply_ops(self, base, ops, **kwargs):
            adjudications_out = kwargs["adjudications_out"]
            adjudications_out.append(
                SimpleNamespace(
                    kind="uk_replay_target_not_found",
                    message="UK replay skipped target",
                    op_id="op-1",
                    source_statute="ukpga/2020/1",
                    detail={"phase": "replay", "target": "section:99"},
                )
            )
            return base

    prefetch_event = {
        "rule_id": "uk_prefetch_http_error",
        "phase": "acquisition",
        "family": "source_pathology",
        "statute_id": "ukpga/1998/42",
        "affecting_act_id": "ukpga/2020/1",
        "status": "error",
        "reason": "http_500",
        "blocking": True,
    }

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_prefetch.fetch_missing_for_statute",
        lambda *args, **kwargs: UKPrefetchReport(0, 0, 1, (prefetch_event,)),
    )
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", lambda *args, **kwargs: base_ir)
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive", lambda *args, **kwargs: [])
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline)
    monkeypatch.setattr("lawvm.core.timeline_consistency.ingest_uk_snapshots", lambda *args, **kwargs: {})

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=False,
            verbose=False,
            fetch_missing=True,
            json=False,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
        )
    )

    out = capsys.readouterr().out
    assert "Source:     enacted=available" in out
    assert "oracle=too_small (8 bytes)" in out
    assert "Enacted URL: https://www.legislation.gov.uk/ukpga/1998/42/enacted/data.xml" in out
    assert "Oracle URL: https://www.legislation.gov.uk/ukpga/1998/42/data.xml" in out
    enacted_sha256 = hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    oracle_sha256 = hashlib.sha256(b"<short/>").hexdigest()
    assert f"Enacted SHA-256: {enacted_sha256}" in out
    assert f"Oracle SHA-256: {oracle_sha256}" in out
    assert (
        "Regime:     metadata_backfill=True oracle_alignment=True "
        "metadata_only_effects=True "
        "applicability=effective_date_plus_feed_applied authority=current_mixed"
    ) in out
    assert "Prefetch:   fetched=0 cached=0 errors=1 events=1 blocking=1" in out
    assert "Prefetch rules: uk_prefetch_http_error=1" in out
    assert "Prefetch blocking rules: uk_prefetch_http_error=1" in out
    assert (
        "Compile observations: source_parse=0 feed_parse=1 effect_source_pathology=0 "
        "manual_compile_frontier=0 source_acquisition=0 lowering=1 authority=1 total=3"
    ) in out
    assert (
        "Compile rejections: source_parse=0 feed_parse=0 effect_source_pathology=0 "
        "manual_compile_frontier=0 source_acquisition=0 lowering=1 authority=0 blocking=1"
    ) in out
    assert "feed_parse rules: uk_effect_feed_xml_parse_rejected=1" in out
    assert "lowering observation rules: uk_effect_lowering_no_ops_rejected=1" in out
    assert "authority rules: uk_effect_authority_filter_rejected=1" in out
    assert (
        "Compile blocking rejections: source_parse=0 feed_parse=0 "
        "effect_source_pathology=0 manual_compile_frontier=0 "
        "source_acquisition=0 lowering=1 authority=0"
    ) in out
    assert "blocking rules: uk_effect_lowering_no_ops_rejected=1" in out
    assert "blocking lowering rules: uk_effect_lowering_no_ops_rejected=1" in out
    assert "Replay adjudications: 1" in out
    assert "Replay adjudication buckets: replay_bug=1" in out
    assert "Replay adjudication kinds: uk_replay_target_not_found=1" in out
    assert (
        "Oracle alignment: enabled=false changed=None cleared=None oracle_assigned=None "
        "local_fallback=None transparent_wrapper_cleared=None samples=0 "
        "reason=oracle_xml_unavailable"
    ) in out


def test_uk_replay_adjudication_text_formatter_prints_requested_samples() -> None:
    lines = uk_replay._uk_replay_adjudication_text_lines(
        [
            CompileAdjudication(
                kind="uk_replay_text_match_missing",
                message="text match missing",
                op_id="op-1",
                source_statute="ukpga/2020/1",
                detail={
                    "target": "section:1/subsection:2",
                    "text_match": "old words",
                    "replacement_text": "new words",
                },
            ),
            CompileAdjudication(
                kind="uk_replay_text_match_missing",
                message="second missing",
                op_id="op-2",
                source_statute="ukpga/2021/2",
                detail={"target": "section:3"},
            ),
            CompileAdjudication(
                kind="uk_replay_repealed_target_gap",
                message="not requested",
                op_id="op-3",
                source_statute="ukpga/2022/3",
                detail={"target": "section:4"},
            ),
        ],
        sample_kinds=("uk_replay_text_match_missing",),
        sample_limit=1,
    )

    assert lines[0] == "Replay adjudications: 3"
    assert (
        lines[1]
        == "Replay adjudication buckets: source_shape=1, text_surface=2"
    )
    assert (
        lines[2]
        == "Replay adjudication kinds: "
        "uk_replay_repealed_target_gap=1, uk_replay_text_match_missing=2"
    )
    assert "Replay adjudication samples:" in lines
    assert "  uk_replay_text_match_missing: shown=1 total=2 omitted=1" in lines
    assert (
        "    kind=uk_replay_text_match_missing source=ukpga/2020/1 op=op-1 "
        "target=section:1/subsection:2 text_match=old words replacement=new words"
    ) in lines
    assert all("op-2" not in line for line in lines)
    assert all("op-3" not in line for line in lines)


def test_uk_replay_compile_rejection_text_formatter_exposes_rule_counts() -> None:
    feed_rejections: tuple[dict[str, object], ...] = (
        {"rule_id": "uk_effect_feed_xml_parse_rejected", "blocking": False},
        {"rule_id": "uk_effect_feed_xml_parse_rejected", "blocking": False},
    )
    lowering_rejections: tuple[dict[str, object], ...] = (
        {"rule_id": "uk_effect_payload_missing", "blocking": True},
    )
    authority_rejections: tuple[dict[str, object], ...] = (
        {"rule_id": "uk_authority_source_text_only_missing", "blocking": False},
    )
    lines = uk_replay._uk_compile_rejection_text_lines(
        effect_feed_parse_rejections=feed_rejections,
        lowering_rejections=lowering_rejections,
        authority_rejections=authority_rejections,
    )

    assert lines == [
        "Compile observations: source_parse=0 feed_parse=2 effect_source_pathology=0 "
        "manual_compile_frontier=0 source_acquisition=0 lowering=1 authority=1 total=4",
        "Compile rejections: source_parse=0 feed_parse=0 effect_source_pathology=0 "
        "manual_compile_frontier=0 source_acquisition=0 lowering=1 authority=0 blocking=1",
        "feed_parse rules: uk_effect_feed_xml_parse_rejected=2",
        "lowering observation rules: uk_effect_payload_missing=1",
        "authority rules: uk_authority_source_text_only_missing=1",
        "Compile blocking rejections: source_parse=0 feed_parse=0 "
        "effect_source_pathology=0 manual_compile_frontier=0 "
        "source_acquisition=0 lowering=1 authority=0",
        "blocking rules: uk_effect_payload_missing=1",
        "blocking lowering rules: uk_effect_payload_missing=1",
    ]


def test_uk_replay_compile_rejection_text_formatter_keeps_recorded_manual_frontier_nonblocking() -> None:
    lines = uk_replay._uk_compile_rejection_text_lines(
        effect_feed_parse_rejections=(),
        manual_compile_frontier_observations=[
            {
                "rule_id": "uk_manual_compile_frontier_classified",
                "strict_disposition": "record",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
            }
        ],
        lowering_rejections=(),
        authority_rejections=(),
    )

    assert lines == [
        "Compile observations: source_parse=0 feed_parse=0 effect_source_pathology=0 "
        "manual_compile_frontier=1 source_acquisition=0 lowering=0 authority=0 total=1",
        "Compile rejections: source_parse=0 feed_parse=0 effect_source_pathology=0 "
        "manual_compile_frontier=0 source_acquisition=0 lowering=0 authority=0 blocking=0",
        "manual_compile_frontier rules: uk_manual_compile_frontier_classified=1",
        "manual_compile_frontier statuses: manual_compile_candidate=1",
        "manual_compile_frontier manual rules: uk_manual_frontier_heading_facet_candidate=1",
    ]


def test_uk_replay_text_alignment_formatter_exposes_event_lanes() -> None:
    lines = uk_replay._uk_oracle_alignment_text_lines(
        {
            "enabled": True,
            "changed_count": 3,
            "cleared_count": 1,
            "oracle_assigned_count": 1,
            "local_fallback_count": 1,
            "transparent_wrapper_cleared_count": 1,
            "event_sample_count": 3,
            "unavailable_reason": "",
            "match_method_counts": {
                "local_fallback": 1,
                "flat": 1,
                "transparent_wrapper_cleared": 1,
            },
        }
    )

    assert lines == [
        "Oracle alignment: enabled=true changed=3 cleared=1 oracle_assigned=1 "
        "local_fallback=1 transparent_wrapper_cleared=1 samples=3 reason=none",
        "Oracle alignment methods: flat=1, local_fallback=1, transparent_wrapper_cleared=1",
    ]


def test_uk_replay_main_json_reports_too_small_oracle_source(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
            ),
        ),
    )
    parsed_labels: list[str] = []

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            if str(url).endswith("/data.xml"):
                return b"<short/>"
            return None

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(self, *args, **kwargs):
            return []

        def apply_ops(self, base, ops, **kwargs):
            return base

    def fake_parse_uk_statute_ir_bytes(*args, **kwargs):
        parsed_labels.append(str(kwargs.get("version_label") or ""))
        return base_ir

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse_uk_statute_ir_bytes)
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive", lambda *args, **kwargs: [])
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline)
    monkeypatch.setattr("lawvm.core.timeline_consistency.ingest_uk_snapshots", lambda *args, **kwargs: {})

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=False,
            verbose=False,
            fetch_missing=False,
            json=True,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert parsed_labels == ["enacted"]
    assert payload["source"]["enacted_source_status"] == "available"
    assert payload["source"]["oracle_source_status"] == "too_small"
    assert payload["source"]["oracle_source_size"] == len(b"<short/>")
    assert payload["source"]["oracle_source_sha256"] == hashlib.sha256(b"<short/>").hexdigest()
    assert payload["source"]["oracle_missing"] is True
    assert payload["oracle"]["available"] is False
    assert payload["uk_oracle_alignment_summary"]["unavailable_reason"] == "oracle_xml_unavailable"


def test_uk_replay_json_reports_missing_enacted_source_context(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return None
            if str(url).endswith("/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)

    with pytest.raises(SystemExit) as excinfo:
        uk_replay.main(
            Namespace(
                statute_id="ukpga/1998/42",
                pit_date=None,
                enacted_only=False,
                verbose=False,
                fetch_missing=False,
                json=True,
                db=str(db_path),
                timeline=False,
                uk_allow_metadata_backfill=None,
                uk_allow_oracle_alignment=None,
                uk_respect_feed_applied=None,
                uk_applicability_mode=None,
                uk_source_first_candidate=False,
                uk_authority_mode=None,
            )
        )

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"].startswith("enacted XML missing from archive")
    assert payload["source"]["archive"] == str(db_path)
    assert payload["source"]["enacted_source_status"] == "absent"
    assert payload["source"]["enacted_source_size"] == 0
    assert payload["source"]["enacted_source_sha256"] is None
    assert payload["source"]["enacted_missing"] is True
    assert payload["source"]["oracle_source_status"] == "available"
    assert payload["source"]["oracle_source_size"] == len(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    )
    assert payload["source"]["oracle_source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert payload["source"]["oracle_missing"] is False
    assert payload["oracle"]["available"] is False
    assert payload["ops_count"] == 0
    assert payload["timeline"]["provisions"] == 0
    assert (
        payload["uk_oracle_alignment_summary"]["unavailable_reason"]
        == "enacted_xml_unavailable"
    )


def test_uk_replay_json_records_malformed_available_enacted_source(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            if str(url).endswith("/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    def fail_parse(*args, **kwargs):
        if kwargs.get("version_label") == "enacted":
            raise ValueError("bad enacted source")
        return IRStatute(
            statute_id="ukpga/1998/42",
            title="Demo",
            body=IRNode(kind=IRNodeKind.BODY),
        )

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fail_parse)

    with pytest.raises(SystemExit) as excinfo:
        uk_replay.main(
            Namespace(
                statute_id="ukpga/1998/42",
                pit_date=None,
                enacted_only=False,
                verbose=False,
                fetch_missing=False,
                json=True,
                db=str(db_path),
                timeline=False,
                uk_allow_metadata_backfill=None,
                uk_allow_oracle_alignment=None,
                uk_respect_feed_applied=None,
                uk_applicability_mode=None,
                uk_source_first_candidate=False,
                uk_authority_mode=None,
                uk_allow_metadata_only_effects=None,
            )
        )

    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"].startswith("enacted XML parse failed")
    assert payload["source"]["enacted_source_status"] == "available"
    assert payload["source"]["enacted_source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert payload["source"]["enacted_missing"] is True
    assert payload["source"]["enacted_source_parse_failed"] is True
    assert payload["compile_rejection_rule_counts"] == {"uk_enacted_xml_parse_rejected": 1}
    assert payload["compile_observation_lane_counts"]["source_parse"] == 1
    assert payload["compile_rejections"]["source_parse"][0]["side"] == "enacted"
    assert (
        payload["uk_oracle_alignment_summary"]["unavailable_reason"]
        == "enacted_xml_parse_rejected"
    )


def test_uk_replay_json_records_malformed_available_oracle_source(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Demo",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", attrs={"eId": "section-1"}),),
        ),
    )

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            if str(url).endswith("/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(self, *args, **kwargs):
            return []

        def apply_ops(self, base, ops, **kwargs):
            return base

    def fake_parse(*args, **kwargs):
        if kwargs.get("version_label") == "oracle":
            raise ValueError("bad oracle source")
        return base_ir

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda *args, **kwargs: {"eid_map": {"body:section-1": "section-1"}, "text_map": {}},
    )
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive", lambda *args, **kwargs: [])
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline)

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=False,
            verbose=False,
            fetch_missing=False,
            json=True,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
            uk_allow_metadata_only_effects=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["source"]["oracle_source_status"] == "available"
    assert payload["source"]["oracle_source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert payload["source"]["oracle_missing"] is True
    assert payload["source"]["oracle_source_parse_failed"] is True
    assert payload["oracle"]["available"] is False
    assert payload["compile_rejection_rule_counts"] == {"uk_oracle_xml_parse_rejected": 1}
    assert payload["compile_observation_lane_counts"]["source_parse"] == 1
    assert payload["compile_rejections"]["source_parse"][0]["side"] == "oracle"
    assert (
        payload["uk_oracle_alignment_summary"]["unavailable_reason"]
        == "oracle_xml_parse_rejected"
    )


def test_uk_replay_source_first_threads_replay_regime(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
            ),
        ),
    )
    seen: dict[str, object] = {}

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(
            self,
            statute_id,
            *,
            pit_date,
            archive,
            allow_metadata_backfill,
            applicability_mode,
            authority_mode,
            allow_metadata_only_effects,
            effect_feed_parse_rejections_out,
            effect_diagnostics_out,
            lowering_rejections_out,
            authority_rejections_out,
        ):
            del pit_date, archive, effect_feed_parse_rejections_out, effect_diagnostics_out
            del lowering_rejections_out
            assert statute_id == "ukpga/1998/42"
            seen["allow_metadata_backfill"] = allow_metadata_backfill
            seen["allow_metadata_only_effects"] = allow_metadata_only_effects
            seen["applicability_mode"] = applicability_mode
            seen["authority_mode"] = authority_mode
            authority_rejections_out.append({"rule_id": "uk_authority_source_text_only_missing"})
            return []

        def apply_ops(self, base, ops, **kwargs):
            del ops
            seen["allow_oracle_alignment"] = kwargs["allow_oracle_alignment"]
            return base

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", lambda *args, **kwargs: base_ir)
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive", lambda *args, **kwargs: [])
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline)
    monkeypatch.setattr("lawvm.core.timeline_consistency.ingest_uk_snapshots", lambda *args, **kwargs: {})

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=False,
            verbose=False,
            fetch_missing=False,
            json=True,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=True,
            uk_authority_mode=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert seen == {
        "allow_metadata_backfill": False,
        "allow_metadata_only_effects": False,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "source_text_only",
        "allow_oracle_alignment": False,
    }
    assert payload["uk_replay_regime"] == {
        "semantic_replay_lane": "effects_assisted_replay",
        "oracle_alignment_lane": "none",
        "source_purity_lane": "source_backed_effects_assisted",
        "source_semantics_clean": True,
        "source_first_candidate": True,
        "source_first_candidate_reasons": [],
        "oracle_alignment_stage": "none",
        "oracle_alignment_enabled": False,
        "metadata_backfill_enabled": False,
        "metadata_only_effects_enabled": False,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "source_text_only",
    }
    assert payload["uk_oracle_alignment_summary"] == {
        "enabled": False,
        "stage": "none",
        "rule_id": "uk_oracle_eid_alignment_adapter",
        "phase": "oracle_alignment",
        "family": "oracle_alignment_adapter",
        "evidence_available": False,
        "changed_count": None,
        "cleared_count": None,
        "oracle_assigned_count": None,
        "local_fallback_count": None,
        "transparent_wrapper_cleared_count": None,
        "match_method_counts": {},
        "event_sample_limit": 20,
        "event_sample_count": 0,
        "event_samples": [],
        "unavailable_reason": "oracle_alignment_disabled_by_regime",
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }
    assert payload["compile_rejection_rule_counts"] == {
        "uk_authority_source_text_only_missing": 1,
    }


def test_uk_replay_main_json_records_oracle_compare_residuals(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_text("placeholder", encoding="utf-8")
    base_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
            ),
        ),
    )
    replayed_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="3",
                    text="Replay-only text",
                    attrs={"eId": "section-3"},
                ),
            ),
        ),
    )
    oracle_ir = IRStatute(
        statute_id="ukpga/1998/42",
        title="Human Rights Act 1998",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base text",
                    attrs={"eId": "section-1"},
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2",
                    text="Oracle-only text",
                    attrs={"eId": "section-2"},
                ),
            ),
        ),
    )

    class _FakeArchive:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml") or str(url).endswith("/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return []

        def apply_ops(self, base, ops, **kwargs):  # noqa: ANN001, ANN003
            del base, ops
            kwargs["oracle_alignment_events_out"].extend(
                [
                    {
                        "rule_id": "uk_oracle_eid_alignment_adapter",
                        "phase": "oracle_alignment",
                        "family": "oracle_alignment_adapter",
                        "kind": "section",
                        "label": "1",
                        "before_eid": "local-section-one",
                        "after_eid": "section-1",
                        "match_method": "flat",
                        "match_key": "flat:body:section-1",
                    },
                    {
                        "rule_id": "uk_oracle_eid_alignment_adapter",
                        "phase": "oracle_alignment",
                        "family": "oracle_alignment_adapter",
                        "kind": "subsection",
                        "label": "1",
                        "before_eid": None,
                        "after_eid": "section-1-1",
                        "match_method": "local_fallback",
                        "match_key": None,
                    },
                    {
                        "rule_id": "uk_oracle_eid_alignment_adapter",
                        "phase": "oracle_alignment",
                        "family": "oracle_alignment_adapter",
                        "kind": "pblock",
                        "label": None,
                        "before_eid": "local-wrapper",
                        "after_eid": None,
                        "match_method": "transparent_wrapper_cleared",
                        "match_key": None,
                    },
                ]
            )
            return replayed_ir

    def fake_parse_uk_statute_ir_bytes(*args, **kwargs):  # noqa: ANN002, ANN003
        if kwargs.get("version_label") == "oracle":
            return oracle_ir
        return base_ir

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr("lawvm.uk_legislation.uk_grafter.parse_uk_statute_ir_bytes", fake_parse_uk_statute_ir_bytes)
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda *args, **kwargs: {
            "eid_map": {
                "n1": "section-1",
                "n2": "section-2",
            },
            "text_map": {
                "section-1": "Base text",
                "section-2": "Oracle-only text",
            },
        },
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.source_adjudication.normalize_uk_replay_compare_eids",
        lambda replayed, oracle: (set(replayed), set(oracle)),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.source_adjudication.classify_uk_bench_comparison",
        lambda **kwargs: "commensurable_delta",
    )
    monkeypatch.setattr("lawvm.uk_legislation.source_adjudication.is_core_uk_comparison", lambda value: True)
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive", lambda *args, **kwargs: [object(), object()])
    monkeypatch.setattr("lawvm.uk_legislation.uk_amendment_replay.UKReplayPipeline", _FakePipeline)
    monkeypatch.setattr("lawvm.core.timeline_consistency.ingest_uk_snapshots", lambda *args, **kwargs: {})

    uk_replay.main(
        Namespace(
            statute_id="ukpga/1998/42",
            pit_date=None,
            enacted_only=False,
            verbose=False,
            fetch_missing=False,
            json=True,
            db=str(db_path),
            timeline=False,
            uk_allow_metadata_backfill=None,
            uk_allow_oracle_alignment=None,
            uk_respect_feed_applied=None,
            uk_applicability_mode=None,
            uk_source_first_candidate=False,
            uk_authority_mode=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["oracle"]["available"] is True
    assert payload["oracle"]["comparison_class"] == "commensurable_delta"
    assert payload["oracle"]["core_benchmark"] is True
    assert payload["oracle"]["eid_similarity"] == 0.5
    assert payload["oracle"]["base_eid_count"] == 1
    assert payload["oracle"]["replayed_eid_count"] == 2
    assert payload["oracle"]["oracle_eid_count"] == 2
    assert payload["oracle"]["replay_compare_eid_count"] == 2
    assert payload["oracle"]["oracle_compare_eid_count"] == 2
    assert payload["oracle"]["common_eid_count"] == 1
    assert payload["oracle"]["only_in_replayed_count"] == 1
    assert payload["oracle"]["only_in_oracle_count"] == 1
    assert payload["oracle"]["only_in_replayed_sample"] == ["section-3"]
    assert payload["oracle"]["only_in_oracle_sample"] == ["section-2"]
    assert payload["amendment_counts"]["total"] == 2
    assert payload["uk_oracle_alignment_summary"] == {
        "enabled": True,
        "stage": "replay_executor_inputs",
        "rule_id": "uk_oracle_eid_alignment_adapter",
        "phase": "oracle_alignment",
        "family": "oracle_alignment_adapter",
        "evidence_available": True,
        "changed_count": 3,
        "cleared_count": 1,
        "oracle_assigned_count": 1,
        "local_fallback_count": 1,
        "transparent_wrapper_cleared_count": 1,
        "match_method_counts": {
            "flat": 1,
            "local_fallback": 1,
            "transparent_wrapper_cleared": 1,
        },
        "event_sample_limit": 20,
        "event_sample_count": 3,
        "event_samples": [
            {
                "rule_id": "uk_oracle_eid_alignment_adapter",
                "phase": "oracle_alignment",
                "family": "oracle_alignment_adapter",
                "kind": "section",
                "label": "1",
                "before_eid": "local-section-one",
                "after_eid": "section-1",
                "match_method": "flat",
                "match_key": "flat:body:section-1",
            },
            {
                "rule_id": "uk_oracle_eid_alignment_adapter",
                "phase": "oracle_alignment",
                "family": "oracle_alignment_adapter",
                "kind": "subsection",
                "label": "1",
                "before_eid": None,
                "after_eid": "section-1-1",
                "match_method": "local_fallback",
                "match_key": None,
            },
            {
                "rule_id": "uk_oracle_eid_alignment_adapter",
                "phase": "oracle_alignment",
                "family": "oracle_alignment_adapter",
                "kind": "pblock",
                "label": None,
                "before_eid": "local-wrapper",
                "after_eid": None,
                "match_method": "transparent_wrapper_cleared",
                "match_key": None,
            },
        ],
        "unavailable_reason": "",
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }
