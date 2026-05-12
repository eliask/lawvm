from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

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
            }
        ],
    )

    assert payload["jurisdiction"] == "uk"
    assert payload["base_id"] == "ukpga/1998/42"
    assert payload["oracle"]["eid_similarity"] == 0.75
    assert payload["timeline"]["mode"] == "ops_first"
    assert payload["timeline"]["versions"] == 120
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
    assert payload["compile_rejections"]["lowering"] == [
        {
            "rule_id": "uk_effect_lowering_no_ops_rejected",
            "phase": "lowering",
            "effect_id": "eff-1",
        }
    ]
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "uk"
    assert evidence_row["rule_id"] == "uk_replay_payload_missing"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["source_artifact_id"] == "ukpga/2020/1"
    assert evidence_row["source_unit_id"] == "op-1"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def test_uk_replay_main_threads_replay_adjudications_into_json(monkeypatch, tmp_path, capsys) -> None:
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

    class _FakePipeline:
        def __init__(self, _repo_root):
            pass

        def compile_ops_for_statute(
            self,
            statute_id,
            *,
            pit_date,
            archive,
            effect_feed_parse_rejections_out,
            lowering_rejections_out,
            authority_rejections_out,
        ):
            assert statute_id == "ukpga/1998/42"
            effect_feed_parse_rejections_out.append(
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "phase": "parse",
                    "feed_locator": "effects.xml",
                }
            )
            lowering_rejections_out.append(
                {
                    "rule_id": "uk_effect_lowering_no_ops_rejected",
                    "phase": "lowering",
                    "effect_id": "eff-1",
                }
            )
            authority_rejections_out.append(
                {
                    "rule_id": "uk_effect_authority_filter_rejected",
                    "phase": "lowering",
                    "effect_id": "eff-2",
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
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["adjudications_count"] == 1
    assert payload["adjudication_kind_counts"] == {"uk_replay_target_not_found": 1}
    assert payload["adjudications"][0]["kind"] == "uk_replay_target_not_found"
    assert payload["adjudications"][0]["source_statute"] == "ukpga/2020/1"
    assert payload["compile_rejection_count"] == 3
    assert payload["compile_rejection_rule_counts"] == {
        "uk_effect_authority_filter_rejected": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
        "uk_effect_lowering_no_ops_rejected": 1,
    }
    assert payload["compile_rejections"]["effect_feed_parse"][0]["feed_locator"] == "effects.xml"
    assert payload["compile_rejections"]["lowering"][0]["effect_id"] == "eff-1"
    assert payload["compile_rejections"]["authority"][0]["effect_id"] == "eff-2"
    evidence_row = payload["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "uk"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["source_unit_id"] == "op-1"
    assert evidence_row["evidence"]["as_of"] == "latest"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()
