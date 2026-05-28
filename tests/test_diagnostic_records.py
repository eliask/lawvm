from __future__ import annotations

import pytest

from lawvm.core.diagnostic_records import diagnostic_detail


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
