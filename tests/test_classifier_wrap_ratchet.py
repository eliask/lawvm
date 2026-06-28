"""Monotone FW-07 classifier-prose WRAP-mandate ratchet (registry row FW-07).

FW-07 — *any classifier regex over long/adversarial prose emitting a gate/recall
feature must be built via ``compile_classifier_regex`` (backtracking-bounded +
sound required-literal prefilter), never raw ``re.compile``.* The primitive
(``lawvm.core.regex_safety.compile_classifier_regex``) exists, but no standing
lint mandates its use across the LawVM source tree — a new classifier can
silently reach for raw ``re.compile`` and reintroduce the catastrophic-
backtracking class that cost ukpga/1970/9 104s (the A8 incident).

This gate freezes the count of raw ``re.compile(...)`` use-sites in the SCANNED
(non-precleared) semantic-plane files across EVERY LawVM frontend
(``_RATCHET_SCAN_ROOTS`` = core, finland, estonia, norway, sweden, new_zealand,
eu, us_federal, uk_legislation, substrate, semantic, open_law, tools) — the
files where a regex is most likely a post-parse classifier rather than a
source-plane locator or a lexer/owning-parser tokenizer (those modules are
pre-cleared by the same ``CATEGORY_MAP`` the regex ratchet uses, so they are
exempt by category). The per-file raw-``re.compile`` count is a committed
baseline that may only FALL. A NEW raw ``re.compile`` in a scanned file trips
the gate; the author must either build the pattern via
``compile_classifier_regex`` (the WRAP mandate) or, if it is a genuinely
non-classifier lexer/locator that simply lives in a scanned module,
consciously bump the baseline.

HONESTY (the generator's stopping rule)
=======================================
This is IMPL-at-frozen-baseline with a NAMED HEURISTIC GAP, not clean-at-0. The
current scanned tree carries a baseline of raw ``re.compile`` sites. A purely
static check CANNOT prove a given pattern is a "classifier over long/adversarial
prose emitting a gate/recall feature" versus a bounded lexer/locator regex —
that distinction is human judgment. So this gate over-includes (it freezes
lexer/locator compiles in scanned modules too) exactly like the naming-hygiene
bare-``status`` surface proxy: the lock is "no NEW raw ``re.compile`` in a
scanned semantic-plane module without a conscious decision", NOT "every
baselined site is a prose classifier". The complementary ``test_regex_perf_gate``
already bounds backtracking risk for the patterns it sees; FW-07 is the
WRAP-adoption ratchet that pushes new classifiers onto the safe primitive.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_parser_smells.py"
_BASELINE_PATH = "tests/data/classifier_wrap_ratchet_baseline.json"


def _load_inventory_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_parser_smells_fw07", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def count_raw_re_compile(text: str) -> int:
    """Number of ``re.compile(...)`` call-sites in a module (AST, so comments /
    docstrings / strings never count). A ``compile_classifier_regex(...)`` call is
    NOT a ``re.compile`` and is therefore never counted — adopting the wrap fixes
    the smell."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "compile"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re"
        ):
            n += 1
    return n


def scan_scanned_raw_compile(repo_root: Path) -> dict[str, int]:
    """{rel: raw-re.compile count} over the SCANNED (non-precleared)
    semantic-plane files across EVERY LawVM frontend (the regex-ratchet scan
    set, ``_RATCHET_SCAN_ROOTS``). Expanding from finland-only to all
    frontends implements the doc's "cross-cutting WRAP" step
    (notes/REGEX_TO_GRAMMAR_MIGRATION.md rank 10) so a new classifier regex
    cannot silently land as raw ``re.compile`` in an un-scanned frontend.
    """
    out: dict[str, int] = {}
    for rel in _INV.iter_scanned_files(repo_root):
        try:
            text = (repo_root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        n = count_raw_re_compile(text)
        if n:
            out[rel] = n
    return out


# Backward-compatible alias retained for external callers and tests.
scan_scanned_finland_raw_compile = scan_scanned_raw_compile


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _BASELINE_PATH
    assert path.exists(), (
        f"Missing FW-07 classifier-wrap baseline at {path}. Generate it with "
        "`uv run python tests/test_classifier_wrap_ratchet.py --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _baseline_counts(baseline: dict[str, Any]) -> dict[str, int]:
    counts = baseline["raw_re_compile_counts"]
    assert isinstance(counts, dict)
    return {str(k): int(v) for k, v in counts.items()}


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestClassifierWrapRatchet:
    def test_no_new_raw_re_compile_in_scanned_files(self) -> None:
        baseline = _baseline_counts(_load_baseline())
        current = scan_scanned_raw_compile(_REPO_ROOT)
        increases = [
            f"  {rel}: {count} raw re.compile (baseline {baseline.get(rel, 0)}, "
            f"+{count - baseline.get(rel, 0)})"
            for rel, count in sorted(current.items())
            if count > baseline.get(rel, 0)
        ]
        if increases:
            pytest.fail(
                "\n[FW-07 CLASSIFIER WRAP] NEW raw re.compile in a scanned "
                "semantic-plane module:\n"
                + "\n".join(increases)
                + "\n\nA classifier regex over prose must be built via "
                "`compile_classifier_regex` (backtracking-bounded + sound prefilter), "
                "not raw `re.compile`. Either adopt the wrap, or — if this is a "
                "genuine bounded lexer/locator that lives in a scanned module — "
                "consciously bump the baseline:\n"
                "  uv run python tests/test_classifier_wrap_ratchet.py "
                "--update-baseline\n"
                "See notes/LAWVM_AUDIT_INVARIANT_REGISTRY.md row FW-07."
            )

    def test_ratchet_only_tightens(self) -> None:
        baseline = _baseline_counts(_load_baseline())
        current = scan_scanned_raw_compile(_REPO_ROOT)
        decreases = [
            f"  {rel}: now {current.get(rel, 0)} (baseline {a})"
            for rel, a in sorted(baseline.items())
            if current.get(rel, 0) < a
        ]
        if decreases:
            pytest.fail(
                "\n[FW-07 CLASSIFIER WRAP] A raw-re.compile count DROPPED (a "
                "classifier likely moved onto the wrap) — lower the baseline to "
                "lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\n  uv run python tests/test_classifier_wrap_ratchet.py "
                "--update-baseline\n(the baseline is a one-way ratchet)."
            )

    def test_total_consistent_and_upper_bounded(self) -> None:
        baseline = _load_baseline()
        counts = _baseline_counts(baseline)
        assert baseline["total_raw_re_compile"] == sum(counts.values())
        current = scan_scanned_raw_compile(_REPO_ROOT)
        assert sum(current.values()) <= baseline["total_raw_re_compile"]

    def test_scan_is_not_blind(self) -> None:
        """Liveness: the scan observes real raw-re.compile sites; zero would mean
        the AST walk went blind (vacuously green)."""
        current = scan_scanned_raw_compile(_REPO_ROOT)
        assert sum(current.values()) > 0


class TestClassifierWrapTripProof:
    def test_injected_raw_compile_exceeds_file_baseline(self) -> None:
        """A real scanned file with one extra raw re.compile appended must scan
        ABOVE its committed per-file baseline → the ratchet would FAIL."""
        baseline = _baseline_counts(_load_baseline())
        current = scan_scanned_raw_compile(_REPO_ROOT)
        # Pick any scanned file present in the live scan.
        assert current, "no scanned file produced a raw-re.compile count"
        rel = next(iter(sorted(current)))
        clean = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        injected = clean + '\n_LEAK_RE = re.compile(r".+adversarial.+")\n'
        assert count_raw_re_compile(injected) > baseline.get(rel, 0)

    def test_compile_classifier_regex_is_not_counted(self) -> None:
        """Adopting the wrap removes the smell — a compile_classifier_regex call is
        not a raw re.compile."""
        src = (
            "from lawvm.core.regex_safety import compile_classifier_regex\n"
            '_X_RE = compile_classifier_regex(r"a+b")\n'
        )
        assert count_raw_re_compile(src) == 0

    def test_clean_tree_at_or_below_baseline(self) -> None:
        baseline = _baseline_counts(_load_baseline())
        current = scan_scanned_raw_compile(_REPO_ROOT)
        over = {rel: c for rel, c in current.items() if c > baseline.get(rel, 0)}
        assert not over, f"unexpected over-baseline files: {over}"


# ---------------------------------------------------------------------------
# Guard-liveness: the AST detector counts re.compile, ignores comments/strings.
# ---------------------------------------------------------------------------


class TestRawCompileDetector:
    def test_re_compile_counted(self) -> None:
        assert count_raw_re_compile('import re\nx = re.compile("a")\n') == 1

    def test_re_compile_in_comment_not_counted(self) -> None:
        assert count_raw_re_compile("x = 1  # re.compile('a')\n") == 0

    def test_re_compile_in_string_not_counted(self) -> None:
        assert count_raw_re_compile('s = "re.compile(x)"\n') == 0

    def test_other_compile_method_not_counted(self) -> None:
        # foo.compile(...) where foo is not the `re` module.
        assert count_raw_re_compile("x = ast.compile(src)\n") == 0


# ---------------------------------------------------------------------------
# Baseline regeneration entry point.
# ---------------------------------------------------------------------------


def _update_baseline() -> None:
    counts = scan_scanned_raw_compile(_REPO_ROOT)
    payload = {
        "_doc": (
            "FW-07 classifier-prose WRAP-mandate ratchet baseline. Per-file count "
            "of raw `re.compile(...)` use-sites in the SCANNED (non-precleared) "
            "semantic-plane files across every LawVM frontend "
            "(``_RATCHET_SCAN_ROOTS``); may only FALL. A NEW raw re.compile "
            "trips the gate — adopt `compile_classifier_regex` (the WRAP mandate) "
            "or consciously bump. HEURISTIC: cannot statically prove "
            "classifier-vs-lexer, so it over-includes (freezes lexers in scanned "
            "modules too). Regenerate: uv run python "
            "tests/test_classifier_wrap_ratchet.py --update-baseline. See "
            "registry row FW-07."
        ),
        "raw_re_compile_counts": dict(sorted(counts.items())),
        "total_raw_re_compile": sum(counts.values()),
    }
    out = _REPO_ROOT / _BASELINE_PATH
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} (total {payload['total_raw_re_compile']})")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        print(json.dumps(scan_scanned_raw_compile(_REPO_ROOT), indent=2))
