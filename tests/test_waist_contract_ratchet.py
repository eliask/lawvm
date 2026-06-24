"""Monotone waist-contract ratchet (Audit-registry row XP-02).

XP-02 — *every waist returns ``StageResult``; a bare-value return is backlog.*

The StageResult endgame (master ``411aec02``) converted all ten pipeline waists
(``notes/LAWVM_PIPELINE_CONTRACT.md`` §2) to the canonical
``StageResult[T] = {value, evidence, residuals, findings, coverage, authority}``
shape. XP-02 is the STANDING RATCHET that PINS that gain: a NEW waist — or a
regression of an existing one — whose canonical production function returns a
BARE VALUE (not a ``StageResult`` / ``PartitionResult`` / a sanctioned documented
typed carrier) trips this gate.

This is a static/AST check (mirrors ``test_determinism_spine_ratchet`` /
``test_typed_carrier_boundary_ratchet``): it enumerates the canonical waist
production functions and reads each one's RETURN ANNOTATION. The "backlog" is the
set of waists still returning a bare value; it is a committed baseline that may
ONLY fall.

HONESTY (the generator's stopping rule)
=======================================
The endgame is COMPLETE, so the backlog baseline is **0** — every one of the ten
waists already returns a StageResult-family carrier. Concretely:

  * Nine waists return ``StageResult[T]`` or ``PartitionResult[T]`` directly
    (``FiProjectionResult`` is a ``PartitionResult`` subclass — the projection
    waist is StageResult-family).
  * The ``certificate`` waist (#9) is the pipeline DESTINATION: it CONSUMES the
    per-stage ``StageResult`` accounts and emits the committed
    ``lawvm.certificate.v0`` envelope (the §2 "Canonical output type"). That
    envelope IS its documented typed carrier — the certificate does not flow
    onward as a stage, so it is SANCTIONED here, not a bare-value violation.

The ratchet therefore pins the backlog at 0 and additionally asserts the
sanctioned carriers stay StageResult-family / the certificate envelope. A new
waist that returns a bare ``str`` / ``bytes`` / ``dict`` / domain object would
trip ``test_no_new_bare_value_waist``.

LIMITATION
==========
This is a NAMED-FUNCTION enumeration, not a whole-pipeline dataflow proof: it
asserts the canonical production function of each enumerated waist returns the
contract carrier. A waist added WITHOUT registering its production function here
is not seen — the companion ``test_waist_registry_is_live`` asserts every
enumerated function still exists (so the registry cannot silently rot), and the
``CONTRACT-01`` per-field check (registry row, separate wave) is the deeper
six-field-completeness audit this only gates at the bare-value boundary.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TypedDict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_PATH = "tests/data/waist_contract_ratchet_baseline.json"

# The StageResult-family carrier base names a return annotation may start with to
# satisfy the waist contract (``StageResult[...]`` / ``PartitionResult[...]`` /
# the ``FiProjectionResult`` PartitionResult subclass).
_CONTRACT_CARRIERS = frozenset(
    {"StageResult", "PartitionResult", "FiProjectionResult"}
)


class _WaistSpec(TypedDict):
    waist: str  # the §2 waist name
    module: str  # repo-relative module path
    function: str  # the canonical production function in that module
    sanctioned_carrier: str  # "" = must be StageResult-family; else the documented carrier


# The ten canonical pipeline waists (LAWVM_PIPELINE_CONTRACT.md §2), each mapped
# to its canonical StageResult-producing function (the StageResult-endgame
# "row #N" / "WAIST #N" staged adapters). ``sanctioned_carrier`` is non-empty ONLY
# for the certificate destination, whose documented output is the certificate
# envelope, not an onward-flowing StageResult.
_WAISTS: tuple[_WaistSpec, ...] = (
    {
        "waist": "source_identity",
        "module": "src/lawvm/finland/transparent_store.py",
        "function": "read_source_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "token_structure",
        "module": "src/lawvm/finland/legal_surface/bundle.py",
        "function": "build_surface_bundle_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "surface_syntax",
        "module": "src/lawvm/finland/legal_surface/source_syntax_graph.py",
        "function": "assemble_source_syntax_graph_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "surface_families",
        "module": "src/lawvm/finland/legal_surface/graph_build.py",
        "function": "build_legal_surface_graph_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "canonical_op",
        "module": "src/lawvm/finland/frontend_compile.py",
        "function": "normalize_and_compile_ops_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "apply_receipt",
        "module": "src/lawvm/finland/apply_resolved_op.py",
        "function": "apply_resolved_op_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "timeline_materialization",
        "module": "src/lawvm/core/timeline.py",
        "function": "materialize_pit_staged",
        "sanctioned_carrier": "",
    },
    {
        "waist": "certificate",
        "module": "src/lawvm/tools/certificate_bundle.py",
        "function": "build_certificate_bundle",
        # The certificate is the DESTINATION: it consumes per-stage StageResult
        # accounts and emits the committed lawvm.certificate.v0 envelope (the §2
        # canonical output). That envelope is its documented typed carrier.
        "sanctioned_carrier": "certificate.v0 envelope",
    },
    {
        "waist": "projection",
        "module": "src/lawvm/tools/export_fi_interlinks.py",
        "function": "_project_interlinks_for_statute",
        "sanctioned_carrier": "",
    },
    {
        "waist": "overlay",
        "module": "src/lawvm/finland/legal_surface/graph_build.py",
        # Overlay is the best-disciplined waist: its surface-graph staged producer
        # carries the per-item overlay coverage through the same StageResult.
        "function": "build_legal_surface_graph_staged",
        "sanctioned_carrier": "",
    },
)


def _carrier_base_name(node: ast.expr | None) -> str | None:
    """The base name of a return annotation (``StageResult`` for
    ``StageResult[X]``; ``FiProjectionResult`` for the bare subclass; etc.),
    unwrapping ``Subscript`` and string-forward-refs. Returns None when there is
    no annotation."""
    if node is None:
        return None
    # ``-> "StageResult[List[AmendmentOp]]"`` forward-ref string annotation.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            inner = ast.parse(node.value, mode="eval").body
        except SyntaxError:  # pragma: no cover - defensive
            return node.value
        return _carrier_base_name(inner)
    # ``StageResult[X] | None`` — take the first non-None arm.
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _carrier_base_name(node.left)
        if left is not None and left != "None":
            return left
        return _carrier_base_name(node.right)
    if isinstance(node, ast.Subscript):
        return _carrier_base_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    return None


def _find_function_return(module_path: Path, function: str) -> tuple[bool, str | None]:
    """(found, return_carrier_base_name) for the named top-level/nested function."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function
        ):
            return True, _carrier_base_name(node.returns)
    return False, None


def _waist_return_carriers(repo_root: Path) -> dict[str, str | None]:
    """{waist_name: return-carrier-base-name (or None if no annotation)}."""
    out: dict[str, str | None] = {}
    for spec in _WAISTS:
        found, carrier = _find_function_return(
            repo_root / spec["module"], spec["function"]
        )
        assert found, (
            f"waist {spec['waist']!r} production function "
            f"{spec['function']!r} not found in {spec['module']} — the XP-02 "
            "registry has rotted; update _WAISTS."
        )
        out[spec["waist"]] = carrier
    return out


def _bare_value_waists(repo_root: Path) -> list[str]:
    """Waists whose canonical function returns a BARE VALUE (not a StageResult-
    family carrier and not its sanctioned documented carrier)."""
    carriers = _waist_return_carriers(repo_root)
    sanctioned = {s["waist"]: s["sanctioned_carrier"] for s in _WAISTS}
    bare: list[str] = []
    for waist, carrier in carriers.items():
        if carrier in _CONTRACT_CARRIERS:
            continue
        if sanctioned[waist]:
            # A waist with a documented typed carrier (the certificate envelope)
            # is sanctioned: not a StageResult, but not a bare-value backlog item.
            continue
        bare.append(waist)
    return sorted(bare)


# ---------------------------------------------------------------------------
# Baseline plumbing
# ---------------------------------------------------------------------------


def _load_baseline() -> dict[str, object]:
    path = _REPO_ROOT / _BASELINE_PATH
    assert path.exists(), (
        f"Missing waist-contract baseline at {path}. Generate it with "
        "`uv run python tests/test_waist_contract_ratchet.py --update-baseline`."
    )
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _baseline_bare_waists(baseline: dict[str, object]) -> list[str]:
    waists = baseline["bare_value_waists"]
    assert isinstance(waists, list)
    return sorted(str(w) for w in waists)


# ---------------------------------------------------------------------------
# XP-02 ratchet
# ---------------------------------------------------------------------------


class TestWaistContractRatchet:
    def test_no_new_bare_value_waist(self) -> None:
        baseline = _load_baseline()
        allowed = set(_baseline_bare_waists(baseline))
        current = _bare_value_waists(_REPO_ROOT)
        new = sorted(w for w in current if w not in allowed)
        if new:
            pytest.fail(
                "\n[WAIST CONTRACT] NEW bare-value waist(s) (XP-02) — a canonical "
                "waist production function returns a bare value, not a "
                "StageResult/PartitionResult (or its sanctioned documented "
                "carrier):\n"
                + "\n".join(f"  {w}" for w in new)
                + "\n\nEvery waist MUST return StageResult[T] "
                "(value/evidence/residuals/findings/coverage/authority). See "
                "notes/LAWVM_PIPELINE_CONTRACT.md §2 and the registry row XP-02."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        allowed = _baseline_bare_waists(baseline)
        current = set(_bare_value_waists(_REPO_ROOT))
        fixed = sorted(w for w in allowed if w not in current)
        if fixed:
            pytest.fail(
                "\n[WAIST CONTRACT] A bare-value waist was CONVERTED — lower the "
                "baseline to lock the gain in:\n"
                + "\n".join(f"  {w}" for w in fixed)
                + "\n\n  uv run python tests/test_waist_contract_ratchet.py "
                "--update-baseline\n(the baseline is a one-way ratchet)."
            )

    def test_backlog_is_empty(self) -> None:
        """HONESTY pin: the StageResult endgame is complete, so the backlog is 0.

        If this ever fails, either a waist regressed (the ratchet above already
        catches NEW ones) or the endgame's completeness claim was overstated.
        """
        assert _bare_value_waists(_REPO_ROOT) == []
        baseline = _load_baseline()
        assert _baseline_bare_waists(baseline) == []

    def test_all_ten_waists_enumerated(self) -> None:
        """The §2 contract names exactly ten waists; the registry must cover them."""
        names = {s["waist"] for s in _WAISTS}
        assert names == {
            "source_identity",
            "token_structure",
            "surface_syntax",
            "surface_families",
            "canonical_op",
            "apply_receipt",
            "timeline_materialization",
            "certificate",
            "projection",
            "overlay",
        }

    def test_waist_registry_is_live(self) -> None:
        """Liveness: every enumerated production function still exists AND nine of
        ten return a StageResult-family carrier (the certificate is sanctioned).
        Zero StageResult-family returns would mean the scan went blind."""
        carriers = _waist_return_carriers(_REPO_ROOT)
        stage_family = [
            w for w, c in carriers.items() if c in _CONTRACT_CARRIERS
        ]
        assert len(stage_family) == 9, (
            f"expected 9 StageResult-family waists, saw {sorted(stage_family)}"
        )


class TestWaistContractTripProof:
    def test_bare_value_return_is_detected(self) -> None:
        """A function annotated to return a bare ``str`` is classified bare."""
        src = "def waist_producer() -> str: ...\n"
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        assert _carrier_base_name(fn.returns) == "str"
        assert _carrier_base_name(fn.returns) not in _CONTRACT_CARRIERS

    def test_stageresult_return_is_accepted(self) -> None:
        src = "def waist_producer() -> StageResult[int]: ...\n"
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        assert _carrier_base_name(fn.returns) == "StageResult"

    def test_optional_stageresult_forward_ref_is_accepted(self) -> None:
        src = 'def f() -> "StageResult[bytes] | None": ...\n'
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        assert _carrier_base_name(fn.returns) == "StageResult"

    def test_partitionresult_subclass_carrier_is_accepted(self) -> None:
        src = "def f() -> FiProjectionResult: ...\n"
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        assert _carrier_base_name(fn.returns) in _CONTRACT_CARRIERS

    def test_missing_annotation_is_not_a_carrier(self) -> None:
        src = "def f(): ...\n"
        tree = ast.parse(src)
        fn = tree.body[0]
        assert isinstance(fn, ast.FunctionDef)
        assert _carrier_base_name(fn.returns) is None


# ---------------------------------------------------------------------------
# Baseline regeneration entry point.
# ---------------------------------------------------------------------------


def _update_baseline() -> None:
    bare = _bare_value_waists(_REPO_ROOT)
    carriers = _waist_return_carriers(_REPO_ROOT)
    payload = {
        "_doc": (
            "Waist-contract ratchet baseline (XP-02). bare_value_waists is the "
            "backlog of pipeline waists whose canonical production function still "
            "returns a bare value (not a StageResult/PartitionResult or its "
            "sanctioned documented carrier); it may ONLY fall. The StageResult "
            "endgame is complete, so it is []. Regenerate: uv run python "
            "tests/test_waist_contract_ratchet.py --update-baseline"
        ),
        "bare_value_waists": bare,
        "waist_return_carriers": {w: c for w, c in sorted(carriers.items())},
    }
    out = _REPO_ROOT / _BASELINE_PATH
    out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {out} (backlog {bare})")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(_waist_return_carriers(_REPO_ROOT), indent=2))
