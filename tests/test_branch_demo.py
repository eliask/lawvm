from __future__ import annotations

import json
import types

from lawvm.tools.branch_demo import build_branch_demo_payload, main
from lawvm.tools import cli


def test_branch_demo_payload_keeps_proposal_out_of_default_enacted_lane() -> None:
    payload = build_branch_demo_payload()

    assert payload["default_enacted_operation_ids"] == ("enacted-op-1",)
    assert payload["branch_operation_ids"] == ("proposal-op-1",)
    row = payload["impact_projection"]["rows"][0]
    assert row["edge_kind"] == "would_replace"
    assert row["current_text"] == "Current enacted text."
    assert row["branch_text"] == "Proposed branch text."


def test_branch_demo_main_outputs_json(capsys) -> None:
    main(types.SimpleNamespace(pretty=False))

    data = json.loads(capsys.readouterr().out)
    assert data["branch"]["authority_layer"] == "proposal"
    assert data["impact_projection"]["rows"][0]["operation_id"] == "proposal-op-1"


def test_branch_demo_cli_parser_accepts_pretty_flag() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(["branch-demo", "--pretty"])

    assert args.command == "branch-demo"
    assert args.pretty is True
