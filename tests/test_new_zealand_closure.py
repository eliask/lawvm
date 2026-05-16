from __future__ import annotations

from lawvm.tools.cli import _build_parser


def test_nz_corpus_closure_cli_parse_seed_defaults() -> None:
    parser = _build_parser()

    args = parser.parse_args(["nz-corpus", "closure", "--work-id", "act_public_1957_87"])

    assert args.command == "nz-corpus"
    assert args.nz_corpus_command == "closure"
    assert args.db == "data/nz_legislation.farchive"
    assert args.work_id == ["act_public_1957_87"]
    assert args.dependency_depth == 1
    assert args.max_versions_per_work == 1
    assert args.seed_latest_only is False
    assert args.sleep_on_rate_limit is False


def test_nz_corpus_closure_cli_parse_autonomous_all_acts() -> None:
    parser = _build_parser()

    args = parser.parse_args(
        [
            "nz-corpus",
            "closure",
            "--all-acts",
            "--sleep-on-rate-limit",
            "--publisher",
            "Parliamentary Counsel Office",
            "--state-json",
            ".tmp/nz_all_acts_state.json",
        ]
    )

    assert args.all_acts is True
    assert args.sleep_on_rate_limit is True
    assert args.legislation_type == "act"
    assert args.publisher == "Parliamentary Counsel Office"
    assert args.state_json == ".tmp/nz_all_acts_state.json"
