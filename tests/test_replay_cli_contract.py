from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from lawvm.core.ir import LegalAddress
from lawvm.core.timeline import ConsistencyDivergence
from lawvm.tools import cli, ee_replay, no_replay
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
            adjudications=[],
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
    )

    assert payload["jurisdiction"] == "uk"
    assert payload["base_id"] == "ukpga/1998/42"
    assert payload["oracle"]["eid_similarity"] == 0.75
    assert payload["timeline"]["mode"] == "ops_first"
    assert payload["timeline"]["versions"] == 120
