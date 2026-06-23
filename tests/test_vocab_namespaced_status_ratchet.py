"""VOCAB-02 namespaced-status discipline gate (registry row VOCAB-02).

VOCAB-02 — *no public/cross-phase schema carries a bare ``status``/``confidence``
field deciding flow; every status field is one of the §11.8 namespaced kinds
(certificate_status / projection_status / seam_status / resolution_status /
authorization_status / overlay_status / phase_status).*

This gate has TWO arms with two HONEST dispositions:

  STATUS arm — IMPL-BY-EXISTING-GATE.
    The bare-``status`` surface ratchet (Gate 46a, ``scan_bare_status`` in
    ``scripts/inventory_naming_hygiene.py``, enforced by
    ``test_naming_hygiene_ratchet.py``) ALREADY locks "no NEW bare ``status``
    surface anywhere in ``src/lawvm``" — a superset of VOCAB-02's
    public/cross-phase-schema scope. Rather than duplicate it, this file PINS that
    coverage: it asserts the bare-status scan exists, is wired, and that the §11.8
    namespaced kinds are NOT counted by it (i.e. the correct fix clears the gate).
    A separate VOCAB-02 status baseline would just shadow the existing one.

  CONFIDENCE arm — IMPL-AT-FROZEN-BASELINE (the gap the status gate does not cover).
    §11.8 also says ``confidence`` is diagnostic metadata only. The bare-status
    ratchet does NOT see ``confidence`` fields, so VOCAB-02 adds the bare-
    ``confidence`` schema-field surface arm via ``scan_bare_confidence``. The
    current tree carries a baseline of bare-``confidence`` surface sites; the
    per-file count is frozen and may only FALL. A NEW bare ``confidence`` schema
    field trips the gate.

HONESTY (the generator's stopping rule)
=======================================
The status arm is genuinely satisfied-by-the-existing-gate (not faked): the
existing surface proxy already over-includes internal sites and locks "no NEW bare
status". The confidence arm is a frozen-baseline ratchet over a SURFACE PROXY (a
serialized ``"confidence":`` key or an annotated ``confidence`` field/param), not
a proven public-vs-internal classifier — it over-includes internal sites so it
never fails on a pre-existing one; the lock is "no NEW bare confidence surface".
Neither arm proves a given field DECIDES flow (the OV-03 confidence-as-control
ratchet, ``test_confidence_control_ratchet.py``, owns the flow-control half);
together they cover VOCAB-02's "bare in a cross-phase schema" surface and the
flow-control predicate.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_naming_hygiene.py"
_CONFIDENCE_BASELINE_PATH = "tests/data/vocab02_bare_confidence_baseline.json"

# The §11.8 namespaced status kinds — the only sanctioned status field names.
_NAMESPACED_STATUS_KINDS = frozenset(
    {
        "certificate_status",
        "projection_status",
        "seam_status",
        "resolution_status",
        "authorization_status",
        "overlay_status",
        "phase_status",
    }
)


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_naming_hygiene_vocab02", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


# ===========================================================================
# STATUS arm — IMPL-by-existing-gate (pin the bare-status ratchet's coverage)
# ===========================================================================


class TestVocab02StatusArmImplByExistingGate:
    def test_bare_status_scan_is_present_and_wired(self) -> None:
        """The status arm of VOCAB-02 is owned by the Gate-46a bare-status ratchet.
        Pin that the scan exists and the committed naming-hygiene baseline holds —
        if that gate disappeared, this would fail and force VOCAB-02 to grow its
        own status arm."""
        assert hasattr(_INV, "scan_bare_status")
        state = _INV.scan_bare_status(_REPO_ROOT)
        baseline = json.loads(
            (_REPO_ROOT / _INV.RATCHET_BASELINE_PATH).read_text(encoding="utf-8")
        )
        # No file exceeds its committed bare-status baseline (the existing lock).
        for rel, count in state["bare_status_counts"].items():
            assert count <= baseline["bare_status_counts"].get(rel, 0), rel

    def test_namespaced_status_kinds_clear_the_gate(self) -> None:
        """The §11.8 namespaced kinds are the CORRECT fix: none of them is counted
        by the bare-status scan (so namespacing a field removes the violation)."""
        for kind in sorted(_NAMESPACED_STATUS_KINDS):
            src = f'd = {{"{kind}": x}}\n'
            sites = _INV._bare_status_sites_in_module(ast.parse(src), "x.py")
            assert sites == [], f"{kind} must NOT be a bare-status violation"

    def test_bare_status_still_trips(self) -> None:
        """Defence: a literal bare ``status`` field is still caught (the gate is
        not vacuous)."""
        src = "class C:\n    status: str\n"
        sites = _INV._bare_status_sites_in_module(ast.parse(src), "x.py")
        assert len(sites) == 1 and sites[0]["shape"] == "ann_field"


# ===========================================================================
# CONFIDENCE arm — frozen-baseline ratchet (the gap the status gate misses)
# ===========================================================================


def _load_confidence_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _CONFIDENCE_BASELINE_PATH
    assert path.exists(), (
        f"Missing VOCAB-02 bare-confidence baseline at {path}. Generate it with "
        "`uv run python tests/test_vocab_namespaced_status_ratchet.py "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


class TestVocab02ConfidenceArm:
    def test_no_new_bare_confidence_surface(self) -> None:
        baseline = _load_confidence_baseline()
        state = _INV.scan_bare_confidence(_REPO_ROOT)
        base_counts: dict[str, int] = baseline["bare_confidence_counts"]
        cur_counts: dict[str, int] = state["bare_confidence_counts"]
        increases = [
            f"  {rel}: {count} bare-confidence surface sites "
            f"(baseline {base_counts.get(rel, 0)}, +{count - base_counts.get(rel, 0)})"
            for rel, count in sorted(cur_counts.items())
            if count > base_counts.get(rel, 0)
        ]
        if increases:
            pytest.fail(
                "\n[VOCAB-02 CONFIDENCE] NEW bare-`confidence` schema-field "
                "surface site(s):\n"
                + "\n".join(increases)
                + "\n\n§11.8: `confidence` is diagnostic metadata only — it must "
                "not become a cross-phase schema control field. Either remove the "
                "bare confidence field, or — if genuinely internal diagnostic "
                "metadata — consciously bump the baseline:\n"
                "  uv run python tests/test_vocab_namespaced_status_ratchet.py "
                "--update-baseline\n"
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md row VOCAB-02."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_confidence_baseline()
        state = _INV.scan_bare_confidence(_REPO_ROOT)
        base_counts: dict[str, int] = baseline["bare_confidence_counts"]
        cur_counts: dict[str, int] = state["bare_confidence_counts"]
        decreases = [
            f"  {rel}: now {cur_counts.get(rel, 0)} (baseline {a})"
            for rel, a in sorted(base_counts.items())
            if cur_counts.get(rel, 0) < a
        ]
        if decreases:
            pytest.fail(
                "\n[VOCAB-02 CONFIDENCE] bare-confidence surface count DROPPED — "
                "lower the baseline to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python "
                "tests/test_vocab_namespaced_status_ratchet.py --update-baseline"
            )

    def test_total_consistent_and_upper_bounded(self) -> None:
        baseline = _load_confidence_baseline()
        assert baseline["total_bare_confidence"] == sum(
            baseline["bare_confidence_counts"].values()
        )
        state = _INV.scan_bare_confidence(_REPO_ROOT)
        assert state["total_bare_confidence"] <= baseline["total_bare_confidence"]


class TestVocab02ConfidenceGuardLiveness:
    def _sites(self, src: str) -> list[dict[str, Any]]:
        return _INV._bare_confidence_sites_in_module(ast.parse(src), "x.py")

    def test_confidence_dict_key_is_detected(self) -> None:
        sites = self._sites('d = {"confidence": x}\n')
        assert len(sites) == 1 and sites[0]["shape"] == "dict_key"

    def test_annotated_confidence_field_is_detected(self) -> None:
        sites = self._sites("class C:\n    confidence: float\n")
        assert len(sites) == 1 and sites[0]["shape"] == "ann_field"

    def test_annotated_confidence_param_is_detected(self) -> None:
        sites = self._sites("def f(confidence: float):\n    return confidence\n")
        assert len(sites) == 1 and sites[0]["shape"] == "ann_param"

    def test_namespaced_confidence_not_detected(self) -> None:
        assert self._sites('d = {"match_confidence": x}\n') == []

    def test_unannotated_confidence_local_not_detected(self) -> None:
        assert self._sites("confidence = compute()\n") == []

    def test_confidence_in_comment_not_detected(self) -> None:
        assert self._sites("x = 1  # confidence: high\n") == []


# ===========================================================================
# Baseline regeneration entry point.
# ===========================================================================


def _update_baseline() -> None:
    state = _INV.scan_bare_confidence(_REPO_ROOT)
    payload = {
        "_doc": (
            "VOCAB-02 bare-`confidence` schema-field surface baseline. Per-file "
            "count of serialized `\"confidence\":` dict keys + annotated "
            "`confidence` fields/params across src/lawvm; may only FALL. §11.8: "
            "confidence is diagnostic-only, never a cross-phase control field. "
            "HEURISTIC surface proxy (over-includes internal sites). The status "
            "arm of VOCAB-02 is IMPL-by-existing-gate (the Gate-46a bare-status "
            "ratchet). Regenerate: uv run python "
            "tests/test_vocab_namespaced_status_ratchet.py --update-baseline."
        ),
        "bare_confidence_counts": state["bare_confidence_counts"],
        "total_bare_confidence": state["total_bare_confidence"],
    }
    out = _REPO_ROOT / _CONFIDENCE_BASELINE_PATH
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} (total {payload['total_bare_confidence']})")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(_INV.scan_bare_confidence(_REPO_ROOT), indent=2))
