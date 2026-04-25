from __future__ import annotations

import json
from argparse import Namespace

from lawvm.tools import verify_chain


def test_verify_chain_main_suppresses_raw_replay_chatter_for_1978_38(
    capsys,
    tmp_path,
) -> None:
    verify_chain.main(
        Namespace(
            sids=["1978/38"],
            no_html=True,
            output=str(tmp_path),
        )
    )

    captured = capsys.readouterr()
    merged = captured.out + captured.err

    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" not in merged
    assert "REPLACE 10 luku otsikko → FAILED" not in merged
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in merged

    payload = json.loads((tmp_path / "1978_38.json").read_text(encoding="utf-8"))
    assert payload["statute_id"] == "1978/38"
    assert payload["total_amendments"] > 0
