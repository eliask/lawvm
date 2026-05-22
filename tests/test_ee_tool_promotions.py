from __future__ import annotations

import csv
from argparse import Namespace
from types import SimpleNamespace

from lawvm.tools import (
    bench_regression_guard,
    cli,
    ee_bench,
    ee_chain_quality,
    ee_corpus,
    ee_publication_db,
    ee_pair_status,
    ops,
)


def test_cli_parser_accepts_promoted_ee_tools() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["ee-chain-quality", "162951"])
    assert args.command == "ee-chain-quality"
    assert args.grupi_ids == ["162951"]

    args = parser.parse_args(
        ["ee-pair-status", "--base-id", "193936", "--oracle-id", "13336397", "--json"]
    )
    assert args.command == "ee-pair-status"
    assert args.base_id == "193936"
    assert args.oracle_id == "13336397"
    assert args.json is True

    args = parser.parse_args(["ee-corpus", "acquire", "--phase", "1", "--parts", "2"])
    assert args.command == "ee-corpus"
    assert args.ee_corpus_command == "acquire"
    assert args.phase == 1
    assert args.parts == "2"

    args = parser.parse_args(["ee-corpus", "curate", "--laws-only"])
    assert args.command == "ee-corpus"
    assert args.ee_corpus_command == "curate"
    assert args.laws_only is True

    args = parser.parse_args(["ee-corpus", "replayable", "--laws-only"])
    assert args.command == "ee-corpus"
    assert args.ee_corpus_command == "replayable"
    assert args.laws_only is True

    args = parser.parse_args(["ee-corpus", "current"])
    assert args.command == "ee-corpus"
    assert args.ee_corpus_command == "current"
    assert args.laws_only is False

    args = parser.parse_args(["bench", "-j", "ee"])
    assert args.command == "bench"
    assert args.jurisdiction == "ee"
    assert args.include_decrees is True
    assert args.ee_corpus is None

    args = parser.parse_args(["bench", "-j", "ee", "--laws-only"])
    assert args.include_decrees is False

    args = parser.parse_args(
        ["ops", "-j", "ee", "102032022002", "--oracle-id", "125082023003", "--json"]
    )
    assert args.command == "ops"
    assert args.jurisdiction == "ee"
    assert args.statute_id == "102032022002"
    assert args.oracle_id == "125082023003"
    assert args.json is True

    args = parser.parse_args(
        ["explain", "-j", "ee", "102032022002", "--oracle-id", "125082023003", "--json"]
    )
    assert args.command == "explain"
    assert args.jurisdiction == "ee"
    assert args.statute_id == "102032022002"
    assert args.oracle_id == "125082023003"
    assert args.json is True

    args = parser.parse_args(["ee-publication-db", "--limit", "10", "--workers", "2"])
    assert args.command == "ee-publication-db"
    assert args.corpus == "data/estonia/current_replayable_corpus.csv"
    assert args.limit == 10
    assert args.workers == 2

    args = parser.parse_args(
        [
            "bench-regression-guard",
            "-j",
            "uk",
            "--baseline",
            "old",
            "--current",
            "new",
            "--duration-threshold-s",
            "2.5",
            "--max-duration-regressions",
            "1",
        ]
    )
    assert args.command == "bench-regression-guard"
    assert args.jurisdiction == "uk"
    assert args.baseline == "old"
    assert args.current == "new"
    assert args.duration_threshold_s == 2.5
    assert args.max_duration_regressions == 1


def test_ee_bench_defaults_to_current_replayable_corpus() -> None:
    assert ee_bench._CORPUS_CSV.name == "current_replayable_corpus.csv"


def test_ee_ops_command_emits_compiled_ops_json(capsys) -> None:
    ops._ops_ee_sync(
        "102032022002",
        source_filter="125082023001",
        target_filter="section:9_1",
        oracle_id="125082023003",
        as_of="",
        verbose=False,
        emit_json=True,
    )

    out = capsys.readouterr().out
    assert '"jurisdiction": "ee"' in out
    assert '"ops_total": 1' in out
    assert '"source_statute": "ee/125082023001"' in out
    assert '"target": "section:9_1"' in out
    assert '"witness_rule_id": "ee_act_citation_section_insert_target"' in out


def test_ee_chain_quality_run_chain_prints_totals(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        ee_chain_quality,
        "fetch_redactions_feed",
        lambda grupi_id, archive: [
            SimpleNamespace(aktViide="a1", effective="2020-01-01"),
            SimpleNamespace(aktViide="a2", effective="2021-01-01"),
        ],
    )
    monkeypatch.setattr(
        ee_chain_quality,
        "replay_ee_to_pit",
        lambda *args, **kwargs: SimpleNamespace(
            error="",
            oracle=object(),
            oracle_id="a2",
            n_ops=5,
            n_mismatch=1,
            n_ops_missing=0,
            n_con_missing=0,
            divergences=[object()],
        ),
    )

    totals = ee_chain_quality.run_chain("162951", "Courts Act", archive=object())

    out = capsys.readouterr().out
    assert "Courts Act  (grupiId=162951)" in out
    assert "a1" in out
    assert "TOTAL" in out
    assert totals["pairs"] == 1
    assert totals["tot"] == 1


def test_ee_pair_status_main_prints_matched_and_open(capsys, monkeypatch) -> None:
    monkeypatch.setattr(ee_pair_status, "open_rt_archive", lambda path: object())
    monkeypatch.setattr(ee_pair_status, "fetch_rt_xml", lambda base_id, archive: b"<base/>")
    monkeypatch.setattr(
        ee_pair_status,
        "plan_ee_oracle_pair",
        lambda **kwargs: SimpleNamespace(plan=SimpleNamespace(source_basis=SimpleNamespace(value="pairwise_terviktekst_delta"))),
    )
    monkeypatch.setattr(
        ee_pair_status,
        "_score_one_pair",
        lambda gid, base_id, oracle_id, title, archive: SimpleNamespace(
            base_id=base_id,
            oracle_id=oracle_id,
            title=title,
            as_of="2011-03-17",
            status="OK",
            comparison_class="commensurable_delta",
            core_benchmark=True,
            n_ops=217,
            n_divs=7,
            sec_match=0.99,
            r_secs=852,
            o_secs=852,
            adjudicated_residual_count=7,
            matched_current_residual_count=7,
            adjudicated_bucket_counts="appendix_display_pathology=1,source_oracle_drift=6",
            unknown_current_residual_count=0,
            open_current_divergence_count=0,
        ),
    )

    payload = ee_pair_status.build_pair_status_payload(
        base_id="193936",
        oracle_id="13336397",
        title="Liiklusseadus",
    )
    ee_pair_status.main(
        Namespace(base_id="193936", oracle_id="13336397", title="Liiklusseadus", json=False)
    )

    out = capsys.readouterr().out
    assert "=== EE Pair Status ===" in out
    assert "basis      : pairwise_terviktekst_delta" in out
    assert "reporting  : EE_CORE_COMMENSURABLE (headline=yes)" in out
    assert "drift      : 2 non-silent comparison rules" in out
    assert "classes   : lexical_institutional_drift=2, manual_exception=0" in out
    assert "rules     : politseiasutus_rename, politsei_plural_rename" in out
    assert "matched current       : 7" in out
    assert "open current          : 0" in out
    assert payload["benchmark_reporting_stratum"] == "EE_CORE_COMMENSURABLE"
    assert payload["benchmark_reporting_headline_eligible"] is True
    assert payload["comparison_policy"]["non_silent_rule_count"] == 2
    assert payload["comparison_policy"]["non_silent_rule_names"] == [
        "politseiasutus_rename",
        "politsei_plural_rename",
    ]

def test_ee_corpus_summarize_pairs_counts_laws_decrees_and_buckets() -> None:
    schema_counts, n_laws, n_decrees, amend_buckets = ee_corpus.summarize_pairs(
        [
            ("g1", "b1", "o1", 0, "tyviseadus"),
            ("g2", "b2", "o2", 2, "maarus"),
            ("g3", "b3", "o3", 12, "juurakt"),
        ]
    )

    assert schema_counts == {"tyviseadus": 1, "maarus": 1, "juurakt": 1}
    assert n_laws == 1
    assert n_decrees == 2
    assert amend_buckets == {"0": 1, "2-3": 1, "11-50": 1}


def test_ee_corpus_run_curate_writes_csv_and_notes(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(ee_corpus, "build_index", lambda archive: {"g1": SimpleNamespace()})
    monkeypatch.setattr(
        ee_corpus,
        "select_pairs",
        lambda groups, include_decrees: ([("g1", "b1", "o1", 2, "tyviseadus")], {"x": 1}),
    )
    notes_path = tmp_path / "notes.md"
    csv_path = tmp_path / "bench.csv"
    db_path = tmp_path / "archive.db"
    db_path.write_bytes(b"stub")

    class _Archive:
        def close(self) -> None:
            return None

    monkeypatch.setattr(ee_corpus, "open_rt_archive", lambda path: _Archive())

    ee_corpus.run_curate(
        Namespace(
            db=str(db_path),
            laws_only=False,
            output_csv=str(csv_path),
            output_notes=str(notes_path),
        )
    )

    out = capsys.readouterr().out
    assert "Selected: 1 pairs" in out
    assert csv_path.exists()
    assert notes_path.exists()
    rows = list(csv.DictReader(csv_path.open()))
    assert rows[0]["grupi_id"] == "g1"
    assert rows[0]["schema"] == "tyviseadus"


def test_ee_corpus_select_replayable_pairs_uses_all_consecutive_versions() -> None:
    groups = {
        "g1": ee_corpus._GroupInfo(
            grupi_id="g1",
            terviktekst_with_body=[
                ("a3", 3000, "2022-01-01"),
                ("a1", 3000, "2020-01-01"),
                ("a2", 3000, "2021-01-01"),
            ],
            n_amendments=3,
            schemas={"tyviseadus"},
            title="Test Act",
        ),
        "g2": ee_corpus._GroupInfo(
            grupi_id="g2",
            terviktekst_with_body=[("b1", 3000, "2020-01-01")],
            n_amendments=1,
            schemas={"tyviseadus"},
        ),
    }

    pairs, excluded = ee_corpus.select_replayable_pairs(groups, include_decrees=False)

    assert excluded == {"fewer_than_2_tervikteksts": 1}
    assert [(row[1], row[2], row[7], row[8]) for row in pairs] == [
        ("a1", "a2", 1, 3),
        ("a2", "a3", 2, 3),
    ]
    assert pairs[0][5:7] == ("2020-01-01", "2021-01-01")


def test_ee_corpus_select_current_replayable_pairs_uses_latest_pair_only() -> None:
    groups = {
        "g1": ee_corpus._GroupInfo(
            grupi_id="g1",
            terviktekst_with_body=[
                ("a1", 3000, "2020-01-01"),
                ("a2", 3000, "2021-01-01"),
                ("a3", 3000, "2022-01-01"),
            ],
            n_amendments=3,
            schemas={"tyviseadus"},
            title="Test Act",
        ),
        "g2": ee_corpus._GroupInfo(
            grupi_id="g2",
            terviktekst_with_body=[
                ("b1", 3000, "2020-01-01"),
                ("b2", 3000, "2021-01-01"),
            ],
            n_amendments=0,
            schemas={"tyviseadus"},
            title="Unamended Act",
        ),
        "g3": ee_corpus._GroupInfo(
            grupi_id="g3",
            terviktekst_with_body=[("c1", 3000, "2020-01-01")],
            n_amendments=1,
            schemas={"tyviseadus"},
            title="Single Version",
        ),
    }

    pairs, excluded = ee_corpus.select_current_replayable_pairs(groups, include_decrees=False)

    assert excluded == {"no_amendments": 1, "fewer_than_2_tervikteksts": 1}
    assert [(row[1], row[2], row[7], row[8], row[9]) for row in pairs] == [
        ("a2", "a3", 2, 3, "Test Act"),
    ]


def test_ee_publication_db_builds_sqlite_from_replayable_corpus(tmp_path, monkeypatch) -> None:
    from lawvm.core.ir import IRNode, IRStatute, LegalAddress
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.core.timeline import ConsistencyDivergence

    corpus = tmp_path / "replayable.csv"
    corpus.write_text(
        "\n".join(
            [
                "grupi_id,base_id,oracle_id,n_amendments,schema,base_effective,oracle_effective,version_index,version_count,title",
                "g1,b1,o1,2,tyviseadus,2020-01-01,2021-01-01,1,2,Test Act",
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "ee.db"
    archive_path = tmp_path / "archive.farchive"
    archive_path.write_bytes(b"stub")

    class _Archive:
        def close(self) -> None:
            return None

    monkeypatch.setattr(ee_publication_db, "open_rt_archive", lambda path, readonly=True: _Archive())
    monkeypatch.setattr(ee_publication_db, "fetch_rt_xml", lambda oracle_id, archive=None: b"<xml/>")
    monkeypatch.setattr(ee_publication_db, "extract_effective_date", lambda xml: "2021-01-01")
    monkeypatch.setattr(
        ee_publication_db,
        "replay_ee_to_pit",
        lambda **kwargs: SimpleNamespace(
            error="",
            replayed=IRStatute(
                statute_id="b1",
                title="Test Act",
                body=IRNode(
                    IRNodeKind.BODY,
                    children=(
                        IRNode(IRNodeKind.SECTION, label="1", text="replay"),
                        IRNode(IRNodeKind.SECTION, label="2", text="same"),
                    ),
                ),
            ),
            oracle=IRStatute(
                statute_id="o1",
                title="Test Act",
                body=IRNode(
                    IRNodeKind.BODY,
                    children=(
                        IRNode(IRNodeKind.SECTION, label="1", text="oracle"),
                        IRNode(IRNodeKind.SECTION, label="2", text="same"),
                    ),
                ),
            ),
            source_basis="pairwise_terviktekst_delta",
            comparison_class="commensurable_delta",
            source_adjudication=SimpleNamespace(oracle_suspect=""),
            n_ops=1,
            n_mismatch=1,
            n_ops_missing=0,
            n_con_missing=0,
            as_of="2021-01-01",
            divergences=[
                ConsistencyDivergence(
                    address=LegalAddress(path=(("section", "1"),)),
                    divergence_type="MISMATCH",
                    ops_text="",
                    consolidated_text="oracle",
                )
            ],
        ),
    )

    stats = ee_publication_db.build_ee_publication_db(
        corpus_path=corpus,
        output_path=output,
        archive_path=archive_path,
        workers=1,
    )

    assert stats == {
        "pairs": 1,
        "errors": 0,
        "divergences": 1,
        "open_divergences": 1,
        "meaningful_candidates": 1,
    }
    import sqlite3

    con = sqlite3.connect(output)
    try:
        pair = con.execute(
            """
            SELECT base_id, oracle_id, divergence_count, section_total_count,
                   section_identical_count, section_divergent_count,
                   section_text_total_chars, section_text_identical_chars
            FROM pairs
            """
        ).fetchone()
        divergence = con.execute(
            """
            SELECT d.address, rt.text, ot.text, d.outreach_bucket,
                   d.meaningful_candidate, d.outreach_evidence
            FROM divergences d
            LEFT JOIN text_blobs rt ON rt.text_hash = d.replay_text_hash
            LEFT JOIN text_blobs ot ON ot.text_hash = d.oracle_text_hash
            """
        ).fetchone()
    finally:
        con.close()
    assert pair == ("b1", "o1", 1, 2, 1, 1, 10, 4)
    assert divergence[:5] == ("section:1", "", "oracle", "publication_candidate", 1)
    assert "candidate for human review" in divergence[5]


def test_ee_publication_db_classifies_exact_cross_address_text_shadows() -> None:
    moved_text = "Moved section text that is long enough to avoid generic short-label matching."
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "Old section text that remains at the replay address.",
            "oracle_text": moved_text,
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:1/section:2",
            "replay_text": moved_text,
            "oracle_text": "Different section text at the oracle address.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:1/section:3",
            "replay_text": "short",
            "oracle_text": "short",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_address_alignment_shadows(divergences)

    assert divergences[0]["residual_bucket"] == "address_alignment_shadow"
    assert divergences[0]["open_current"] == 0
    assert divergences[0]["alignment_peer_addresses"] == "chapter:1/section:2"
    assert divergences[1]["residual_bucket"] == "address_alignment_shadow"
    assert divergences[1]["open_current"] == 0
    assert divergences[1]["alignment_peer_addresses"] == "chapter:1/section:1"
    assert divergences[2]["residual_bucket"] is None
    assert divergences[2]["open_current"] == 1


def test_ee_publication_db_classifies_failed_amendment_chain_as_coverage_gap() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "changed text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:1/section:2",
            "replay_text": "known drift",
            "oracle_text": "known drift changed",
            "residual_bucket": "source_oracle_drift",
            "residual_evidence": "already adjudicated",
            "alignment_peer_addresses": "",
            "open_current": 0,
        },
    ]

    ee_publication_db._classify_replay_coverage_gaps(
        divergences,
        amendments_failed=["123122017034"],
        unsupported_action_sources=[],
        unparsed_operation_sources=[],
        n_ops=0,
        comparison_class="commensurable_delta",
    )

    assert divergences[0]["residual_bucket"] == "replay_coverage_gap"
    assert divergences[0]["open_current"] == 0
    assert "123122017034" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] == "source_oracle_drift"
    assert divergences[1]["residual_evidence"] == "already adjudicated"


def test_ee_publication_db_classifies_unsupported_action_as_coverage_gap() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "replacement text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_replay_coverage_gaps(
        divergences,
        amendments_failed=[],
        unsupported_action_sources=["ee/117022021004"],
        unparsed_operation_sources=[],
        n_ops=1,
        comparison_class="commensurable_delta",
    )

    assert divergences[0]["residual_bucket"] == "replay_coverage_gap"
    assert divergences[0]["open_current"] == 0
    assert "unsupported source refs: ee/117022021004" in divergences[0]["residual_evidence"]


def test_ee_publication_db_does_not_classify_meta_skip_as_unsupported_action() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "replacement text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_replay_coverage_gaps(
        divergences,
        amendments_failed=[],
        unsupported_action_sources=[],
        unparsed_operation_sources=[],
        n_ops=1,
        comparison_class="commensurable_delta",
    )

    assert divergences[0]["residual_bucket"] is None
    assert divergences[0]["open_current"] == 1


def test_ee_publication_db_classifies_unparsed_operation_as_coverage_gap() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "replacement text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_replay_coverage_gaps(
        divergences,
        amendments_failed=[],
        unsupported_action_sources=[],
        unparsed_operation_sources=["ee/116012013003"],
        n_ops=1,
        comparison_class="commensurable_delta",
    )

    assert divergences[0]["residual_bucket"] == "replay_coverage_gap"
    assert divergences[0]["open_current"] == 0
    assert "unparsed source refs: ee/116012013003" in divergences[0]["residual_evidence"]


def test_ee_publication_db_classifies_empty_program_as_coverage_gap() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "changed text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_replay_coverage_gaps(
        divergences,
        amendments_failed=[],
        unsupported_action_sources=[],
        unparsed_operation_sources=[],
        n_ops=0,
        comparison_class="commensurable_delta",
    )

    assert divergences[0]["residual_bucket"] == "replay_coverage_gap"
    assert divergences[0]["open_current"] == 0
    assert "compiled no executable amendment operations" in divergences[0]["residual_evidence"]


def test_ee_publication_db_does_not_reclassify_zero_ops_noncommensurable_pair() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "future text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_replay_coverage_gaps(
        divergences,
        amendments_failed=[],
        unsupported_action_sources=[],
        unparsed_operation_sources=[],
        n_ops=0,
        comparison_class="forward_looking_oracle",
    )

    assert divergences[0]["residual_bucket"] is None
    assert divergences[0]["open_current"] == 1


def test_ee_publication_db_classifies_exact_institutional_name_projection() -> None:
    divergences = [
        {
            "address": "chapter:1/section:3",
            "replay_text": "Meetme rakendusasutus on Siseministeerium.",
            "oracle_text": "Meetme rakendusasutus on Rahandusministeerium.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:1/section:4",
            "replay_text": "Meetme rakendusasutus on Siseministeerium ja muu tekst.",
            "oracle_text": "Meetme rakendusasutus on Rahandusministeerium ja teine tekst.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_institutional_name_projection(divergences)

    assert divergences[0]["residual_bucket"] == "source_oracle_drift"
    assert divergences[0]["open_current"] == 0
    assert "Siseministeerium -> Rahandusministeerium" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] is None
    assert divergences[1]["open_current"] == 1


def test_ee_publication_db_classifies_exact_source_typo_projection() -> None:
    divergences = [
        {
            "address": "section:2",
            "replay_text": "Andmekogu haldab Tarbijakatise ja Tehnilise Järelevalve Amet.",
            "oracle_text": "Andmekogu haldab Tarbijakaitse ja Tehnilise Järelevalve Amet.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "section:3",
            "replay_text": "Andmekogu haldab Tarbijakatise ja Tehnilise Järelevalve Amet ja muu tekst.",
            "oracle_text": "Andmekogu haldab Tarbijakaitse ja Tehnilise Järelevalve Amet ja teine tekst.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_source_typo_projection(divergences)

    assert divergences[0]["residual_bucket"] == "source_oracle_drift"
    assert divergences[0]["open_current"] == 0
    assert "ee_source_typo_126022019001_tarbijakaitse" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] is None
    assert divergences[1]["open_current"] == 1


def test_ee_publication_db_classifies_exact_symbol_placeholder_projection() -> None:
    divergences = [
        {
            "address": "chapter:3/section:220",
            "replay_text": "CCRM j puhul; β=1,4. Tsoon > 1 ≤ 5 aastat.",
            "oracle_text": "CCRM j puhul; ?=1,4. Tsoon > 1? 5 aastat.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:3/section:310",
            "replay_text": "sisuline tekst β=1,4 ja veel midagi",
            "oracle_text": "sisuline teine tekst ?=1,4 ja veel midagi",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_symbol_placeholder_projection(divergences)

    assert divergences[0]["residual_bucket"] == "source_oracle_drift"
    assert divergences[0]["open_current"] == 0
    assert "symbol-placeholder projection" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] is None
    assert divergences[1]["open_current"] == 1


def test_ee_publication_db_classifies_punctuation_whitespace_only_rows() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "EVS-EN 16798–1 nõuded; pindala 100 m2.",
            "oracle_text": "EVS EN 16798 1 nõuded pindala 100 m2",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:1/section:2",
            "replay_text": "Sisuline tekst 100 m2.",
            "oracle_text": "Sisuline tekst 101 m2.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_punctuation_whitespace_only(divergences)

    assert divergences[0]["residual_bucket"] == "presentation_punctuation_whitespace"
    assert divergences[0]["open_current"] == 0
    assert "punctuation and whitespace" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] is None
    assert divergences[1]["open_current"] == 1


def test_ee_publication_db_classifies_publication_note_projection_rows() -> None:
    divergences = [
        {
            "address": "chapter:6/section:19",
            "replay_text": (
                "Rakendussätted Esimene lause. Määruse lisad on avaldatud "
                "elektroonilises Riigi Teatajas. Alus: \"Riigi Teataja seaduse\" "
                "§4 lõige 2 ja riigisekretäri 1.09.2008. a resolutsioon nr "
                "17–1/08–05359. Teine lause."
            ),
            "oracle_text": "Rakendussätted Esimene lause. Teine lause.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:4/section:12",
            "replay_text": (
                "Määruse jõustumine Määrus jõustub 2006. aasta 1. jaanuaril. "
                "1Euroopa Parlamendi ja EL nõukogu direktiiv 2002/19/EÜ "
                "elektroonilistele sidevõrkudele ja nendega seotud vahenditele "
                "juurdepääsu ja vastastikuse sidumise kohta (juurdepääsu "
                "käsitlev direktiiv) (ELT L 108, 24.04.2002, lk 7–20)."
            ),
            "oracle_text": "Määruse jõustumine Määrus jõustub 2006. aasta 1. jaanuaril.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:4/section:13",
            "replay_text": "Määruse jõustumine Sisuline tekst. 1Euroopa Parlamendi muu viide.",
            "oracle_text": "Määruse jõustumine Teine sisuline tekst.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_publication_note_projection(divergences)

    assert divergences[0]["residual_bucket"] == "publication_note_projection"
    assert divergences[0]["open_current"] == 0
    assert "publication/legal-basis note" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] == "publication_note_projection"
    assert divergences[1]["open_current"] == 0
    assert "publication/legal-basis note" in divergences[1]["residual_evidence"]
    assert divergences[2]["residual_bucket"] is None
    assert divergences[2]["open_current"] == 1


def test_ee_publication_db_classifies_omitted_text_placeholder_display_rows() -> None:
    divergences = [
        {
            "address": "chapter:5/section:68",
            "replay_text": "",
            "oracle_text": "Määruse kehtetuks tunnistamine [Käesolevast tekstist välja jäetud].",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:3/section:9",
            "replay_text": "[Kehtetud]",
            "oracle_text": "",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "chapter:6/section:27",
            "replay_text": "Määruse jõustumine [Käesolevast tekstist välja jäetud]",
            "oracle_text": "Määruse jõustumine Määrus jõustub 1. mail 2003. a.",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_omitted_text_placeholder_display(divergences)

    assert divergences[0]["residual_bucket"] == "presentation_omitted_text_placeholder"
    assert divergences[0]["open_current"] == 0
    assert "omitted-text/repealed-section display" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] == "presentation_omitted_text_placeholder"
    assert divergences[1]["open_current"] == 0
    assert divergences[2]["residual_bucket"] is None
    assert divergences[2]["open_current"] == 1


def test_ee_publication_db_classifies_descendant_projection_residuals() -> None:
    divergences = [
        {
            "address": "section:1",
            "divergence_type": "MISMATCH",
            "replay_text": "Heading Child table text",
            "oracle_text": "Heading",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "section:2",
            "divergence_type": "MISMATCH",
            "replay_text": "Heading Different body",
            "oracle_text": "Heading",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]
    raw_divergences = [
        {
            "address": "section:1",
            "divergence_type": "MISMATCH",
            "replay_text": "Heading Child table text",
            "oracle_text": "Heading",
        },
        {
            "address": "section:1/subsection:1",
            "divergence_type": "CONSOLIDATED_MISSING",
            "replay_text": "Child table text",
            "oracle_text": None,
        },
        {
            "address": "section:2/subsection:1",
            "divergence_type": "CONSOLIDATED_MISSING",
            "replay_text": "Child table text",
            "oracle_text": None,
        },
    ]

    ee_publication_db._classify_descendant_projection_residuals(
        divergences,
        raw_divergences=raw_divergences,
    )

    assert divergences[0]["residual_bucket"] == "comparison_descendant_projection"
    assert divergences[0]["open_current"] == 0
    assert divergences[0]["alignment_peer_addresses"] == "section:1/subsection:1"
    assert "descendant missing-row" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] is None
    assert divergences[1]["open_current"] == 1


def test_ee_publication_db_classifies_table_fragment_replay_gaps() -> None:
    divergences = [
        {
            "address": "section:1",
            "divergence_type": "MISMATCH",
            "replay_text": "PEREMEDITSIINI OSAKOND Osakonnajuhataja 1 Analüütik 1 Peaspetsialist 3",
            "oracle_text": (
                "Terviseameti struktuuri ja koosseisu kinnitamine "
                "Terviseameti struktuur ja teenistujate koosseis kinnitatakse "
                "alljärgnevalt: Ametinimetus Koosseisuüksuste arv"
            ),
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
        {
            "address": "section:2",
            "divergence_type": "MISMATCH",
            "replay_text": "Short unrelated section fragment that should not classify",
            "oracle_text": "Longer heading text for a different section",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]
    raw_divergences = [
        {
            "address": "section:1/subsection:1",
            "divergence_type": "OPS_MISSING",
            "replay_text": None,
            "oracle_text": (
                "Terviseameti struktuur ja teenistujate koosseis kinnitatakse "
                "alljärgnevalt: PEREMEDITSIINI OSAKOND Osakonnajuhataja 1 "
                "Analüütik 1 Peaspetsialist 3 Kõik kokku"
            ),
        },
        {
            "address": "section:2/subsection:1",
            "divergence_type": "OPS_MISSING",
            "replay_text": None,
            "oracle_text": "A different child body that does not contain the replay fragment.",
        },
    ]

    ee_publication_db._classify_table_fragment_replay_gaps(
        divergences,
        raw_divergences=raw_divergences,
    )

    assert divergences[0]["residual_bucket"] == "table_fragment_replay_gap"
    assert divergences[0]["open_current"] == 0
    assert divergences[0]["alignment_peer_addresses"] == "section:1/subsection:1"
    assert "tabeliosa operation" in divergences[0]["residual_evidence"]
    assert divergences[1]["residual_bucket"] is None
    assert divergences[1]["open_current"] == 1


def test_ee_publication_db_classifies_noncommensurable_pair_surface() -> None:
    divergences = [
        {
            "address": "chapter:1/section:1",
            "replay_text": "base text",
            "oracle_text": "future text",
            "residual_bucket": None,
            "residual_evidence": None,
            "alignment_peer_addresses": "",
            "open_current": 1,
        },
    ]

    ee_publication_db._classify_noncommensurable_pair_surface(
        divergences,
        comparison_class="forward_looking_oracle",
    )

    assert divergences[0]["residual_bucket"] == "pair_surface_classification"
    assert divergences[0]["open_current"] == 0
    assert "forward_looking_oracle" in divergences[0]["residual_evidence"]


def test_ee_publication_db_assigns_publication_outreach_triage() -> None:
    divergences = [
        {
            "residual_bucket": None,
            "open_current": 1,
        },
        {
            "residual_bucket": "presentation_punctuation_whitespace",
            "open_current": 0,
        },
        {
            "residual_bucket": "replay_coverage_gap",
            "open_current": 0,
        },
        {
            "residual_bucket": "table_fragment_replay_gap",
            "open_current": 0,
        },
        {
            "residual_bucket": "source_oracle_drift",
            "open_current": 0,
        },
        {
            "residual_bucket": "pair_surface_classification",
            "open_current": 0,
        },
        {
            "residual_bucket": "comparison_descendant_projection",
            "open_current": 0,
        },
    ]

    ee_publication_db._assign_publication_outreach_triage(divergences)

    assert [
        divergence["outreach_bucket"] for divergence in divergences
    ] == [
        "publication_candidate",
        "excluded_presentation",
        "excluded_replay_coverage",
        "excluded_replay_coverage",
        "excluded_source_surface",
        "excluded_pair_surface",
        "excluded_comparison_projection",
    ]
    assert [divergence["meaningful_candidate"] for divergence in divergences] == [1, 0, 0, 0, 0, 0, 0]
    assert "punctuation_whitespace" in str(divergences[1]["outreach_evidence"])


def test_bench_regression_guard_run_guard_pass_and_fail(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(bench_regression_guard, "BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "20260329_old.csv"
    current = tmp_path / "20260329_new.csv"
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "similarity"])
        writer.writeheader()
        writer.writerow({"statute_id": "s1", "similarity": "0.90"})
        writer.writerow({"statute_id": "s2", "similarity": "0.95"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "similarity"])
        writer.writeheader()
        writer.writerow({"statute_id": "s1", "similarity": "0.91"})
        writer.writerow({"statute_id": "s2", "similarity": "0.949"})

    rc = bench_regression_guard.run_guard("old", "new", threshold=0.02, max_regressions=1)
    out = capsys.readouterr().out
    assert rc == 0
    assert "RESULT: PASS" in out

    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "similarity"])
        writer.writeheader()
        writer.writerow({"statute_id": "s1", "similarity": "0.70"})
        writer.writerow({"statute_id": "s2", "similarity": "0.60"})

    rc = bench_regression_guard.run_guard("old", "new", threshold=0.02, max_regressions=0)
    out = capsys.readouterr().out
    assert rc == 1
    assert "RESULT: FAIL" in out


def test_bench_regression_guard_fails_without_common_scored_statutes(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "20260329_old.csv"
    current = tmp_path / "20260329_new.csv"
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "similarity"])
        writer.writeheader()
        writer.writerow({"statute_id": "s1", "similarity": "0.90"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "similarity"])
        writer.writeheader()
        writer.writerow({"statute_id": "s2", "similarity": "0.95"})

    rc = bench_regression_guard.run_guard("old", "new", threshold=0.02, max_regressions=0)

    out = capsys.readouterr().out
    assert rc == 1
    assert "Common statutes      : 0" in out
    assert "ERROR: baseline and current have no common scored statutes" in out


def test_bench_regression_guard_supports_uk_saved_run_shape(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "UK_BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "old.csv"
    current = tmp_path / "new.csv"
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "score", "replay_score"])
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90", "replay_score": "0.95"})
        writer.writerow({"statute_id": "ukpga/2000/2", "score": "0.80", "replay_score": "0.85"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "score", "replay_score"])
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.91", "replay_score": "0.95"})
        writer.writerow({"statute_id": "ukpga/2000/2", "score": "0.79", "replay_score": "0.85"})

    assert bench_regression_guard.find_csv_by_label("old", jurisdiction="uk") == baseline
    assert bench_regression_guard.load_scores(baseline) == {
        "ukpga/2000/1": 0.90,
        "ukpga/2000/2": 0.80,
    }

    rc = bench_regression_guard.run_guard(
        "old",
        "new",
        threshold=0.02,
        max_regressions=0,
        jurisdiction="uk",
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Baseline : old.csv" in out
    assert "Current  : new.csv" in out
    assert "Score column         : uk_replay_primary" in out
    assert "RESULT: PASS" in out


def test_bench_regression_guard_prefers_uk_replay_score_when_present(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "UK_BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "old.csv"
    current = tmp_path / "new.csv"
    fieldnames = ["statute_id", "score", "replay_score"]
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.70", "replay_score": "0.95"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.70", "replay_score": "0.70"})

    rc = bench_regression_guard.run_guard(
        "old",
        "new",
        threshold=0.02,
        max_regressions=0,
        jurisdiction="uk",
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "Score column         : uk_replay_primary" in out
    assert "ukpga/2000/1" in out
    assert "RESULT: FAIL" in out


def test_bench_regression_guard_uses_rowwise_uk_replay_primary_score(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "UK_BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "old.csv"
    current = tmp_path / "new.csv"
    fieldnames = ["statute_id", "score", "replay_score", "replay_commencement_score"]
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "ukpga/2000/1",
                "score": "0.70",
                "replay_score": "0.90",
                "replay_commencement_score": "",
            }
        )
        writer.writerow(
            {
                "statute_id": "ukpga/2000/2",
                "score": "0.70",
                "replay_score": "0.80",
                "replay_commencement_score": "0.95",
            }
        )
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "ukpga/2000/1",
                "score": "0.70",
                "replay_score": "0.90",
                "replay_commencement_score": "",
            }
        )
        writer.writerow(
            {
                "statute_id": "ukpga/2000/2",
                "score": "0.70",
                "replay_score": "0.80",
                "replay_commencement_score": "0.95",
            }
        )

    rc = bench_regression_guard.run_guard(
        "old",
        "new",
        threshold=0.02,
        max_regressions=0,
        jurisdiction="uk",
    )

    out = capsys.readouterr().out
    assert rc == 0
    assert "Statutes in baseline : 2" in out
    assert "Score column         : uk_replay_primary" in out


def test_bench_regression_guard_can_fail_on_duration_regressions(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "UK_BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "old.csv"
    current = tmp_path / "new.csv"
    fieldnames = ["statute_id", "score", "duration_s"]
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90", "duration_s": "1.0"})
        writer.writerow({"statute_id": "ukpga/2000/2", "score": "0.80", "duration_s": "2.0"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90", "duration_s": "1.4"})
        writer.writerow({"statute_id": "ukpga/2000/2", "score": "0.80", "duration_s": "4.5"})

    rc = bench_regression_guard.run_guard(
        "old",
        "new",
        threshold=0.02,
        max_regressions=0,
        jurisdiction="uk",
        duration_threshold_s=1.0,
        max_duration_regressions=0,
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "Duration regressions > 1.000s : 1 statute(s)" in out
    assert "ukpga/2000/2" in out
    assert "RESULT: FAIL" in out


def test_bench_regression_guard_requires_duration_column_when_enabled(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "UK_BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "old.csv"
    current = tmp_path / "new.csv"
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "score"])
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["statute_id", "score"])
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90"})

    rc = bench_regression_guard.run_guard(
        "old",
        "new",
        threshold=0.02,
        max_regressions=0,
        jurisdiction="uk",
        max_duration_regressions=0,
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR loading duration CSV data" in out
    assert "missing expected column 'duration_s'" in out


def test_bench_regression_guard_fails_without_common_duration_rows(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(bench_regression_guard, "UK_BENCH_RUNS_DIR", tmp_path)

    baseline = tmp_path / "old.csv"
    current = tmp_path / "new.csv"
    fieldnames = ["statute_id", "score", "duration_s"]
    with baseline.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90", "duration_s": "1.0"})
    with current.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"statute_id": "ukpga/2000/1", "score": "0.90", "duration_s": ""})

    rc = bench_regression_guard.run_guard(
        "old",
        "new",
        threshold=0.02,
        max_regressions=0,
        jurisdiction="uk",
        max_duration_regressions=0,
    )

    out = capsys.readouterr().out
    assert rc == 1
    assert "ERROR: baseline and current have no common duration_s rows" in out
