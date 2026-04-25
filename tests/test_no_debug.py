from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import lawvm.tools.no_debug as no_debug


def _fake_verify_result() -> SimpleNamespace:
    divergence = SimpleNamespace(
        address=SimpleNamespace(path=(("section", "7-1"), ("subsection", "1"))),
        divergence_type="MISMATCH",
        ops_text="ops-1",
        consolidated_text="cur-1",
    )
    return SimpleNamespace(
        base_id="no/lov/2024-01-12-1",
        as_of="2026-03-29",
        current_title="Lov om suppleringsskatt på underbeskattet inntekt i konsern (suppleringsskatteloven)",
        replay_status="replayed",
        consistent=False,
        divergence_count=1,
        divergence_counts={"MISMATCH": 1},
        raw_divergence_count=1,
        raw_divergence_counts={"MISMATCH": 1},
        divergences=[divergence],
        indexed_amendment_count=3,
        applied_amendment_count=3,
        replay_op_count=115,
        source_signal="",
        error=None,
    )


def test_no_debug_json_combines_reports(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: _fake_verify_result(),
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_law_report",
        lambda *args, **kwargs: {
            "title": "Lov om suppleringsskatt på underbeskattet inntekt i konsern (suppleringsskatteloven)",
            "amendment_count": 3,
            "replay_status": "fully_replayable",
            "executable_replay_status": "fully_replayable",
            "blocking_count": 0,
            "blocking_ops": 0,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_coverage.build_no_coverage_report",
        lambda *args, **kwargs: {
            "touched_divergence_count": 1,
            "untouched_divergence_count": 0,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_op_trace.build_no_op_trace_report",
        lambda *args, **kwargs: {
            "source_count": 2,
            "matched_source_count": 1,
            "op_count": 4,
            "sources": [
                {
                    "source_id": "no/lovtid/2025-12-22-123",
                    "effective_status": "dated",
                    "title": "Lov om endringer i suppleringsskatteloven",
                    "compiled_op_count": 59,
                    "matched_op_count": 1,
                }
            ],
            "ops": [
                {
                    "source_id": "no/lovtid/2025-12-22-123",
                    "sequence": 46,
                    "action": "replace",
                    "target_text": "section:7-1/subsection:1/item:c/item:1",
                }
            ],
        },
    )

    no_debug.main(
        Namespace(
            base_id="no/lov/2024-01-12-1",
            as_of="2026-03-29",
            data_dir="data/norway.farchive",
            index=".tmp/no_index_farchive.json",
            commencement=None,
            path=["section:7-1"],
            limit=1,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["base_id"] == "no/lov/2024-01-12-1"
    assert payload["overall_hint"] == "text_drift"
    assert payload["amendment_count"] == 3
    assert payload["source_count"] == 2
    assert payload["matched_source_count"] == 1
    assert payload["touched_divergence_count"] == 1
    assert payload["untouched_divergence_count"] == 0
    assert payload["divergences"][0]["address_text"] == "section:7-1/subsection:1"
    assert payload["ops"][0]["target_text"] == "section:7-1/subsection:1/item:c/item:1"


def test_no_debug_text_prints_combined_summary(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: _fake_verify_result(),
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_law_report",
        lambda *args, **kwargs: {
            "title": "Lov om suppleringsskatt på underbeskattet inntekt i konsern (suppleringsskatteloven)",
            "amendment_count": 3,
            "replay_status": "fully_replayable",
            "executable_replay_status": "fully_replayable",
            "blocking_count": 0,
            "blocking_ops": 0,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_coverage.build_no_coverage_report",
        lambda *args, **kwargs: {
            "touched_divergence_count": 1,
            "untouched_divergence_count": 0,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_op_trace.build_no_op_trace_report",
        lambda *args, **kwargs: {
            "source_count": 1,
            "matched_source_count": 1,
            "op_count": 1,
            "sources": [],
            "ops": [],
        },
    )

    no_debug.main(
        Namespace(
            base_id="no/lov/2024-01-12-1",
            as_of="2026-03-29",
            data_dir="data/norway.farchive",
            index=".tmp/no_index_farchive.json",
            commencement=None,
            path=[],
            limit=1,
            json=False,
        )
    )

    output = capsys.readouterr().out
    assert "Norway Debug" in output
    assert "overall hint" in output
    assert "law coverage" in output
    assert "trace coverage" in output
    assert "divergence split" in output
    assert "divergences:" in output


def test_no_debug_json_prefers_untouched_drift_hint_when_no_touched_divergences(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: _fake_verify_result(),
    )
    monkeypatch.setattr(
        "lawvm.norway.commencement.build_no_law_report",
        lambda *args, **kwargs: {
            "title": "Lov om suppleringsskatt på underbeskattet inntekt i konsern (suppleringsskatteloven)",
            "amendment_count": 3,
            "replay_status": "fully_replayable",
            "executable_replay_status": "fully_replayable",
            "blocking_count": 0,
            "blocking_ops": 0,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_coverage.build_no_coverage_report",
        lambda *args, **kwargs: {
            "touched_divergence_count": 0,
            "untouched_divergence_count": 1,
        },
    )
    monkeypatch.setattr(
        "lawvm.tools.no_op_trace.build_no_op_trace_report",
        lambda *args, **kwargs: {
            "source_count": 1,
            "matched_source_count": 1,
            "op_count": 1,
            "sources": [],
            "ops": [],
        },
    )

    no_debug.main(
        Namespace(
            base_id="no/lov/2024-01-12-1",
            as_of="2026-03-29",
            data_dir="data/norway.farchive",
            index=".tmp/no_index_farchive.json",
            commencement=None,
            path=[],
            limit=1,
            json=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["overall_hint"] == "untouched_base_current_drift"
    assert payload["touched_divergence_count"] == 0
    assert payload["untouched_divergence_count"] == 1
