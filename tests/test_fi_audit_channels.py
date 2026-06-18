"""Audit channel wiring tests."""
from __future__ import annotations

from lawvm.tools.audit_channels import (
    adjudications_channel_spec,
    invariants_channel_spec,
    warnings_channel_spec,
)
from lawvm.tools.fi_adjudication_audit import compile_one_statute
from lawvm.tools.fi_invariant_audit import audit_one_statute, classify_violation


def test_invariants_channel_spec_worker_callable() -> None:
    spec = invariants_channel_spec()
    assert spec.channel.value == "invariants"
    assert spec.worker is not None


def test_adjudications_channel_spec_worker_callable() -> None:
    spec = adjudications_channel_spec()
    assert spec.channel.value == "adjudications"


def test_classify_violation_duplicate_label() -> None:
    vtype, path, detail = classify_violation("body/section:1: duplicate section:5a (2 times)")
    assert vtype == "duplicate_label"
    assert path == "body/section:1"
    assert detail == "section:5a"


def test_warnings_channel_spec() -> None:
    assert warnings_channel_spec().channel.value == "warnings"


def test_fi_adjudication_audit_import() -> None:
    assert compile_one_statute.__name__ == "compile_one_statute"


def test_fi_invariant_audit_import() -> None:
    assert audit_one_statute.__name__ == "audit_one_statute"
