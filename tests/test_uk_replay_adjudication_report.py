"""Synthetic aggregation + determinism tests for the UK replay-adjudication report.

These tests exercise the pure aggregation core over hand-built payloads shaped
like ``lawvm uk-replay --json`` output (each statute carries an ``adjudications``
array whose rows have ``kind``, ``owner_phase`` and a nested
``agreement_residual.detail.bucket``). No archive/replay is invoked, so the
tests are deterministic and parallel-safe.
"""
from __future__ import annotations

import json

from scripts import uk_replay_adjudication_report as report_mod
from scripts.uk_replay_adjudication_report import (
    aggregate_replay_adjudication_report,
)


def _row(kind: str, bucket: str, owner_phase: str) -> dict:
    return {
        "kind": kind,
        "owner_phase": owner_phase,
        "agreement_residual": {"detail": {"bucket": bucket}},
    }


def _payload(*rows: dict) -> dict:
    return {"adjudications": list(rows)}


def test_sums_kind_counts_across_statutes():
    payloads = [
        (
            "ukpga/1998/42",
            _payload(
                _row("uk_replay_target_missing", "replay_bug", "replay"),
                _row("uk_empty_schedule_shape_gap", "source_shape", "extraction"),
            ),
        ),
        (
            "ukpga/2000/8",
            _payload(
                _row("uk_replay_target_missing", "replay_bug", "replay"),
            ),
        ),
    ]

    report = aggregate_replay_adjudication_report(payloads)

    assert report.total_adjudications == 3
    assert report.kind_counts == {
        "uk_empty_schedule_shape_gap": 1,
        "uk_replay_target_missing": 2,
    }
    assert report.bucket_counts == {"replay_bug": 2, "source_shape": 1}
    assert report.owner_phase_counts == {"extraction": 1, "replay": 2}


def test_kind_rows_carry_bucket_phase_and_statute_ids_sorted():
    payloads = [
        ("b/2/2", _payload(_row("k", "replay_bug", "replay"))),
        ("a/1/1", _payload(_row("k", "replay_bug", "replay"))),
    ]

    report = aggregate_replay_adjudication_report(payloads)

    assert len(report.kind_rows) == 1
    row = report.kind_rows[0]
    assert row.kind == "k"
    assert row.bucket == "replay_bug"
    assert row.owner_phase == "replay"
    assert row.count == 2
    # statute ids sorted, both contributing statutes present
    assert row.statute_ids == ("a/1/1", "b/2/2")


def test_missing_payload_recorded_not_dropped():
    payloads = [
        ("ukpga/1998/42", _payload(_row("k", "replay_bug", "replay"))),
        ("ukpga/1999/1", None),
    ]

    report = aggregate_replay_adjudication_report(payloads)

    assert report.statutes_with_payload == ("ukpga/1998/42",)
    assert report.statutes_missing_payload == ("ukpga/1999/1",)
    assert report.statute_ids == ("ukpga/1998/42", "ukpga/1999/1")
    assert report.total_adjudications == 1


def test_divergent_classification_surfaces_not_hidden():
    # Same kind classified into two buckets across statutes: visible join,
    # never a silent pick (AGENTS §1.8).
    payloads = [
        ("a/1/1", _payload(_row("k", "replay_bug", "replay"))),
        ("b/2/2", _payload(_row("k", "source_shape", "extraction"))),
    ]

    report = aggregate_replay_adjudication_report(payloads)

    row = report.kind_rows[0]
    assert row.bucket == "replay_bug|source_shape"
    assert row.owner_phase == "extraction|replay"


def test_missing_bucket_or_phase_defaults_to_unknown():
    payloads = [
        ("a/1/1", _payload({"kind": "k"})),  # no residual / owner_phase
    ]

    report = aggregate_replay_adjudication_report(payloads)

    row = report.kind_rows[0]
    assert row.bucket == "unknown"
    assert row.owner_phase == "unknown"


def test_empty_id_set_yields_empty_report():
    report = aggregate_replay_adjudication_report([])

    assert report.total_adjudications == 0
    assert report.kind_rows == ()
    assert report.kind_counts == {}
    assert report.bucket_counts == {}
    assert report.statute_ids == ()


def test_aggregation_is_deterministic_under_input_reordering():
    rows_a = _payload(
        _row("z_kind", "text_surface", "oracle"),
        _row("a_kind", "replay_bug", "replay"),
    )
    rows_b = _payload(
        _row("a_kind", "replay_bug", "replay"),
        _row("m_kind", "nonblocking_observation", "frontier"),
    )
    forward = aggregate_replay_adjudication_report(
        [("s/1/1", rows_a), ("s/2/2", rows_b)]
    )
    reversed_ = aggregate_replay_adjudication_report(
        [("s/2/2", rows_b), ("s/1/1", rows_a)]
    )

    assert forward.to_dict() == reversed_.to_dict()
    # JSON serialization is byte-stable (sorted keys, no timestamps in body).
    assert json.dumps(forward.to_dict(), sort_keys=True) == json.dumps(
        reversed_.to_dict(), sort_keys=True
    )


def test_kind_rows_sorted_by_kind():
    payload = _payload(
        _row("z", "replay_bug", "replay"),
        _row("a", "source_shape", "extraction"),
        _row("m", "text_surface", "oracle"),
    )
    report = aggregate_replay_adjudication_report([("s/1/1", payload)])

    assert [row.kind for row in report.kind_rows] == ["a", "m", "z"]


def test_to_dict_marks_read_only_and_replay_unchanged():
    report = aggregate_replay_adjudication_report(
        [("s/1/1", _payload(_row("k", "replay_bug", "replay")))]
    )
    payload = report.to_dict()

    assert payload["read_only"] is True
    assert payload["replay_unchanged"] is True
    assert payload["report_kind"] == "uk_replay_adjudication_aggregation"
    assert payload["schema"] == "lawvm.uk_replay_adjudication_aggregation.v1"


def test_text_render_is_deterministic_and_timestamp_free():
    report = aggregate_replay_adjudication_report(
        [
            ("s/1/1", _payload(_row("k", "replay_bug", "replay"))),
            ("s/2/2", None),
        ]
    )
    text_a = report_mod._render_text(report)
    text_b = report_mod._render_text(report)

    assert text_a == text_b
    assert "k: 1 [replay_bug | replay]" in text_a
    # no obvious timestamp tokens in the body
    assert "T00:" not in text_a and "UTC" not in text_a
