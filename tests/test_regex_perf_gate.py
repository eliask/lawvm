"""Regex safety lint + adversarial perf gate.

Sensor H batch 5 (2026-05-29).

Two test groups:

Group A — static AST lint over module-scope ``_*_RE`` / ``_*_PATTERN`` constants
    Walks ``src/lawvm/`` via ``ast.parse``.  For each module-scope pattern
    constant, runs ``lawvm_regex_risks()``.  Fails if any non-allowlisted file
    has violations.  Warns (does not fail) for allowlisted files.

    This gate catches regressions introduced in NEW code.  Pre-existing
    violations are allowlisted with a reason; the allowlist is the technical
    debt ledger for Sensor H batch 6+.

    Conservative false-positive note: ``adjacent_repeat_risks()`` treats
    CATEGORY escapes (``\\d``, ``\\w``, ``\\s``) as unknown first-char sets and
    flags them as potentially overlapping.  This is correct behaviour for
    patterns like ``\\d+\\d+`` (actual risk) but produces false positives for
    patterns like ``\\d+[a-z]*`` (no real risk).  ALL currently-flagged files
    are in the allowlist; the gate blocks only NEW violations.

Group B — adversarial timing for classifiers fixed in A8, A10, A14
    Re-verifies that the five key classifier functions introduced or fixed by
    Actuators 8, 10, and 14 remain fast on worst-case inputs.  Uses
    ``time.perf_counter()`` with a generous 100 ms ceiling — these should now
    be sub-millisecond; the ceiling only catches order-of-magnitude regressions.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from lawvm.core.regex_safety import lawvm_regex_risks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"


def _scan_patterns(src_root: Path) -> dict[str, list[tuple[int, str, str, list[str]]]]:
    """AST-scan all _*_RE / _*_PATTERN module-scope constants.

    Returns: {rel_path: [(lineno, name, pattern_str[:120], risks), ...]}
    """
    result: dict[str, list[tuple[int, str, str, list[str]]]] = {}

    for pyfile in sorted(src_root.rglob("*.py")):
        if pyfile.name == "regex_safety.py":
            continue  # don't lint the linter itself
        try:
            source = pyfile.read_text()
            tree = ast.parse(source, filename=str(pyfile))
        except Exception:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if not (
                    name.startswith("_")
                    and (name.endswith("_RE") or name.endswith("_PATTERN"))
                ):
                    continue
                val = node.value
                pat_str: str | None = None
                # re.compile(pattern_str, ...)
                if (
                    isinstance(val, ast.Call)
                    and isinstance(val.func, ast.Attribute)
                    and val.func.attr == "compile"
                    and val.args
                ):
                    arg0 = val.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(
                        arg0.value, str
                    ):
                        pat_str = arg0.value
                # bare string constant (pattern assigned directly)
                elif isinstance(val, ast.Constant) and isinstance(val.value, str):
                    pat_str = val.value

                if pat_str is None:
                    continue

                try:
                    risks = lawvm_regex_risks(pat_str)
                except Exception:
                    continue

                if risks:
                    rel = str(pyfile.relative_to(_REPO_ROOT))
                    if rel not in result:
                        result[rel] = []
                    result[rel].append(
                        (node.lineno, name, pat_str[:120], risks)
                    )

    return result


# ---------------------------------------------------------------------------
# Group A — allowlist
#
# Files in this set have pre-existing violations as of 2026-05-29 (Sensor H
# batch 5), updated by A18 (2026-05-29) CATEGORY first-char analysis.
#
# A18 eliminated 21 files (77 pattern entries) that were pure CATEGORY
# false-positives — \\d+[a-z]? / \\d+\\s+ shapes that are provably disjoint
# now that first_chars() resolves CATEGORY_DIGIT, CATEGORY_WORD,
# CATEGORY_SPACE to concrete ASCII frozensets.  Remaining 48 files have
# genuine adjacent-repeat risks (.{0,N}?/.+ adjacent pairs, nested
# quantifiers, or mixed CATEGORY+bounded patterns) awaiting batch 6 cleanup.
#
# Rules:
#   - Removing a file from this set = you have fixed all its violations.
#   - Adding a file to this set requires a reason comment.
#   - New files not in this set MUST be clean on commit.
# ---------------------------------------------------------------------------

_KNOWN_UNFIXED: dict[str, str] = {
    # estonia
    "src/lawvm/estonia/compare.py": (
        "Pre-existing baseline: adjacent-repeat patterns in identifier/footnote "
        "normalisation regexes (nested quantifiers + date digit-group adjacency). "
        "Sensor H batch 6."
    ),
    "src/lawvm/estonia/grafter.py": (
        "Pre-existing baseline: _EE_RT_INLINE_CHANGE_NOTE_RE nested+adjacent "
        "quantifiers. Sensor H batch 6."
    ),
    # finland
    "src/lawvm/finland/address_parse.py": (
        "Pre-existing baseline: complex nested quantifiers in subsection address "
        "parsing patterns. Sensor H batch 6."
    ),
    "src/lawvm/finland/citation_routing.py": (
        "Pre-existing baseline: _FI_META_REPEAL_RE — bounded .{0,400}? with "
        "keyword guards; adjacent repeats at boundary positions flagged by AST "
        "lint even though pattern was fixed by Actuator 10. Sensor H batch 6."
    ),
    "src/lawvm/finland/consolidated_artifacts.py": (
        "Pre-existing baseline: _CONSOLIDATED_LOCATOR_RE has nested quantifiers "
        "and adjacent .{0,N} repeats. Sensor H batch 6."
    ),
    "src/lawvm/finland/corrigendum.py": (
        "Pre-existing baseline: multiple patterns in corrigendum parse regexes "
        "(nested quantifiers; CATEGORY false-positives resolved by A18). "
        "Sensor H batch 6."
    ),
    "src/lawvm/finland/cross_refs.py": (
        "Pre-existing baseline: _REF_PATTERN has nested quantifiers. "
        "Sensor H batch 6."
    ),
    "src/lawvm/finland/frontend_compile.py": (
        "Pre-existing baseline: address/label patterns with adjacent repeats "
        "(CATEGORY false-positives partially resolved by A18; genuine nested "
        "quantifiers remain). Sensor H batch 6."
    ),
    "src/lawvm/finland/frontend_observations.py": (
        "Pre-existing baseline: _SAME_LABEL_MOVE_CLAUSE_RE — complex nested "
        "quantifiers. Sensor H batch 6."
    ),
    "src/lawvm/finland/inline_repeal_stub.py": (
        "Pre-existing baseline: _PARA_KUMOTTU_RE has nested quantifiers. "
        "Sensor H batch 6."
    ),
    "src/lawvm/finland/johtolause/clause_patterns.py": (
        "Pre-existing baseline: _SINGLE_ROW_{REPLACE,REPEAL}_RE have complex "
        "adjacent quantifier patterns (partially fixed by A10; lint still flags "
        "bounded variants). Sensor H batch 6."
    ),
    "src/lawvm/finland/johtolause/lexicon.py": (
        "Pre-existing baseline: _CITE_RE nested quantifiers (CATEGORY false-"
        "positives resolved by A18). Sensor H batch 6."
    ),
    "src/lawvm/finland/normalize.py": (
        "Pre-existing baseline: _SECTION_TOKEN_RE nested quantifiers. "
        "Sensor H batch 6."
    ),
    "src/lawvm/finland/profile/normalize.py": (
        "Pre-existing baseline: embedded-number patterns with adjacent repeats "
        "(CATEGORY false-positives partially resolved by A18; bounded .{N} "
        "adjacent pairs remain). Sensor H batch 6."
    ),
    "src/lawvm/finland/scope.py": (
        "Pre-existing baseline: _SAME_LABEL_MOVE_CLAUSE_RE and "
        "_SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE nested+adjacent quantifiers. "
        "Sensor H batch 6."
    ),
    "src/lawvm/finland/source_normalize.py": (
        "Pre-existing baseline: _NUM_IN_INTRO_CAPTURE_RE adjacent repeat "
        "(CATEGORY false-positives on _ITEM_NUM_RE/_ARABIC_LABEL_RE resolved "
        "by A18). Sensor H batch 6."
    ),
    "src/lawvm/finland/temporal_lowering.py": (
        "Pre-existing baseline: date/commencement patterns with adjacent bounded "
        "repeats (CATEGORY digit-group false-positives partially resolved by A18; "
        "\\s+/\\d+ adjacency in BRANCH context remains). Sensor H batch 6."
    ),
    # new zealand
    "src/lawvm/new_zealand/dependencies.py": (
        "Pre-existing baseline: _ACT_CITATION_RE nested quantifiers. "
        "Sensor H batch 6."
    ),
    "src/lawvm/new_zealand/operation_surface.py": (
        "Pre-existing baseline: section/schedule target patterns with nested "
        "quantifiers. Sensor H batch 6."
    ),
    # norway
    "src/lawvm/norway/grafter.py": (
        "Pre-existing baseline: filename/amendment patterns with adjacent repeats "
        "(CATEGORY+range combos partially resolved by A18; bounded pairs remain). "
        "Sensor H batch 6."
    ),
    "src/lawvm/norway/statsrad.py": (
        "Pre-existing baseline: adjacent repeat patterns in statsrad regexes. "
        "Sensor H batch 6."
    ),
    "src/lawvm/norway/verify.py": (
        "Pre-existing baseline: verify patterns with adjacent quantifiers. "
        "Sensor H batch 6."
    ),
    # open_law
    "src/lawvm/open_law/maryland.py": (
        "Pre-existing baseline: adjacent .+/.* patterns. Sensor H batch 6."
    ),
    # semantic
    "src/lawvm/semantic/projection.py": (
        "Pre-existing baseline: adjacent repeat (CATEGORY false-positive "
        "partially resolved by A18; genuine adjacent-repeat remains). "
        "Sensor H batch 6."
    ),
    # sweden
    "src/lawvm/sweden/fetch.py": (
        "Pre-existing baseline: fetch patterns with adjacent .{0,N} repeats. "
        "Sensor H batch 6."
    ),
    # sweden/grafter.py — fixed by A19 (2026-05-29)
    # tools
    "src/lawvm/tools/divergence_heuristics.py": (
        "Pre-existing baseline: _SECTION_KEY_RE nested quantifiers (lint flags "
        "optional prefix group). Sensor H batch 6."
    ),
    "src/lawvm/tools/editorial_hygiene.py": (
        "Pre-existing baseline: adjacent repeat (CATEGORY false-positive "
        "partially resolved by A18; genuine adjacent-repeat remains). "
        "Sensor H batch 6."
    ),
    "src/lawvm/tools/evidence.py": (
        "Pre-existing baseline: adjacent repeats. Sensor H batch 6."
    ),
    "src/lawvm/tools/section_keys.py": (
        "Pre-existing baseline: adjacent repeats (CATEGORY false-positives "
        "partially resolved by A18). Sensor H batch 6."
    ),
    "src/lawvm/tools/verify_chain.py": (
        "Pre-existing baseline: adjacent quantifiers (CATEGORY false-positives "
        "partially resolved by A18; bounded adjacent pairs remain). "
        "Sensor H batch 6."
    ),
    # uk_legislation
    "src/lawvm/uk_legislation/effect_lowering_tail.py": (
        "Pre-existing baseline: bounded .{0,N}? adjacent-repeat (not CATEGORY; "
        "genuine bounded-pair risk). Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/nlp_parser.py": (
        "Sensor H batch 3: NLP parser regexes slated for replacement by "
        "surface pipeline. Not yet fixed."
    ),
    "src/lawvm/uk_legislation/replay_table_apply.py": (
        "Pre-existing baseline: adjacent .{0,N} repeats. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/replay_text_apply.py": (
        "Pre-existing baseline: adjacent quantifiers. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_adjudication.py": (
        "Pre-existing baseline: residual flags after A8/A14 fixes — bounded "
        "patterns still trigger adjacent-repeat check. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_amendment_program_fragments.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in amendment fragment "
        "patterns. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_child_tail_rewrites.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in child-tail rewrite "
        "patterns. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_definition_context.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in definition context "
        "patterns. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_definition_fragments.py": (
        "Pre-existing baseline: multiple adjacent .{0,N}? repeats in definition "
        "fragment patterns. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_definition_structural_insert.py": (
        "Pre-existing baseline: multiple adjacent .{0,N}? repeats in definition "
        "structural insert patterns. Sensor H batch 6."
    ),
    # source_fragment_context.py — fixed by A19 (2026-05-29)
    # source_parent_payloads.py — fixed by A19 (2026-05-29)
    "src/lawvm/uk_legislation/source_structural_sibling.py": (
        "Pre-existing baseline: adjacent .+ repeat. Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/source_table_entry_paragraph.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in table entry patterns. "
        "Sensor H batch 6."
    ),
    # source_text_reclassifications.py — fixed by A19 (2026-05-29)
    "src/lawvm/uk_legislation/table_selectors.py": (
        "Pre-existing baseline: adjacent quantifier patterns in table selectors. "
        "Sensor H batch 6."
    ),
    "src/lawvm/uk_legislation/table_sources.py": (
        "Pre-existing baseline: bounded adjacent-repeat (not CATEGORY; genuine "
        "bounded-pair risk). Sensor H batch 6."
    ),
}


# ---------------------------------------------------------------------------
# Group A tests
# ---------------------------------------------------------------------------


class TestRegexSanitySelf:
    """Basic sanity checks: the lint must correctly flag known-bad patterns
    and pass known-safe patterns."""

    def test_adjacent_dot_plus_flagged(self) -> None:
        assert lawvm_regex_risks(r".+.+") != []

    def test_adjacent_grouped_dot_plus_flagged(self) -> None:
        assert lawvm_regex_risks(r"(?:.+)(?:.+)") != []

    def test_nested_quantifiers_flagged(self) -> None:
        assert lawvm_regex_risks(r"(a+)+$") != []

    def test_ambiguous_alternation_flagged(self) -> None:
        assert lawvm_regex_risks(r"^(a|aa)+$") != []

    def test_adjacent_dot_plus_with_word_boundary_flagged(self) -> None:
        # \b is zero-width; items 0 and 2 in the flat list still overlap
        assert lawvm_regex_risks(r".+\b.+") != []

    def test_adjacent_same_class_flagged(self) -> None:
        assert lawvm_regex_risks(r"[a-z]+[a-z]+") != []

    # Known-safe patterns must not flag
    def test_simple_anchored_safe(self) -> None:
        assert lawvm_regex_risks(r"^[a-z]+$") == []

    def test_disjoint_classes_safe(self) -> None:
        assert lawvm_regex_risks(r"[a-z]+[0-9]+") == []

    def test_simple_digit_safe(self) -> None:
        assert lawvm_regex_risks(r"\d+") == []


class TestCategoryFirstCharSets:
    """A18: CATEGORY escapes resolved to concrete ASCII char-sets in first_chars().

    These tests verify the reduction of false positives introduced in A18
    (2026-05-29).  The canonical LawVM label shape ``\\d+[a-z]?`` was the
    single most common false-positive before this fix.
    """

    # --- patterns that are genuinely disjoint — must NOT flag ---

    def test_digit_then_lower_disjoint(self) -> None:
        """\\d and [a-z] have no common ASCII code-points."""
        assert lawvm_regex_risks(r"\d+[a-z]+") == []

    def test_digit_then_optional_lower_disjoint(self) -> None:
        """Canonical LawVM label suffix shape \\d+[a-z]? must be clean."""
        assert lawvm_regex_risks(r"\d+[a-z]?") == []

    def test_digit_then_optional_any_case_letter_disjoint(self) -> None:
        """\\d+[a-zA-Z]? — digits and ASCII letters are disjoint."""
        assert lawvm_regex_risks(r"\d+[a-zA-Z]?") == []

    def test_digit_then_space_disjoint(self) -> None:
        """\\d and \\s have no common ASCII code-points."""
        assert lawvm_regex_risks(r"\d+\s+") == []

    def test_space_then_lower_disjoint(self) -> None:
        """\\s and [a-z] are disjoint."""
        assert lawvm_regex_risks(r"\s+[a-z]+") == []

    def test_word_then_space_disjoint(self) -> None:
        """\\w and \\s are disjoint (no char is both word and whitespace)."""
        assert lawvm_regex_risks(r"\w+\s+") == []

    def test_space_then_digit_disjoint(self) -> None:
        """\\s and \\d are disjoint."""
        assert lawvm_regex_risks(r"\s+\d+") == []

    def test_anchored_digit_suffix_clean(self) -> None:
        """Common legal label pattern: anchored, disjoint suffix."""
        assert lawvm_regex_risks(r"^(\d+)([a-z]*)$") == []

    # --- patterns with genuine overlap — MUST flag ---

    def test_word_then_digit_overlaps(self) -> None:
        """\\w includes digits, so \\w+ and \\d+ share first chars."""
        assert lawvm_regex_risks(r"\w+\d+") != []

    def test_word_then_word_overlaps(self) -> None:
        """Identical CATEGORY: \\w+\\w+ is a genuine adjacent-repeat risk."""
        assert lawvm_regex_risks(r"\w+\w+") != []

    def test_digit_then_digit_overlaps(self) -> None:
        """Identical CATEGORY: \\d+\\d+ is a genuine adjacent-repeat risk."""
        assert lawvm_regex_risks(r"\d+\d+") != []

    def test_space_then_space_overlaps(self) -> None:
        """Identical CATEGORY: \\s+\\s+ is a genuine adjacent-repeat risk."""
        assert lawvm_regex_risks(r"\s+\s+") != []

    # --- existing checks must remain unaffected ---

    def test_dot_plus_still_flagged(self) -> None:
        """A18 must not regress the .+.+ detector."""
        assert lawvm_regex_risks(r".+.+") != []

    def test_nested_quantifiers_still_flagged(self) -> None:
        """A18 must not regress the nested-quantifier detector."""
        assert lawvm_regex_risks(r"(a+)+$") != []


class TestAstLintGate:
    """Scan all module-scope _*_RE / _*_PATTERN constants in src/lawvm/.

    Passes if every non-allowlisted file is clean.
    Warns (prints) about allowlisted files but does not fail.
    """

    def test_no_new_violations(self) -> None:
        violations = _scan_patterns(_SRC_ROOT)

        allowlisted: dict[str, list[tuple[int, str, str, list[str]]]] = {}
        new_violations: dict[str, list[tuple[int, str, str, list[str]]]] = {}

        for rel, viols in violations.items():
            if rel in _KNOWN_UNFIXED:
                allowlisted[rel] = viols
            else:
                new_violations[rel] = viols

        # Report allowlisted warnings (informational, not failure)
        if allowlisted:
            summary_lines = [
                f"\n[REGEX GATE] Allowlisted (pre-existing) violations — "
                f"{len(allowlisted)} file(s), clean up in Sensor H batch 6+:\n"
            ]
            for rel, viols in sorted(allowlisted.items()):
                summary_lines.append(f"  {rel} ({len(viols)} pattern(s))")
            print("\n".join(summary_lines))

        # Fail on any new violations
        if new_violations:
            lines = [
                f"\n[REGEX GATE] NEW violations found — {len(new_violations)} file(s) "
                f"not in allowlist:\n"
            ]
            for rel, viols in sorted(new_violations.items()):
                lines.append(f"\n  {rel}:")
                for lineno, name, pat, risks in viols:
                    lines.append(f"    L{lineno} {name!r}: {risks}")
                    lines.append(f"      pattern: {pat!r}")
            lines.append(
                "\nTo fix: bound every quantifier in long-text patterns "
                "(see AGENTS.md §1.11).\n"
                "To defer: add the file to _KNOWN_UNFIXED in this test with a reason."
            )
            pytest.fail("\n".join(lines))

    def test_allowlist_has_no_unknown_entries(self) -> None:
        """Every entry in _KNOWN_UNFIXED must correspond to a real file."""
        for rel in _KNOWN_UNFIXED:
            path = _REPO_ROOT / rel
            assert path.exists(), (
                f"_KNOWN_UNFIXED entry {rel!r} does not correspond to a real file. "
                "Remove it from the allowlist."
            )

    def test_patterns_discovered_count(self) -> None:
        """Sanity: at least 300 module-scope patterns should be found."""
        violations = _scan_patterns(_SRC_ROOT)
        # The scan itself doesn't return a total count, so count via a separate walk
        total = 0
        for pyfile in _SRC_ROOT.rglob("*.py"):
            if pyfile.name == "regex_safety.py":
                continue
            try:
                source = pyfile.read_text()
                tree = ast.parse(source, filename=str(pyfile))
            except Exception:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    name = target.id
                    if name.startswith("_") and (
                        name.endswith("_RE") or name.endswith("_PATTERN")
                    ):
                        val = node.value
                        if isinstance(val, ast.Call) and isinstance(
                            val.func, ast.Attribute
                        ) and val.func.attr == "compile":
                            total += 1
                        elif isinstance(val, ast.Constant) and isinstance(
                            val.value, str
                        ):
                            total += 1
        assert total >= 300, (
            f"Only {total} module-scope patterns found — "
            "scan may be broken or codebase shrank unexpectedly."
        )
        _ = violations  # consumed above for allowlist check


# ---------------------------------------------------------------------------
# Group B — adversarial timing tests
#
# These re-verify the five key classifier sites fixed in A8 / A10 / A14.
# Each must complete in < 100 ms on a worst-case adversarial input.
# ---------------------------------------------------------------------------

_CEILING_MS = 100


class TestAdversarialTimingA8:
    """Actuator 8 — UK source_adjudication._looks_like_referent_qualified_text_substitution."""

    def test_adversarial_is_fast(self) -> None:
        from lawvm.uk_legislation.source_adjudication import (
            _looks_like_referent_qualified_text_substitution,
        )

        text = (
            "for "
            + "x" * 5000
            + " substitute something where it refers to end but no quote chars"
        )
        t0 = time.perf_counter()
        result = _looks_like_referent_qualified_text_substitution(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is False
        assert elapsed_ms < _CEILING_MS, (
            f"A8 adversarial: {elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); "
            "catastrophic backtracking may have regressed"
        )


class TestAdversarialTimingA10:
    """Actuator 10 — FI citation_routing._looks_like_fi_meta_repeal."""

    def test_adversarial_is_fast(self) -> None:
        from lawvm.finland.citation_routing import _looks_like_fi_meta_repeal

        text = (
            "kumotaan " + "x" * 5000 + " muuttamisesta " + "y" * 5000
            + " lain ( 99/2010 ) 1 §"
        )
        assert "annetun" not in text.lower()
        t0 = time.perf_counter()
        result = _looks_like_fi_meta_repeal(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is False
        assert elapsed_ms < _CEILING_MS, (
            f"A10 adversarial (no-annetun guard): {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); guard may have regressed"
        )


class TestAdversarialTimingA14CarriedTail:
    """Actuator 14 — UK source_adjudication._looks_like_source_carried_structured_tail_substitution."""

    def test_adversarial_is_fast(self) -> None:
        from lawvm.uk_legislation.source_adjudication import (
            _looks_like_source_carried_structured_tail_substitution,
        )

        text = "for the words from " + "a" * 5000 + " substitute " + "b" * 5000
        assert "to the end" not in text
        t0 = time.perf_counter()
        result = _looks_like_source_carried_structured_tail_substitution(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is False
        assert elapsed_ms < _CEILING_MS, (
            f"A14 carried-tail adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); fast-guard may have regressed"
        )


class TestAdversarialTimingA14ScheduleTable:
    """Actuator 14 — UK source_adjudication._looks_like_repeal_schedule_table_source."""

    def test_adversarial_is_fast(self) -> None:
        from lawvm.uk_legislation.source_adjudication import (
            _looks_like_repeal_schedule_table_source,
        )

        text = (
            "Short title and chapter " + "x" * 5000
            + " reference enactment but no terminal word present here " + "y" * 5000
        )
        assert "extent" not in text.lower()
        t0 = time.perf_counter()
        result = _looks_like_repeal_schedule_table_source(
            extracted_tag="Schedule",
            effect_type="repeal",
            text=text,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is False
        assert elapsed_ms < _CEILING_MS, (
            f"A14 schedule-table adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); fast-guard may have regressed"
        )


class TestAdversarialTimingA14UnloweredOverlap:
    """Actuator 14 — UK effect_lowering_tail._unlowered_overlap_source_shape_classification.

    This tests the compiled constants _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE and
    _AMENDMENT_TABLE_PAYLOAD_RE which are used by
    _unlowered_overlap_source_shape_classification (the function itself is
    tested via those constants since it is not exported).
    """

    def test_scoped_occurrence_adversarial_is_fast(self) -> None:
        from lawvm.uk_legislation.effect_lowering_tail import (
            _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE,
        )

        text = (
            "where it occurs without " + "a" * 3000
            + " substitute " + "b" * 3000
            + " but this is something else no apply at end"
        )
        assert "but this does not apply" not in text
        t0 = time.perf_counter()
        result = _SCOPED_OCCURRENCE_WITH_EXCLUSIONS_RE.search(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is None
        assert elapsed_ms < _CEILING_MS, (
            f"A14 scoped-occurrence adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); bounded regex may have regressed "
            f"(was O(N^3) before fix)"
        )

    def test_amendment_table_adversarial_is_fast(self) -> None:
        from lawvm.uk_legislation.effect_lowering_tail import (
            _AMENDMENT_TABLE_PAYLOAD_RE,
        )

        text = (
            "part 1 amendments of the act " + "x" * 4000
            + " column 1 provision " + "y" * 4000
            + " no second column here"
        )
        assert "column 2" not in text
        t0 = time.perf_counter()
        result = _AMENDMENT_TABLE_PAYLOAD_RE.match(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result is None
        assert elapsed_ms < _CEILING_MS, (
            f"A14 amendment-table adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); bounded regex may have regressed"
        )
