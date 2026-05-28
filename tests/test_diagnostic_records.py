from __future__ import annotations

import pytest

from lawvm.core.diagnostic_records import (
    BLOCKING_STRICT_DISPOSITIONS,
    diagnostic_detail,
    validate_blocking_disposition,
    validate_diagnostic_detail,
)


def test_diagnostic_detail_defaults_strict_and_quirks_dispositions() -> None:
    assert diagnostic_detail(rule_id="rule", phase="replay", blocking=True) == {
        "rule_id": "rule",
        "phase": "replay",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }
    assert diagnostic_detail(rule_id="rule", phase="replay", blocking=False)["strict_disposition"] == "record"


def test_diagnostic_detail_preserves_phase_local_extra_fields() -> None:
    detail = diagnostic_detail(
        rule_id="uk_replay_rule",
        phase="replay",
        family="target_resolution_recovery",
        reason="fallback_scope",
        blocking=False,
        strict_disposition="block",
        quirks_disposition="apply",
        detail={"target": "section:1"},
        action="replace",
    )

    assert detail == {
        "rule_id": "uk_replay_rule",
        "phase": "replay",
        "blocking": False,
        "strict_disposition": "block",
        "quirks_disposition": "apply",
        "family": "target_resolution_recovery",
        "reason": "fallback_scope",
        "target": "section:1",
        "action": "replace",
    }


def test_diagnostic_detail_requires_rule_id_and_phase() -> None:
    with pytest.raises(ValueError, match="rule_id"):
        diagnostic_detail(rule_id="", phase="replay", blocking=True)
    with pytest.raises(ValueError, match="phase"):
        diagnostic_detail(rule_id="rule", phase="", blocking=True)


def test_validate_diagnostic_detail_accepts_shared_envelope() -> None:
    row = diagnostic_detail(
        rule_id="uk_replay_rule",
        phase="replay",
        family="target_resolution_recovery",
        reason="exact target selected",
        blocking=False,
        strict_disposition="block",
    )

    assert validate_diagnostic_detail(row) == ()


def test_validate_diagnostic_detail_rejects_bad_envelope_shapes() -> None:
    issues = validate_diagnostic_detail(
        {
            "rule_id": "",
            "phase": "lowering",
            "blocking": True,
            "strict_disposition": "record",
            "quirks_disposition": "",
            "family": 3,
            "reason": [],
            "message": {},
        }
    )

    assert "rule_id is required" in issues
    assert "quirks_disposition is required" in issues
    assert "family must be a string when present" in issues
    assert "reason must be a string when present" in issues
    assert "message must be a string when present" in issues
    assert "blocking diagnostic must have blocking strict_disposition" in issues


def test_validate_diagnostic_detail_requires_boolean_blocking() -> None:
    assert "blocking must be a boolean" in validate_diagnostic_detail(
        {
            "rule_id": "rule",
            "phase": "parse",
            "blocking": "yes",
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    )


def test_blocking_strict_dispositions_are_shared_contract_surface() -> None:
    assert {"block", "reject", "fail", "hard_fail", "strict_block"} <= BLOCKING_STRICT_DISPOSITIONS


def test_validate_blocking_disposition_is_shared_contract_surface() -> None:
    assert validate_blocking_disposition(
        {
            "blocking": True,
            "strict_disposition": "record",
        },
        subject="row",
    ) == ("blocking row must have blocking strict_disposition",)
