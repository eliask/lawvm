from __future__ import annotations

import json
from argparse import Namespace

from lawvm.tools import diagnose_phase


def test_diagnose_phase_json_suppresses_raw_replay_chatter_for_1978_38(capsys) -> None:
    diagnose_phase.main(
        Namespace(
            statute_id="1978/38",
            source="2003/741",
            target="",
            detector="duplicate_label",
            mode="legal_pit",
            first_bad_amendment="",
            certificate=False,
            json=True,
        )
    )

    captured = capsys.readouterr()
    merged = captured.out + captured.err

    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" not in merged
    payload = json.loads(captured.out)
    assert payload["statute_id"] == "1978/38"
    assert payload["source_id"] == "2003/741"
    assert "phases" in payload
