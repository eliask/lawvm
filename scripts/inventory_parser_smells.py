"""Generate a bounded parser-smell inventory from source files.

The script is intentionally mechanical: it only reports explicit pattern hits that
are known to indicate fallback- or heuristic-heavy parser behavior.

It also hosts the reusable scan for the *regex ratchet* gate
(``tests/test_regex_ratchet.py``). That gate enforces the pipeline-contract rule
"no NEW post-parse raw-text semantic regex without a waiver/category"
(``notes/LAWVM_PIPELINE_CONTRACT.md`` §4, AGENTS.md §1.12 / §2.4): every file in
``src/lawvm/{core,finland}`` is either pre-cleared by ``CATEGORY_MAP`` (genuine
source-plane / lexer / owning-parser / diagnostic regex) or *scanned*, and every
regex use-site in a scanned (post-parse / legal-state / projection) file must
carry an inline ``# lawvm-regex:`` waiver or be a baselined leak. The committed
baseline (``tests/data/regex_ratchet_baseline.json``) is a monotone ratchet: the
un-waived semantic-plane count may never increase, only fall.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collections.abc import Iterable


DEFAULT_FILES = (
    Path("src/lawvm/finland/grafter.py"),
    Path("src/lawvm/finland/normalize.py"),
    Path("src/lawvm/finland/payload_normalize.py"),
    Path("src/lawvm/finland/johtolause/clause_patterns.py"),
    Path("src/lawvm/uk_legislation/nlp_parser.py"),
    Path("src/lawvm/uk_legislation/source_definition_fragments.py"),
    Path("src/lawvm/uk_legislation/text_selectors.py"),
    Path("src/lawvm/new_zealand/instruction_workqueue.py"),
)

SMELL_MARKERS = {
    "fallback_heuristics": (
        "Fallback-path handling",
        r"(?i)fallback",
    ),
    "clause_modifier_filter": (
        "Clause modifier / marker filtering",
        r"(?i)\b(clause_modifier_blacklist|blacklist)\b",
    ),
    "row_target_normalization": (
        "Row/target normalization fallback",
        r"(?i)\b(continuation_row_subsections|parse_ops_fallback_heuristic|allows_omission_expansion|_sec1_fallback_peg_skip_required|_collapse_intro_list_subsections_inside_section_ir)\b",
    ),
    "regex_structural_heuristic": (
        "Regex-driven structural heuristics",
        r"(?i)\bre\.(match|search|findall|finditer|sub|subn|split|compile)\(",
    ),
    "bounded_wildcard_gap": (
        "Bounded wildcard gap needing semantic span ownership",
        r"\.\{[01],\d+\}\??",
    ),
    "regex_coverage_surface": (
        "Regex recognition coverage / skipped-span ownership surface",
        r"\b(RegexRecognitionCoverage|regex_recognition_coverage|coverage_status|ignored_spans)\b",
    ),
    "text_selector_sentinel": (
        "Stringly TEXT_* selector sentinel",
        r"\bTEXT_[A-Z0-9_]+",
    ),
}


# ===========================================================================
# Regex ratchet (Gate "no new post-parse raw-text semantic regex")
# ===========================================================================
#
# CATEGORY_MAP pre-clears files whose regex use is *legitimate* under the
# pipeline contract: source-plane I/O (locators/spans/bytes/XML), lexical
# tokenization, the ONE owning parser for a construction family (regex is then
# "input to the owning parser"), or pure diagnostic/oracle-comparison rendering.
# A pre-cleared file is exempt from the ratchet by category, NOT by being outside
# the scan. Be CONSERVATIVE: only pre-clear a file you are confident is genuinely
# source/lexer/owning-parser/diagnostic. Anything post-parse semantic / replay /
# legal-state / projection is SCANNED (i.e. absent from CATEGORY_MAP).
#
# The four pre-clear categories and the planes they map to
# (notes/LAWVM_PIPELINE_CONTRACT.md §3):
#   source_plane  -> plane A (bytes, locators, spans, URL/path/XML parsing)
#   lexer         -> plane B (lexical tokenization, label normalization)
#   owning_parser -> plane B (the canonical parser that OWNS a family; regex feeds it)
#   diagnostic    -> plane D/E (audit/oracle-comparison rendering, no replay authority)
#
# Files NOT listed here are SCANNED on the semantic plane and must waive every
# regex use-site (or be baselined). The known leaks named in the Gate-2 spec
# (normalize.parse_ops_fallback_heuristic, kumotaan_replay, replay_products,
# effect_lowering, scope, metadata, ...) are deliberately absent so they are
# baselined and fenced, never hidden.
PRECLEAR_CATEGORIES: frozenset[str] = frozenset(
    {"source_plane", "lexer", "owning_parser", "diagnostic"}
)

CATEGORY_MAP: dict[str, str] = {
    # --- source plane (A): locators / paths / XML / byte-origin parsing ---
    "src/lawvm/finland/corpus.py": "source_plane",
    "src/lawvm/finland/transparent_store.py": "source_plane",
    "src/lawvm/finland/finlex_api.py": "source_plane",
    "src/lawvm/finland/xml_ir.py": "source_plane",
    # eId / locator / AKN-component / version-suffix parsers (no legal prose):
    "src/lawvm/finland/section_text_extractor.py": "source_plane",
    "src/lawvm/finland/section_resolver.py": "source_plane",
    "src/lawvm/finland/provision_ref_locator.py": "source_plane",
    "src/lawvm/finland/interlink_targets.py": "source_plane",
    "src/lawvm/finland/editorial_adjudication.py": "source_plane",
    # PDF/XML corrigendum corrector: parses corrigendum PDF text + AKN XML and
    # emits CORRECTED XML bytes (text_replace on bytes) consumed pre-parse; the
    # derived LegalAddress is metadata only (not a patch-lookup target). No
    # timeline op / legal-state is minted from these regexes (C4 triage).
    "src/lawvm/finland/corrigendum.py": "source_plane",
    # finlex:// locator + FRBR/AKN identity-byte parsing for canonical artifact
    # identity / version derivation. Plane A throughout (C4 triage).
    "src/lawvm/finland/consolidated_artifacts.py": "source_plane",
    # Source-fact amendment->parent edge discovery over consolidated oracle
    # metadata; johtolause gating is delegated to the owning citation router, not
    # local regex. Index/identity layer, not a timeline producer (C4 triage).
    "src/lawvm/finland/amendment_index.py": "source_plane",
    # HTML/RSC oracle ingest: regex heading fallback when JSON parse fails. Plane
    # A byte/HTML ingest (C4 triage).
    "src/lawvm/finland/finlex_html.py": "source_plane",
    # Own-source structural normalization between raw XML parse and body-pairing:
    # every regex reads an IRNode label/text/irnode_to_text of the node being
    # normalized (plane-A source-IR, the module's own input); every rewrite emits
    # a witnessed SourceNormalizationFact reaching production (RB-C triage).
    "src/lawvm/finland/source_normalize.py": "source_plane",
    # --- lexer (B): label / numeric-token normalization only ---
    "src/lawvm/core/tree_ops.py": "lexer",
    "src/lawvm/finland/labels.py": "lexer",
    "src/lawvm/finland/profile/normalize.py": "lexer",
    # --- lexer (B): johtolause tokenizer (raw johto fragment -> Token) ---
    "src/lawvm/finland/johtolause/lexer.py": "lexer",
    # Shared FI date lexer (E1c): pure day-month-year/year-end token recognizer
    # over already-located clause text; returns value+form, mints no legal state.
    "src/lawvm/finland/fi_dates.py": "lexer",
    # --- owning parser (B): the canonical parser for a construction family ---
    "src/lawvm/finland/johtolause/api.py": "owning_parser",
    "src/lawvm/finland/johtolause/clause_patterns.py": "owning_parser",
    "src/lawvm/finland/johtolause/clause_surface.py": "owning_parser",
    "src/lawvm/finland/johtolause/affected_statute.py": "owning_parser",
    "src/lawvm/finland/johtolause/surface_parse.py": "owning_parser",
    "src/lawvm/finland/johtolause/grammar/sections.py": "owning_parser",
    "src/lawvm/finland/johtolause_supplements.py": "owning_parser",
    "src/lawvm/finland/claim_kinds/inline_statute_resolution.py": "owning_parser",
    "src/lawvm/finland/legal_surface/delegation_parse.py": "owning_parser",
    "src/lawvm/finland/legal_surface/modal_parse.py": "owning_parser",
    "src/lawvm/finland/references/by_name.py": "owning_parser",
    "src/lawvm/finland/references/sections.py": "owning_parser",
    "src/lawvm/finland/amendment_payload_lookup.py": "owning_parser",
    # references/ core owning recognizers (C5 triage: each OWNS a reference family
    # per notes/FI_REFERENCE_CATALOGUE.md §4; regex feeds the family parser over the
    # module's OWN prose / AKN-id surface — mirrors the by_name / sections preclear).
    "src/lawvm/finland/references/ref_mention_extractor.py": "owning_parser",
    "src/lawvm/finland/references/internal_refs.py": "owning_parser",
    "src/lawvm/finland/references/inline_citation_extractor.py": "owning_parser",
    "src/lawvm/finland/references/cross_refs.py": "owning_parser",
    "src/lawvm/finland/references/eu_reference.py": "owning_parser",
    "src/lawvm/finland/references/eu_directive.py": "owning_parser",
    "src/lawvm/finland/references/preparatory_reference_extractor.py": "owning_parser",
    "src/lawvm/finland/references/freetext_addresses.py": "owning_parser",
    "src/lawvm/finland/references/anaphora.py": "owning_parser",
    "src/lawvm/finland/references/elliptical_resolve.py": "owning_parser",
    "src/lawvm/finland/references/resolve.py": "owning_parser",
    "src/lawvm/finland/references/shared_reference_orchestrator.py": "owning_parser",
    # Cited-version item-clause recognizer (E1b): owns the item-cited-version parse
    # over its own clause text, routes the cited statute id to the references id
    # constructor; the snapshot-drop it informs is witnessed (CitedVersionSnapshotDrop).
    "src/lawvm/finland/references/cited_version.py": "owning_parser",
    # references/ tail owning recognizers (C6 triage: surface-fact recognizers, each
    # the owning parser for its family — temporal H3, treaty/SopS, modal actor,
    # sanction, vague targetless-phrase selector — over their own input `text`).
    "src/lawvm/finland/references/actor_modal.py": "owning_parser",
    "src/lawvm/finland/references/sanction.py": "owning_parser",
    "src/lawvm/finland/references/temporal.py": "owning_parser",
    "src/lawvm/finland/references/treaty.py": "owning_parser",
    "src/lawvm/finland/references/treaty_article.py": "owning_parser",
    "src/lawvm/finland/references/vague.py": "owning_parser",
    # legal_surface owning construction parsers / lenses (C6 triage: each reads its
    # own scoped surface text / segment — the §1.12 owning-parser-input carve-out).
    "src/lawvm/finland/legal_surface/case_frame.py": "owning_parser",
    "src/lawvm/finland/legal_surface/condition_exception_parse.py": "owning_parser",
    "src/lawvm/finland/legal_surface/delegation_canonical.py": "owning_parser",
    "src/lawvm/finland/legal_surface/sentence_parse.py": "owning_parser",
    "src/lawvm/finland/legal_surface/temporal_parse.py": "owning_parser",
    # legal_surface source-structure normalizers (C6 triage: parse AKN <num>/eId
    # surface + already-typed provision_path into labels — source-structure plane).
    "src/lawvm/finland/legal_surface/provision_index.py": "source_plane",
    "src/lawvm/finland/legal_surface/reference_projection.py": "source_plane",
    # THE owning voimaantulosäännös (transitional-provision) cross-statute repeal
    # extractor: reads its OWN amendment source XML (xml_bytes) and mints REPEAL
    # ops via the §1.12-sanctioned owning rail; address parse already delegated to
    # the shared scan_legal_addresses grammar driver; fails closed (records
    # VtsSkippedTarget/VtsSourceDiagnostic). Local regexes are the citation/title
    # lexer + fragment-boundary truncation feeding it (C4 triage).
    "src/lawvm/finland/vts.py": "owning_parser",
    # scope.py is the canonical chapter/part-scope assignment parser: it
    # consumes its OWN johtolause + already-typed los to assign/strip scope and
    # mints no ops. Mirrors johto_scope_mentions / references/sections (both
    # precleared owning_parser). No reach-back site (none of its 44 regexes read
    # raw_text/source_text/irnode_to_text/.description). Whole-file preclear.
    "src/lawvm/finland/scope.py": "owning_parser",
    # --- diagnostic (D/E): audit / oracle-comparison rendering, no authority ---
    "src/lawvm/core/ir_helpers.py": "diagnostic",
    "src/lawvm/finland/inline_repeal_stub.py": "diagnostic",
    "src/lawvm/finland/oracle_comparison.py": "diagnostic",
    # pure measurement module, explicitly off the replay/apply path
    # ("changes no production behaviour"); cheap-signal proxy is corroboration only.
    "src/lawvm/finland/references/annotation_independence_census.py": "diagnostic",
}

# Inline waiver vocabulary (a use-site is waived if its line, or the line above,
# carries `# lawvm-regex: <category> <rationale>`). legacy_escape_hatch is the
# highest-severity waiver — see part (d) below.
WAIVER_CATEGORIES: frozenset[str] = frozenset(
    {
        "owning_parser",
        "witness_only",
        "diagnostic",
        "prefilter",
        "legacy_escape_hatch",
    }
)
_RE_WAIVER_COMMENT = re.compile(
    r"#\s*lawvm-regex:\s*(?P<category>[a-z_]+)\b(?P<rationale>.*)$"
)

# The four targeted regex methods (the *use* sites). re.compile alone is NOT a
# use-site; it only counts once invoked. We match calls whose receiver is either
# the ``re`` module OR an identifier that follows the project's regex-constant
# naming convention (``_NAME_RE`` / ``NAME_PATTERN`` / ``foo_re`` / ``bar_pattern``),
# so module-scope compiled patterns invoked via ``_X_RE.finditer(...)`` count too
# (e.g. replay_products rank-2), while ``.match(...)`` on arbitrary objects does not.
_REGEX_METHODS = ("search", "finditer", "findall", "match")
_RE_REGEX_USE_SITE = re.compile(
    r"\b(?P<receiver>re|[A-Za-z_][A-Za-z0-9_]*(?:_RE|_PATTERN|_re|_pattern))"
    r"\.(?P<method>search|finditer|findall|match)\("
)

# Part (d): regex over these raw/rendered-text accessors in a scanned (semantic)
# file is the highest-severity class. An un-waived hit of this shape is reported
# as ``legacy_escape_hatch`` so the rank-1/2/3/15 raw-text reach-back sites cannot
# be added to without an explicit waiver naming a leak-ledger rank.
#
# Two ways a use-site touches raw text:
#   (1) line-local: the call line itself names a raw-text accessor (the original,
#       UNSOUND-on-its-own heuristic — kept for module-const calls like
#       ``_X_RE.finditer(raw_text)`` and for calls inside comprehensions/exprs).
#   (2) renamed-local (added here): the searched string is a local variable that
#       was assigned — directly or transitively, within the same function — from a
#       raw-text accessor expression. This is the common evading shape
#       (``txt = op.source.raw_text`` then ``re.search(pat, txt)``). An AST taint
#       pass (``raw_text_tainted_call_lines``) recovers these so the detector is
#       sound-leaning: it prefers honest over-flagging (→ explicit waivers) to
#       silently missing a reach-back.
_RAW_TEXT_ACCESSOR_ATTRS = frozenset({"raw_text", "source_text", "description"})
_RAW_TEXT_ACCESSOR_FUNCS = frozenset({"irnode_to_text"})
# A bare variable literally named like a raw-text accessor token is itself a
# raw-text string by convention (the line-local heuristic already flags any line
# containing the word ``raw_text``). Seeding taint with such parameter/local names
# recovers the inter-procedural case where a helper takes a ``raw_text`` parameter
# (assigned from ``*.raw_text`` at the call site) and regexes a local derived from
# it, e.g. ``merge.py`` ``_source_targets_plain_subsection(raw_text, ...)``.
_RAW_TEXT_SEED_NAMES = frozenset({"raw_text", "source_text"})
_RE_RAW_TEXT_ACCESSOR = re.compile(
    r"\b(raw_text|source_text|irnode_to_text|\.description)\b"
)
_RE_LEAK_LEDGER_RANK = re.compile(r"\brank[\s_-]*\d+\b", re.IGNORECASE)


def _expr_is_raw_text_accessor(node: ast.AST, tainted: set[str]) -> bool:
    """True if ``node`` is (or transitively contains) a raw-text reach-back source.

    Conservative / sound-leaning: returns True if anywhere inside the expression
    there is

      * an attribute access ending in ``.raw_text`` / ``.source_text`` /
        ``.description`` (``op.lo.source.raw_text``, ``x.description`` …),
      * a call to ``irnode_to_text(...)`` (bare or dotted), or
      * a reference to a name already known to be raw-text tainted in this scope.

    Walking the whole sub-tree means ``a = (op.source.raw_text or "")``,
    ``b = " ".join(raw.split())``, and ``c = f(tainted)`` all stay tainted — the
    taint cannot be laundered through wrapping/normalization without the detector
    seeing it.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in _RAW_TEXT_ACCESSOR_ATTRS:
            return True
        if isinstance(child, ast.Call):
            func = child.func
            fname = ""
            if isinstance(func, ast.Name):
                fname = func.id
            elif isinstance(func, ast.Attribute):
                fname = func.attr
            if fname in _RAW_TEXT_ACCESSOR_FUNCS:
                return True
        if isinstance(child, ast.Name) and child.id in tainted:
            return True
    return False


def _assignment_targets(node: ast.AST) -> list[str]:
    """Bare local names bound by an assignment / for / with / walrus target."""
    names: list[str] = []
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = [node.target]
    elif isinstance(node, (ast.AugAssign, ast.NamedExpr)):
        targets = [node.target]
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        targets = [node.target]
    for target in targets:
        for sub in ast.walk(target):
            if isinstance(sub, ast.Name):
                names.append(sub.id)
    return names


def _scope_value_nodes(node: ast.AST) -> list[tuple[list[str], ast.AST]]:
    """(target-names, value-expr) pairs for binding statements in a scope body."""
    pairs: list[tuple[list[str], ast.AST]] = []
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        value = node.value
        if value is not None:
            pairs.append((_assignment_targets(node), value))
    elif isinstance(node, (ast.For, ast.AsyncFor)):
        # `for name in <iter>:` taints `name` if the iterable is raw text.
        pairs.append((_assignment_targets(node), node.iter))
    return pairs


def _function_scopes(tree: ast.AST) -> Iterable[ast.AST]:
    """Yield every function/module scope (each is a separate taint universe)."""
    yield tree  # module scope catches top-level binds + bare expressions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            yield node


def _nodes_owned_by_scope(scope: ast.AST) -> Iterable[ast.AST]:
    """Walk ``scope`` but do NOT descend into nested function/lambda bodies.

    Each function is its own taint universe (a local in an inner function must not
    taint a same-named local of the enclosing one). Comprehensions and class/if/
    for/with blocks ARE part of the scope and are descended into.
    """
    def _push(seq: Iterable[object]) -> None:
        for child in seq:
            if not isinstance(child, ast.AST):
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested scope; handled by its own _function_scopes pass
            stack.append(child)

    stack: list[ast.AST] = []
    body = getattr(scope, "body", None)
    if isinstance(body, list):
        _push(reversed(body))
    elif body is not None:
        _push([body])  # Lambda: single expression body
    while stack:
        node = stack.pop()
        yield node
        _push(ast.iter_child_nodes(node))


def _scope_param_names(scope: ast.AST) -> set[str]:
    """Parameter names of a function/lambda scope (empty for module scope)."""
    args = getattr(scope, "args", None)
    if not isinstance(args, ast.arguments):
        return set()
    names: set[str] = set()
    for group in (args.posonlyargs, args.args, args.kwonlyargs):
        names.update(a.arg for a in group)
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _call_arg_names(call: ast.Call) -> set[str]:
    """Bare names that appear anywhere inside a call's positional/keyword args."""
    names: set[str] = set()
    for arg in list(call.args) + [kw.value for kw in call.keywords]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
    return names


def _is_regex_call(call: ast.Call) -> bool:
    """True if ``call`` is a targeted regex use-site (``re.<m>(`` or ``*_RE.<m>(``).

    Mirrors ``_RE_REGEX_USE_SITE``: receiver is the ``re`` module or an identifier
    following the ``*_RE`` / ``*_PATTERN`` / ``*_re`` / ``*_pattern`` convention;
    method is search / finditer / findall / match.
    """
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _REGEX_METHODS:
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name):
        rid = receiver.id
        if rid == "re":
            return True
        return bool(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:_RE|_PATTERN|_re|_pattern)", rid)
        )
    return False


def raw_text_tainted_call_lines(text: str) -> set[int]:
    """Line numbers of regex use-sites whose searched string is raw-text tainted.

    Per function/module scope, iterate a fixpoint over binding statements: a local
    name becomes tainted when it is bound from an expression that is (or
    transitively contains) a raw-text accessor or an already-tainted name. A regex
    call whose arguments reference any tainted name is a renamed-local reach-back;
    its ``lineno`` is returned.

    The fixpoint (repeat to convergence) makes the analysis order-independent and
    transitive across an arbitrary number of rename hops within a scope; treating
    the whole expression as tainted (no kill on re-bind) is the conservative,
    sound-leaning choice — we never untaint, so we never silently lose a hit.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Conservative: if we cannot parse, claim no AST-derived hits (the
        # line-local heuristic still applies in the caller). Scanned source files
        # are valid Python, so this only guards malformed test fixtures.
        return set()

    hit_lines: set[int] = set()
    for scope in _function_scopes(tree):
        owned = list(_nodes_owned_by_scope(scope))
        bind_pairs: list[tuple[list[str], ast.AST]] = []
        for stmt in owned:
            bind_pairs.extend(_scope_value_nodes(stmt))

        # Seed: parameters / locals literally named like a raw-text accessor token
        # (``raw_text`` / ``source_text``) are raw-text strings by convention.
        tainted: set[str] = set(_scope_param_names(scope) & _RAW_TEXT_SEED_NAMES)
        for names, _value in bind_pairs:
            tainted.update(n for n in names if n in _RAW_TEXT_SEED_NAMES)
        changed = True
        while changed:
            changed = False
            for names, value in bind_pairs:
                if not names:
                    continue
                if any(n in tainted for n in names):
                    continue
                if _expr_is_raw_text_accessor(value, tainted):
                    tainted.update(names)
                    changed = True

        if not tainted:
            continue
        for node in owned:
            if isinstance(node, ast.Call) and _is_regex_call(node):
                if _call_arg_names(node) & tainted:
                    hit_lines.add(node.lineno)
    return hit_lines


_BOUND_COVERAGE_NEARBY_LINES = 80
_RE_PATTERN_OWNER_ASSIGNMENT = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:re\.(?:compile|finditer|search|match)|\()?"
)
_RE_FUNCTION_DEF = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_RE_COVERAGE_FUNCTION = re.compile(r"^\s*def\s+\w*coverage\w*\(")
_RE_REGEX_COVERAGE_SENSOR_FIELD = re.compile(
    r"\b(regex_recognition_coverage|coverage_status|ignored_spans)\b"
)
_RE_NAMED_CAPTURE = re.compile(r"\?P<([A-Za-z_][A-Za-z0-9_]*)>")
_SEMANTIC_CAPTURE_NAMES = frozenset(
    {
        "anchor",
        "inserted",
        "items",
        "original",
        "payload",
        "replacement",
        "terms",
        "text",
    }
)
_COVERED_BOUNDED_WILDCARD_STATUSES = frozenset(
    {
        "coverage_function_reference",
        "nearby_coverage_surface",
    }
)


def _is_comment_only_line(line: str) -> bool:
    return line.lstrip().startswith("#")


def _bounded_wildcard_semantic_role(line: str) -> str:
    capture_names = frozenset(_RE_NAMED_CAPTURE.findall(line))
    if capture_names & _SEMANTIC_CAPTURE_NAMES:
        return "semantic_payload_capture"
    if "\\b" in line or re.search(
        r"\b(?:omit|insert|substitute|replace|repeal|kumot|muut|lisät)",
        line,
        re.I,
    ):
        return "drafting_classifier"
    return "unknown_pattern_bound"


def _bounded_wildcard_soundness_risk(
    *,
    coverage_status: str,
    semantic_role: str,
) -> str:
    if coverage_status in _COVERED_BOUNDED_WILDCARD_STATUSES:
        return "covered_by_regex_coverage_surface"
    if semantic_role == "semantic_payload_capture":
        return "needs_typed_coverage_or_grammar"
    if semantic_role == "drafting_classifier":
        return "needs_classifier_safety_review"
    return "needs_triage"


def _owner_symbol_for_line(lines: list[str], line_no: int) -> str:
    """Return the closest variable/table name owning a regex pattern line."""
    start = max(0, line_no - 40)
    for idx in range(line_no - 1, start - 1, -1):
        match = _RE_PATTERN_OWNER_ASSIGNMENT.search(lines[idx])
        if match:
            return match.group(1)
    return ""


def _owner_function_for_line(lines: list[str], line_no: int) -> str:
    """Return the nearest enclosing function name for a regex pattern line."""
    for idx in range(line_no - 1, -1, -1):
        match = _RE_FUNCTION_DEF.search(lines[idx])
        if match:
            return match.group(1)
    return ""


def _is_referenced_from_coverage_function(lines: list[str], owner_symbol: str) -> bool:
    if not owner_symbol:
        return False
    for idx, line in enumerate(lines):
        if owner_symbol not in line:
            continue
        window_start = max(0, idx - 40)
        if any(_RE_COVERAGE_FUNCTION.search(prev_line) for prev_line in lines[window_start:idx + 1]):
            return True
    return False


def _bounded_wildcard_grammar_family(
    *,
    owner_symbol: str,
    owner_function: str,
    snippet: str,
    semantic_role: str,
) -> str:
    """Coarse migration family for bounded wildcard recognizers.

    The label is intentionally advisory. It helps prioritize grammar extraction
    without claiming that a line-level regex scan has proven semantics.
    """

    haystack = f"{owner_symbol} {owner_function} {snippet}".lower()
    if semantic_role != "semantic_payload_capture":
        if "omit" in haystack or "repeal" in haystack:
            return "omission_classifier"
        if "insert" in haystack:
            return "insertion_classifier"
        return "lexical_or_classifier"
    if "definition" in haystack or "entr" in haystack:
        return "definition_entry_or_definition_body_instruction"
    if "step" in haystack:
        return "step_insert_instruction"
    if "bracket" in haystack or "parenthes" in haystack:
        return "bracket_or_parenthetical_text_selector_instruction"
    if "ordinal" in haystack or "anchor" in haystack:
        return "anchor_ordered_insert_instruction"
    if "at_end" in haystack or "at the end" in haystack:
        return "at_end_insert_instruction"
    if "omit" in haystack or "repeal" in haystack:
        return "omission_instruction"
    return "unclassified_semantic_payload_instruction"


def _annotate_bounded_wildcard_coverage(
    hits: list[dict[str, Any]],
    lines: list[str],
) -> None:
    coverage_lines = sorted({
        line_no
        for line_no, line in enumerate(lines, start=1)
        if _RE_REGEX_COVERAGE_SENSOR_FIELD.search(line)
    } | {
        int(hit["line"])
        for hit in hits
        if hit["category"] == "regex_coverage_surface"
    })

    for hit in hits:
        if hit["category"] != "bounded_wildcard_gap":
            continue
        line_no = int(hit["line"])
        semantic_role = _bounded_wildcard_semantic_role(str(hit.get("snippet") or ""))
        nearest_line = min(
            coverage_lines,
            key=lambda coverage_line: abs(coverage_line - line_no),
            default=None,
        )
        nearest_distance = (
            None
            if nearest_line is None
            else abs(int(nearest_line) - line_no)
        )
        owner_symbol = _owner_symbol_for_line(lines, line_no)
        owner_function = _owner_function_for_line(lines, line_no)
        referenced_from_coverage = _is_referenced_from_coverage_function(
            lines,
            owner_symbol,
        )

        if referenced_from_coverage:
            coverage_status = "coverage_function_reference"
        elif nearest_distance is not None and nearest_distance <= _BOUND_COVERAGE_NEARBY_LINES:
            coverage_status = "nearby_coverage_surface"
        elif nearest_line is not None:
            coverage_status = "file_level_coverage_surface"
        else:
            coverage_status = "missing_coverage_surface"
        soundness_risk = _bounded_wildcard_soundness_risk(
            coverage_status=coverage_status,
            semantic_role=semantic_role,
        )

        hit["coverage_sensor"] = {
            "status": coverage_status,
            "recognizer_name": owner_symbol,
            "owner_symbol": owner_symbol,
            "owner_function": owner_function,
            "semantic_role": semantic_role,
            "soundness_risk": soundness_risk,
            "grammar_family": _bounded_wildcard_grammar_family(
                owner_symbol=owner_symbol,
                owner_function=owner_function,
                snippet=str(hit.get("snippet") or ""),
                semantic_role=semantic_role,
            ),
            "nearest_coverage_line": nearest_line,
            "nearest_coverage_distance": nearest_distance,
            "nearby_line_window": _BOUND_COVERAGE_NEARBY_LINES,
        }


def _collect_hits(path: Path, markers: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").splitlines()
    hits: list[dict[str, Any]] = []
    compiled = {
        key: (label, re.compile(pattern))
        for key, (label, pattern) in markers.items()
    }

    for line_no, line in enumerate(text, start=1):
        for key, (label, regex) in compiled.items():
            if regex.search(line):
                if key == "bounded_wildcard_gap" and _is_comment_only_line(line):
                    continue
                hits.append(
                    {
                        "category": key,
                        "label": label,
                        "line": line_no,
                        "snippet": line.strip(),
                    }
                )

    hits.sort(key=lambda hit: (hit["category"], hit["line"]))
    _annotate_bounded_wildcard_coverage(hits, text)
    return hits


def build_inventory(
    file_paths: Iterable[Path],
    markers: dict[str, tuple[str, str]] | None = None,
    *,
    categories: set[str] | None = None,
    marker_filter: str | None = None,
) -> dict[str, Any]:
    marker_map = dict(SMELL_MARKERS if markers is None else markers)
    if categories is not None:
        marker_map = {
            category: (label, pattern)
            for category, (label, pattern) in marker_map.items()
            if category in categories
        }
    marker_regex = (
        re.compile(marker_filter, re.IGNORECASE)
        if marker_filter is not None
        else None
    )
    by_file: dict[str, list[dict[str, Any]]] = {}
    category_totals: Counter[str] = Counter()
    file_totals: Counter[str] = Counter()

    for path in sorted(file_paths, key=lambda p: str(p)):
        if not path.exists():
            continue
        hits = _collect_hits(path, marker_map)
        if marker_regex is not None:
            hits = [
                hit
                for hit in hits
                if marker_regex.search(hit["snippet"]) or marker_regex.search(hit["label"])
            ]
        by_file[str(path)] = hits
        file_totals[str(path)] = len(hits)
        category_totals.update(hit["category"] for hit in hits)

    for category in marker_map:
        category_totals.setdefault(category, 0)

    bounded_wildcard_coverage_status_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("status") or "not_applicable")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    bounded_wildcard_semantic_role_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("semantic_role") or "unknown_pattern_bound")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    bounded_wildcard_soundness_risk_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("soundness_risk") or "needs_triage")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    bounded_wildcard_grammar_family_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("grammar_family") or "unclassified")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    for status in (
        "coverage_function_reference",
        "file_level_coverage_surface",
        "missing_coverage_surface",
        "nearby_coverage_surface",
    ):
        bounded_wildcard_coverage_status_counts.setdefault(status, 0)
    for role in (
        "drafting_classifier",
        "semantic_payload_capture",
        "unknown_pattern_bound",
    ):
        bounded_wildcard_semantic_role_counts.setdefault(role, 0)
    for risk in (
        "covered_by_regex_coverage_surface",
        "needs_classifier_safety_review",
        "needs_triage",
        "needs_typed_coverage_or_grammar",
    ):
        bounded_wildcard_soundness_risk_counts.setdefault(risk, 0)

    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    category_count = len(marker_map)
    return {
        "generated_with": "scripts/inventory_parser_smells.py",
        "generated_at": generated_at,
        "hit_count": sum(file_totals.values()),
        "summary": {
            "file_count": len(file_totals),
            "category_count": category_count,
            "filtered_category_count": len(marker_map),
            "hit_count": sum(file_totals.values()),
            "bounded_wildcard_coverage_status_counts": dict(
                sorted(bounded_wildcard_coverage_status_counts.items())
            ),
            "bounded_wildcard_semantic_role_counts": dict(
                sorted(bounded_wildcard_semantic_role_counts.items())
            ),
            "bounded_wildcard_soundness_risk_counts": dict(
                sorted(bounded_wildcard_soundness_risk_counts.items())
            ),
            "bounded_wildcard_grammar_family_counts": dict(
                sorted(bounded_wildcard_grammar_family_counts.items())
            ),
            "bounded_wildcard_soundness_note": (
                "bounded wildcard regexes are recognizer-local span claims, not semantic "
                "exhaustiveness proofs; coverage sensors must still classify captured "
                "semantic slots, skipped context, and unclassified gaps"
            ),
        },
        "file_counts": dict(sorted(file_totals.items())),
        "category_counts": dict(sorted(category_totals.items())),
        "by_file": by_file,
    }


def _to_markdown(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "# Parser Smell Inventory (Generated)",
        "",
        f"> generated_at: {inventory['generated_at']}",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| files | {summary['file_count']} |",
        f"| categories | {summary['category_count']} |",
        f"| filtered_categories | {summary['filtered_category_count']} |",
        f"| hits | {summary['hit_count']} |",
        "",
        f"Total hit rows: {inventory['hit_count']}",
        "",
        "| File | Hits |",
        "| --- | ---: |",
    ]
    for file_path, hit_count in inventory["file_counts"].items():
        lines.append(f"| {file_path} | {hit_count} |")

    lines.extend(
        [
            "",
            "| Category | Count |",
            "| --- | ---: |",
        ]
    )
    for category, count in inventory["category_counts"].items():
        lines.append(f"| {category} | {count} |")

    coverage_status_counts = summary.get("bounded_wildcard_coverage_status_counts") or {}
    if coverage_status_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Coverage Status | Count |",
                "| --- | ---: |",
            ]
        )
        for status, count in sorted(coverage_status_counts.items()):
            lines.append(f"| {status} | {count} |")
        lines.extend(
            [
                "",
                "> Bounded wildcard note: a bounded regex span is not a semantic "
                "exhaustiveness proof. Coverage means the recognizer exposes owned "
                "semantic slots, skipped context, or unclassified gaps for review.",
            ]
        )

    semantic_role_counts = summary.get("bounded_wildcard_semantic_role_counts") or {}
    if semantic_role_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Semantic Role | Count |",
                "| --- | ---: |",
            ]
        )
        for role, count in sorted(semantic_role_counts.items()):
            lines.append(f"| {role} | {count} |")

    soundness_risk_counts = summary.get("bounded_wildcard_soundness_risk_counts") or {}
    if soundness_risk_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Soundness Risk | Count |",
                "| --- | ---: |",
            ]
        )
        for risk, count in sorted(soundness_risk_counts.items()):
            lines.append(f"| {risk} | {count} |")

    grammar_family_counts = summary.get("bounded_wildcard_grammar_family_counts") or {}
    if grammar_family_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Grammar Family | Count |",
                "| --- | ---: |",
            ]
        )
        for family, count in sorted(grammar_family_counts.items()):
            lines.append(f"| {family} | {count} |")

    for path, hits in inventory["by_file"].items():
        lines.extend(
            [
                "",
                f"## {path}",
                "",
                "| Line | Category | Label | Snippet |",
                "| --- | --- | --- | --- |",
            ]
        )
        if not hits:
            lines.append("| n/a | no smells | n/a | no matching lines |")
            continue
        for hit in hits:
            snippet = hit["snippet"].replace("|", "\\|")
            coverage_sensor = hit.get("coverage_sensor")
            if isinstance(coverage_sensor, dict):
                status = str(coverage_sensor.get("status") or "")
                recognizer = str(coverage_sensor.get("recognizer_name") or "")
                owner_function = str(coverage_sensor.get("owner_function") or "")
                role = str(coverage_sensor.get("semantic_role") or "")
                risk = str(coverage_sensor.get("soundness_risk") or "")
                family = str(coverage_sensor.get("grammar_family") or "")
                if status:
                    suffix = f" [coverage={status}"
                    if recognizer:
                        suffix += f", recognizer={recognizer}"
                    if owner_function:
                        suffix += f", function={owner_function}"
                    if role:
                        suffix += f", role={role}"
                    if risk:
                        suffix += f", risk={risk}"
                    if family:
                        suffix += f", family={family}"
                    suffix += "]"
                    snippet = f"{snippet}{suffix}"
            lines.append(
                f"| {hit['line']} | {hit['category']} | {hit['label']} | {snippet} |"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Regex ratchet scan (imported by tests/test_regex_ratchet.py)
# ---------------------------------------------------------------------------

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
_RATCHET_SCAN_ROOTS = (
    Path("src/lawvm/core"),
    Path("src/lawvm/finland"),
)


def _rel_posix(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def iter_scanned_files(repo_root: Path | None = None) -> list[str]:
    """All ``src/lawvm/{core,finland}`` python files that are NOT pre-cleared.

    A file is scanned unless ``CATEGORY_MAP`` pre-clears it as source-plane /
    lexer / owning-parser / diagnostic. Test/``__pycache__`` files are excluded.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    scanned: list[str] = []
    for scan_root in _RATCHET_SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        for pyfile in sorted(base.rglob("*.py")):
            rel = _rel_posix(pyfile, root)
            if "/tests/" in f"/{rel}" or pyfile.name.startswith("test_"):
                continue
            if rel in CATEGORY_MAP:
                continue
            scanned.append(rel)
    return scanned


def _line_is_waived(lines: list[str], idx: int) -> tuple[bool, str]:
    """A use-site is waived if its own line, or the line directly above it,
    carries a ``# lawvm-regex: <category> <rationale>`` comment with a known
    category. Returns (waived, waiver_category)."""
    for probe in (idx, idx - 1):
        if probe < 0:
            continue
        match = _RE_WAIVER_COMMENT.search(lines[probe])
        if not match:
            continue
        category = match.group("category")
        if category in WAIVER_CATEGORIES:
            return True, category
    return False, ""


def scan_file_regex_use_sites(
    rel_path: str,
    text: str,
) -> list[dict[str, Any]]:
    """Find every targeted regex use-site in one scanned file.

    Returns one record per ``re.(search|finditer|findall|match)(`` style call
    (including module-scope compiled-constant call sites), each annotated with
    whether it is waived, the waiver category, and whether it is a raw-text
    ``legacy_escape_hatch`` shape.
    """
    lines = text.splitlines()
    # AST taint pass: line numbers of regex use-sites whose searched string is a
    # local transitively assigned from a raw-text accessor (the renamed-local
    # reach-back the line-local heuristic alone misses, e.g. merge.py:2542/2544).
    tainted_call_lines = raw_text_tainted_call_lines(text)
    records: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue  # a commented-out call is not a live use-site
        for use_site in _RE_REGEX_USE_SITE.finditer(line):
            waived, waiver_category = _line_is_waived(lines, idx)
            line_local_raw = bool(_RE_RAW_TEXT_ACCESSOR.search(line))
            renamed_local_raw = (idx + 1) in tainted_call_lines
            is_raw_text = line_local_raw or renamed_local_raw
            records.append(
                {
                    "file": rel_path,
                    "line": idx + 1,
                    "receiver": use_site.group("receiver"),
                    "method": use_site.group("method"),
                    "waived": waived,
                    "waiver_category": waiver_category,
                    "raw_text_accessor": is_raw_text,
                    "raw_text_via": (
                        "line_local"
                        if line_local_raw
                        else "renamed_local"
                        if renamed_local_raw
                        else ""
                    ),
                    "snippet": stripped,
                }
            )
    return records


def scan_regex_ratchet(repo_root: Path | None = None) -> dict[str, Any]:
    """Compute the full ratchet state for the scanned (semantic-plane) files.

    Returns:
      - ``unwaived_counts``: {rel_path: count} of UN-waived regex use-sites
        (this is the monotone ratchet quantity);
      - ``total_unwaived``: sum across files;
      - ``waived_counts`` / ``total_waived``;
      - ``legacy_escape_hatch_unwaived``: raw-text reach-back use-sites that are
        un-waived (part d) — these MUST be acknowledged with a leak-ledger rank;
      - ``legacy_escape_hatch_waived_without_rank``: hatch sites whose waiver does
        not cite a leak-ledger rank (a discipline violation under part d);
      - ``records``: every use-site record;
      - ``preclear_summary``: per-category pre-clear counts.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    records: list[dict[str, Any]] = []
    for rel in iter_scanned_files(root):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        records.extend(scan_file_regex_use_sites(rel, text))

    unwaived_counts: Counter[str] = Counter()
    waived_counts: Counter[str] = Counter()
    legacy_escape_hatch_unwaived: list[dict[str, Any]] = []
    legacy_escape_hatch_waived_without_rank: list[dict[str, Any]] = []
    for rec in records:
        if rec["waived"]:
            waived_counts[rec["file"]] += 1
        else:
            unwaived_counts[rec["file"]] += 1
        if rec["raw_text_accessor"]:
            if not rec["waived"]:
                legacy_escape_hatch_unwaived.append(rec)
            elif rec["waiver_category"] == "legacy_escape_hatch" and not _RE_LEAK_LEDGER_RANK.search(
                rec["snippet"]
            ):
                legacy_escape_hatch_waived_without_rank.append(rec)

    preclear_summary: Counter[str] = Counter(CATEGORY_MAP.values())

    return {
        "unwaived_counts": dict(sorted(unwaived_counts.items())),
        "total_unwaived": sum(unwaived_counts.values()),
        "waived_counts": dict(sorted(waived_counts.items())),
        "total_waived": sum(waived_counts.values()),
        "legacy_escape_hatch_unwaived": legacy_escape_hatch_unwaived,
        "legacy_escape_hatch_waived_without_rank": legacy_escape_hatch_waived_without_rank,
        "records": records,
        "scanned_file_count": len(iter_scanned_files(root)),
        "precleared_file_count": len(CATEGORY_MAP),
        "preclear_summary": dict(sorted(preclear_summary.items())),
    }


RATCHET_BASELINE_PATH = Path("tests/data/regex_ratchet_baseline.json")


def ratchet_baseline_snapshot(repo_root: Path | None = None) -> dict[str, Any]:
    """The committed-baseline shape: per-file un-waived counts + hatch ceiling.

    Only the monotone quantities are persisted (un-waived per-file counts, the
    raw-text legacy_escape_hatch ceiling, and the total). Volatile detail (line
    numbers, snippets) is NOT persisted so the baseline is stable across cosmetic
    edits and only changes when a real count changes.
    """
    state = scan_regex_ratchet(repo_root)
    return {
        "_doc": (
            "Monotone regex ratchet baseline for the post-parse semantic plane. "
            "Generated by scripts/inventory_parser_smells.py "
            "(ratchet_baseline_snapshot). Per-file 'unwaived' counts may only "
            "fall, never rise; a fall must be committed (regenerate with "
            "`uv run python scripts/inventory_parser_smells.py --update-baseline`). "
            "See tests/test_regex_ratchet.py and notes/LAWVM_PIPELINE_CONTRACT.md §4."
        ),
        "total_unwaived": state["total_unwaived"],
        "legacy_escape_hatch_unwaived_ceiling": len(state["legacy_escape_hatch_unwaived"]),
        "unwaived_counts": state["unwaived_counts"],
    }


def write_ratchet_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / RATCHET_BASELINE_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = ratchet_baseline_snapshot(root)
    out_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate parser smell inventory from known heuristic patterns."
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "Regenerate tests/data/regex_ratchet_baseline.json from the current "
            "tree (the regex ratchet baseline). Only ever commit a baseline whose "
            "counts are <= the committed one."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; if omitted, prints to stdout",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        help="Optional category filter (repeatable). "
        "Known values: "
        + ", ".join(sorted(SMELL_MARKERS))
        + ".",
    )
    parser.add_argument(
        "--marker",
        default=None,
        help="Optional substring/regex filter over marker snippet/label.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        default=DEFAULT_FILES,
        help="Files to scan; defaults to key Finland parser files",
    )
    return parser


# Backward-compatible alias retained for external callers and tests.
_build_parser = build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.update_baseline:
        out_path = write_ratchet_baseline()
        snapshot = json.loads(out_path.read_text(encoding="utf-8"))
        print(
            f"wrote {out_path} "
            f"(total_unwaived={snapshot['total_unwaived']}, "
            f"legacy_escape_hatch_ceiling="
            f"{snapshot['legacy_escape_hatch_unwaived_ceiling']})"
        )
        return 0
    categories = None if args.category is None else {category.strip() for category in args.category}
    if categories is not None:
        unknown = categories - set(SMELL_MARKERS)
        if unknown:
            raise SystemExit(f"Unknown categories: {', '.join(sorted(unknown))}")

    try:
        inventory = build_inventory(
            args.files,
            categories=categories,
            marker_filter=args.marker,
        )
    except re.error as exc:
        raise SystemExit(f"Invalid marker regex: {exc}") from exc

    if args.format == "json":
        text = json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False)
    else:
        text = _to_markdown(inventory)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
