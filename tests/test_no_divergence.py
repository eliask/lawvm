from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import lawvm.tools.no_divergence as no_divergence


def _fake_result(*, source_signal: str = "", divergences: list[object] | None = None):
    counts: dict[str, int] = {}
    for divergence in divergences or []:
        kind = getattr(divergence, "divergence_type", "MISMATCH")
        counts[kind] = counts.get(kind, 0) + 1
    return SimpleNamespace(
        base_id="no/lov/2024-01-12-1",
        as_of="2026-03-29",
        current_title="Lov om suppleringsskatt på underbeskattet inntekt i konsern (suppleringsskatteloven)",
        replay_status="replayed",
        consistent=False,
        divergence_count=len(divergences or []),
        divergence_counts=counts,
        raw_divergence_count=len(divergences or []),
        raw_divergence_counts=counts,
        divergences=divergences or [],
        indexed_amendment_count=3,
        applied_amendment_count=3,
        replay_op_count=115,
        source_signal=source_signal,
        error=None,
    )


def _fake_divergence(kind: str, path: list[tuple[str, str]], ops_text: str, consolidated_text: str):
    return SimpleNamespace(
        address=SimpleNamespace(path=tuple(path)),
        divergence_type=kind,
        ops_text=ops_text,
        consolidated_text=consolidated_text,
    )


def test_no_divergence_json_emits_bounded_primary_divergences(monkeypatch, capsys) -> None:
    divergences = [
        _fake_divergence("MISMATCH", [("chapter", "I"), ("section", "4-2")], "ops-1", "cur-1"),
        _fake_divergence("OPS_MISSING", [("section", "7-1")], "ops-2", "cur-2"),
    ]
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: _fake_result(divergences=divergences),
    )
    monkeypatch.setattr(
        "lawvm.tools.no_coverage.build_no_coverage_report",
        lambda *args, **kwargs: {
            "touched_divergence_count": 2,
            "untouched_divergence_count": 0,
        },
    )

    args = Namespace(
        base_id="no/lov/2024-01-12-1",
        as_of="2026-03-29",
        data_dir="data/norway.farchive",
        index=".tmp/no_index_farchive.json",
        commencement=None,
        max_divergences=1,
        json=True,
    )

    no_divergence.main(args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["base_id"] == "no/lov/2024-01-12-1"
    assert payload["overall_hint"] == "mixed_replay_and_text_drift"
    assert payload["touched_divergence_count"] == 2
    assert payload["untouched_divergence_count"] == 0
    assert payload["divergence_count"] == 2
    assert len(payload["divergences"]) == 1
    assert payload["divergences"][0]["address_text"] == "chapter:I/section:4-2"
    assert payload["divergences"][0]["hint"] == "text_drift"


def test_no_divergence_text_prints_hints_and_texts(monkeypatch, capsys) -> None:
    divergences = [
        _fake_divergence("MISMATCH", [("chapter", "I"), ("section", "7-1")], "ops-1", "cur-1"),
        _fake_divergence("OPS_MISSING", [("section", "6")], "ops-2", "cur-2"),
    ]
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: _fake_result(source_signal="sparse_indexed_history", divergences=divergences),
    )
    monkeypatch.setattr(
        "lawvm.tools.no_coverage.build_no_coverage_report",
        lambda *args, **kwargs: {
            "touched_divergence_count": 1,
            "untouched_divergence_count": 1,
        },
    )

    args = Namespace(
        base_id="no/lov/2024-01-12-1",
        as_of="2026-03-29",
        data_dir="data/norway.farchive",
        index=".tmp/no_index_farchive.json",
        commencement=None,
        max_divergences=1,
        json=False,
    )

    no_divergence.main(args)
    output = capsys.readouterr().out

    assert "Norway Divergence Explainer" in output
    assert "overall hint    : sparse_indexed_history" in output
    assert "[source_sparse|MISMATCH] chapter:I/section:7-1" in output
    assert "ops : ops-1" in output
    assert "cur : cur-1" in output
    assert "section:6" not in output


def test_no_divergence_json_prefers_untouched_drift_hint_when_no_touched_divergences(monkeypatch, capsys) -> None:
    divergences = [
        _fake_divergence("OPS_MISSING", [("section", "28"), ("subsection", "3")], "ops-1", "cur-1"),
    ]
    monkeypatch.setattr(
        "lawvm.norway.verify.verify_no_against_current",
        lambda *args, **kwargs: _fake_result(divergences=divergences),
    )
    monkeypatch.setattr(
        "lawvm.tools.no_coverage.build_no_coverage_report",
        lambda *args, **kwargs: {
            "touched_divergence_count": 0,
            "untouched_divergence_count": 1,
        },
    )

    args = Namespace(
        base_id="no/lov/2024-01-12-1",
        as_of="2026-03-29",
        data_dir="data/norway.farchive",
        index=".tmp/no_index_farchive.json",
        commencement=None,
        max_divergences=1,
        json=True,
    )

    no_divergence.main(args)
    payload = json.loads(capsys.readouterr().out)

    assert payload["overall_hint"] == "untouched_base_current_drift"
    assert payload["touched_divergence_count"] == 0
    assert payload["untouched_divergence_count"] == 1
