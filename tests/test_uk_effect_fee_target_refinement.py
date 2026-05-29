"""Tests for UK fee-target refinement exception narrowing (AGENTS.md §1.10).

The broad ``except Exception`` at
``effect_compiler.py:compile_effect_to_ir_ops`` (fee-target refinement loop)
has been replaced with a narrow ``except ValueError``.  On a caught failure the
loop now emits ``uk_effect_fee_target_refinement_failed`` via
``_append_uk_effect_lowering_observation`` so the silent fallback is visible
and strict-mode callers can gate on it.

AGENTS.md obligations covered:
  §1.10  narrow try-except in non-test code
  §15.1  synthetic unit test
  §15.2  negative test (valid target — no observation)
  §15.3  finding/observation test (witness fields)
  §15.4  strict-mode test (strict_disposition=block)
  §15.5  narrow-exception regression (RuntimeError must NOT be caught)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

from lawvm.uk_legislation.effect_compiler import (
    compile_effect_to_ir_ops,
    _UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID,
)
from lawvm.uk_legislation.effects import UKEffectRecord


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fee_target_effect(
    *,
    affected_provisions: str = "s. 5(3)",
    effect_type: str = "words substituted",
) -> UKEffectRecord:
    """Minimal UKEffectRecord that reaches the fee-target refinement loop."""
    return UKEffectRecord(
        effect_id="key-test-ftr-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2000/1/section/5/subsection/3",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions=affected_provisions,
        affecting_uri="/id/ukpga/2024/99",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2024",
        affecting_number="99",
        affecting_provisions="s. 1",
        affecting_title="Test Amending Act 2024",
    )


# ===========================================================================
# Test 1 — Synthetic positive: ValueError in _parse_affected_target
# ===========================================================================

def test_fee_target_refinement_value_error_emits_observation() -> None:
    """When _parse_affected_target raises ValueError, the observation is emitted.

    The original t_str must still appear in the lowering output path (fallback
    preserved).  The observation must be present in lowering_rejections_out.
    """
    observations: list[dict[str, Any]] = []
    effect = _fee_target_effect()

    with patch(
        "lawvm.uk_legislation.effect_compiler._parse_affected_target",
        side_effect=ValueError("synthetic malformed target for test"),
    ):
        compile_effect_to_ir_ops(
            effect,
            None,
            lowering_rejections_out=observations,
        )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID in rule_ids, (
        f"Expected {_UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID!r} in {rule_ids!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Observation carries the required witness fields
# ---------------------------------------------------------------------------

def test_fee_target_refinement_observation_has_witness_fields() -> None:
    """The observation records input_t_str, failed_helper, and exc_message."""
    observations: list[dict[str, Any]] = []
    effect = _fee_target_effect(affected_provisions="s. 5(3)")

    with patch(
        "lawvm.uk_legislation.effect_compiler._parse_affected_target",
        side_effect=ValueError("bad path element"),
    ):
        compile_effect_to_ir_ops(
            effect,
            None,
            lowering_rejections_out=observations,
        )

    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"

    assert "input_t_str" in obs, "Must record input_t_str"
    assert "failed_helper" in obs, "Must record failed_helper"
    assert "exc_message" in obs, "Must record exc_message"

    assert obs["input_t_str"] == "s. 5(3)", (
        f"input_t_str must be the original target string; got {obs['input_t_str']!r}"
    )
    assert obs["failed_helper"] == "parse_affected_target", (
        f"failed_helper must identify which helper raised; got {obs['failed_helper']!r}"
    )
    assert "bad path element" in obs["exc_message"], (
        f"exc_message must contain the exception message; got {obs['exc_message']!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Strict-mode gate: strict_disposition=block
# ---------------------------------------------------------------------------

def test_fee_target_refinement_observation_strict_disposition_is_block() -> None:
    """The observation carries strict_disposition=block so strict callers can gate."""
    observations: list[dict[str, Any]] = []
    effect = _fee_target_effect()

    with patch(
        "lawvm.uk_legislation.effect_compiler._parse_affected_target",
        side_effect=ValueError("strict mode test"),
    ):
        compile_effect_to_ir_ops(
            effect,
            None,
            lowering_rejections_out=observations,
        )

    obs = next(
        (o for o in observations
         if o.get("rule_id") == _UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID),
        None,
    )
    assert obs is not None, "Observation must be emitted"
    assert obs.get("strict_disposition") == "block", (
        f"Expected strict_disposition=block, got {obs.get('strict_disposition')!r}"
    )
    assert obs.get("quirks_disposition") == "apply", (
        f"Expected quirks_disposition=apply, got {obs.get('quirks_disposition')!r}"
    )
    assert obs.get("blocking") is False, (
        "Observation must be non-blocking (quirks mode allows fallback to continue)"
    )


# ===========================================================================
# Test 4 — Negative: valid fee-target string — NO observation emitted
# ===========================================================================

def test_fee_target_refinement_no_observation_for_valid_parse() -> None:
    """When _parse_affected_target succeeds, no fee-target-refinement observation fires."""
    observations: list[dict[str, Any]] = []
    effect = _fee_target_effect(affected_provisions="s. 5(3)")

    # No monkeypatch — let the real _parse_affected_target run
    compile_effect_to_ir_ops(
        effect,
        None,
        lowering_rejections_out=observations,
    )

    rule_ids = [obs.get("rule_id") for obs in observations]
    assert _UK_EFFECT_FEE_TARGET_REFINEMENT_FAILED_RULE_ID not in rule_ids, (
        "Fee-target refinement observation must NOT fire when parsing succeeds"
    )


# ===========================================================================
# Test 5 — Narrow-exception regression: RuntimeError must NOT be caught
# ===========================================================================

def test_fee_target_refinement_runtime_error_propagates() -> None:
    """A non-ValueError exception (e.g. RuntimeError) is NOT silently caught.

    This is the §1.10 test: the narrow except clause must NOT swallow
    unanticipated exception types.  If _parse_affected_target raises
    RuntimeError, it must propagate out of compile_effect_to_ir_ops.
    """
    effect = _fee_target_effect()

    with patch(
        "lawvm.uk_legislation.effect_compiler._parse_affected_target",
        side_effect=RuntimeError("unexpected internal failure for test"),
    ):
        try:
            compile_effect_to_ir_ops(effect, None)
            raise AssertionError(
                "Expected RuntimeError to propagate but compile_effect_to_ir_ops returned normally"
            )
        except RuntimeError as exc:
            assert "unexpected internal failure for test" in str(exc), (
                f"RuntimeError message mismatch: {exc!r}"
            )
        except Exception as exc:  # noqa: BLE001  (test-only broad catch for assertion)
            raise AssertionError(
                f"Expected RuntimeError to propagate but got {type(exc).__name__}: {exc}"
            ) from exc
