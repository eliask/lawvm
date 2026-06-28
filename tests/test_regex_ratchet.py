"""Monotone regex ratchet gate for the post-parse semantic plane.

Enforces the pipeline-contract rule "no NEW post-parse raw-text semantic regex
without a waiver/category" (AGENTS.md §1.12 / §2.4,
notes/LAWVM_PIPELINE_CONTRACT.md §4, the "Gate 2" spec in
notes/ARCHITECTURE_LEAK_LEDGER.md).

How it works:
  - Every file under ``src/lawvm/{core,finland}`` is either pre-cleared by
    ``CATEGORY_MAP`` (genuine source-plane / lexer / owning-parser / diagnostic
    regex use) or SCANNED on the semantic plane.
  - In a scanned file, each ``re.(search|finditer|findall|match)(`` use-site
    (including module-scope compiled-constant call sites like
    ``_X_RE.finditer(...)``) must carry an inline
    ``# lawvm-regex: <category> <rationale>`` waiver, or it counts as an
    un-waived semantic-plane regex hit.
  - The committed baseline (``tests/data/regex_ratchet_baseline.json``) records
    the per-file un-waived counts. This test FAILS if any file's count INCREASES
    over the baseline (a new leak) and also FAILS — with an instruction to commit
    the lowered baseline — if any count DROPS (a permanent ratchet tightening).
  - Raw-text reach-back use-sites (regex over ``raw_text`` / ``source_text`` /
    ``irnode_to_text`` / ``.description`` in a scanned file) are the highest
    severity (``legacy_escape_hatch``); their count may not grow either.

The existing baselined leaks (normalize.parse_ops_fallback_heuristic,
kumotaan_replay, replay_products, effect_lowering, scope, metadata, ...) are
fenced, not fixed, here. The fixes happen later in the seam-conversion work.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "inventory_parser_smells.py"


def _load_inventory_module() -> Any:
    """Import scripts/inventory_parser_smells.py (not on sys.path as a package)."""
    spec = importlib.util.spec_from_file_location(
        "lawvm_inventory_parser_smells", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_INV = _load_inventory_module()


def _load_baseline() -> dict[str, Any]:
    path = _REPO_ROOT / _INV.RATCHET_BASELINE_PATH
    assert path.exists(), (
        f"Missing regex ratchet baseline at {path}. Generate it with "
        "`uv run python scripts/inventory_parser_smells.py --update-baseline`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CATEGORY_MAP integrity
# ---------------------------------------------------------------------------


class TestCategoryMapIntegrity:
    def test_entries_are_real_files(self) -> None:
        for rel in _INV.CATEGORY_MAP:
            assert (_REPO_ROOT / rel).exists(), (
                f"CATEGORY_MAP entry {rel!r} does not correspond to a real file."
            )

    def test_categories_are_known(self) -> None:
        for rel, category in _INV.CATEGORY_MAP.items():
            assert category in _INV.PRECLEAR_CATEGORIES, (
                f"CATEGORY_MAP[{rel!r}] = {category!r} is not a valid pre-clear "
                f"category {sorted(_INV.PRECLEAR_CATEGORIES)}."
            )

    def test_precleared_files_are_in_scan_roots(self) -> None:
        scan_root_prefixes = tuple(
            f"{p.as_posix()}/" for p in _INV._RATCHET_SCAN_ROOTS
        )
        for rel in _INV.CATEGORY_MAP:
            assert rel.startswith(scan_root_prefixes), (
                f"CATEGORY_MAP entry {rel!r} is outside the scanned roots "
                f"({_INV._RATCHET_SCAN_ROOTS})."
            )

    def test_precleared_files_are_excluded_from_scan(self) -> None:
        scanned = set(_INV.iter_scanned_files(_REPO_ROOT))
        for rel in _INV.CATEGORY_MAP:
            assert rel not in scanned, (
                f"Pre-cleared file {rel!r} leaked into the scanned set."
            )


# ---------------------------------------------------------------------------
# The monotone ratchet
# ---------------------------------------------------------------------------


class TestRegexRatchet:
    def test_no_new_unwaived_semantic_regex(self) -> None:
        baseline = _load_baseline()
        state = _INV.scan_regex_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwaived_counts"]
        current_counts: dict[str, int] = state["unwaived_counts"]

        increases: list[str] = []
        for rel, count in sorted(current_counts.items()):
            allowed = baseline_counts.get(rel, 0)
            if count > allowed:
                increases.append(
                    f"  {rel}: {count} un-waived regex use-sites "
                    f"(baseline {allowed}, +{count - allowed})"
                )

        if increases:
            pytest.fail(
                "\n[REGEX RATCHET] NEW un-waived post-parse semantic regex "
                "use-sites added:\n"
                + "\n".join(increases)
                + "\n\nThis file is on the post-parse/semantic plane "
                "(not pre-cleared in CATEGORY_MAP). Either:\n"
                "  (1) route the recognizer through the owning parser / a typed "
                "RegexRecognitionCoverage row, or\n"
                "  (2) add an inline waiver "
                "`# lawvm-regex: <owning_parser|witness_only|diagnostic|prefilter|"
                "legacy_escape_hatch> <rationale>` on or above the use-site, or\n"
                "  (3) if the whole file is genuinely source-plane/lexer/"
                "owning-parser/diagnostic, add it to CATEGORY_MAP.\n"
                "See notes/LAWVM_PIPELINE_CONTRACT.md §4 and AGENTS.md §1.12."
            )

    def test_ratchet_only_tightens(self) -> None:
        """If any committed count is now achievable lower, the baseline MUST be
        re-committed at the lower value (the ratchet is permanent)."""
        baseline = _load_baseline()
        state = _INV.scan_regex_ratchet(_REPO_ROOT)
        baseline_counts: dict[str, int] = baseline["unwaived_counts"]
        current_counts: dict[str, int] = state["unwaived_counts"]

        decreases: list[str] = []
        for rel, allowed in sorted(baseline_counts.items()):
            count = current_counts.get(rel, 0)
            if count < allowed:
                decreases.append(
                    f"  {rel}: now {count} un-waived (baseline {allowed}, "
                    f"-{allowed - count})"
                )

        if decreases:
            pytest.fail(
                "\n[REGEX RATCHET] The un-waived semantic regex count DROPPED — "
                "good work, but the baseline must be lowered to lock the gain in:\n"
                + "\n".join(decreases)
                + "\n\nRegenerate and commit the baseline:\n"
                "  uv run python scripts/inventory_parser_smells.py "
                "--update-baseline\n"
                "(the baseline is a one-way ratchet; it may only ever fall)."
            )

    def test_legacy_escape_hatch_does_not_grow(self) -> None:
        """Part (d): raw-text reach-back regex (raw_text/source_text/
        irnode_to_text/.description) in a scanned file is the highest-severity
        class — the rank-1/2/3/15 sites. Their un-waived count may not grow."""
        baseline = _load_baseline()
        state = _INV.scan_regex_ratchet(_REPO_ROOT)
        ceiling = int(baseline["legacy_escape_hatch_unwaived_ceiling"])
        hatches = state["legacy_escape_hatch_unwaived"]

        if len(hatches) > ceiling:
            offenders = "\n".join(
                f"  {h['file']}:{h['line']}  {h['snippet']}" for h in hatches
            )
            pytest.fail(
                f"\n[REGEX RATCHET] legacy_escape_hatch raw-text reach-back "
                f"use-sites grew to {len(hatches)} (ceiling {ceiling}):\n"
                + offenders
                + "\n\nA NEW regex over raw_text/source_text/irnode_to_text/"
                ".description on the legal-state plane must be acknowledged with a "
                "`# lawvm-regex: legacy_escape_hatch <rationale citing leak-ledger "
                "rank N>` waiver, or removed. See notes/ARCHITECTURE_LEAK_LEDGER.md."
            )

    def test_legacy_escape_hatch_waivers_cite_a_rank(self) -> None:
        """A legacy_escape_hatch waiver MUST cite a leak-ledger rank (part d)."""
        state = _INV.scan_regex_ratchet(_REPO_ROOT)
        offenders = state["legacy_escape_hatch_waived_without_rank"]
        if offenders:
            lines = "\n".join(
                f"  {h['file']}:{h['line']}  {h['snippet']}" for h in offenders
            )
            pytest.fail(
                "\n[REGEX RATCHET] legacy_escape_hatch waiver(s) without a "
                "leak-ledger rank citation (e.g. 'rank 2'):\n" + lines
            )

    def test_total_unwaived_matches_baseline_invariant(self) -> None:
        """The committed total must be the sum of the per-file counts and must be
        an upper bound on the current total (defence in depth over the per-file
        checks above)."""
        baseline = _load_baseline()
        state = _INV.scan_regex_ratchet(_REPO_ROOT)
        assert baseline["total_unwaived"] == sum(
            baseline["unwaived_counts"].values()
        ), "Baseline total_unwaived is inconsistent with its per-file counts."
        assert state["total_unwaived"] <= baseline["total_unwaived"], (
            f"Total un-waived semantic regex hits {state['total_unwaived']} "
            f"exceeds baseline {baseline['total_unwaived']}."
        )


# ---------------------------------------------------------------------------
# Guard-liveness: the gate must actually catch a NEW un-waived hit, must honor
# a waiver, and must keep a pre-cleared file exempt. Drives synthetic inputs
# through the production scan functions (AGENTS.md §2.9 guard-liveness).
# ---------------------------------------------------------------------------


class TestRatchetGuardLiveness:
    _SCANNED_FILE = "src/lawvm/finland/normalize.py"  # a real scanned file

    def test_new_unwaived_use_site_is_counted(self) -> None:
        text = "x = re.search(r'foo', bar)\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert len(records) == 1
        assert records[0]["waived"] is False
        assert records[0]["method"] == "search"

    def test_inline_waiver_clears_a_use_site(self) -> None:
        text = "x = re.search(r'foo', bar)  # lawvm-regex: prefilter cheap guard\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert len(records) == 1
        assert records[0]["waived"] is True
        assert records[0]["waiver_category"] == "prefilter"

    def test_waiver_on_line_above_clears_a_use_site(self) -> None:
        text = (
            "# lawvm-regex: owning_parser feeds the johtolause parser\n"
            "y = _SOME_RE.finditer(text)\n"
        )
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert len(records) == 1
        assert records[0]["waived"] is True
        assert records[0]["waiver_category"] == "owning_parser"

    def test_unknown_waiver_category_does_not_clear(self) -> None:
        text = "z = re.match(r'q', s)  # lawvm-regex: totally_made_up reason\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert len(records) == 1
        assert records[0]["waived"] is False

    def test_compiled_constant_call_site_is_a_hit(self) -> None:
        text = "for m in _FI_CITED_VERSION_ID_RE.finditer(raw_text):\n    pass\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert len(records) == 1
        assert records[0]["raw_text_accessor"] is True

    def test_re_compile_alone_is_not_a_use_site(self) -> None:
        text = "_X_RE = re.compile(r'foo')\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert records == []

    def test_commented_out_call_is_not_a_use_site(self) -> None:
        text = "# x = re.search(r'foo', bar)\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert records == []

    def test_match_on_arbitrary_object_is_not_a_hit(self) -> None:
        # `.match(` on a non-regex receiver (not `re` and not *_RE/*_PATTERN)
        # must not be counted, to bound false positives.
        text = "result = some_dict.match(key)\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert records == []

    def test_raw_text_use_site_is_legacy_escape_hatch_shape(self) -> None:
        text = "hit = re.search(p, source_text)\n"
        records = _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)
        assert len(records) == 1
        assert records[0]["raw_text_accessor"] is True


# ---------------------------------------------------------------------------
# Renamed-local reach-back: the line-local heuristic alone is UNSOUND — it only
# sees raw-text accessors named on the call line. The hardened detector follows
# intra-procedural assignment so a regex over a local that was (transitively)
# assigned from a raw_text/source_text/irnode_to_text/.description accessor is
# still flagged as a legacy_escape_hatch. Each fixture is driven through the
# REAL scan path (scan_file_regex_use_sites) — guard-liveness (AGENTS.md §2.9).
# ---------------------------------------------------------------------------


class TestRenamedLocalReachBack:
    _SCANNED_FILE = "src/lawvm/finland/normalize.py"

    def _scan(self, text: str) -> list[Any]:
        return _INV.scan_file_regex_use_sites(self._SCANNED_FILE, text)

    # ---- POSITIVES: the OLD line-local heuristic would MISS these ----

    def test_one_hop_renamed_local_from_raw_text_is_flagged(self) -> None:
        """`txt = op.source.raw_text` then `re.search(pat, txt)` — the exact
        evading shape (merge.py:2542). Line-local `_RE_RAW_TEXT_ACCESSOR` would
        not fire on the call line; the AST taint pass must."""
        text = (
            "def f(op):\n"
            "    txt = op.source.raw_text\n"
            "    return re.search(PAT, txt)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "search"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is True
        assert call[0]["raw_text_via"] == "renamed_local"
        # And the line-local regex genuinely does NOT see it (proves we added value):
        line = next(ln for ln in text.splitlines() if "re.search" in ln)
        assert _INV._RE_RAW_TEXT_ACCESSOR.search(line) is None

    def test_multi_hop_transitive_rename_is_flagged(self) -> None:
        """Taint must survive an arbitrary number of rename hops within a scope."""
        text = (
            "def f(node):\n"
            "    a = irnode_to_text(node)\n"
            "    b = a.strip()\n"
            "    c = ' '.join(b.split())\n"
            "    return _CUE_RE.finditer(c)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "finditer"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is True
        assert call[0]["raw_text_via"] == "renamed_local"

    def test_wrapped_normalizer_call_preserves_taint(self) -> None:
        """Taint cannot be laundered through a wrapping helper call whose argument
        is itself an accessor (fixed_term_expiry shape:
        `normalized = _normalize(irnode_to_text(x))`)."""
        text = (
            "def f(v):\n"
            "    normalized = _normalize(irnode_to_text(v.content))\n"
            "    return _EXPIRY_RE.search(normalized)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "search"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is True

    def test_raw_text_named_parameter_is_seeded(self) -> None:
        """A helper taking a `raw_text` parameter, normalizing it into a local,
        then regexing the local (merge.py `_source_targets_plain_subsection`)."""
        text = (
            "def f(raw_text, n):\n"
            "    text = ' '.join(raw_text.split())\n"
            "    return re.search(PAT, text)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "search"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is True

    def test_for_loop_target_from_accessor_is_tainted(self) -> None:
        text = (
            "def f(node):\n"
            "    for chunk in irnode_to_text(node).split():\n"
            "        if re.match(PAT, chunk):\n"
            "            return chunk\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "match"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is True

    # ---- NEGATIVES: must NOT over-flag (false positives become wasted waivers) ----

    def test_local_from_non_raw_text_source_is_not_flagged(self) -> None:
        """A local assigned from a NON-accessor field (`child.text`, not
        `.raw_text`/`.description`) regexed must NOT be flagged."""
        text = (
            "def f(child):\n"
            "    text = (child.text or '').lstrip()\n"
            "    return re.match(PAT, text)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "match"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is False
        assert call[0]["raw_text_via"] == ""

    def test_plain_parameter_not_named_raw_text_is_not_flagged(self) -> None:
        text = (
            "def f(s):\n"
            "    return re.search(PAT, s)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "search"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is False

    def test_taint_does_not_leak_across_function_scopes(self) -> None:
        """A tainted local in one function must not flag a same-named local in a
        sibling function that has a clean source."""
        text = (
            "def dirty(op):\n"
            "    text = op.source.raw_text\n"
            "    return re.search(PAT, text)\n"
            "def clean(s):\n"
            "    text = s.upper()\n"
            "    return re.search(PAT, text)\n"
        )
        records = self._scan(text)
        by_line = {r["line"]: r for r in records if r["method"] == "search"}
        assert by_line[3]["raw_text_accessor"] is True  # dirty()
        assert by_line[6]["raw_text_accessor"] is False  # clean()

    def test_nested_function_local_does_not_taint_enclosing(self) -> None:
        """An inner function's tainted local must not taint a same-named local of
        the enclosing function (each function is its own taint universe)."""
        text = (
            "def outer(s):\n"
            "    text = s.lower()\n"
            "    def inner(op):\n"
            "        text = op.source.raw_text\n"
            "        return re.search(PAT, text)\n"
            "    return re.search(PAT, text)\n"
        )
        records = self._scan(text)
        by_line = {r["line"]: r for r in records if r["method"] == "search"}
        assert by_line[5]["raw_text_accessor"] is True   # inner(): tainted
        assert by_line[6]["raw_text_accessor"] is False  # outer(): clean

    def test_module_scope_does_not_pull_in_function_locals(self) -> None:
        """Module-scope taint must not absorb function-body locals (regression:
        an early version descended into function bodies from module scope)."""
        text = (
            "def f(op):\n"
            "    raw = op.source.raw_text\n"
            "def g(t):\n"
            "    return re.search(PAT, t)\n"
        )
        records = self._scan(text)
        call = [r for r in records if r["method"] == "search"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is False

    def test_unparseable_text_falls_back_to_line_local_only(self) -> None:
        """If the file does not parse, the AST pass yields no hits but the
        line-local heuristic still applies (no crash, no silent over-claim)."""
        text = "def f(  # syntactically broken\n    re.search(PAT, raw_text)\n"
        # raw_text_tainted_call_lines must not raise:
        assert _INV.raw_text_tainted_call_lines(text) == set()
        records = self._scan(text)
        # The line-local accessor on the call line is still detected.
        call = [r for r in records if r["method"] == "search"]
        assert len(call) == 1
        assert call[0]["raw_text_accessor"] is True
        assert call[0]["raw_text_via"] == "line_local"

    def test_former_merge_blind_spot_reach_back_is_migrated_out(self) -> None:
        """End-to-end on real source: the confirmed live blind spot
        (the old merge.py `_source_targets_plain_subsection` renamed-local reach-back)
        has been MIGRATED into scope.py's owning descendant-scope recognizer, so
        merge.py must no longer carry any raw-text-accessor regex use-site.

        The detector's renamed-local taint logic itself remains exercised by the
        synthetic fixtures above (`test_raw_text_named_parameter_is_seeded`)."""
        merge_path = _REPO_ROOT / "src/lawvm/finland/merge.py"
        if not merge_path.exists():  # pragma: no cover - defensive
            pytest.skip("merge.py not present in this checkout")
        text = merge_path.read_text(encoding="utf-8")
        # The migrated reach-back was the source-plane `N momentti` / `N momentin
        # johdanto` subsection predicate; it now lives in scope.py. merge.py must
        # no longer contain those source-plane patterns.
        assert "momentin\\s+johdanto" not in text and "momentti\\b" not in text, (
            "merge.py still carries the migrated source-plane momentti regex; the "
            "predicate must route through scope.source_targets_plain_subsection_moment"
        )
        # The renamed-local taint shape (`_source_targets_plain_subsection`) is gone.
        assert "_source_targets_plain_subsection" not in text
