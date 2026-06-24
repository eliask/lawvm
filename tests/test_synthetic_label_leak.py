"""Tests for scripts/audit_synthetic_label_leak.py (LS-13 + LS-12 companion).

The script's self-test sweeps a clean synthetic dossier green; a synthetic
marker injected into a LegalAddress path is caught; the combined sweep also
flags positional ids. These exercise the script's own ``_combined_sweep`` /
``main`` so the CLI gate, not just the core API, is covered.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.semantic_types import IRNodeKind

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_synthetic_label_leak.py"


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("audit_synthetic_label_leak", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_self_test_dossier_is_clean() -> None:
    mod = _load_script()
    report = mod._combined_sweep(
        mod._self_test_dossier(),
        root_name="self_test",
        positional_only=False,
        synthetic_only=False,
    )
    assert report.clean, report.findings


def test_main_self_test_exits_zero() -> None:
    mod = _load_script()
    assert mod.main([]) == 0


def test_synthetic_marker_in_address_path_is_caught() -> None:
    mod = _load_script()
    addr = LegalAddress(path=(("section", "1"), ("subsection", "sec_1__n4")))
    timeline = ProvisionTimeline(address=addr, versions=[ProvisionVersion(effective="2025-01-01")])
    report = mod._combined_sweep(
        {"timelines": [timeline]},
        root_name="injected",
        positional_only=False,
        synthetic_only=False,
    )
    assert not report.clean
    assert any(f.finding_kind == "APPLY.SYNTHETIC_LABEL_LEAK" for f in report.findings)
    assert any("__n4" in f.value for f in report.findings)


def test_synthetic_marker_in_materialized_label_is_caught() -> None:
    mod = _load_script()
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="n9"),),
    )
    report = mod._combined_sweep(
        {"materialized_ir": body},
        root_name="injected",
        positional_only=False,
        synthetic_only=False,
    )
    assert not report.clean
    assert any(f.finding_kind == "APPLY.SYNTHETIC_LABEL_LEAK" for f in report.findings)


def test_positional_id_caught_by_combined_sweep() -> None:
    mod = _load_script()
    addr = LegalAddress(path=(("section", "expr#3"),))
    timeline = ProvisionTimeline(address=addr, versions=[ProvisionVersion(effective="2025-01-01")])
    report = mod._combined_sweep(
        {"timelines": [timeline]},
        root_name="injected",
        positional_only=False,
        synthetic_only=False,
    )
    assert not report.clean
    assert any(f.finding_kind == "APPLY.POSITIONAL_ID_LEAK" for f in report.findings)


def test_synthetic_only_skips_positional() -> None:
    mod = _load_script()
    addr = LegalAddress(path=(("section", "expr#3"),))
    timeline = ProvisionTimeline(address=addr, versions=[ProvisionVersion(effective="2025-01-01")])
    report = mod._combined_sweep(
        {"timelines": [timeline]},
        root_name="injected",
        positional_only=False,
        synthetic_only=True,
    )
    # Positional id present but synthetic-only sweep ignores it.
    assert report.clean
