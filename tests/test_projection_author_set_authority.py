"""Tree-wide author-set-replay-authority-at-projection sweep (audit row PROJ-02).

Registry assertion (LAWVM_AUDIT_INVARIANT_REGISTRY.md §7 / §3.E, PROJECTION
plane; ``core/stage_result.py`` AuthoritySurface): a projection row is
NON-AUTHORITATIVE by construction. ``replay_authorized`` — the legal-state
replay-authority field — may be granted ONLY by an explicit, granting
``ExecutionAuthorization`` carrier; it may NEVER be author-set to True on any
other row / dataclass / dict at projection time.

IMPL status. PROJ-02 is **IMPL-by-construction at the per-row level** today:
  * ``core/frontier_work_item.py``: ``validate_frontier_work_item`` RAISES if a
    FrontierWorkItem (or its suggested claim template) carries
    ``replay_authorized`` != False;
  * ``core/legal_surface_assembler.py``: the assembler RAISES on any surface
    node/edge with ``replay_authorized=True`` (§D7);
  * ``tools/export_fi_refs.py``: the fi_refs deterministic export — the CANONICAL
    prior violation (it stamped every deterministic extraction row
    ``replay_authorized: True`` via ``_DETERMINISTIC_ROW_EXTRAS``) — was fixed
    (2f7f30e6) to ``False`` + a positive ``deterministic_extraction`` surface fact.

This module supplies the **tree-wide closed-property sweep** that was the PART
left to deliver: the fi_refs fix, generalized. An AST scan
(``scripts/inventory_architecture_smells.py:scan_projection_authority_ratchet``)
finds every site that author-sets ``replay_authorized`` to a TRUTHY literal and
classifies it ALLOWED iff it is a keyword inside an ``ExecutionAuthorization(...)``
grant carrier, VIOLATION otherwise (a dict literal, a non-grant constructor
kwarg, or a class/module default). The committed baseline is 0 — the firewall is
intact tree-wide — and this gate FAILS if any file's violation count rises above
0, fencing a NEW author-set replay-authority crossing anywhere under
``src/lawvm``. The proposed finding-kind ``PROJECTION_AUTHOR_SET_AUTHORITY`` is
carried in the failure message; like the other static ratchets it registers no
``observation_registry`` kind — there is no production sink that emits it, the
gate IS the enforcement.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_architecture_smells.py"


def _load_inventory_module() -> Any:
    """Import scripts/inventory_architecture_smells.py (not on sys.path)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_architecture_smells", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.PROJECTION_AUTHORITY_BASELINE_PATH
    assert path.exists(), (
        f"Missing projection-authority ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_architecture_smells.py --ratchet projection "
        "--update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The tree-wide closed-property sweep (monotone-at-zero)
# ---------------------------------------------------------------------------


class TestProjectionAuthorSetAuthorityRatchet:
    def test_baseline_is_zero_violations(self) -> None:
        """The firewall is type-enforced tree-wide; the committed baseline is 0."""
        baseline = _load_baseline()
        assert baseline["total_violations"] == 0, (
            "The projection-authority baseline must be 0 — no projection row may "
            "author-set replay_authorized outside an ExecutionAuthorization grant "
            f"carrier. Baseline records {baseline['total_violations']} violation(s): "
            f"{baseline['violation_counts']}"
        )
        assert baseline["violation_counts"] == {}

    def test_no_new_author_set_replay_authority_crossing(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_projection_authority_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["violation_counts"]
        current_counts: dict[str, int] = state["violation_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} author-set truthy replay_authorized site(s) "
                    f"outside an ExecutionAuthorization carrier (baseline {allowed}, "
                    f"+{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[PROJECTION_AUTHOR_SET_AUTHORITY] NEW author-set replay-authority "
                "crossing(s) at projection:\n"
                + "\n".join(increases)
                + "\n\nA projection row is non-authoritative by construction; "
                "replay_authorized may be granted ONLY by an explicit, granting "
                "ExecutionAuthorization carrier — never author-set True on a dict "
                "row, a class default, or a non-grant constructor. Set the row's "
                "value to surface-truthful False (record a positive surface fact "
                "instead), or route the grant through an ExecutionAuthorization. "
                "See tools/export_fi_refs.py (_DETERMINISTIC_ROW_EXTRAS) for the "
                "canonical fix and core/stage_result.py AuthoritySurface for the "
                "firewall."
            )

    def test_ratchet_only_tightens(self) -> None:
        """The committed total is a permanent upper bound (it may only ever fall)."""
        baseline = _load_baseline()
        state = _INV.scan_projection_authority_ratchet(_REPO_ROOT)
        assert state["total_violations"] <= baseline["total_violations"], (
            f"Total author-set replay-authority crossings {state['total_violations']} "
            f"exceeds baseline {baseline['total_violations']}."
        )
        # If the baseline ever rose above 0, a drop back to a lower value must be
        # re-committed (defence in depth; today both are 0 so this is a no-op).
        decreases: list[str] = []
        baseline_counts: dict[str, int] = baseline["violation_counts"]
        current_counts: dict[str, int] = state["violation_counts"]
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(f"  {rel}: now {count} (baseline {allowed})")
        if decreases:
            pytest.fail(
                "\n[PROJECTION_AUTHOR_SET_AUTHORITY] The crossing count DROPPED — "
                "lower and re-commit the baseline:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python scripts/inventory_architecture_smells.py "
                "--ratchet projection --update-baseline"
            )


# ---------------------------------------------------------------------------
# Guard-liveness: the scan must classify ALLOWED vs VIOLATION correctly. Drives
# synthetic inputs through the production scan function (AGENTS.md §2.9), so the
# gate cannot pass vacuously by being blind to the firewall it claims to enforce.
# ---------------------------------------------------------------------------


class TestProjectionAuthorSetAuthorityGuardLiveness:
    _FILE = "src/lawvm/example_projection.py"

    def _scan(self, text: str) -> list[dict[str, Any]]:
        return _INV.scan_file_projection_authority(self._FILE, text)

    def test_execution_authorization_grant_is_allowed(self) -> None:
        text = (
            "def grant():\n"
            "    return ExecutionAuthorization(replay_authorized=True)\n"
        )
        assert self._scan(text) == []

    def test_qualified_execution_authorization_grant_is_allowed(self) -> None:
        text = (
            "def grant():\n"
            "    return mod.ExecutionAuthorization(replay_authorized=True)\n"
        )
        assert self._scan(text) == []

    def test_non_grant_constructor_kwarg_is_violation(self) -> None:
        text = (
            "def make():\n"
            "    return FrontierWorkItem(replay_authorized=True)\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "keyword_non_grant_carrier"

    def test_dict_literal_truthy_is_violation(self) -> None:
        text = (
            "ROW = {\n"
            "    'target': 'x',\n"
            "    'replay_authorized': True,\n"
            "}\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "dict_literal"

    def test_class_default_truthy_is_violation(self) -> None:
        text = (
            "class Row:\n"
            "    replay_authorized: bool = True\n"
        )
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "default_assignment"

    def test_bare_assign_truthy_is_violation(self) -> None:
        text = "replay_authorized = True\n"
        records = self._scan(text)
        assert len(records) == 1
        assert records[0]["kind"] == "default_assignment"

    def test_false_literal_is_not_flagged(self) -> None:
        """The surface-truthful default (the fi_refs fix) must NOT be flagged."""
        text = (
            "ROW = {'replay_authorized': False}\n"
            "class Row:\n"
            "    replay_authorized: bool = False\n"
            "def make():\n"
            "    return FrontierWorkItem(replay_authorized=False)\n"
        )
        assert self._scan(text) == []

    def test_comparison_is_not_flagged(self) -> None:
        """A read / equality check is not an author-set authority claim."""
        text = (
            "def check(row):\n"
            "    return row.replay_authorized == True\n"
        )
        assert self._scan(text) == []

    def test_derived_value_is_not_flagged(self) -> None:
        """A value derived from an expression (not a hard-coded literal) is not an
        author-set hard-coded authority; it must flow through the grant carrier,
        which the keyword-allow path handles separately."""
        text = (
            "def make(authorized):\n"
            "    return FrontierWorkItem(replay_authorized=authorized)\n"
        )
        assert self._scan(text) == []

    def test_fi_refs_canonical_fix_would_be_clean(self) -> None:
        """The shape of the fixed fi_refs _DETERMINISTIC_ROW_EXTRAS is clean; the
        pre-fix shape (replay_authorized: True) would have been a violation."""
        fixed = "EXTRAS = {'replay_authorized': False, 'deterministic_extraction': True}\n"
        assert self._scan(fixed) == []
        pre_fix = "EXTRAS = {'replay_authorized': True}\n"
        records = self._scan(pre_fix)
        assert len(records) == 1
        assert records[0]["kind"] == "dict_literal"
