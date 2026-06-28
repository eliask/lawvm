"""Regex safety lint + adversarial perf gate.

Two test groups:

Group A — static AST lint over module-scope ``_*_RE`` / ``_*_PATTERN`` constants
    Walks ``src/lawvm/`` via ``ast.parse``.  For each module-scope pattern
    constant, runs ``lawvm_regex_risks()``.  Fails if any non-allowlisted file
    has violations.  Warns (does not fail) for allowlisted files.

    This gate catches regressions introduced in NEW code.  Pre-existing
    violations are allowlisted with a reason; the allowlist is the technical
    debt ledger for incremental cleanup.

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
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from lawvm.core.regex_safety import lawvm_regex_risks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"
_MODULE_PATTERN_ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*_[A-Za-z0-9_]*(?:_RE|_PATTERN)\s*="
)


@dataclass(frozen=True, slots=True)
class RegexPatternScan:
    """AST scan result for module-scope regex constants."""

    violations: dict[str, list[tuple[int, str, str, list[str]]]]
    total_patterns: int


@lru_cache(maxsize=None)
def _scan_patterns(src_root: Path) -> RegexPatternScan:
    """AST-scan all _*_RE / _*_PATTERN module-scope constants.

    Returns violations plus total discovered pattern count.
    """
    result: dict[str, list[tuple[int, str, str, list[str]]]] = {}
    total_patterns = 0

    for pyfile in sorted(src_root.rglob("*.py")):
        if pyfile.name == "regex_safety.py":
            continue  # don't lint the linter itself
        try:
            source = pyfile.read_text()
            if _MODULE_PATTERN_ASSIGNMENT_RE.search(source) is None:
                continue
            tree = ast.parse(source, filename=str(pyfile))
        except Exception:
            continue

        for node in tree.body:
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

                total_patterns += 1
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

    return RegexPatternScan(violations=result, total_patterns=total_patterns)


# ---------------------------------------------------------------------------
# Group A — allowlist
#
# Files in this set have pre-existing violations as of 2026-05-29,
# updated by A18 (2026-05-29) CATEGORY first-char analysis.
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
        "Pre-existing baseline."
    ),
    "src/lawvm/estonia/grafter.py": (
        "Pre-existing baseline: _EE_RT_INLINE_CHANGE_NOTE_RE nested+adjacent "
        "quantifiers. Pre-existing baseline."
    ),
    # finland
    "src/lawvm/finland/citation_routing.py": (
        "Pre-existing baseline: _FI_META_REPEAL_RE — bounded .{0,400}? with "
        "keyword guards; adjacent repeats at boundary positions flagged by AST "
        "lint even though pattern was already fixed. Pre-existing baseline."
    ),
    "src/lawvm/finland/johto_scope_mentions.py": (
        "Section/sub-ref structural parsing is DEMOTED onto the shared grammar "
        "driver (scan_legal_addresses) + a bounded label-run anchor; the section "
        "list/range regexes are gone. Residual flag is the two CHAPTER-MOVE "
        "recognizers (_MOVE_DESTINATION_CHAPTER_RE / _MOVE_SECTION_TO_CHAPTER_RE): "
        "verb-anchored 'siirretään … N lukuun' move-pairing whose source/dest "
        "semantics the address recognizer does not model. Their gaps are BOUNDED "
        "lazy [^§\\n]{0,200}? / [^§\\n]{0,120}? between literal anchors "
        "(siirretään / § / lukuun) with possessive label atoms, so per-anchor "
        "work is capped and total is linear; the lint flags the bounded-lazy gap "
        "adjacent to the label group, a benign-linear false positive that only "
        "runs on short johtolause clauses."
    ),
    "src/lawvm/finland/johtolause/affected_statute.py": (
        "_AFFECTED_HEAD_RE date/title/citation quantifiers bounded and possessive; "
        "residual flag is the optional leading date group ((?:...)?) wrapping the "
        "bounded body, intrinsic to the statute-head shape and run only on short "
        "johtolause heads."
    ),
    "src/lawvm/finland/consolidated_artifacts.py": (
        "Pre-existing baseline: _CONSOLIDATED_LOCATOR_RE has nested quantifiers "
        "and adjacent .{0,N} repeats. Pre-existing baseline."
    ),
    "src/lawvm/finland/corrigendum.py": (
        "Pre-existing baseline: multiple patterns in corrigendum parse regexes "
        "(nested quantifiers; CATEGORY false-positives resolved by A18). "
        "Pre-existing baseline."
    ),
    "src/lawvm/finland/frontend_compile.py": (
        "Pre-existing baseline: address/label patterns with adjacent repeats "
        "(CATEGORY false-positives partially resolved by A18; genuine nested "
        "quantifiers remain). Pre-existing baseline."
    ),
    "src/lawvm/finland/frontend_observations.py": (
        "_SAME_LABEL_MOVE_CLAUSE_RE is the grammar-subordinate same-label move "
        "ANCHOR (Q6 demotion): every quantifier is explicitly bounded "
        "(\\s{0,8}, \\d{1,4}, [^§]{0,120}), so the pattern is provably linear and "
        "the adjacent-variable-repeat risks are gone. The residual 'nested "
        "backtracking quantifiers' flag is the benign-linear false positive "
        "(bounded x bounded). Move semantics are modelled by "
        "johtolause/grammar/moves.py; this is observation-only residue for the "
        "plural 'joista … siirretään N lukuun' coordination the clause grammar "
        "still declines."
    ),
    "src/lawvm/finland/he_branch_parser.py": (
        "Pre-existing baseline from Finland proposal-branch parsing: anchored "
        "government-proposal preamble/relative-section recognizers flagged by "
        "the AST lint. Proposal branch support is non-replay authority; clean up "
        "with the next HE parser grammar pass."
    ),
    "src/lawvm/finland/inline_repeal_stub.py": (
        "Pre-existing baseline: _PARA_KUMOTTU_RE has nested quantifiers. "
        "Pre-existing baseline."
    ),
    "src/lawvm/finland/johtolause_supplements.py": (
        "_SPARSE_OSALTA_ROW_OMISSION_RE has two BOUNDED lazy gaps "
        "(.{0,500}? / .{0,300}?) between literal anchors (muut… / a section + § / "
        "oikeusaputoimiston … osalta seuraavasti). Because both gaps are bounded, "
        "the per-anchor work is capped and the total is linear — verified worst-case "
        "48 ms on a 64k string packed with thousands of muut/§/oikeusaputoimiston "
        "near-misses, no catastrophic blowup. The static lint flags the adjacent "
        "bounded-lazy .{0,N}? pair as overlapping; benign-linear false positive."
    ),
    "src/lawvm/finland/johtolause/clause_patterns.py": (
        "Pre-existing baseline: _SINGLE_ROW_{REPLACE,REPEAL}_RE have complex "
        "adjacent quantifier patterns (partially fixed by A10; lint still flags "
        "bounded variants). Pre-existing baseline."
    ),
    "src/lawvm/finland/johtolause/lexicon.py": (
        "Pre-existing baseline: _CITE_RE nested quantifiers (CATEGORY false-"
        "positives resolved by A18). Pre-existing baseline."
    ),
    "src/lawvm/finland/kumotaan.py": (
        "_WHOLE_SECTION_SITE_RE is the section-run site anchor introduced by the "
        "Q2 regex->grammar demotion. Every quantifier is explicitly bounded "
        "(\\d{1,4}, \\s{0,8}, run coordination {0,64}), so the pattern is provably "
        "linear; the residual 'nested backtracking quantifiers' flag is the "
        "benign-linear false positive (bounded x bounded). Structure is parsed by "
        "the grammar (parse_body_provision_tail), not this anchor."
    ),
    "src/lawvm/finland/metadata.py": (
        "Pre-existing baseline: _LEADING_SECTION_MARKER_AFTER_CITATION_RE has "
        "nullable-separated same-class repeats (\\s* around an optional label) "
        "newly detected by the 2026-05-30 soundness hardening (nullable-separator "
        "look-ahead). Anchored short label pattern; low practical risk. Batch 6."
    ),
    "src/lawvm/finland/normalize.py": (
        "Pre-existing baseline: multiple fallback recognizers in this file "
        "(_INSERT_*_FALLBACK_RE family, _INSERT_ROOT/CHAPTER_SECTION_FALLBACK_RE, "
        "and _SECTION_TOKEN_RE) carry adjacent-repeat and nested-quantifier flags "
        "from the lookahead alternations and ``[a-z]?``/``\\d+`` nullable-separator "
        "shapes; these are the RETAINED rank-3 load-bearing fallback residual "
        "(census-proven; see normalize_fallback_heuristic_census).  Per AGENTS.md "
        "§2.4 the body captures of _INSERT_SUBSECTION_FALLBACK_RE and "
        "_INSERT_ITEM_FALLBACK_RE (and their inline duplicate at the "
        "_extract_insert_subsection_ops_fallback call site, plus the §2.4 inline "
        "``sellaisena kuin`` predicate converted to ``re.match`` start-anchor and "
        "the ``muutetaan`` tail recognizer) were bounded to ``.{0,400}?`` "
        "(2026-06-27) to cap the lazy tail-walk on anchor-less clauses — the "
        "residual flags come from the bounded lookahead (?:...)?/\\d+/[a-z]? "
        "adjacent groups, not the bounded body capture."
    ),
    "src/lawvm/finland/profile/normalize.py": (
        "Pre-existing baseline: embedded-number patterns with adjacent repeats "
        "(CATEGORY false-positives partially resolved by A18; bounded .{N} "
        "adjacent pairs remain). Pre-existing baseline."
    ),
    "src/lawvm/finland/scope.py": (
        "_SAME_LABEL_MOVE_CLAUSE_RE and _SINGULAR_SAME_LABEL_MOVE_CLAUSE_RE are "
        "the grammar-subordinate same-label move ANCHORs (Q6 demotion): every "
        "quantifier is explicitly bounded (\\s{0,8}, \\d{1,4}, [^§]{0,120}), so "
        "both are provably linear and the adjacent-variable-repeat risks are "
        "gone. The residual 'nested backtracking quantifiers' flag is the "
        "benign-linear false positive (bounded x bounded). The move carrier "
        "(lo.move_clause_target_unit_kind, set by johtolause/grammar/moves.py) "
        "is the PRIMARY signal in strip_unjustified_chapter_scope_*; these "
        "anchors are the residue fallback for the plural 'joista … siirretään N "
        "lukuun' coordination the clause grammar still declines."
    ),
    "src/lawvm/finland/section_resolver.py": (
        "Pre-existing baseline: short EID version-tail helper flagged by nested "
        "quantifier lint. Section resolver identity normalization should be "
        "cleaned with the next locator/resolver pass."
    ),
    "src/lawvm/finland/section_text_extractor.py": (
        "Pre-existing baseline: section EID recognizer flagged by nested "
        "quantifier lint. Bounded section-text extractor helper; clean up with "
        "the next locator/resolver pass."
    ),
    "src/lawvm/finland/source_normalize.py": (
        "Pre-existing baseline: _NUM_IN_INTRO_CAPTURE_RE adjacent repeat "
        "(CATEGORY false-positives on _ITEM_NUM_RE/_ARABIC_LABEL_RE resolved "
        "by A18). Pre-existing baseline."
    ),
    "src/lawvm/finland/temporal_lowering.py": (
        "Pre-existing baseline: date/commencement patterns with adjacent bounded "
        "repeats (CATEGORY digit-group false-positives partially resolved by A18; "
        "\\s+/\\d+ adjacency in BRANCH context remains). Pre-existing baseline."
    ),
    # new zealand
    "src/lawvm/new_zealand/dependencies.py": (
        "Pre-existing baseline: _ACT_CITATION_RE nested quantifiers. "
        "Pre-existing baseline."
    ),
    "src/lawvm/new_zealand/operation_surface.py": (
        "Pre-existing baseline: section/schedule target patterns with nested "
        "quantifiers. Pre-existing baseline."
    ),
    "src/lawvm/new_zealand/dry_run.py": (
        "_INSTRUCTION_TARGET_SECTION_RE is the section-reference recognizer shape "
        "(\\d+[A-Za-z]* followed by (?:\\s*\\(label\\))* bracketed sub-components). "
        "The inner repetition is delimited by literal parentheses, so each "
        "iteration consumes a (...) group — no unbounded backtracking. Verified "
        "linear on adversarial inputs (<3 ms at 12k chars). Static lint flags the "
        "outer-*/inner-+ nesting; it is a false positive of the same family."
    ),
    "src/lawvm/new_zealand/source_tree.py": (
        "_AMEND_INSTRUCTION_SECTION_RE is the anchored amend-instruction unit-kind "
        "recognizer; the target id group is bounded and the keyword alternation is "
        "literal. Verified linear on adversarial inputs (<2 ms at 12k chars). "
        "Static lint flags the nested quantifier; false positive."
    ),
    # norway
    "src/lawvm/norway/grafter.py": (
        "Pre-existing baseline: filename/amendment patterns with adjacent repeats "
        "(CATEGORY+range combos partially resolved by A18; bounded pairs remain). "
        "Pre-existing baseline."
    ),
    "src/lawvm/norway/statsrad.py": (
        "Pre-existing baseline: adjacent repeat patterns in statsrad regexes. "
        "Pre-existing baseline."
    ),
    "src/lawvm/norway/verify.py": (
        "Pre-existing baseline: verify patterns with adjacent quantifiers. "
        "Pre-existing baseline."
    ),
    # open_law
    "src/lawvm/open_law/maryland.py": (
        "Pre-existing baseline: adjacent .+/.* patterns. Pre-existing baseline."
    ),
    # semantic
    "src/lawvm/semantic/projection.py": (
        "Pre-existing baseline: adjacent repeat (CATEGORY false-positive "
        "partially resolved by A18; genuine adjacent-repeat remains). "
        "Pre-existing baseline."
    ),
    # sweden
    "src/lawvm/sweden/fetch.py": (
        "Pre-existing baseline: fetch patterns with adjacent .{0,N} repeats. "
        "Pre-existing baseline."
    ),
    "src/lawvm/sweden/grafter.py": (
        "Pre-existing baseline: _SECTION_RE — \\d+\\s*[a-z]? label with trailing "
        "\\s*§ leaves nullable-separated \\s repeats, newly detected by the "
        "2026-05-30 soundness hardening (A19 bounded the old risk; this is the "
        "stricter nullable-separator class). Anchored; low practical risk. Batch 6."
    ),
    # core
    "src/lawvm/core/selector.py": (
        "Pre-existing baseline: _BARE_SECTION_LABEL_RE — anchored "
        "\\d+\\s*[a-z]?\\s*§ section-label pattern leaves nullable-separated \\s "
        "repeats flagged by the AST lint. Short anchored pattern; low practical "
        "risk. Surfaced after earlier-shard test fixes un-masked this gate; not "
        "modified by the test-rootcause work. Batch 6."
    ),
    # tools
    "src/lawvm/tools/audit.py": (
        "Pre-existing baseline: _HTML_PRESENTATION_RANGE_RE — \\d+\\s*[a-z]? range "
        "anchors leave nullable-separated \\s repeats, newly detected by the "
        "2026-05-30 soundness hardening. Anchored short pattern; low risk. Batch 6."
    ),
    "src/lawvm/tools/hyperlinks.py": (
        "Pre-existing baseline: _STATUTE_RE — anchored ^\\d{4}/\\d+(?:-\\w+)?$ "
        "statute-id pattern flagged for nested backtracking quantifiers. Fully "
        "anchored, bounded; low practical risk. Surfaced after earlier-shard "
        "fixes un-masked this gate (hyperlinks merge 514bd8f5 predates this "
        "branch and is not modified here). Batch 6."
    ),
    "src/lawvm/tools/oracle_check.py": (
        "_SECTION_HEADING_RE (^\\d+\\s*[a-zäöå]?\\s*§\\s*(.+?)(?:\\s{2,}|\\n|$)) is "
        "^-anchored, so .match never restarts at a later position; the lazy (.+?) walks "
        "forward once and is hard-stopped by the \\s{2,}/\\n/$ terminator. Verified "
        "linear — worst-case 2.3 ms on a 60k single-spaced no-terminator input. The "
        "lazy (.+?) legitimately captures whitespace (a heading alpha tail), so the "
        "inter-token \\s* cannot be made possessive without changing the match; the "
        "static adjacent-\\s* flag is a benign-linear false positive under the anchor."
    ),
    "src/lawvm/tools/oracle_text.py": (
        "Pre-existing baseline: _REPEAL_MARKER_RE — anchored Finnish "
        "repeal-marker pattern with bounded .{0,40}? leaves adjacent "
        "nullable-separated repeats flagged by the AST lint. Anchored, bounded; "
        "low risk. Surfaced after earlier-shard fixes un-masked this gate; not "
        "modified here. Batch 6."
    ),
    "src/lawvm/tools/reconcile.py": (
        "Pre-existing baseline: _SECTION_NUM_MARKER_RE — anchored "
        "^\\s*\\d+\\s*[a-zäöå]?\\s*§\\s* section-number pattern leaves "
        "nullable-separated \\s repeats flagged by the AST lint. Short anchored "
        "pattern; low risk. Surfaced after earlier-shard fixes un-masked this "
        "gate; not modified here. Batch 6."
    ),
    "src/lawvm/tools/divergence_heuristics.py": (
        "Pre-existing baseline: _SECTION_KEY_RE nested quantifiers (lint flags "
        "optional prefix group). Pre-existing baseline."
    ),
    "src/lawvm/tools/editorial_hygiene.py": (
        "Pre-existing baseline: adjacent repeat (CATEGORY false-positive "
        "partially resolved by A18; genuine adjacent-repeat remains). "
        "Pre-existing baseline."
    ),
    "src/lawvm/tools/evidence.py": (
        "Pre-existing baseline: adjacent repeats. Pre-existing baseline."
    ),
    "src/lawvm/tools/faults.py": (
        "Pre-existing baseline: _SEC_NUM_RE — \\d+\\s*[a-zäöå]? section label with "
        "trailing \\s*§ leaves nullable-separated \\s repeats, newly detected by "
        "the 2026-05-30 soundness hardening. Short label pattern; low risk. Batch 6."
    ),
    "src/lawvm/tools/section_keys.py": (
        "Pre-existing baseline: adjacent repeats (CATEGORY false-positives "
        "partially resolved by A18). Pre-existing baseline."
    ),
    "src/lawvm/tools/verify_chain.py": (
        "Pre-existing baseline: adjacent quantifiers (CATEGORY false-positives "
        "partially resolved by A18; bounded adjacent pairs remain). "
        "Pre-existing baseline."
    ),
    # uk_legislation
    "src/lawvm/uk_legislation/appropriate_place_claim.py": (
        "_SOURCE_NAMED_ANCHOR_RE is the anchored 'immediately after/before "
        "<unit-kind>' appropriate-place recognizer; the unit-kind alternation is "
        "literal and the trailing label is bounded. Verified linear on adversarial "
        "inputs (~1 ms at 12k chars). Static lint flags the nested quantifier; "
        "false positive."
    ),
    "src/lawvm/uk_legislation/payload_identity.py": (
        "_UK_FOREIGN_PHYSICAL_SOURCE_ID_RE (^p\\d{3,}(?:-.*)?$) is fully anchored "
        "at both ends with a single optional suffix; no overlapping repeats. "
        "Verified <0.1 ms on adversarial inputs. Static lint false positive."
    ),
    "src/lawvm/uk_legislation/effect_lowering_tail.py": (
        "Pre-existing baseline: bounded .{0,N}? adjacent-repeat (not CATEGORY; "
        "genuine bounded-pair risk). Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/effect_text_fragment_lowering.py": (
        "Pre-existing baseline from UK source-fragment lowering: many bounded "
        "drafting-instruction recognizers now flagged by the stricter lint. "
        "Requires a UK grammar/recognizer pass, not ad hoc regex edits."
    ),
    "src/lawvm/uk_legislation/heading_facets.py": (
        "Pre-existing baseline from UK heading-facet source parsing. This family "
        "is grammar-shaped and should be migrated through typed selectors when "
        "next touched."
    ),
    "src/lawvm/uk_legislation/metadata_rewrites.py": (
        "Pre-existing baseline from UK effect-metadata rewrite helpers. Keep "
        "allowlisted until metadata rewrite parsing is replaced with typed "
        "target/action records."
    ),
    "src/lawvm/uk_legislation/nlp_parser.py": (
        "NLP parser regexes slated for replacement by "
        "surface pipeline. Not yet fixed."
    ),
    "src/lawvm/uk_legislation/provision_extractor.py": (
        "Pre-existing baseline from UK provision-source extraction helpers. "
        "Short label/insert recognizers; clean up with the provision extractor "
        "grammar pass."
    ),
    "src/lawvm/uk_legislation/replay_table_apply.py": (
        "Pre-existing baseline: adjacent .{0,N} repeats. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/replay_text_apply.py": (
        "Pre-existing baseline: adjacent quantifiers. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_adjudication.py": (
        "Pre-existing baseline: residual flags after A8/A14 fixes — bounded "
        "patterns still trigger adjacent-repeat check. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_amendment_program_fragments.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in amendment fragment "
        "patterns. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_child_tail_rewrites.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in child-tail rewrite "
        "patterns. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_definition_context.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in definition context "
        "patterns. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_definition_fragments.py": (
        "Pre-existing baseline: multiple adjacent .{0,N}? repeats in definition "
        "fragment patterns. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_definition_structural_insert.py": (
        "Pre-existing baseline: multiple adjacent .{0,N}? repeats in definition "
        "structural insert patterns. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_fragment_context.py": (
        "Pre-existing baseline: grouped anchor/insert child patterns leave "
        "nullable-separated repeats (ordinal/quoted-anchor shapes), newly detected "
        "by the 2026-05-30 soundness hardening (A19 bounded the old risk). "
        "Anchored instruction patterns; low practical risk. Batch 6."
    ),
    "src/lawvm/uk_legislation/schedule_list_selectors.py": (
        "Pre-existing baseline from UK schedule/list selector parsing. This is a "
        "typed-selector migration target; defer broad regex surgery until that "
        "bounded family is active."
    ),
    "src/lawvm/uk_legislation/source_labeled_child_parts.py": (
        "Pre-existing baseline: _ROMAN/_ALPHA_CHILD_LABEL_RE prefix alternation "
        "leaves \\s*(?:and|or)?\\s+ nullable-separated \\s repeats, newly detected "
        "by the 2026-05-30 soundness hardening. Anchored; low risk. Batch 6."
    ),
    "src/lawvm/uk_legislation/source_parent_payloads.py": (
        "Pre-existing baseline: after/at-end insert instruction patterns leave "
        "nullable-separated \\s repeats, newly detected by the 2026-05-30 "
        "soundness hardening (A19 bounded the old risk). Low risk. Batch 6."
    ),
    "src/lawvm/uk_legislation/source_context.py": (
        "Pre-existing baseline from UK source-context normalization. Compound "
        "paragraph/table context patterns are grammar-shaped; clean up with the "
        "source-context recognizer pass."
    ),
    "src/lawvm/uk_legislation/source_structural_sibling.py": (
        "Pre-existing baseline: adjacent .+ repeat. Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_table_entry_paragraph.py": (
        "Pre-existing baseline: adjacent .{0,N}? repeats in table entry patterns. "
        "Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/source_text_reclassifications.py": (
        "Pre-existing baseline: word-omission prefix patterns "
        "(`(?:label)[.)]? `-style optional prefixes) leave nullable-separated "
        "repeats, newly detected by the 2026-05-30 soundness hardening (A19 "
        "bounded the old risk). Anchored instruction patterns; low risk. Batch 6."
    ),
    "src/lawvm/uk_legislation/table_selectors.py": (
        "Pre-existing baseline: adjacent quantifier patterns in table selectors. "
        "Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/table_sources.py": (
        "Pre-existing baseline: bounded adjacent-repeat (not CATEGORY; genuine "
        "bounded-pair risk). Pre-existing baseline."
    ),
    "src/lawvm/uk_legislation/target_parser.py": (
        "Pre-existing baseline from UK target-string parsing. This should be "
        "cleaned by typed target grammar migration, not isolated regex rewrites."
    ),
    "src/lawvm/uk_legislation/uk_grafter.py": (
        "Pre-existing baseline: short clean-number-prefix helper flagged by "
        "nullable lookahead lint. Low practical risk; clean when UK grafter "
        "target normalization is next touched."
    ),
    # us_federal
    "src/lawvm/us_federal/dry_run.py": (
        "_SECTION_CATCHLINE_RE is the anchored USC new-section catchline head "
        "(^\\s*[\"quote]?\\[?\\s*§+\\s*<bounded num>\\.) — same family as "
        "source_tree._SECTION_HEAD_RE: the number group is bounded "
        "([0-9]+[A-Za-z]* with a single optional dashed suffix), the whole "
        "pattern is ^-anchored and only matches a short catchline prefix. The AST "
        "lint flags the adjacent optional-\\s* runs (quote/bracket leading "
        "whitespace), but the anchor + bounded num make it linear; static lint "
        "false positive of the same class already allowlisted for source_tree.py."
    ),
    "src/lawvm/us_federal/amendatory.py": (
        "USC amendatory-instruction target recognizers (_PROSE_TARGET_RE, "
        "_HREF_TARGET_RE, _RELATIVE_PROSE_TARGET_RE, _LEADING_SUBUNIT_ANCHOR_RE, "
        "_NEW_SECTION_PAYLOAD_HEAD_RE, _RELATIVE_HEAD_SECTION_RE) are the "
        "section-reference recognizer family: bounded \\d+[A-Za-z]* tokens plus "
        "(?:\\s*\\(label\\))* bracketed sub-paths delimited by literal parentheses, "
        "and literal unit-kind alternations. Verified linear on adversarial inputs "
        "(<10 ms at 12k chars). The static lint flags the outer-*/inner-+ nesting; "
        "these are false positives of that family."
    ),
    "src/lawvm/us_federal/nonpositive.py": (
        "_PAREN_CITE_RE / _HREF_RE are USC parenthetical/href citation "
        "recognizers (bounded section token + literal-paren-delimited sub-path). "
        "Verified linear on adversarial inputs (<2 ms at 12k chars). Static lint "
        "false positive."
    ),
    "src/lawvm/us_federal/source_tree.py": (
        "_SECTION_HEAD_RE is the anchored USC section-head recognizer "
        "(^\\[?\\s*§+\\s*<bounded num>\\.); the number group is bounded with a "
        "single optional dashed suffix. Verified <0.3 ms on adversarial inputs. "
        "Static lint false positive."
    ),
    "src/lawvm/us_federal/sunset.py": (
        "_EXPLICIT_DATE_RE matches 'effective <Month> <day>, <year>' with a "
        "bounded lazy prefix; verified linear on adversarial inputs (~3 ms at 12k "
        "chars). Static lint flags the lazy-prefix/empty-repeat heuristic; false "
        "positive in practice."
    ),
    "src/lawvm/us_federal/usc_witness.py": (
        "_STAT_RE matches a Statutes-at-Large cite ('134 Stat. 2145'); guarded "
        "with a (?<!\\d) lookbehind so an unanchored .search over a long digit "
        "run cannot restart inside the run. Verified linear on adversarial inputs "
        "(<1 ms at 20k chars). Static lint flags the bounded page-suffix repeat; "
        "false positive."
    ),
    # finland references subpackage (Legal Surface Algebra recognizers).
    # These are AKN-href / official-citation recognizers whose number groups are
    # bounded (\\d{1,N}) or literal-delimited (\\d+(?:-\\d+)? around a '-'); the
    # static lint flags the optional (?:...)? sub-groups as nested quantifiers.
    # Each verified linear and sub-millisecond on adversarial inputs.
    "src/lawvm/finland/references/by_name.py": (
        "_DESC_WORD_RE (^[a-zäöå]{2,40}(?:-[a-zäöå]{2,40})?(?::[a-zäöå]{1,20})?$) is "
        "fully anchored at both ends with bounded quantifiers, the optional sub-groups "
        "delimited by literal '-'/':' (no overlap), so a single token can never "
        "backtrack — <0.01 ms on adversarial inputs. _DATE_PHRASE_RE matches a "
        "Finnish enactment date ('14 päivänä heinäkuuta 1898 ') anchored at $; every "
        "group is bounded and the month is a literal alternation — verified <3 ms on "
        "15-20k adversarial inputs. The static lint flags the optional (?:...)? groups "
        "as nested quantifiers; benign-linear false positives."
    ),
    "src/lawvm/finland/references/sections.py": (
        "_CLAUSE_SEP_RE (\\s*(?:,\\s*)?(?:(?:ja|sekä|tai)\\s+)?) is an all-nullable "
        "separator used with .match at a fixed offset; every branch is bounded literal "
        "+ \\s* and it consumes a tiny separator — <0.001 ms on 20-30k adversarial "
        "runs. The static lint flags the optional comma/joiner groups as nested "
        "quantifiers; benign-linear false positive. (_REF_ID_PAREN_RE was rewritten "
        "to a bounded tempered-possessive form and is no longer flagged.)"
    ),
    "src/lawvm/finland/references/cross_refs.py": (
        "_REF_PATTERN and _HE_REF_PATTERN are AKN-href recognizers. The id group "
        "is \\d+(?:-\\d+)? (the optional range half is delimited by a literal '-', "
        "no overlap) and the fragment tail is [^#]*# (the lazy run is hard-stopped "
        "by a literal '#' it cannot itself match). Verified <0.01 ms on adversarial "
        "16k inputs. Static lint flags the optional groups as nested quantifiers; "
        "false positive. Owned by the references lane — not modified here."
    ),
    "src/lawvm/finland/references/eu_reference.py": (
        "_OJ_RE matches an Official Journal cite (EUVL L 123, 1.2.2020, s. 45); "
        "every number group is bounded (\\d{1,6} / \\d{1,2} / \\d{4}) and separated "
        "by literal punctuation, so there is no overlapping backtracking. Verified "
        "<0.1 ms on adversarial inputs. Static lint flags the optional (?:N:o\\s+)? "
        "prefix as a nested quantifier; false positive."
    ),
    "src/lawvm/finland/references/inline_citation_extractor.py": (
        "_EOA_RE/_OKA_RE/_VTV_RE/_OLD_COMMITTEE_RE are ombudsman/chancellor/audit/"
        "committee citation recognizers; all number groups are bounded (\\d{1,8} / "
        "\\d{1,6} / \\d{2,4}) and delimited by literal '/' or whitespace. Verified "
        "<0.2 ms on adversarial inputs. Static lint flags the optional trailing "
        "(?:...)? citation groups as nested quantifiers; false positive."
    ),
    "src/lawvm/finland/references/preparatory_reference_extractor.py": (
        "_HE_REF_HREF_RE is the government-proposal AKN-href recognizer; the id "
        "group is \\d+(?:-\\d+)? with the optional range half delimited by a literal "
        "'-' (no overlap). Verified <0.01 ms on adversarial inputs. Static lint "
        "flags the optional range group as a nested quantifier; false positive."
    ),
    "src/lawvm/finland/references/temporal.py": (
        "_VALIDITY_OPEN_RE matches 'on voimassa( toistaiseksi)?' — a literal phrase "
        "with one optional literal suffix; no repeats can overlap. Verified <0.2 ms "
        "on adversarial inputs. Static lint flags the optional group as a nested "
        "quantifier; false positive."
    ),
    "src/lawvm/finland/references/treaty_article.py": (
        "_SOPS_RE matches a treaty-series cite (SopS 12/2020); the number group is "
        "\\d{1,6} and the year is \\d{2}(?:\\d{2})?, both bounded and '/'-delimited. "
        "Verified <0.2 ms on adversarial inputs. Static lint flags the optional "
        "two-extra-digit year group as a nested quantifier; false positive."
    ),
    # tools
    "src/lawvm/tools/resolution_miss_analysis.py": (
        "_AMEND_TITLE_RE is ^-anchored on a literal statute-class keyword; the lazy "
        "(?P<mod>.+?) is bounded ahead by the mandatory ':n' and a literal verb "
        "alternation, so it cannot backtrack catastrophically. Verified <0.1 ms on "
        "adversarial 8k inputs that omit the colon/verb. Static lint flags the "
        "adjacent .+? / verb-alternation as overlapping; false positive."
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
        scan = _scan_patterns(_SRC_ROOT)
        assert scan.total_patterns >= 300, (
            f"Only {scan.total_patterns} module-scope patterns found — "
            "scan may be broken or codebase shrank unexpectedly."
        )
        violations = scan.violations

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
                f"{len(allowlisted)} file(s), clean up incrementally:\n"
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


# ---------------------------------------------------------------------------
# Group B — adversarial timing tests
#
# These re-verify the five key classifier sites fixed in A8 / A10 / A14.
# Each must complete in < 100 ms on a worst-case adversarial input.
# ---------------------------------------------------------------------------

_CEILING_MS = 100


class TestAdversarialTimingA8:
    """UK source_adjudication._looks_like_referent_qualified_text_substitution."""

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
    """FI citation_routing._looks_like_fi_meta_repeal."""

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
    """UK source_adjudication._looks_like_source_carried_structured_tail_substitution."""

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
    """UK source_adjudication._looks_like_repeal_schedule_table_source."""

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
    """UK effect_lowering_tail._unlowered_overlap_source_shape_classification.

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


# ---------------------------------------------------------------------------
# Group B (extension) — FI normalize.py §2.4 bounding guards
#
# Drives the bounded _INSERT_SUBSECTION_FALLBACK_RE / _INSERT_ITEM_FALLBACK_RE
# (and the inline ``muutetaan`` tail recognizer + ``sellaisena kuin`` predicate)
# with worst-case 5K-char johtolause bodies that contain NONE of the alternation
# anchors (next ``§`` lead-in, ``seuraavasti``, ``lakiin uusi`` or end-of-text
# within the bounded window).  Before the 2026-06-27 ``.*? -> .{0,400}?`` fix
# the lazy body capture walked to end-of-text on every match position; the
# ceiling verifies the bound holds (defense against a future regression that
# removes or expands the quantifier).
# ---------------------------------------------------------------------------


class TestAdversarialTimingFIBound:
    """Bounded quantifier guards for the FI normalize fallback recognizers."""

    def test_insert_subsection_fallback_re_bounded_is_fast(self) -> None:
        from lawvm.finland.normalize import _INSERT_SUBSECTION_FALLBACK_RE

        # Worst case: a §-lead-in followed by 5K chars that contain no alternation
        # anchor (no further ``§``, no ``seuraavasti``, no ``lakiin uusi``, no
        # ``luvun``).  The unbounded ``.*?`` would walk to end-of-text on every
        # match position; the bounded ``.{0,400}?`` walks at most 400 chars per
        # match attempt before bailing on the unable lookahead.
        text = "1 §:ään " + "x" * 5000
        assert "seuraavasti" not in text and "lakiin uusi" not in text
        t0 = time.perf_counter()
        matches = list(_INSERT_SUBSECTION_FALLBACK_RE.finditer(text))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # No ``$`` is reachable within the 400-char bound from any match start,
        # so a body this long with no anchor produces no full matches.
        assert matches == []
        assert elapsed_ms < _CEILING_MS, (
            f"FI bounded insert-subsection adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); .{{0,400}}? bound may have regressed "
            f"back to an unbounded .*?"
        )

    def test_insert_item_fallback_re_bounded_is_fast(self) -> None:
        from lawvm.finland.normalize import _INSERT_ITEM_FALLBACK_RE

        # Same shape but anchored on the item-insert prelude ``§:n N momenttiin``.
        text = "1 §:n 1 momenttiin " + "x" * 5000
        assert "seuraavasti" not in text and "lakiin uusi" not in text
        t0 = time.perf_counter()
        matches = list(_INSERT_ITEM_FALLBACK_RE.finditer(text))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert matches == []
        assert elapsed_ms < _CEILING_MS, (
            f"FI bounded insert-item adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); .{{0,400}}? bound may have regressed "
            f"back to an unbounded .*?"
        )

    def test_insert_subsection_fallback_re_many_restart_positions_is_fast(self) -> None:
        """Many potential ``§:ään`` restart positions followed by a long body.

        ``finditer`` restarts after each match attempt; this stress-tests the
        pattern at every ``§:ään`` occurrence in a long generated string to
        ensure the bounded ``.{0,400}?`` does not blow up combinatorially.
        """
        from lawvm.finland.normalize import _INSERT_SUBSECTION_FALLBACK_RE

        text = ("1 luvun 1 §:ään " * 500) + ("x" * 5000)
        t0 = time.perf_counter()
        matches = list(_INSERT_SUBSECTION_FALLBACK_RE.finditer(text))
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # Each restart position may produce a match whose body extends to the
        # next ``luvun ... §`` lead-in; the count depends on the engine's
        # restart behaviour, but the wall budget is the invariant.
        assert elapsed_ms < _CEILING_MS, (
            f"FI bounded insert-subsection multi-restart adversarial: "
            f"{elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); .{{0,400}}? bound "
            f"may have regressed"
        )
        # Sanity: at least one restart position is captured (the very first
        # ``luvun 1 §:ään`` lead-in starts a non-empty chain of matches).
        assert matches, "expected at least one match in the multi-restart fixture"

    def test_muutetaan_tail_recognizer_bounded_is_fast(self) -> None:
        """Drives the inline ``\\bmuutetaan\\b(.{0,400}?)(?:\\bseuraavasti\\b|$)``
        recognizer at line 899 of normalize.py via the public
        ``_extract_replace_ops_from_muutetaan_tail`` entry point."""
        from lawvm.finland.normalize import _extract_replace_ops_from_muutetaan_tail

        # Worst case: ``muutetaan`` lead-in followed by 5K chars with no
        # ``seuraavasti`` anchor; the ``.{0,400}?`` bound caps the tail walk.
        text = "muutetaan " + "x" * 5000
        assert "seuraavasti" not in text
        t0 = time.perf_counter()
        result = _extract_replace_ops_from_muutetaan_tail(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert result == []
        assert elapsed_ms < _CEILING_MS, (
            f"FI bounded muutetaan-tail adversarial: {elapsed_ms:.1f} ms "
            f"(ceiling {_CEILING_MS} ms); .{{0,400}}? bound may have regressed"
        )

    def test_sellaisena_kuin_predicate_bounded_is_fast(self) -> None:
        """Drives the ``re.match(r"(?:sellaisena|sellaisina)\\s+kuin\\b", lowered)``
        prefix predicate at line 547 of normalize.py via the public
        ``_classify_regex_ignored_span`` entry point.  The original
        ``re.fullmatch(... \\b.*)`` was converted to a start-anchored ``re.match``
        (suffix irrelevant to the classifier) per AGENTS.md §2.4 — this test
        guards against a regression back to ``re.fullmatch(...\\b.*)`` (which
        would walk the full string on each call)."""
        from lawvm.finland.normalize import _classify_regex_ignored_span

        # Worst case for the predicate: the literal ``sellaisena kuin`` prelude
        # followed by 5K chars of qualifier text.  Before bounding, ``.*`` in a
        # ``fullmatch`` walked the entire string; the bound caps at 400 chars.
        text = "sellaisena kuin " + "x" * 5000
        t0 = time.perf_counter()
        classification = _classify_regex_ignored_span(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert classification == "source_version_qualifier"
        assert elapsed_ms < _CEILING_MS, (
            f"FI bounded sellaisena-kuin predicate adversarial: "
            f"{elapsed_ms:.1f} ms (ceiling {_CEILING_MS} ms); .{{0,400}}? bound "
            f"may have regressed"
        )
