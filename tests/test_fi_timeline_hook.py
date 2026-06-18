"""Tests for opt-in timeline invariant replay hook."""
from __future__ import annotations

from datetime import date

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding
from lawvm.core.invariant_profiles import core_replay_strict_profile
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.replay_capture import ReplayCaptureSinks
from lawvm.finland.replay_pipeline import ReplaySignalBuffers
from lawvm.finland.replay_product_assembly import (
    ReplayProductAssemblyRequest,
    assemble_replay_products,
)
from lawvm.core.invariant_surface_matrix import FI_REPLAY_FOLD_SURFACE
from lawvm.finland.replay_timeline_diagnostics import (
    fi_bench_timeline_invariants_enabled,
    fi_timeline_invariants_opt_in_enabled,
    project_timeline_invariant_findings,
)
from tests.corpus_pin_helpers import pinned_replay


def test_timeline_hook_noop_without_timelines() -> None:
    findings: list[Finding] = []
    meta: dict[str, object] = {}
    project_timeline_invariant_findings(
        ir=IRNode(kind=IRNodeKind.BODY),
        timelines=None,
        pit_date=date(2024, 1, 1),
        profile=core_replay_strict_profile("replay_fold_tree"),
        replay_findings=findings,
        replay_meta_out=meta,
        replay_print=lambda _message: None,
    )
    assert findings == []
    assert "timeline_invariant_violations" not in meta


def test_bench_diagnostic_replay_enables_timeline_invariants_by_default(monkeypatch) -> None:
    monkeypatch.delenv("LAWVM_FI_ENABLE_TIMELINE_INVARIANTS", raising=False)
    monkeypatch.delenv("LAWVM_FI_BENCH_TIMELINE_INVARIANTS", raising=False)
    assert fi_bench_timeline_invariants_enabled(diagnostic_replay=True) is True
    monkeypatch.setenv("LAWVM_FI_BENCH_TIMELINE_INVARIANTS", "0")
    assert fi_bench_timeline_invariants_enabled(diagnostic_replay=True) is False
    assert fi_bench_timeline_invariants_enabled(diagnostic_replay=False) is False


def test_timeline_invariants_env_sets_replay_meta_flag(monkeypatch) -> None:
    """LAWVM_FI_ENABLE_TIMELINE_INVARIANTS sets the opt-in meta flag before projection."""
    monkeypatch.setenv("LAWVM_FI_ENABLE_TIMELINE_INVARIANTS", "1")
    assert fi_timeline_invariants_opt_in_enabled()
    meta: dict[str, object] = {}
    from lawvm.finland import replay_product_assembly as assembly_mod

    def _capture_meta(request, *args, **kwargs):
        assert request.replay_meta_out is not None
        assert request.replay_meta_out.get("enable_timeline_invariants") is True
        return request.products

    monkeypatch.setattr(assembly_mod, "project_replay_products", _capture_meta)
    monkeypatch.setattr(assembly_mod, "_normalize_product_trees", lambda products: products)
    monkeypatch.setattr(assembly_mod, "_apply_law_level_patches_if_needed", lambda p, _r: p)
    monkeypatch.setattr(assembly_mod, "_base_chapter_expiries_from_base", lambda *_a: {})
    monkeypatch.setattr(assembly_mod, "build_source_adjudication", lambda *_a, **_kw: None)
    monkeypatch.setattr(assembly_mod, "_split_operatives_from_attachments_wrapper", lambda m, _f: m)
    monkeypatch.setattr(
        assembly_mod,
        "choose_replay_horizon",
        lambda *_a, **_kw: type("H", (), {"materialize_as_of": "2024-01-01", "expires_as_of": None})(),
    )

    class _FakeProducts:
        materialized_state = type("S", (), {"ir": IRNode(kind=IRNodeKind.BODY)})()
        replay_fold_state = type("S", (), {"ir": IRNode(kind=IRNodeKind.BODY)})()
        timelines = None
        materialization_spec = None
        fold_timeline_backfills = ()

    monkeypatch.setattr(assembly_mod, "build_replay_products", lambda **_kw: _FakeProducts())

    class _Plan:
        ctx = type("Ctx", (), {"base_xml_bytes": b"<root/>"})()
        amendment_records = []
        cutoff_date = None
        oracle_version_amendment_id = ""
        oracle_suspect = ""

    class _Profile:
        synthesize_repeal_placeholders = False

    request = ReplayProductAssemblyRequest(
        parent_id="1991/3",
        mode="legal_pit",
        as_of=None,
        profile=_Profile(),
        plan=_Plan(),
        corpus=None,
        oracle_selector=None,
        replay_fold_state=object(),
        capture_sinks=ReplayCaptureSinks(
            compiled_ops=None,
            legal_operations=None,
            failed_ops=None,
        ),
        signals=ReplaySignalBuffers.empty(),
        build_full_products=True,
        strict_johto_temporal=False,
        replay_meta_out=meta,
        replay_print=lambda _m: None,
        debug_enabled=False,
        debug_log=lambda *_a: None,
    )

    assemble_replay_products(request)
    assert meta.get("enable_timeline_invariants") is True


@pytest.fixture(scope="module")
def replay_2009_953_legal_pit():
    return pinned_replay("2009/953", mode="legal_pit", quiet=True)


def test_timeline_invariants_corpus_pin_2009_953(replay_2009_953_legal_pit) -> None:
    """Pinned legal_pit replay: timeline hook emits one known heading witness."""
    products = replay_2009_953_legal_pit.products
    assert products.timelines is not None
    assert products.materialization_spec is not None

    findings: list[Finding] = []
    meta: dict[str, object] = {}
    project_timeline_invariant_findings(
        ir=products.materialized_state.ir,
        timelines=products.timelines,
        pit_date=products.materialization_spec.as_of,
        profile=FI_REPLAY_FOLD_SURFACE.replay_profile,
        replay_findings=findings,
        replay_meta_out=meta,
        replay_print=lambda _message: None,
        source_statute="2009/953",
    )

    rows = meta.get("timeline_invariant_violations")
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["kind"] == "timeline_without_ir"
    assert rows[0]["address"] == "/heading"
    assert len(findings) == 1
    assert findings[0].kind == "timeline_invariant_violation"
    assert findings[0].detail.get("code") == "timeline_without_ir"
