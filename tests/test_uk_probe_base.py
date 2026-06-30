"""Tests for the UK feature-typed EnvGatedProbe harness ``probe_base``.

Per AGENTS.md §2.6 rule-of-three: 9 probe modules shipped the same env-
gated observation-only probe shape, so the shape is settled and this
shared harness extracts the boilerplate. These tests pin the ProbeSpec
field-validation + the skip/observed adjudication envelope shape + the
JSON-safe detail converter. They are abstract over the per-probe tail;
each per-probe test file owns its own shape tests.
"""
from __future__ import annotations

import dataclasses

import pytest

from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    detail_mapping_to_json_safe_dict,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)


def _sample_spec(
    *,
    env_flag: str = "LAWVM_UK_TEST_PROBE",
    kind: str = "uk_replay_test_observed",
    skipped_kind: str = "uk_replay_test_probe_skipped",
    family: str = "test_family",
    audit_module_path: str = "core.test_module.audit_fn",
    witness_prior_art: str = "test_wpa",
    core_registry_finding_kind: str = "TEST.KIND",
) -> ProbeSpec:
    return ProbeSpec(
        env_flag=env_flag,
        kind=kind,
        skipped_kind=skipped_kind,
        family=family,
        audit_module_path=audit_module_path,
        witness_prior_art=witness_prior_art,
        core_registry_finding_kind=core_registry_finding_kind,
    )


def test_probe_spec_is_frozen_dataclass_with_slots() -> None:
    """ProbeSpec instances are immutable module-scope constants — frozen +
    slots so a future mutation-via-direct-assignment is structurally
    impossible. Pins §2.6 synthesis-immutable identity."""
    s = _sample_spec()
    assert dataclasses.is_dataclass(s)
    assert s.__dataclass_params__.frozen is True
    assert s.__slots__ is not None  # slots=True is load-bearing


def test_probe_spec_rejects_empty_required_fields() -> None:
    """Per AGENTS.md §1.10 fail-loud: a ProbeSpec with an empty env_flag
    or kind drops its discipline silently. Each required field must
    raise on construction."""
    base_kwargs = dict(
        env_flag="X",
        kind="uk_replay_x_observed",
        skipped_kind="uk_replay_x_probe_skipped",
        family="x_family",
        audit_module_path="mod",
        witness_prior_art="wpa",
    )
    for required_field in (
        "env_flag",
        "kind",
        "skipped_kind",
        "family",
        "audit_module_path",
        "witness_prior_art",
    ):
        kwargs = dict(base_kwargs)
        kwargs[required_field] = ""
        with pytest.raises(ValueError, match=required_field):
            ProbeSpec(**kwargs)


def test_probe_spec_core_registry_finding_kind_defaults_empty() -> None:
    """core_registry_finding_kind is OPTIONAL — some probes (timeline_
    invariants) emit a family of invariant-kind codes rather than a
    single registered finding code. Default empty string passes the
    validator (no fail-loud on the optional field)."""
    s = ProbeSpec(
        env_flag="X",
        kind="uk_replay_x_observed",
        skipped_kind="uk_replay_x_probe_skipped",
        family="x",
        audit_module_path="mod",
        witness_prior_art="wpa",
    )
    assert s.core_registry_finding_kind == ""


def test_probe_env_enabled_returns_true_only_when_set_to_1() -> None:
    """``probe_env_enabled`` checks the env flag against the literal
    ``"1"`` — case-sensitive, rejects truthy-looking other values
    (mirrors the existing per-probe ``_probe_enabled`` pattern that all
    9 probes defined inline before this extraction)."""
    assert probe_env_enabled("LAWVM_PROBE_BASE_TEST_1") is False
    import os
    os.environ["LAWVM_PROBE_BASE_TEST_1"] = "1"
    try:
        assert probe_env_enabled("LAWVM_PROBE_BASE_TEST_1") is True
    finally:
        del os.environ["LAWVM_PROBE_BASE_TEST_1"]
    # Non-"1" values must NOT enable — the canonical opt-in is exactly "1".
    os.environ["LAWVM_PROBE_BASE_TEST_1"] = "true"
    try:
        assert probe_env_enabled("LAWVM_PROBE_BASE_TEST_1") is False
    finally:
        del os.environ["LAWVM_PROBE_BASE_TEST_1"]


def test_make_probe_skip_adjudication_uses_uniform_envelope() -> None:
    """The probe-skip diagnostic envelope is uniform across all probes —
    ProbeSpec's ``skipped_kind`` + ``family`` flow into a non-blocking
    CompileAdjudication with reason_code=probe_skipped, blocking=False,
    phase=replay_products. Pins the §2.6-synthesised shape."""
    s = _sample_spec()
    skip = make_probe_skip_adjudication(s, statute_id="ukpga/test/1", reason="test reason")
    assert skip.kind == "uk_replay_test_probe_skipped"
    assert skip.source_statute == "ukpga/test/1"
    assert skip.blocking is False
    assert skip.phase == "replay_products"
    assert skip.detail["rule_id"] == "uk_replay_test_probe_skipped"
    assert skip.detail["family"] == "test_family"
    assert skip.detail["reason_code"] == "probe_skipped"
    assert skip.detail["shortfall_probe_skip_reason"] == "test reason"
    assert skip.detail["strict_disposition"] == "record"


def test_make_probe_observed_adjudication_carries_uniform_envelope() -> None:
    """Probe observed-adjudication: the harness-describing envelope
    (rule_id, family, probe_mode, strict_disposition, quirks_disposition,
    witness_class, witness_prior_art, core_registry_finding_kind) is
    uniform across all probes; per-finding extension fields go through
    ``extra_detail``."""
    s = _sample_spec()
    obs = make_probe_observed_adjudication(
        s,
        statute_id="ukpga/test/2",
        message="a real finding",
        extra_detail={
            "audit_kind": "TAILORED",
            "cited_policy_id": "fake:1",
        },
        op_id="op-1",
    )
    assert obs.kind == "uk_replay_test_observed"
    assert obs.source_statute == "ukpga/test/2"
    assert obs.op_id == "op-1"
    assert obs.blocking is False
    assert obs.phase == "replay_products"
    # Harness-describing envelope fields (uniform):
    assert obs.detail["rule_id"] == "uk_replay_test_observed"
    assert obs.detail["family"] == "test_family"
    assert obs.detail["probe_mode"] == "observation_only"
    assert obs.detail["strict_disposition"] == "record"
    assert obs.detail["quirks_disposition"] == "record"
    assert obs.detail["witness_class"] == "core.test_module.audit_fn"
    assert obs.detail["witness_prior_art"] == "test_wpa"
    assert obs.detail["core_registry_finding_kind"] == "TEST.KIND"
    # Per-finding tail fields (passed through extra_detail):
    assert obs.detail["audit_kind"] == "TAILORED"
    assert obs.detail["cited_policy_id"] == "fake:1"


def test_make_probe_observed_adjudication_omits_core_registry_kind_when_empty() -> None:
    """When the ProbeSpec has no single registered finding code (e.g.
    timeline_invariants emits a family of invariant-kind codes), the
    harness OMITS the ``core_registry_finding_kind`` key at all so
    downstream readers don't see an empty-string sentinel."""
    s = _sample_spec(core_registry_finding_kind="")
    obs = make_probe_observed_adjudication(
        s, statute_id="ukpga/test/3", message="m"
    )
    assert "core_registry_finding_kind" not in obs.detail


def test_detail_mapping_to_json_safe_dict_handles_nested_mappings() -> None:
    """The JSON-safe converter recurses into sub-mappings; primitives
    pass through; non-JSON-shaped values are stringified."""
    out = detail_mapping_to_json_safe_dict(
        {
            "a": 1,
            "b": "text",
            "c": None,
            "d": {"nested": "value", "n2": {"deep": True}},
            "e": ("tuple", "value"),
            "f": object(),  # non-JSON-shaped — must stringify.
        }
    )
    assert out["a"] == 1
    assert out["b"] == "text"
    assert out["c"] is None
    assert out["d"] == {"nested": "value", "n2": {"deep": True}}
    # Tuple: iterable but has no ``items`` callable — falls through to
    # stringification (consistent defensive posture).
    assert isinstance(out["e"], str)
    assert isinstance(out["f"], str)


def test_detail_mapping_to_json_safe_dict_returns_empty_for_empty_input() -> None:
    """An empty mapping (or None) yields an empty dict — the audit's
    empty-input case is honest documentation, never silent folklore."""
    assert detail_mapping_to_json_safe_dict({}) == {}
    assert detail_mapping_to_json_safe_dict(None) == {}
