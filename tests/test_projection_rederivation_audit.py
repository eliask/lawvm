"""Synthetic tests for D9 ``PROJECTION.REDERIVABLE_FROM_DOSSIER``.

Covers the four roadmap acceptance cases plus the fail-loud input contract:

* a projection row that re-derives cleanly -> no finding;
* a row mutated away from its derivation -> one ``PROJECTION.REDERIVATION_DRIFT``
  finding carrying the audited fields (row id, expected vs actual hash,
  derivation inputs);
* deterministic, committed-row-order finding stream;
* empty input -> empty output;
* malformed rows (no payload / no committed hash / non-string hash / non-mapping
  row) fail loud as ``ProjectionRederivationInputError``, not as drift findings.

These are PURE-CARRIER tests: they build wrapper rows by hand and re-hash with
the SAME ``projection_payload_hash`` the dossier writer commits, so the audit's
"re-derivable" verdict is grounded in the real §3.4 hash, not a stand-in.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

import pytest

from lawvm.core.projection_rederivation_audit import (
    PROJECTION_REDERIVATION_DRIFT,
    ProjectionRederivationInputError,
    assert_projection_rows_rederivable,
)
from lawvm.tools.certificate_bundle import projection_payload_hash

_EXCLUDED = ("engine",)


def _payload(provision_status: str, address: str) -> dict[str, Any]:
    return {
        "schema": "lawvm.provision_state.v1",
        "provision_status": provision_status,
        "statute_id": "482/2024",
        "provision": address,
        "hashes": {"derived_state_hash": "abc", "content_hash": "def"},
        # The §3.4-excluded run-provenance member: present in the row, never hashed.
        "engine": {"git_commit": "a" * 40, "git_dirty": "false"},
    }


def _row(provision_status: str, address: str, interval: tuple[str, str]) -> dict[str, Any]:
    """A faithful seam wrapper row: committed hash recomputes from the payload."""
    payload = _payload(provision_status, address)
    return {
        "projection_payload": payload,
        "certification_status": "selected",
        "universe": {"address": address, "interval": [interval[0], interval[1]]},
        "certificate": {
            "projection_kind": "lawvm.provision_state",
            "projection_schema": "lawvm.provision_state.v1",
            "projection_hash": projection_payload_hash(payload, _EXCLUDED),
        },
    }


def test_clean_row_rederives_no_finding() -> None:
    rows = [_row("selected", "section:1", ("2024-07-22", "2025-07-01"))]
    findings = assert_projection_rows_rederivable(
        rows, hash_excluded_members=_EXCLUDED, source_statute="482/2024"
    )
    assert findings == ()


def test_engine_provenance_churn_does_not_drift() -> None:
    # §3.4: a row whose engine block changed but committed hash was minted under
    # the hash-VIEW (engine excluded) still re-derives — provenance churn is not
    # drift. The committed hash is over the excluded view; mutating only `engine`
    # leaves the view, hence the hash, unchanged.
    row = _row("selected", "section:1", ("2024-07-22", "2025-07-01"))
    row["projection_payload"]["engine"] = {"git_commit": "b" * 40, "git_dirty": "true"}
    findings = assert_projection_rows_rederivable(
        [row], hash_excluded_members=_EXCLUDED, source_statute="482/2024"
    )
    assert findings == ()


def test_mutated_row_emits_one_drift_with_audited_fields() -> None:
    row = _row("selected", "section:1", ("2024-07-22", "2025-07-01"))
    committed = row["certificate"]["projection_hash"]
    # Hand-edit a SEMANTIC (hashed) member away from the committed derivation.
    row["projection_payload"]["provision_status"] = "HAND_EDITED_OPAQUE"
    findings = assert_projection_rows_rederivable(
        [row], hash_excluded_members=_EXCLUDED, source_statute="482/2024"
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == PROJECTION_REDERIVATION_DRIFT
    assert f.stage == "projection-rederivation"
    assert f.source_statute == "482/2024"
    detail = f.detail
    assert detail["reason"] == "committed_projection_hash_does_not_recompute_from_payload"
    assert detail["owner"] == "projection_rederivation_audit"
    # Fixed-shape evidence: row id, expected vs actual hash, derivation inputs.
    assert detail["row_id"]["address"] == "section:1"
    assert detail["row_id"]["interval_from"] == "2024-07-22"
    assert detail["row_id"]["interval_to"] == "2025-07-01"
    assert detail["row_id"]["row_index"] == 0
    assert detail["expected_hash"] == committed
    assert detail["actual_hash"] != committed
    assert detail["actual_hash"] == projection_payload_hash(
        row["projection_payload"], _EXCLUDED
    )
    assert tuple(detail["hash_excluded_members"]) == _EXCLUDED
    assert detail["projection_schema"] == "lawvm.provision_state.v1"


def test_only_mutated_row_drifts_among_clean_rows_deterministic_order() -> None:
    rows = [
        _row("selected", "section:1", ("2024-07-22", "2025-07-01")),
        _row("selected", "section:2", ("2024-07-22", "2025-07-01")),
        _row("selected", "section:3", ("2024-07-22", "2025-07-01")),
    ]
    # Drift the MIDDLE row only.
    rows[1]["projection_payload"]["provision_status"] = "OPAQUE"
    findings = assert_projection_rows_rederivable(
        rows, hash_excluded_members=_EXCLUDED, source_statute="482/2024"
    )
    assert len(findings) == 1
    assert findings[0].detail["row_id"]["address"] == "section:2"
    assert findings[0].detail["row_id"]["row_index"] == 1


def test_multiple_drifts_preserve_committed_row_order() -> None:
    rows = [
        _row("selected", "section:1", ("2024-07-22", "2025-07-01")),
        _row("selected", "section:2", ("2024-07-22", "2025-07-01")),
        _row("selected", "section:3", ("2024-07-22", "2025-07-01")),
    ]
    rows[2]["projection_payload"]["provision_status"] = "OPAQUE_C"
    rows[0]["projection_payload"]["provision_status"] = "OPAQUE_A"
    findings = assert_projection_rows_rederivable(
        rows, hash_excluded_members=_EXCLUDED, source_statute="482/2024"
    )
    assert [f.detail["row_id"]["address"] for f in findings] == ["section:1", "section:3"]
    # Re-running on the same input is byte-stable.
    again = assert_projection_rows_rederivable(
        rows, hash_excluded_members=_EXCLUDED, source_statute="482/2024"
    )
    assert [f.detail for f in findings] == [f.detail for f in again]


def test_empty_input_empty_output() -> None:
    assert assert_projection_rows_rederivable([], hash_excluded_members=_EXCLUDED) == ()


def test_top_level_projection_hash_accepted_for_non_seam_family() -> None:
    # A non-seam family carrying the hash at top level (no `certificate` block)
    # is auditable without a seam-specific shape.
    payload = _payload("selected", "section:1")
    row = {
        "projection_payload": payload,
        "projection_hash": projection_payload_hash(payload, _EXCLUDED),
    }
    assert assert_projection_rows_rederivable([row], hash_excluded_members=_EXCLUDED) == ()
    row["projection_payload"]["provision_status"] = "OPAQUE"
    findings = assert_projection_rows_rederivable([row], hash_excluded_members=_EXCLUDED)
    assert len(findings) == 1


def test_missing_payload_fails_loud() -> None:
    row = {"certificate": {"projection_hash": "sha256:" + "0" * 64}}
    with pytest.raises(ProjectionRederivationInputError, match="no projection_payload"):
        assert_projection_rows_rederivable([row], hash_excluded_members=_EXCLUDED)


def test_missing_committed_hash_fails_loud() -> None:
    row = {"projection_payload": _payload("selected", "section:1")}
    with pytest.raises(ProjectionRederivationInputError, match="no committed projection_hash"):
        assert_projection_rows_rederivable([row], hash_excluded_members=_EXCLUDED)


def test_non_string_committed_hash_fails_loud() -> None:
    row = {
        "projection_payload": _payload("selected", "section:1"),
        "projection_hash": 12345,
    }
    with pytest.raises(ProjectionRederivationInputError, match="not a .*string"):
        assert_projection_rows_rederivable([row], hash_excluded_members=_EXCLUDED)


def test_non_mapping_row_fails_loud() -> None:
    bad_rows = cast(Sequence[Mapping[str, Any]], ["not-a-row"])
    with pytest.raises(ProjectionRederivationInputError, match="not a mapping"):
        assert_projection_rows_rederivable(bad_rows, hash_excluded_members=_EXCLUDED)
