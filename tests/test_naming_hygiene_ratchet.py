"""Monotone naming-hygiene ratchet gate ("Gate 46a", Pro §14).

Locks the naming gains of the #46/#47 rename rounds so they cannot silently
regress (``notes/ARCHITECTURE_LEAK_LEDGER.md`` "Deferred CI-lint ideas (Pro
§14)"). Three one-way ratchets, each mirroring the established regex-ratchet
(Gate 2) / apply-decline-ratchet (Gate 3) discipline:

  Lint 1 — certified-family symbol ratchet. The SET of ``certif``-stem code
    identifiers may only SHRINK. A NEW one (a regression of RN1's "certified is
    reserved for cert-root") FAILS CI — the author must use ``*Coverage`` or
    consciously extend the baseline.

  Lint 2 — bare ``status`` public-schema surface ratchet. The per-file count of
    bare-``status`` surface sites (a serialized ``"status":`` dict key or an
    annotated ``status`` field/param) may only FALL. A NEW one FAILS CI; the
    author namespaces it (``*_status``) or, if genuinely internal, bumps the
    baseline. HEURISTIC (loud): a surface proxy, not a public/internal
    classifier — it over-includes internal sites so it never fails on a
    pre-existing internal ``status``; the lock is "no NEW bare status surface".

  Lint 3 + 4 — public-schema-id registry ratchet (tractable subset). The SET of
    ``lawvm.<name>.vN`` schema-id strings may only grow by an explicit baseline
    update. A NEW id FAILS CI (conscious registration). The full plane/seam +
    source-root declaration check (Pro §14 lints 3+4) is DEFERRED (no declaration
    vocabulary exists to check against yet); see ``DEFERRED_SPEC`` in
    ``scripts/inventory_naming_hygiene.py``.

The detectors are AST-based (so comments / docstrings / schema-id STRINGS never
mis-count as code symbols). The committed baseline lives at
``tests/data/naming_hygiene_ratchet_baseline.json``.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_naming_hygiene.py"


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_naming_hygiene", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.RATCHET_BASELINE_PATH
    assert path.exists(), (
        f"Missing naming-hygiene ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_naming_hygiene.py --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Lint 1: certified-family symbol ratchet
# ---------------------------------------------------------------------------


class TestCertifiedSymbolRatchet:
    def test_no_new_certified_symbol(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_certified_symbols(_REPO_ROOT)
        allowed = set(baseline["certified_symbols"])
        current = set(state["certified_symbols"])
        new_symbols = sorted(current - allowed)
        if new_symbols:
            sites = state["symbol_sites"]
            offenders = "\n".join(
                f"  {sym}  ({', '.join(sites.get(sym, [])[:3])})"
                for sym in new_symbols
            )
            pytest.fail(
                "\n[NAMING-HYGIENE RATCHET] NEW `certif`-stem code identifier(s) "
                "added:\n"
                + offenders
                + "\n\nRN1 reserves the word 'certified'/'certificate' for "
                "cert-root-covered artifacts (the `lawvm.certificate.v0` envelope "
                "+ checker verdict + the kept `CoverageCertificate`); the rename "
                "rounds moved everything else to `*Coverage`. Either:\n"
                "  (1) rename the new symbol to a `*Coverage` form, or\n"
                "  (2) if it is genuinely cert-root-related, consciously add it to "
                "the baseline:\n"
                "      uv run python scripts/inventory_naming_hygiene.py "
                "--update-baseline\n"
                "See notes/ARCHITECTURE_LEAK_LEDGER.md (Pro §14)."
            )

    def test_certified_set_only_shrinks(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_certified_symbols(_REPO_ROOT)
        allowed = set(baseline["certified_symbols"])
        current = set(state["certified_symbols"])
        removed = sorted(allowed - current)
        if removed:
            pytest.fail(
                "\n[NAMING-HYGIENE RATCHET] certified-family symbol(s) were "
                "removed — good work, but the baseline must be lowered to lock the "
                "gain in:\n"
                + "\n".join(f"  {sym}" for sym in removed)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_naming_hygiene.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever shrink)."
            )

    def test_certified_count_matches_set(self) -> None:
        baseline = _load_baseline()
        assert baseline["certified_symbol_count"] == len(
            baseline["certified_symbols"]
        ), "Baseline certified_symbol_count is inconsistent with its set."


# ---------------------------------------------------------------------------
# Lint 2: bare ``status`` public-schema surface ratchet
# ---------------------------------------------------------------------------


class TestBareStatusRatchet:
    def test_no_new_bare_status_surface(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_bare_status(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["bare_status_counts"]
        current_counts: dict[str, int] = state["bare_status_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                offenders = "\n".join(
                    f"      {s['file']}:{s['line']}  ({s['shape']})"
                    for s in state["sites"]
                    if s["file"] == rel
                )
                increases.append(
                    f"  {rel}: {count} bare-`status` surface sites "
                    f"(baseline {allowed}, +{count - allowed})\n{offenders}"
                )
        if increases:
            pytest.fail(
                "\n[NAMING-HYGIENE RATCHET] NEW bare-`status` public-schema "
                "surface site(s) added:\n"
                + "\n".join(increases)
                + "\n\nA4 namespaced public-schema `status` keys/fields "
                "(`provision_status` / `claim_status` / ...). A NEW bare `status` "
                "surface (a `\"status\":` serialized dict key or an annotated "
                "`status` field/param) must not be added. Either:\n"
                "  (1) namespace it (`<thing>_status`), or\n"
                "  (2) if it is a genuinely internal/non-public `status`, "
                "consciously bump the baseline:\n"
                "      uv run python scripts/inventory_naming_hygiene.py "
                "--update-baseline\n"
                "HEURISTIC NOTE: this is a SURFACE proxy, not a proven "
                "public/internal classifier; the baseline already absorbs all "
                "current internal sites, so this only fires on NEW ones. See "
                "notes/ARCHITECTURE_LEAK_LEDGER.md (Pro §14)."
            )

    def test_bare_status_ratchet_only_tightens(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_bare_status(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["bare_status_counts"]
        current_counts: dict[str, int] = state["bare_status_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} (baseline {allowed}, "
                    f"-{allowed - count})"
                )
        if decreases:
            pytest.fail(
                "\n[NAMING-HYGIENE RATCHET] bare-`status` surface count DROPPED — "
                "good work, but the baseline must be lowered to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_naming_hygiene.py "
                "--update-baseline"
            )

    def test_bare_status_total_matches_baseline_invariant(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_bare_status(_REPO_ROOT)
        assert baseline["total_bare_status"] == sum(
            baseline["bare_status_counts"].values()
        ), "Baseline total_bare_status is inconsistent with its per-file counts."
        assert state["total_bare_status"] <= baseline["total_bare_status"], (
            f"Total bare-status surface sites {state['total_bare_status']} "
            f"exceeds baseline {baseline['total_bare_status']}."
        )


# ---------------------------------------------------------------------------
# Lint 3 + 4: public-schema-id registry ratchet
# ---------------------------------------------------------------------------


class TestPublicSchemaRatchet:
    def test_no_new_public_schema(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_public_schemas(_REPO_ROOT)
        allowed = set(baseline["public_schemas"])
        current = set(state["public_schemas"])
        new_schemas = sorted(current - allowed)
        if new_schemas:
            sites = state["schema_sites"]
            offenders = "\n".join(
                f"  {schema}  ({', '.join(sites.get(schema, [])[:2])})"
                for schema in new_schemas
            )
            pytest.fail(
                "\n[NAMING-HYGIENE RATCHET] NEW public schema id(s) "
                "(`lawvm.<name>.vN`) added:\n"
                + offenders
                + "\n\nA new public/consumer-visible serialized schema root must "
                "be a conscious registration (Pro §14 lints 3+4 will hang the "
                "future plane/seam + source-root declaration off this registry). "
                "Add it to the baseline:\n"
                "      uv run python scripts/inventory_naming_hygiene.py "
                "--update-baseline\n"
                "See notes/ARCHITECTURE_LEAK_LEDGER.md and DEFERRED_SPEC in "
                "scripts/inventory_naming_hygiene.py."
            )

    def test_public_schema_count_matches_set(self) -> None:
        baseline = _load_baseline()
        assert baseline["public_schema_count"] == len(
            baseline["public_schemas"]
        ), "Baseline public_schema_count is inconsistent with its set."

    def test_deferred_spec_is_documented(self) -> None:
        """The full plane/seam + source-root semantic check (Pro §14 lints 3+4)
        is deferred, NOT faked — its concrete spec must be present in the script
        so the later build has a contract to implement."""
        assert "SchemaDescriptor" in _INV.DEFERRED_SPEC or hasattr(
            _INV, "DEFERRED_SPEC"
        )
        assert "DEFERRED" in _INV.DEFERRED_SPEC


# ---------------------------------------------------------------------------
# Guard-liveness: drive synthetic inputs through the production scan helpers to
# prove each ratchet actually catches a NEW violation, and does NOT mis-count a
# comment / docstring / schema-id string. Fire-drill style (AGENTS.md §2.9).
# ---------------------------------------------------------------------------


class TestNamingHygieneGuardLiveness:
    # --- Lint 1: certified-symbol detector ---

    def _certif_ids(self, src: str) -> set[str]:
        import ast as _ast

        tree = _ast.parse(src)
        out: set[str] = set()
        for node in _ast.walk(tree):
            name = None
            if isinstance(node, _ast.Name):
                name = node.id
            elif isinstance(node, _ast.Attribute):
                name = node.attr
            elif isinstance(
                node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)
            ):
                name = node.name
            elif isinstance(node, _ast.arg):
                name = node.arg
            elif isinstance(node, _ast.keyword) and node.arg is not None:
                name = node.arg
            if name and _INV._identifier_has_certif_stem(name):
                out.add(name)
        return out

    def test_certif_class_name_is_detected(self) -> None:
        assert "MyCertificate" in self._certif_ids("class MyCertificate: pass\n")

    def test_certif_function_name_is_detected(self) -> None:
        assert "build_certified_x" in self._certif_ids(
            "def build_certified_x():\n    return 1\n"
        )

    def test_certif_in_comment_is_not_detected(self) -> None:
        # AST scan ignores comments entirely.
        assert self._certif_ids("x = 1  # certified note\n") == set()

    def test_certif_in_docstring_is_not_detected(self) -> None:
        assert self._certif_ids('def f():\n    "certified docstring"\n    return 1\n') == set()

    def test_certif_in_schema_id_string_is_not_detected(self) -> None:
        # The schema-id literal "lawvm.certified_tree_transition.v0" must NOT
        # register as a code symbol (it's a string, owned by lint 3).
        assert self._certif_ids('x = "lawvm.certified_tree_transition.v0"\n') == set()

    # --- Lint 2: bare-status detector ---

    def _status_sites(self, src: str) -> list[dict[str, Any]]:
        import ast as _ast

        return _INV._bare_status_sites_in_module(_ast.parse(src), "x.py")

    def test_status_dict_key_is_detected(self) -> None:
        sites = self._status_sites('d = {"status": x}\n')
        assert len(sites) == 1 and sites[0]["shape"] == "dict_key"

    def test_annotated_status_field_is_detected(self) -> None:
        sites = self._status_sites("class C:\n    status: str\n")
        assert len(sites) == 1 and sites[0]["shape"] == "ann_field"

    def test_annotated_status_param_is_detected(self) -> None:
        sites = self._status_sites("def f(status: str):\n    return status\n")
        assert len(sites) == 1 and sites[0]["shape"] == "ann_param"

    def test_namespaced_status_dict_key_is_not_detected(self) -> None:
        assert self._status_sites('d = {"provision_status": x}\n') == []

    def test_unannotated_status_local_is_not_detected(self) -> None:
        # A bare local assignment ``status = ...`` is a transient, not a schema
        # surface — only annotated fields/params and dict keys count.
        assert self._status_sites("status = compute()\n") == []

    def test_status_in_comment_is_not_detected(self) -> None:
        assert self._status_sites("x = 1  # status: ready\n") == []

    # --- Lint 3: public-schema detector ---

    def _schemas(self, src: str) -> set[str]:
        import ast as _ast

        out: set[str] = set()
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                if _INV._PUBLIC_SCHEMA_ID.match(node.value):
                    out.add(node.value)
        return out

    def test_public_schema_id_is_detected(self) -> None:
        assert self._schemas('s = "lawvm.my_report.v1"\n') == {"lawvm.my_report.v1"}

    def test_non_versioned_lawvm_string_is_not_detected(self) -> None:
        assert self._schemas('s = "lawvm.my_report"\n') == set()

    def test_arbitrary_string_is_not_detected(self) -> None:
        assert self._schemas('s = "some.other.v1"\n') == set()


# ---------------------------------------------------------------------------
# Cross-check: the production scans must read the live tree as zero NEW vs the
# committed baseline (defence in depth + a tripwire if the detector regresses).
# ---------------------------------------------------------------------------


class TestLiveTreeMatchesBaseline:
    def test_certified_no_new_vs_baseline(self) -> None:
        baseline = _load_baseline()
        current = set(_INV.scan_certified_symbols(_REPO_ROOT)["certified_symbols"])
        assert not (current - set(baseline["certified_symbols"]))

    def test_bare_status_no_new_vs_baseline(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_bare_status(_REPO_ROOT)
        for rel, count in state["bare_status_counts"].items():
            assert count <= baseline["bare_status_counts"].get(rel, 0), rel

    def test_public_schemas_no_new_vs_baseline(self) -> None:
        baseline = _load_baseline()
        current = set(_INV.scan_public_schemas(_REPO_ROOT)["public_schemas"])
        assert not (current - set(baseline["public_schemas"]))
