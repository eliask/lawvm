from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from lawvm.finland.ops import FailedOp
from lawvm.finland.strict_profile import FINLAND_INGESTION_V1
from lawvm.tools import strict_report


def test_load_strict_run_reads_source_pathology_codes(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "statute_id,n_canonical,n_failed,source_pathology_codes,"
        "source_pathology_diagnostic_reasons,html_noncommensurable_reason,"
        "contingent_effective_sources,fail_reasons,source_incomplete,chain_length,"
        "source_available,elapsed_s,error\n"
        "1994/1472,10,0,"
        "MALFORMED_BROAD_REPLACE_BODY|DESTRUCTIVE_SHAPE_LOSS_RISK,"
        "live_body_dominates_amend_body|partial_body_only,"
        "oracle_extra_scoped_labels:chapter:15/section:1,,"
        "APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["source_pathology_codes"] == [
        "MALFORMED_BROAD_REPLACE_BODY",
        "DESTRUCTIVE_SHAPE_LOSS_RISK",
    ]
    assert rows[0]["source_pathology_diagnostic_reasons"] == [
        "live_body_dominates_amend_body",
        "partial_body_only",
    ]
    assert rows[0]["source_pathology_rows"] == []
    assert rows[0]["html_noncommensurable_reason"] == (
        "oracle_extra_scoped_labels:chapter:15/section:1"
    )

def test_load_strict_run_ignores_legacy_adjudication_kinds_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                "statute_id,n_canonical,n_failed,source_pathology_codes,"
                "source_pathology_diagnostic_reasons,html_noncommensurable_reason,"
                "contingent_effective_sources,fail_reasons,source_incomplete,chain_length,"
                "source_available,elapsed_s,error",
                "1994/1472,10,0,"
                "MALFORMED_BROAD_REPLACE_BODY|DESTRUCTIVE_SHAPE_LOSS_RISK,"
                "live_body_dominates_amend_body|partial_body_only,"
                "oracle_extra_scoped_labels:chapter:15/section:1,,"
                "APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["projection_kinds"] == []


def test_load_strict_run_ignores_legacy_n_adjudications_column(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    run.write_text(
        "\n".join(
            [
                (
                    "statute_id,n_canonical,n_failed,n_adjudications,"
                    "projection_kinds,source_pathology_codes,source_pathology_diagnostic_reasons,"
                    "html_noncommensurable_reason,contingent_effective_sources,fail_reasons,"
                    "source_incomplete,chain_length,source_available,elapsed_s,error"
                ),
                (
                    "1994/1472,10,0,7,"
                    ",,,,"
                    ","
                    "APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["n_projection_rows"] == 0


def test_save_strict_run_writes_source_pathology_codes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(tmp_path))

    path = strict_report._save_strict_run(
        [
            {
                "sid": "2001/1234",
                "n_canonical": 4,
                "n_failed": 0,
                "n_projection_rows": 2,
                "n_source_pathologies": 1,
                "n_contingent_effective_dates": 0,
                "projection_kinds": ["ELAB.SOURCE_PATHOLOGY", "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY"],
                "source_pathology_codes": ["DESTRUCTIVE_SHAPE_LOSS_RISK"],
                "source_pathology_rows": [
                    {
                        "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                        "message": "Replay encountered a source pathology.",
                        "source_statute": "2001/1234",
                        "target_unit_kind": "section",
                        "target_label": "6 §",
                        "detail": {"diagnostic_reason": "partial_body_only"},
                    }
                ],
                "source_pathology_diagnostic_reasons": ["partial_body_only"],
                "html_noncommensurable_reason": "oracle_extra_scoped_labels:chapter:15/section:1",
                "contingent_effective_sources": [],
                "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
                "source_incomplete": False,
                "chain_length": 1,
                "source_available": 1,
                "elapsed_s": 0.5,
                "error": "",
            }
        ],
        "demo",
        "2026-03-28T12:00",
    )

    text = path.read_text(encoding="utf-8")
    assert "n_source_pathologies" in text
    assert "source_pathology_codes" in text
    assert "source_pathology_rows_json" in text
    assert "source_pathology_diagnostic_reasons" in text
    assert "html_noncommensurable_reason" in text
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in text
    assert "partial_body_only" in text
    assert '""target_unit_kind"": ""section""' in text
    assert '""target_label"": ""6 \\u00a7""' in text
    assert "oracle_extra_scoped_labels:chapter:15/section:1" in text


def test_load_strict_run_reads_source_pathology_rows_json(tmp_path, monkeypatch) -> None:
    strict_dir = tmp_path / "strict_runs"
    strict_dir.mkdir()
    run = strict_dir / "20260328T0000_demo.csv"
    rows_json = json.dumps(
        [
            {
                "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                "target_unit_kind": "section",
                "target_label": "6 §",
                "detail": {"diagnostic_reason": "partial_body_only"},
            }
        ],
        ensure_ascii=False,
    ).replace('"', '""')
    run.write_text(
        "\n".join(
            [
                (
                    "statute_id,n_canonical,n_failed,n_projection_rows,n_source_pathologies,"
                    "n_contingent_effective_dates,projection_kinds,source_pathology_codes,"
                    "source_pathology_rows_json,source_pathology_diagnostic_reasons,"
                    "html_noncommensurable_reason,contingent_effective_sources,fail_reasons,"
                    "source_incomplete,chain_length,source_available,elapsed_s,error"
                ),
                (
                    "1994/1472,10,0,2,1,0,"
                    "APPLY.SOURCE_PATHOLOGY_DETECTED,"
                    "DESTRUCTIVE_SHAPE_LOSS_RISK,"
                    f"\"{rows_json}\","
                    "partial_body_only,,APPLY.SOURCE_PATHOLOGY_DETECTED,0,43,43,1.00,"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(strict_report, "_strict_runs_dir", lambda: Path(strict_dir))

    rows = strict_report._load_strict_run("demo")

    assert rows is not None
    assert rows[0]["source_pathology_rows"] == [
        {
            "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
            "target_unit_kind": "section",
            "target_label": "6 §",
            "detail": {"diagnostic_reason": "partial_body_only"},
        }
    ]


def test_show_corpus_summary_reports_source_pathology_codes(capsys) -> None:
    strict_report._show_corpus_summary(
        [
            {
                "sid": "1994/1472",
                "source_incomplete": False,
                "n_canonical": 4,
                "n_failed": 0,
                "n_projection_rows": 2,
                "n_source_pathologies": 2,
                "n_contingent_effective_dates": 1,
                "projection_kinds": ["ELAB.SOURCE_PATHOLOGY", "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY"],
                "source_pathology_codes": ["MALFORMED_BROAD_REPLACE_BODY", "DESTRUCTIVE_SHAPE_LOSS_RISK"],
                "source_pathology_diagnostic_reasons": ["live_body_dominates_amend_body", "partial_body_only"],
                "html_noncommensurable_reason": "oracle_extra_scoped_labels:chapter:15/section:1",
                "contingent_effective_sources": ["2005/544"],
                "fail_reasons": ["APPLY.SOURCE_PATHOLOGY_DETECTED"],
                "chain_length": 43,
                "source_available": 43,
                "error": "",
            }
        ],
        "demo",
    )

    out = capsys.readouterr().out
    assert "Source pathology codes" in out
    assert "MALFORMED_BROAD_REPLACE_BODY" in out
    assert "DESTRUCTIVE_SHAPE_LOSS_RISK" in out
    assert "Source pathology diagnostic reasons" in out
    assert "live_body_dominates_amend_body" in out
    assert "partial_body_only" in out
    assert "HTML/XML noncommensurable reasons" in out
    assert "oracle_extra_scoped_labels:chapter:15/section:1" in out
    assert "Contingent effective-date sources" in out
    assert "2005/544" in out


def test_print_facade_summary_includes_source_pathology_reasons(capsys) -> None:
    facade = SimpleNamespace(
        source_pathology_rows=lambda: (
            {
                "code": "PARTIAL_WHOLE_SECTION_PAYLOAD",
                "detail": {"diagnostic_reason": "live_body_dominates_amend_body"},
            },
        ),
    )

    strict_report._print_facade_summary(facade)

    out = capsys.readouterr().out
    assert "Pathologies  : PARTIAL_WHOLE_SECTION_PAYLOAD" in out
    assert "Pathology reasons : live_body_dominates_amend_body" in out


def test_print_facade_summary_accepts_explicit_html_noncomm_reason(capsys) -> None:
    facade = SimpleNamespace(
        source_pathology_rows=lambda: (),
    )

    strict_report._print_facade_summary(
        facade,
        html_noncommensurable_reason="oracle_extra_scoped_labels:chapter:15/section:1",
    )

    out = capsys.readouterr().out
    assert "HTML/XML reason : oracle_extra_scoped_labels:chapter:15/section:1" in out


def test_format_report_verbose_prefers_typed_provenance_tags() -> None:
    cr = {
        "statute_id": "2001/1234",
        "replay_mode": "legal_pit",
        "compile_mode": "strict",
        "profile": FINLAND_INGESTION_V1,
        "compiled_ops": [
            {
                "op_id": "op-1",
                "description": "typed extraction op",
                "extraction_provenance_tags": ["extraction_fallback_heuristic"],
            },
            {
                "op_id": "op-2",
                "description": "typed scope op",
                "scope_provenance_tags": ["chapter_scope_from_johtolause"],
            },
            {
                "op_id": "op-3",
                "description": "typed target op",
                "target_guessing_provenance_tags": ["normalize_item_like_target"],
            },
        ],
    }

    out = strict_report._format_report(cr, verbose=True)

    assert "extraction_fallback_heuristic" in out
    assert "chapter_scope_from_johtolause" in out
    assert "normalize_item_like_target" in out


def test_format_report_verbose_ignores_legacy_resolution_hint_tags() -> None:
    cr = {
        "statute_id": "2001/1234",
        "replay_mode": "legal_pit",
        "compile_mode": "strict",
        "profile": FINLAND_INGESTION_V1,
        "compiled_ops": [
            {
                "op_id": "op-1",
                "description": "legacy only op",
                "resolution_hint": "legacy_only_tag",
            },
        ],
    }

    out = strict_report._format_report(cr, verbose=True)

    assert "legacy_only_tag" not in out
    assert "canonical" in out


def test_to_json_preserves_projection_row_detail() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                "message": "destinationless move/relabel observed",
                "source": "2020/1",
                "detail": {
                    "collapse_kind": "destinationless_move_relabel",
                    "destination_missing": True,
                },
            },
        ),
    )

    payload = strict_report._to_json(cr)

    assert payload["projection_rows"][0]["detail"]["collapse_kind"] == "destinationless_move_relabel"
    assert payload["projection_rows"][0]["detail"]["destination_missing"] is True
    assert payload["source_pathologies"] == []


def test_to_json_preserves_failed_op_rule_and_scope_detail() -> None:
    failed_op = FailedOp.from_scope(
        amendment_id="2020/1",
        description="replace chapter-scoped section",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_unit_kind="section",
        target_section="5",
        target_chapter="4",
    )
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        canonical_ops=[],
        failed_ops=[failed_op],
        projection_rows=lambda: (),
        source_pathology_rows=lambda: (),
    )

    payload = strict_report._to_json(cr)

    assert payload["failed_ops"] == [
        {
            "amendment_id": "2020/1",
            "description": "replace chapter-scoped section",
            "reason": "no deterministic path",
            "reason_code": "no_deterministic_path",
            "target_unit_kind": "section",
            "target_section": "5",
            "target_chapter": "4",
            "target_part": None,
            "source": "2020/1",
            "target_kind": "P",
        }
    ]


def test_to_json_uses_projection_rows_when_available() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                "message": "destinationless move/relabel observed",
                "source": "2020/1",
                "detail": {
                    "collapse_kind": "destinationless_move_relabel",
                    "destination_missing": True,
                },
            },
        ),
    )

    payload = strict_report._to_json(cr)

    assert payload["projection_rows"][0]["kind"] == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
    assert payload["projection_rows"][0]["source"] == "2020/1"
    assert payload["projection_rows"][0]["detail"]["collapse_kind"] == "destinationless_move_relabel"


def test_to_json_ignores_legacy_dict_adjudications_field() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "adjudications": [
                {
                    "kind": "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
                    "message": "destinationless move/relabel observed",
                    "source": "2020/1",
                    "detail": {"collapse_kind": "destinationless_move_relabel"},
                }
            ],
        }
    )

    assert payload["projection_rows"] == []


def test_to_json_preserves_source_pathology_target_unit_kind() -> None:
    payload = strict_report._to_json(
        {
            "statute_id": "2001/1234",
            "replay_mode": "legal_pit",
            "compile_mode": "strict",
            "profile": FINLAND_INGESTION_V1,
            "source_pathologies": [
                {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "message": "source pathology",
                    "source_statute": "2001/748",
                    "target_unit_kind": "chapter",
                    "target_kind": "L",
                    "target_label": "4a luku",
                    "detail": {"diagnostic_reason": "partial_body_only"},
                }
            ],
        }
    )

    pathology = payload["source_pathologies"][0]
    assert pathology["target_unit_kind"] == "chapter"
    assert "target_kind" not in pathology
    assert pathology["target_label"] == "4a luku"


def test_format_report_surfaces_target_scoped_projection_row_detail() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                "message": "Compilation required context-dependent anchor resolution.",
                "source": "2020/1",
                "detail": {
                    "tag": "chapter_scope_from_johtolause",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "5",
                },
            },
        ),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in out
    assert "source: 2020/1" in out
    assert "detail: target(kind=section, norm=35, chapter=5); tag=chapter_scope_from_johtolause" in out


def test_format_report_surfaces_failed_op_reason_code() -> None:
    failed_op = FailedOp.from_scope(
        amendment_id="2020/1",
        description="replace chapter-scoped section",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_unit_kind="section",
        target_section="5",
        target_chapter="4",
    )
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        canonical_ops=[],
        failed_ops=[failed_op],
        strict_fail_reasons=["failed_ops"],
        projection_rows=lambda: (),
        source_pathology_rows=lambda: (),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "no deterministic path" in out
    assert "no_deterministic_path" in out
    assert "section 5" in out


def test_format_report_uses_projection_rows_when_available() -> None:
    cr = SimpleNamespace(
        statute_id="2001/1234",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                "message": "Compilation required context-dependent anchor resolution.",
                "source": "2020/1",
                "detail": {
                    "tag": "chapter_scope_from_johtolause",
                    "target_unit_kind": "section",
                    "target_norm": "35",
                    "target_chapter": "5",
                },
            },
        ),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in out
    assert "source: 2020/1" in out
    assert "detail: target(kind=section, norm=35, chapter=5); tag=chapter_scope_from_johtolause" in out


def test_format_report_surfaces_source_pathology_projection_row_detail() -> None:
    cr = SimpleNamespace(
        statute_id="1997/1339",
        replay_mode="legal_pit",
        compile_mode="strict",
        profile=FINLAND_INGESTION_V1,
        projection_rows=lambda: (
            {
                "kind": "ELAB.SOURCE_PATHOLOGY",
                "message": "Replay encountered a source pathology.",
                "source": "2001/748",
                "detail": {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "target_unit_kind": "section",
                    "target_label": "6 §",
                    "diagnostic_reason": "partial_body_only",
                },
            },
        ),
    )

    out = strict_report._format_report(cr, verbose=False)

    assert "ELAB.SOURCE_PATHOLOGY" in out
    assert "source: 2001/748" in out
    assert "detail: code=DESTRUCTIVE_SHAPE_LOSS_RISK; target(kind=section); target_label=6 §; diagnostic_reason=partial_body_only" in out


def test_build_facade_for_statute_preserves_projection_row_detail(monkeypatch) -> None:
    def fake_compile_fi_facade(
        statute_id: str,
        *,
        replay_mode: str = "legal_pit",
    ):
        assert statute_id == "1990/1295"
        assert replay_mode == "legal_pit"
        return SimpleNamespace(
            finding_ledger=(
                SimpleNamespace(
                    role="obligation",
                    kind="LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    detail={
                        "tag": "chapter_scope_from_johtolause",
                        "target_unit_kind": "section",
                        "target_norm": "35",
                        "target_chapter": "5",
                    },
                ),
            )
        )

    monkeypatch.setattr("lawvm.finland.compile.compile_fi_facade", fake_compile_fi_facade)

    facade = strict_report._build_facade_for_statute("1990/1295", mode="legal_pit")

    obligations = tuple(f for f in facade.finding_ledger if f.role == "obligation")
    assert len(obligations) == 1
    obl = obligations[0]
    assert obl.kind == "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION"
    assert obl.detail["tag"] == "chapter_scope_from_johtolause"
    assert obl.detail["target_norm"] == "35"
    assert obl.detail["target_chapter"] == "5"


def test_compile_one_replays_quietly(monkeypatch) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
    ):
        assert statute_id == "1990/1295"
        assert quiet is True
        return SimpleNamespace(
            source_adjudication=None,
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        lambda **kwargs: SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        ),
    )

    row = strict_report._compile_one((1, "1990/1295"))

    assert row["sid"] == "1990/1295"


def test_compile_one_prefers_typed_source_adjudication_lineage_over_replay_meta(monkeypatch) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
    ):
        assert statute_id == "1990/1295"
        assert quiet is True
        if replay_meta_out is not None:
            replay_meta_out.update(
                {
                    "lineage": [
                        {"included": False, "effective_date": ""},
                        {"included": False, "effective_date": ""},
                    ]
                }
            )
        return SimpleNamespace(
            source_adjudication=SimpleNamespace(
                lineage=(
                    {"included": True, "effective_date": "2025-01-01"},
                ),
                html_noncommensurable_reason="",
            ),
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        lambda **kwargs: SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        ),
    )

    row = strict_report._compile_one((1, "1990/1295"))

    assert row["chain_length"] == 1
    assert row["source_available"] == 1


def test_compile_one_hydrates_source_adjudication_from_replay_meta(monkeypatch) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
        lo_ops_out=None,
        failed_ops_out=None,
    ):
        assert statute_id == "1990/1295"
        assert quiet is True
        if replay_meta_out is not None:
            replay_meta_out.update(
                {
                    "lineage": [
                        {"included": True, "effective_date": "2025-01-01"},
                        {"included": False, "effective_date": ""},
                    ],
                    "oracle_version_amendment_id": "raw-mid",
                }
            )
        return SimpleNamespace(
            source_adjudication=None,
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        )

    monkeypatch.setattr("lawvm.finland.grafter.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.finland.compile.compile_fi_facade_from_replay",
        lambda **kwargs: SimpleNamespace(
            finding_ledger=(),
            verdict=None,
            bundle=SimpleNamespace(structural_ops=()),
        ),
    )

    row = strict_report._compile_one((1, "1990/1295"))

    assert row["chain_length"] == 2
    assert row["source_available"] == 1


def test_strict_report_main_suppresses_raw_replay_failed_chatter_for_1978_38(capsys) -> None:
    strict_report.main(
        Namespace(
            statute_id="1978/38",
            mode="legal_pit",
            facade=False,
            json_output=False,
            verbose=False,
        )
    )

    out = capsys.readouterr().out

    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out
