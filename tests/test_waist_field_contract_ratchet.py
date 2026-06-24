"""Monotone waist field-contract ratchets (registry rows CONTRACT-01 / CONTRACT-02).

These two gates extend the bare-value waist ratchet (XP-02,
``test_waist_contract_ratchet.py``) from "every waist returns a StageResult-family
carrier" to the deeper per-field and per-type contract:

CONTRACT-01 — six-field StageResult completeness
================================================
XP-02 bans the BARE-VALUE return; CONTRACT-01 is the PER-FIELD arm. A waist that
returns a ``StageResult`` but populates only ``value`` and leaves the other five
fields {evidence, residuals, findings, coverage, authority} at their trivial
defaults is "returning a stub" for those fields (the §2 "TODAY most return only
value plus convention-bridged side-channels" state). This gate freezes, per waist
module, the SET of six fields that are NEVER explicitly passed to any
StageResult-family construction in that module (the module's "stub set"). The stub
set may only SHRINK — a waist that starts populating ``coverage=`` removes it from
the set, which must be re-committed.

CONTRACT-02 — waist input/output type conformance
=================================================
Every waist's runtime input/output types should equal its §2 canonical types. The
§2 table's ``(TODAY: bare str / lxml._Element / dict[str,Any])`` parentheticals
are NOT prose — each is a typed conformance-GAP row (a waist consuming/emitting an
untyped shape where the contract names a typed carrier). This gate parses the §2
table and freezes the count of ``(TODAY: …)`` gap annotations per waist row at a
committed baseline. The doc IS the source of truth: closing a conformance gap =
deleting the parenthetical, which lowers the count; the baseline may only fall.

HONESTY (the generator's stopping rule)
=======================================
Both are PART-with-named-gap, not clean-at-0:

  * CONTRACT-01 is MODULE-granular and STATIC: it sees which fields are ever
    passed to a StageResult construction in the waist's module, NOT whether the
    runtime values are non-trivial, and NOT constructions built in a cross-module
    helper. A field passed ``coverage=EMPTY_COVERAGE`` counts as "passed" even
    though it is the trivial default. The lock is "the stub set may only shrink",
    a monotone completeness ratchet — not a proof of runtime completeness.
  * CONTRACT-02 is a DOC-DERIVED conformance-gap ratchet: it freezes the
    self-reported ``(TODAY: …)`` gaps in the §2 table. It enforces that the
    documented gap count may only fall (a closed gap = a deleted parenthetical),
    NOT that the runtime signatures independently match the canonical column. The
    deeper runtime-type conformance check needs per-waist signature typing that the
    contract's own typed-carrier migration (XP-02/StageResult endgame) is landing
    incrementally; this gate fences the documented backlog so it cannot grow.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_DOC = "notes/LAWVM_PIPELINE_CONTRACT.md"
_C1_BASELINE_PATH = "tests/data/contract01_stageresult_fields_baseline.json"
_C2_BASELINE_PATH = "tests/data/contract02_type_conformance_baseline.json"

_SIX_FIELDS = ("value", "evidence", "residuals", "findings", "coverage", "authority")
_STAGE_CTORS = frozenset({"StageResult", "PartitionResult", "FiProjectionResult"})


# Reuse the canonical waist registry from the XP-02 ratchet rather than
# re-declaring it (single source of truth; if it rots, that test fails first).
def _load_waist_registry() -> Any:
    path = _REPO_ROOT / "tests" / "test_waist_contract_ratchet.py"
    spec = importlib.util.spec_from_file_location("lawvm_waist_contract_reg", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_WAIST_MOD = _load_waist_registry()
_WAISTS = _WAIST_MOD._WAISTS  # tuple[_WaistSpec, ...]


# ===========================================================================
# CONTRACT-01 — six-field StageResult completeness (module-granular stub set)
# ===========================================================================


def stage_fields_passed_in_module(text: str) -> set[str]:
    """The set of six-contract fields explicitly passed to ANY StageResult-family
    construction in a module (union over all constructions). AST-based."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    passed: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _STAGE_CTORS
        ):
            for kw in node.keywords:
                if kw.arg in _SIX_FIELDS:
                    passed.add(kw.arg)
            # First positional is ``value`` by signature.
            if node.args:
                passed.add("value")
    return passed


def waist_stub_sets(repo_root: Path) -> dict[str, list[str]]:
    """{waist: sorted[fields NEVER passed to a StageResult ctor in its module]}.

    A waist whose module contains NO StageResult-family construction (it builds
    its carrier in a cross-module helper, e.g. the timeline_materialization waist)
    is recorded with the SENTINEL ``["<no-local-construction>"]`` so it is visible
    debt, not silently treated as stubbing all six."""
    out: dict[str, list[str]] = {}
    seen_mods: dict[str, set[str]] = {}
    for spec in _WAISTS:
        mod = spec["module"]
        if mod not in seen_mods:
            try:
                text = (repo_root / mod).read_text(encoding="utf-8")
            except OSError:
                seen_mods[mod] = set()
            else:
                seen_mods[mod] = stage_fields_passed_in_module(text)
        passed = seen_mods[mod]
        if not _module_has_stage_ctor(repo_root, mod):
            out[spec["waist"]] = ["<no-local-construction>"]
        else:
            out[spec["waist"]] = sorted(set(_SIX_FIELDS) - passed)
    return out


def _module_has_stage_ctor(repo_root: Path, mod: str) -> bool:
    try:
        tree = ast.parse((repo_root / mod).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _STAGE_CTORS
        ):
            return True
    return False


def _load_c1_baseline() -> dict[str, list[str]]:
    path = _REPO_ROOT / _C1_BASELINE_PATH
    assert path.exists(), (
        f"Missing CONTRACT-01 baseline at {path}. Generate it with "
        "`uv run python tests/test_waist_field_contract_ratchet.py --update-baseline`."
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): [str(x) for x in v] for k, v in data["waist_stub_sets"].items()}


class TestContract01StageResultFields:
    def test_no_new_stubbed_field(self) -> None:
        baseline = _load_c1_baseline()
        current = waist_stub_sets(_REPO_ROOT)
        regressions: list[str] = []
        for waist, stubbed in sorted(current.items()):
            allowed = set(baseline.get(waist, []))
            new = sorted(set(stubbed) - allowed)
            if new:
                regressions.append(f"  {waist}: newly-stubbed {new}")
        if regressions:
            pytest.fail(
                "\n[CONTRACT-01] A waist STOPPED populating a StageResult field "
                "(regressed to a stub):\n"
                + "\n".join(regressions)
                + "\n\nEvery waist's StageResult should populate all six fields "
                "{value, evidence, residuals, findings, coverage, authority}. "
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md row CONTRACT-01."
            )

    def test_stub_set_only_shrinks(self) -> None:
        baseline = _load_c1_baseline()
        current = waist_stub_sets(_REPO_ROOT)
        completions: list[str] = []
        for waist, allowed in sorted(baseline.items()):
            now = set(current.get(waist, []))
            fixed = sorted(set(allowed) - now)
            if fixed:
                completions.append(f"  {waist}: now populates {fixed}")
        if completions:
            pytest.fail(
                "\n[CONTRACT-01] A waist now populates a previously-stubbed field "
                "— lower the baseline to lock the gain in:\n"
                + "\n".join(completions)
                + "\n\n  uv run python tests/test_waist_field_contract_ratchet.py "
                "--update-baseline\n(the stub set is a one-way ratchet)."
            )

    def test_every_waist_in_baseline(self) -> None:
        baseline = _load_c1_baseline()
        current = waist_stub_sets(_REPO_ROOT)
        assert set(baseline) == set(current), (
            "CONTRACT-01 baseline waist set is out of sync with the registry."
        )

    def test_scan_observes_some_completion(self) -> None:
        """Liveness: at least one waist already populates a non-value field (else
        the scan is blind and every waist would look fully stubbed)."""
        current = waist_stub_sets(_REPO_ROOT)
        non_value_populated = any(
            set(_SIX_FIELDS) - {"value"} - set(stub)
            for stub in current.values()
            if stub != ["<no-local-construction>"]
        )
        assert non_value_populated


class TestContract01GuardLiveness:
    def test_full_construction_has_empty_stub(self) -> None:
        src = (
            "StageResult(value=v, evidence=e, residuals=r, findings=f, "
            "coverage=c, authority=a)\n"
        )
        assert stage_fields_passed_in_module(src) == set(_SIX_FIELDS)

    def test_value_only_construction_stubs_five(self) -> None:
        src = "StageResult(value=v)\n"
        passed = stage_fields_passed_in_module(src)
        assert passed == {"value"}
        assert set(_SIX_FIELDS) - passed == {
            "evidence",
            "residuals",
            "findings",
            "coverage",
            "authority",
        }

    def test_positional_value_counts(self) -> None:
        src = "StageResult(v, coverage=c)\n"
        assert stage_fields_passed_in_module(src) == {"value", "coverage"}

    def test_non_stage_call_ignored(self) -> None:
        src = "SomeOther(value=v, coverage=c)\n"
        assert stage_fields_passed_in_module(src) == set()


# ===========================================================================
# CONTRACT-02 — §2 waist input/output type conformance (doc-derived gap ratchet)
# ===========================================================================

_TODAY_RE = re.compile(r"\(\s*TODAY[^)]*\)", re.IGNORECASE)
# A §2 table data row: `| waist | input | output | cert | authority |`.
_TABLE_ROW_RE = re.compile(r"^\|\s*([a-z_]+)\s*\|")


def parse_today_gap_counts(doc_text: str) -> dict[str, int]:
    """{waist: count of `(TODAY: …)` conformance-gap parentheticals on its §2 row}.

    Reads the canonical §2 waist table from the pipeline contract. Only rows whose
    first cell is a known waist name are considered (skips the header/separator)."""
    known = {spec["waist"] for spec in _WAISTS}
    counts: dict[str, int] = {}
    in_section = False
    for line in doc_text.splitlines():
        if line.startswith("## 2."):
            in_section = True
            continue
        if in_section and line.startswith("## ") and not line.startswith("## 2."):
            break
        if not in_section:
            continue
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        waist = m.group(1)
        if waist not in known:
            continue
        counts[waist] = len(_TODAY_RE.findall(line))
    return counts


def _load_c2_baseline() -> dict[str, int]:
    path = _REPO_ROOT / _C2_BASELINE_PATH
    assert path.exists(), (
        f"Missing CONTRACT-02 baseline at {path}. Generate it with "
        "`uv run python tests/test_waist_field_contract_ratchet.py --update-baseline`."
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in data["today_gap_counts"].items()}


class TestContract02TypeConformance:
    def test_no_new_conformance_gap(self) -> None:
        baseline = _load_c2_baseline()
        doc = (_REPO_ROOT / _CONTRACT_DOC).read_text(encoding="utf-8")
        current = parse_today_gap_counts(doc)
        increases = [
            f"  {waist}: {count} (TODAY:…) gaps (baseline {baseline.get(waist, 0)})"
            for waist, count in sorted(current.items())
            if count > baseline.get(waist, 0)
        ]
        if increases:
            pytest.fail(
                "\n[CONTRACT-02] NEW (TODAY:…) waist type-conformance gap in the §2 "
                "table:\n"
                + "\n".join(increases)
                + "\n\nA waist's runtime input/output type should equal its §2 "
                "canonical type; a new (TODAY:…) parenthetical is a new typed "
                "conformance gap. See registry row CONTRACT-02."
            )

    def test_gap_count_only_falls(self) -> None:
        baseline = _load_c2_baseline()
        doc = (_REPO_ROOT / _CONTRACT_DOC).read_text(encoding="utf-8")
        current = parse_today_gap_counts(doc)
        decreases = [
            f"  {waist}: now {current.get(waist, 0)} (baseline {a})"
            for waist, a in sorted(baseline.items())
            if current.get(waist, 0) < a
        ]
        if decreases:
            pytest.fail(
                "\n[CONTRACT-02] A waist conformance gap was CLOSED (a (TODAY:…) "
                "parenthetical removed) — lower the baseline to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python tests/test_waist_field_contract_ratchet.py "
                "--update-baseline\n(the baseline is a one-way ratchet)."
            )

    def test_total_consistent_and_nonzero(self) -> None:
        baseline = _load_c2_baseline()
        # The §2 table self-reports real gaps; a zero total would mean the parser
        # stopped seeing the table (vacuously green).
        assert sum(baseline.values()) > 0
        doc = (_REPO_ROOT / _CONTRACT_DOC).read_text(encoding="utf-8")
        current = parse_today_gap_counts(doc)
        assert sum(current.values()) <= sum(baseline.values())


class TestContract02GuardLiveness:
    def test_row_with_today_is_counted(self) -> None:
        doc = (
            "## 2. waists\n"
            "| Waist | in | out |\n"
            "|---|---|---|\n"
            "| source_identity | X (TODAY: bare str) | Y |\n"
            "## 3. next\n"
        )
        assert parse_today_gap_counts(doc) == {"source_identity": 1}

    def test_row_with_two_today_counted_twice(self) -> None:
        doc = (
            "## 2. waists\n"
            "| token_structure | A (TODAY: str) | B (TODAY: lxml) |\n"
            "## 3.\n"
        )
        assert parse_today_gap_counts(doc) == {"token_structure": 2}

    def test_clean_row_is_zero(self) -> None:
        doc = "## 2.\n| surface_syntax | A | B |\n## 3.\n"
        assert parse_today_gap_counts(doc) == {"surface_syntax": 0}

    def test_unknown_waist_row_ignored(self) -> None:
        doc = "## 2.\n| not_a_waist | A (TODAY: x) | B |\n## 3.\n"
        assert parse_today_gap_counts(doc) == {}

    def test_live_doc_has_gaps(self) -> None:
        doc = (_REPO_ROOT / _CONTRACT_DOC).read_text(encoding="utf-8")
        counts = parse_today_gap_counts(doc)
        assert sum(counts.values()) > 0, "live §2 table should self-report gaps"


# ===========================================================================
# Baseline regeneration entry point.
# ===========================================================================


def _update_baseline() -> None:
    stub_sets = waist_stub_sets(_REPO_ROOT)
    out1 = _REPO_ROOT / _C1_BASELINE_PATH
    out1.write_text(
        json.dumps(
            {
                "_doc": (
                    "CONTRACT-01 baseline: per-waist-MODULE set of the six "
                    "StageResult fields never explicitly passed to a StageResult-"
                    "family construction in that module (the 'stub set'). May only "
                    "SHRINK. MODULE-granular + STATIC: not a runtime-non-triviality "
                    "proof, and a `<no-local-construction>` sentinel marks waists "
                    "whose carrier is built in a cross-module helper. Regenerate: "
                    "uv run python tests/test_waist_field_contract_ratchet.py "
                    "--update-baseline. See registry row CONTRACT-01."
                ),
                "waist_stub_sets": {k: v for k, v in sorted(stub_sets.items())},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    doc = (_REPO_ROOT / _CONTRACT_DOC).read_text(encoding="utf-8")
    gap_counts = parse_today_gap_counts(doc)
    out2 = _REPO_ROOT / _C2_BASELINE_PATH
    out2.write_text(
        json.dumps(
            {
                "_doc": (
                    "CONTRACT-02 baseline: per-waist count of `(TODAY: …)` type-"
                    "conformance-gap parentheticals in the §2 waist table of "
                    "notes/LAWVM_PIPELINE_CONTRACT.md. May only FALL (closing a gap "
                    "= deleting the parenthetical). DOC-derived: it fences the "
                    "self-reported backlog, not an independent runtime-type check. "
                    "Regenerate: uv run python "
                    "tests/test_waist_field_contract_ratchet.py --update-baseline. "
                    "See registry row CONTRACT-02."
                ),
                "today_gap_counts": {k: v for k, v in sorted(gap_counts.items())},
                "total_gaps": sum(gap_counts.values()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out1} and {out2} (C2 total gaps {sum(gap_counts.values())})")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(waist_stub_sets(_REPO_ROOT), indent=2))
