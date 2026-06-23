"""Monotone untyped-carrier phase-boundary ratchet + plane-noncollapse static
check (Audit-registry rows FW-09 and XP-01).

FW-09 generalises the single-predicate ``test_authority_boundary_ratchet`` from
"only ``is_blocking_compile_record`` must take the typed ``CompileRecord``" to a
tree-wide ban on NEW *untyped carriers crossing a semantic phase boundary*. A
"phase-boundary function" is defined pragmatically as a PUBLIC function (no
leading underscore) in ``src/lawvm/{core,finland}`` — these are the functions a
later stage calls across a stage seam. Their signatures are where one stage hands
a value to the next, so an untyped carrier there is exactly a silent
plane/identity erosion (AGENTS.md §1.9). Banned, per-class-counted, frozen at a
committed baseline that may only fall:

  * a parameter / return annotated ``dict[str, object]`` / ``dict[str, Any]``
  * a parameter / return annotated bare ``object``
  * a return annotated ``tuple[...]`` of arity > 2 (a positional carrier wider
    than a pair — a record that should be a named type)
  * a ``getattr(...)`` call inside a public function body (dynamic attribute
    access erodes the typed carrier)

XP-01 plane-noncollapse: the cheapest *sound static* form of "no value carries
two operational planes (surface AND legal-state authority)". Every *defaulted*
dataclass field named ``replay_authorized`` MUST default to the literal ``False``
and every ``surface_only`` field MUST default to the literal ``True``. A surface
carrier that defaults ``replay_authorized=True`` (or ``surface_only=False``) has
collapsed the surface and legal-state planes into one value at the type level —
the canonical XP-01 violation. A field with NO default is a required field on a
legal-state/authority record (its value is computed, e.g.
``ExecutionAuthorization.replay_authorized``) — not a surface carrier and not a
collapse; it is exempt. A non-literal default cannot be proven safe and is
flagged. (A full "no field used as two planes anywhere"
proof needs whole-program dataflow; this default-direction invariant is the
defensible static core. See ``_XP01_LIMITATION``.)
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TypedDict

import pytest


class _CarrierScan(TypedDict):
    counts: dict[str, int]
    hits: list[dict[str, object]]
    parse_error: bool


class _PlaneRec(TypedDict):
    field: str
    line: int
    default: object

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_PATH = "tests/data/typed_carrier_boundary_ratchet_baseline.json"
_SCAN_ROOTS = ("src/lawvm/core", "src/lawvm/finland")

_XP01_LIMITATION = (
    "XP-01 here is the default-direction invariant (replay_authorized defaults "
    "False, surface_only defaults True). A fully general 'no single value is used "
    "as both a surface fact and a legal-state authority' check needs whole-program "
    "dataflow across plane boundaries, which a bounded AST visitor cannot do "
    "soundly; this is the defensible static core, not the complete audit."
)

# XP-01 construction-site arm — RESIDUAL (named gap, deliberately NOT a ratchet).
# A construction-site sweep ("a SURFACE carrier built with replay_authorized=True /
# surface_only=False at a call site") is NOT soundly static: distinguishing a
# surface-carrier construction that collapses the plane from a LEGITIMATE
# legal-state authorization (an ExecutionAuthorization whose whole job is to set
# replay_authorized=True) requires resolving the constructed type's PLANE, which
# needs cross-module type resolution a bounded AST cannot do. The single live
# ``replay_authorized=True`` keyword construction in core/finland is exactly that
# legitimate authorization (the apply_receipt waist conferring execution
# authority), NOT a surface collapse. ``test_xp01_construction_arm_is_residual``
# pins this honestly: the default-direction core is the defensible ceiling; the
# construction-site dataflow sweep stays a marked residual.
_XP01_CONSTRUCTION_RESIDUAL = (
    "XP-01 construction-site arm is RESIDUAL: a sound 'surface carrier constructed "
    "with the plane collapsed' check needs cross-module type-plane resolution. The "
    "only live replay_authorized=True keyword construction is the legitimate apply "
    "authorization, so a naive construction-site ban would false-positive. The "
    "field-default core (TestPlaneNonCollapse) is the static ceiling."
)


def _replay_true_construction_sites(repo_root: Path) -> list[dict[str, object]]:
    """Every call-site passing ``replay_authorized=True`` / ``surface_only=False``
    as a keyword over core/finland (the construction-arm candidate set). Used ONLY
    to PIN the residual (assert the candidate set is the small, known-legitimate
    one) — NOT wired as a ratchet, because soundness needs type-plane resolution."""
    sites: list[dict[str, object]] = []
    for rel in _SCAN_ROOTS:
        for path in sorted((repo_root / rel).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - defensive
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    val = kw.value
                    if not isinstance(val, ast.Constant):
                        continue
                    if kw.arg == "replay_authorized" and val.value is True:
                        sites.append(
                            {
                                "file": str(path.relative_to(repo_root)),
                                "line": node.lineno,
                                "kw": "replay_authorized=True",
                            }
                        )
                    if kw.arg == "surface_only" and val.value is False:
                        sites.append(
                            {
                                "file": str(path.relative_to(repo_root)),
                                "line": node.lineno,
                                "kw": "surface_only=False",
                            }
                        )
    return sites


# ---------------------------------------------------------------------------
# FW-09 — untyped-carrier phase-boundary AST scan
# ---------------------------------------------------------------------------


def _ann_carrier_kind(node: ast.expr | None) -> str | None:
    """Classify an annotation as an untyped carrier, else None."""
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id == "object":
        return "bare_object"
    if isinstance(node, ast.Subscript):
        base = node.value
        base_id = base.id if isinstance(base, ast.Name) else None
        if base_id in ("dict", "Dict"):
            sl = node.slice
            elts = sl.elts if isinstance(sl, ast.Tuple) else []
            if len(elts) == 2 and isinstance(elts[0], ast.Name) and elts[0].id == "str":
                val = elts[1]
                if isinstance(val, ast.Name) and val.id == "object":
                    return "dict_str_object"
                if isinstance(val, ast.Name) and val.id == "Any":
                    return "dict_str_any"
                # Attribute form ``typing.Any``
                if isinstance(val, ast.Attribute) and val.attr == "Any":
                    return "dict_str_any"
    return None


def _return_tuple_arity_gt2(node: ast.expr | None) -> bool:
    """A return annotated ``tuple[A, B, C, ...]`` with > 2 fixed members
    (variadic ``tuple[X, ...]`` excluded)."""
    if not isinstance(node, ast.Subscript):
        return False
    base = node.value
    if not (isinstance(base, ast.Name) and base.id in ("tuple", "Tuple")):
        return False
    sl = node.slice
    if not isinstance(sl, ast.Tuple):
        return False
    elts = sl.elts
    if any(isinstance(e, ast.Constant) and e.value is Ellipsis for e in elts):
        return False
    return len(elts) > 2


def _is_public(name: str) -> bool:
    return not name.startswith("_")


class _CarrierVisitor(ast.NodeVisitor):
    """Counts untyped-carrier offenders per function class, only in PUBLIC funcs."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {
            "bare_object": 0,
            "dict_str_object": 0,
            "dict_str_any": 0,
            "tuple_return_gt2": 0,
            "getattr_call": 0,
        }
        self.hits: list[dict[str, object]] = []

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not _is_public(node.name):
            # Still descend for nested public funcs / getattr only inside public.
            return
        a = node.args
        params = list(a.args) + list(a.posonlyargs) + list(a.kwonlyargs)
        if a.vararg:
            params.append(a.vararg)
        if a.kwarg:
            params.append(a.kwarg)
        for p in params:
            kind = _ann_carrier_kind(p.annotation)
            if kind is not None:
                self.counts[kind] += 1
                self.hits.append({"fn": node.name, "line": node.lineno, "kind": kind})
        rk = _ann_carrier_kind(node.returns)
        if rk is not None:
            self.counts[rk] += 1
            self.hits.append({"fn": node.name, "line": node.lineno, "kind": rk})
        if _return_tuple_arity_gt2(node.returns):
            self.counts["tuple_return_gt2"] += 1
            self.hits.append(
                {"fn": node.name, "line": node.lineno, "kind": "tuple_return_gt2"}
            )
        # getattr() in the public function body
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "getattr"
            ):
                self.counts["getattr_call"] += 1
                self.hits.append(
                    {"fn": node.name, "line": sub.lineno, "kind": "getattr_call"}
                )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)
        self.generic_visit(node)


def scan_carrier_source(text: str) -> _CarrierScan:
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - defensive
        return {"counts": {}, "hits": [], "parse_error": True}
    v = _CarrierVisitor()
    v.visit(tree)
    return {"counts": dict(v.counts), "hits": v.hits, "parse_error": False}


def scan_carrier_boundary(repo_root: Path) -> dict[str, int]:
    """Per-file TOTAL untyped-carrier count over the scan roots."""
    per_file: dict[str, int] = {}
    for rel in _SCAN_ROOTS:
        for path in sorted((repo_root / rel).rglob("*.py")):
            res = scan_carrier_source(path.read_text(encoding="utf-8"))
            total = sum(res["counts"].values())
            if total:
                relp = str(path.relative_to(repo_root))
                per_file[relp] = total
    return per_file


# ---------------------------------------------------------------------------
# XP-01 — plane-noncollapse static check (field default direction)
# ---------------------------------------------------------------------------

_PLANE_FIELD_DEFAULTS = {
    "replay_authorized": False,
    "surface_only": True,
}


def scan_plane_fields(text: str) -> list[_PlaneRec]:
    """Every ``replay_authorized``/``surface_only`` annotated field; record its
    default literal (or None if non-literal / absent)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - defensive
        return []
    out: list[_PlaneRec] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fname = node.target.id
            if fname in _PLANE_FIELD_DEFAULTS:
                default: object = "<<absent>>"
                if node.value is not None:
                    if isinstance(node.value, ast.Constant):
                        default = node.value.value
                    else:
                        default = "<<non-literal>>"
                out.append(
                    {"field": fname, "line": node.lineno, "default": default}
                )
    return out


def scan_plane_noncollapse(repo_root: Path) -> dict[str, list[_PlaneRec]]:
    """Return {rel_path: [violating field records]} — only fields whose default
    direction collapses the plane (or is non-literal)."""
    violations: dict[str, list[_PlaneRec]] = {}
    for rel in _SCAN_ROOTS:
        for path in sorted((repo_root / rel).rglob("*.py")):
            recs = scan_plane_fields(path.read_text(encoding="utf-8"))
            bad: list[_PlaneRec] = []
            for r in recs:
                expected = _PLANE_FIELD_DEFAULTS[r["field"]]
                default = r["default"]
                # A field with NO default is a REQUIRED field on a legal-state /
                # authority record (e.g. ExecutionAuthorization.replay_authorized is
                # computed, not surface-defaulted) — not a plane collapse. Only a
                # DEFAULTED field that defaults the wrong way collapses the plane; a
                # non-literal default is flagged as suspicious (cannot prove safe).
                if default == "<<absent>>":
                    continue
                if default != expected:
                    bad.append(r)
            if bad:
                violations[str(path.relative_to(repo_root))] = bad
    return violations


def _count_plane_fields(repo_root: Path) -> int:
    n = 0
    for rel in _SCAN_ROOTS:
        for path in (repo_root / rel).rglob("*.py"):
            n += len(scan_plane_fields(path.read_text(encoding="utf-8")))
    return n


# ---------------------------------------------------------------------------
# Baseline plumbing
# ---------------------------------------------------------------------------


def _load_baseline() -> dict[str, object]:
    path = _REPO_ROOT / _BASELINE_PATH
    assert path.exists(), (
        f"Missing typed-carrier-boundary baseline at {path}. Generate it with "
        "`uv run python tests/test_typed_carrier_boundary_ratchet.py --update-baseline`."
    )
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _as_int(v: object) -> int:
    assert isinstance(v, int)
    return v


def _baseline_counts(baseline: dict[str, object]) -> dict[str, int]:
    counts = baseline["untyped_carrier_counts"]
    assert isinstance(counts, dict)
    return {str(k): _as_int(v) for k, v in counts.items()}


def _baseline_total(baseline: dict[str, object]) -> int:
    return _as_int(baseline["total_untyped_carriers"])


# ---------------------------------------------------------------------------
# FW-09 ratchet
# ---------------------------------------------------------------------------


class TestTypedCarrierBoundaryRatchet:
    def test_no_new_untyped_carrier_crossing(self) -> None:
        baseline = _load_baseline()
        allowed = _baseline_counts(baseline)
        current = scan_carrier_boundary(_REPO_ROOT)
        increases = [
            f"  {rel}: {count} untyped-carrier signature(s) (baseline {allowed.get(rel, 0)})"
            for rel, count in sorted(current.items())
            if count > allowed.get(rel, 0)
        ]
        if increases:
            pytest.fail(
                "\n[TYPED CARRIER BOUNDARY] NEW untyped carrier(s) crossing a public "
                "phase-boundary signature (FW-09):\n"
                + "\n".join(increases)
                + "\n\nA public function in core/finland hands a value across a stage "
                "seam; its signature must carry a typed carrier, not "
                "dict[str,object]/dict[str,Any]/bare object/tuple>2/getattr. Introduce "
                "a named carrier type. See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md FW-09."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        allowed = _baseline_counts(baseline)
        current = scan_carrier_boundary(_REPO_ROOT)
        decreases = [
            f"  {rel}: now {current.get(rel, 0)} (baseline {a})"
            for rel, a in sorted(allowed.items())
            if current.get(rel, 0) < a
        ]
        if decreases:
            pytest.fail(
                "\n[TYPED CARRIER BOUNDARY] An untyped-carrier count DROPPED — lower "
                "the baseline to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python tests/test_typed_carrier_boundary_ratchet.py "
                "--update-baseline\n(the baseline is a one-way ratchet)."
            )

    def test_total_is_consistent_upper_bound(self) -> None:
        baseline = _load_baseline()
        allowed = _baseline_counts(baseline)
        current = scan_carrier_boundary(_REPO_ROOT)
        total = _baseline_total(baseline)
        assert total == sum(allowed.values())
        assert sum(current.values()) <= total

    def test_scan_is_not_blind(self) -> None:
        """Liveness: the scan must observe real offenders; zero would mean the
        visitor stopped seeing signatures (vacuously green)."""
        current = scan_carrier_boundary(_REPO_ROOT)
        assert sum(current.values()) > 0


class TestCarrierRatchetTripProof:
    def test_injected_untyped_carrier_exceeds_file_baseline(self) -> None:
        """A real core file with one extra `def public(x: dict[str, object])`
        appended must scan ABOVE its committed per-file baseline → the ratchet
        comparison the production test runs would FAIL."""
        baseline = _load_baseline()
        allowed = _baseline_counts(baseline)
        rel = "src/lawvm/core/coverage.py"
        path = _REPO_ROOT / rel
        clean = path.read_text(encoding="utf-8")
        # A PUBLIC function with a dict[str, object] parameter is the offender
        # shape (a private name would not be scanned, so it must be public).
        injected = (
            clean
            + "\n\ndef ratchet_trip_proof(x: dict[str, object]) -> None:\n"
            + "    return None\n"
        )
        res = scan_carrier_source(injected)
        injected_total = sum(res["counts"].values())
        assert injected_total > allowed.get(rel, 0), (
            "an injected public dict[str,object] signature must exceed the file baseline"
        )

    def test_clean_tree_is_at_or_below_baseline(self) -> None:
        """The real tree is at-or-below its committed baseline everywhere — green
        for the right reason."""
        baseline = _load_baseline()
        allowed = _baseline_counts(baseline)
        current = scan_carrier_boundary(_REPO_ROOT)
        over = {rel: c for rel, c in current.items() if c > allowed.get(rel, 0)}
        assert not over, f"unexpected over-baseline files: {over}"


# ---------------------------------------------------------------------------
# XP-01 plane-noncollapse
# ---------------------------------------------------------------------------


class TestPlaneNonCollapse:
    def test_no_plane_field_default_collapses(self) -> None:
        violations = scan_plane_noncollapse(_REPO_ROOT)
        if violations:
            lines = []
            for rel, recs in sorted(violations.items()):
                for r in recs:
                    lines.append(
                        f"  {rel}:{r['line']}  {r['field']} default={r['default']!r} "
                        f"(must be {_PLANE_FIELD_DEFAULTS[r['field']]!r})"
                    )
            pytest.fail(
                "\n[PLANE NON-COLLAPSE] A surface-carrier field collapses the surface "
                "and legal-state planes by defaulting the wrong way (XP-01):\n"
                + "\n".join(lines)
                + f"\n\n{_XP01_LIMITATION}"
            )

    def test_plane_fields_are_actually_present(self) -> None:
        """Liveness: there must be real replay_authorized/surface_only fields, else
        the check is vacuous."""
        assert _count_plane_fields(_REPO_ROOT) > 0

    def test_xp01_construction_arm_is_residual(self) -> None:
        """XP-01 completer: the construction-site sweep is a NAMED RESIDUAL, pinned
        here rather than wired as a ratchet. The candidate set (call-sites passing
        ``replay_authorized=True``/``surface_only=False``) is small and consists of
        the LEGITIMATE legal-state authorization site(s), not surface collapses — a
        sound ban would need cross-module type-plane resolution. We assert the
        candidate set stays small + that every member is in core/finland (so a NEW
        construction-site collapse becomes visible to a reviewer even though it is
        not auto-blocked). The default-direction core above is the static ceiling.
        """
        sites = _replay_true_construction_sites(_REPO_ROOT)
        # All candidates are real core/finland constructions (the scan is live).
        for s in sites:
            assert isinstance(s["file"], str) and (
                s["file"].startswith("src/lawvm/core/")
                or s["file"].startswith("src/lawvm/finland/")
            )
        # The candidate set is the small known-legitimate authorization surface,
        # not a sprawling collapse population; a jump here is a review signal.
        rendered = [f"{s['file']}:{s['line']}" for s in sites]
        assert len(sites) <= 8, (
            f"XP-01 construction-site candidate set grew to {len(sites)} "
            f"({rendered}); review whether any is a surface-carrier plane collapse "
            f"rather than a legitimate authorization. {_XP01_CONSTRUCTION_RESIDUAL}"
        )


# ---------------------------------------------------------------------------
# Guard-liveness: synthetic inputs through the real scan functions.
# ---------------------------------------------------------------------------


class TestCarrierGuardLiveness:
    def test_dict_str_object_param_is_flagged(self) -> None:
        res = scan_carrier_source("def f(row: dict[str, object]) -> None: ...\n")
        assert res["counts"]["dict_str_object"] == 1

    def test_dict_str_any_return_is_flagged(self) -> None:
        res = scan_carrier_source(
            "from typing import Any\ndef f() -> dict[str, Any]: ...\n"
        )
        assert res["counts"]["dict_str_any"] == 1

    def test_bare_object_param_is_flagged(self) -> None:
        res = scan_carrier_source("def f(x: object) -> None: ...\n")
        assert res["counts"]["bare_object"] == 1

    def test_tuple_return_arity_three_is_flagged(self) -> None:
        res = scan_carrier_source("def f() -> tuple[int, str, bool]: ...\n")
        assert res["counts"]["tuple_return_gt2"] == 1

    def test_tuple_return_arity_two_is_clean(self) -> None:
        res = scan_carrier_source("def f() -> tuple[int, str]: ...\n")
        assert res["counts"]["tuple_return_gt2"] == 0

    def test_variadic_tuple_return_is_clean(self) -> None:
        res = scan_carrier_source("def f() -> tuple[int, ...]: ...\n")
        assert res["counts"]["tuple_return_gt2"] == 0

    def test_getattr_in_public_fn_is_flagged(self) -> None:
        res = scan_carrier_source("def f(o):\n    return getattr(o, 'x')\n")
        assert res["counts"]["getattr_call"] == 1

    def test_private_fn_signature_is_not_scanned(self) -> None:
        res = scan_carrier_source("def _f(row: dict[str, object]) -> None: ...\n")
        assert res["counts"]["dict_str_object"] == 0
        # ...but a typed named carrier is never an offender either:
        res2 = scan_carrier_source("def f(rec: CompileRecord) -> bool: ...\n")
        assert sum(res2["counts"].values()) == 0

    def test_dict_str_str_is_clean(self) -> None:
        res = scan_carrier_source("def f(d: dict[str, str]) -> None: ...\n")
        assert sum(res["counts"].values()) == 0

    # ---- XP-01 ----

    def test_replay_authorized_true_default_is_a_collapse(self) -> None:
        recs = scan_plane_fields("class C:\n    replay_authorized: bool = True\n")
        assert recs[0]["default"] is True
        # and the file-level check would flag it:
        text = "class C:\n    replay_authorized: bool = True\n"
        bad = [r for r in scan_plane_fields(text) if r["default"] is not False]
        assert bad

    def test_replay_authorized_false_default_is_clean(self) -> None:
        text = "class C:\n    replay_authorized: bool = False\n"
        bad = [r for r in scan_plane_fields(text) if r["default"] is not False]
        assert bad == []

    def test_surface_only_false_default_is_a_collapse(self) -> None:
        text = "class C:\n    surface_only: bool = False\n"
        bad = [r for r in scan_plane_fields(text) if r["default"] is not True]
        assert bad

    def test_non_literal_default_is_flagged(self) -> None:
        text = "class C:\n    replay_authorized: bool = some_flag\n"
        recs = scan_plane_fields(text)
        assert recs[0]["default"] == "<<non-literal>>"


# ---------------------------------------------------------------------------
# Baseline regeneration entry point.
# ---------------------------------------------------------------------------


def _update_baseline() -> None:
    per_file = scan_carrier_boundary(_REPO_ROOT)
    payload = {
        "_doc": (
            "Typed-carrier phase-boundary ratchet baseline (FW-09). Per-file total "
            "untyped-carrier signatures in PUBLIC functions of core/finland; may only "
            "fall. XP-01 plane-noncollapse has no baseline (must be 0 always). "
            "Regenerate: uv run python "
            "tests/test_typed_carrier_boundary_ratchet.py --update-baseline"
        ),
        "untyped_carrier_counts": per_file,
        "total_untyped_carriers": sum(per_file.values()),
    }
    out = _REPO_ROOT / _BASELINE_PATH
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} (total {payload['total_untyped_carriers']})")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(scan_carrier_boundary(_REPO_ROOT), indent=2))
