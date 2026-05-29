"""Regex safety lint and sound prefilter for LawVM classifier patterns.

Purpose:
    Static AST-based lint for module-scope ``_*_RE`` / ``_*_PATTERN`` constants
    in ``src/lawvm/``.  Catches catastrophic-backtracking regex patterns before
    they reach production.  This is a CI lint only — not a runtime monkey-patch.

    Also provides ``compile_classifier_regex`` and ``build_regex_prefilter`` for
    classifier patterns that benefit from a sound necessary-condition prefilter.

Reference: AGENTS.md §1.11, §1.13 (Hot-path performance discipline; regex vs bespoke).
Used by: ``tests/test_regex_perf_gate.py`` (Sensor H batch 5).

Two risk detectors are combined in ``lawvm_regex_risks()``:

1. ``regex_risks()``
   Catches nested backtracking quantifiers, nullable repeated bodies, ambiguous
   alternation inside unbounded repeats, and backreferences / conditional groups.

2. ``adjacent_repeat_risks()``
   Catches the LawVM-specific bug class: adjacent variable backtracking repeats
   whose first-char sets cannot be proven disjoint.  This is the class that caused
   ukpga/1970/9 to spend 104 s on a single classifier (A8 fix, 2026-05-29).

Known-safe examples::

    lawvm_regex_risks(r'^[a-z]+$')        # [] — no risk
    lawvm_regex_risks(r'[a-z]+[0-9]+')   # [] — disjoint character classes
    lawvm_regex_risks(r'\\d+[a-z]+')      # [] — disjoint (CATEGORY_DIGIT vs [a-z])
    lawvm_regex_risks(r'\\w+\\s+[a-z]+')  # [] — \\s and [a-z] are disjoint

Known-risky examples::

    lawvm_regex_risks(r'.+.+')            # adjacent overlapping repeats
    lawvm_regex_risks(r'(a+)+$')          # nested backtracking quantifiers
    lawvm_regex_risks(r'^(a|aa)+$')       # ambiguous alternation in unbounded repeat
    lawvm_regex_risks(r'\\w+\\d+')        # \\w includes digits — genuine overlap

Implementation note:
    Uses ``re._parser`` (private CPython detail).  Stable across CPython 3.11–3.13.
    Falls back to ``sre_parse`` on older Python (unused in this codebase).
    No project-specific imports.  Pure stdlib.

CATEGORY first-char sets (A18 enhancement, 2026-05-29):
    ``first_chars()`` now resolves the standard CATEGORY escapes (``\\d``, ``\\w``,
    ``\\s`` and their Unicode variants) to concrete frozensets of ASCII code-points.
    This eliminates the bulk of CATEGORY false positives in the gate.

    ASCII approximation: Python 3 patterns default to Unicode semantics, so ``\\w``
    can match letters outside ASCII and ``\\d`` can match Unicode digit characters.
    For the purposes of this LINT (catching obvious first-char overlap), the ASCII-
    equivalent sets are sufficient and correct for all LawVM classifier patterns,
    which operate exclusively on ASCII-range legal text.

    NOT-category variants (``\\D``, ``\\W``, ``\\S``) are left as ``None``
    (conservative/unknown) because their char-sets are too large to enumerate and
    the overlap test cannot usefully shrink them.

Sound prefilter (``build_regex_prefilter`` / ``compile_classifier_regex``):
    LawVM occupies an intersection no off-the-shelf engine serves well:
    load-bearing lookarounds (Estonian morphology lookbehind like
    ``(?<![A-Za-zÄÖÕÜäöõüŠŽšž-])``, Finnish ``§(?!:)`` discrimination) mean
    re2/ripgrep/grep are not feasible.  stdlib ``re`` supports all PCRE-like
    features LawVM needs but has no built-in prefilter.  This module provides one.

    The prefilter extracts a boolean predicate tree (``And``/``Or``/``Lit`` nodes)
    of *necessary conditions* from the regex AST.  A ``Lit`` node asserts that a
    literal substring must be present.  ``And`` requires all children; ``Or``
    requires at least one.  Only conditions that are logically required by the
    pattern are emitted — when in doubt, ``TRUE`` (no constraint) is returned.

    Soundness invariant (the only guarantee that matters):
        The prefilter is a NECESSARY-CONDITION filter only.  It will never
        reject a string that the full regex could match.  It may pass strings
        that the regex rejects (false positives are fine; false negatives are
        not).  This guarantee is derived from AST analysis, not from fuzzing —
        fuzzing is a guardrail; the AST derivation rules are the proof.

    Lookarounds → ``TRUE``:  A lookbehind like ``(?<![A-Za-z])`` constrains the
    match *position*, not the substring content; using it as a segment-local
    literal precondition can be unsound.  Lookarounds are always excluded and
    return ``TRUE``.

    Zero-copy substring checks:  Case-sensitive literals use ``str.find`` (no
    allocation).  IGNORECASE literals use a tiny per-literal cached ``re.search``
    on the bounded segment — never ``text.lower()`` over the full document.

    Extraction-readiness:  Pure stdlib, no project-specific imports.  In-tree for
    now (same pattern as ``farchive`` was before extraction); ready to become a
    standalone package when the corpus grows to other consumers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

try:
    from re import _parser as _sre  # type: ignore[attr-defined]  # ty: ignore[unresolved-import]
except ImportError:
    import sre_parse as _sre  # type: ignore[no-redef]  # older Python fallback

# ---------------------------------------------------------------------------
# Prefilter plan nodes — AND / OR / Lit predicate tree.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _True:
    """Plan node meaning "no required literal" — always passes."""


TRUE: _True = _True()


@dataclass(frozen=True)
class Lit:
    """Plan node: the given literal substring must be present in the text."""

    text: str
    flags: int = 0  # only re.IGNORECASE is meaningful here


@dataclass(frozen=True)
class And:
    """Plan node: all children must pass."""

    parts: tuple[Any, ...]


@dataclass(frozen=True)
class Or:
    """Plan node: at least one child must pass."""

    parts: tuple[Any, ...]


# Mask for flags that affect literal matching (only IGNORECASE changes substring
# matching; ASCII and LOCALE don't change whether a substring is present).
_FLAG_MASK: int = re.IGNORECASE | re.ASCII | re.LOCALE


def _is_true(x: Any) -> bool:
    return x is TRUE or isinstance(x, _True)


def _lit_implies(a: Lit, b: Lit) -> bool:
    """Return True when CONTAINS(a.text) implies CONTAINS(b.text).

    This holds when b.text is a substring of a.text and the flags match.
    Used in AND-simplification to drop weaker (shorter) conditions.
    """
    return (
        isinstance(a, Lit)
        and isinstance(b, Lit)
        and a.flags == b.flags
        and b.text in a.text
    )


def _dedupe(parts: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[Any] = set()
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _simplify(node: Any, *, max_or: int = 12) -> Any:
    """Simplify a prefilter plan node."""
    if _is_true(node) or isinstance(node, Lit):
        return node
    if isinstance(node, And):
        parts: list[Any] = []
        for p in node.parts:
            sp = _simplify(p, max_or=max_or)
            if _is_true(sp):
                continue
            if isinstance(sp, And):
                parts.extend(sp.parts)
            else:
                parts.append(sp)
        parts = _dedupe(parts)
        # AND: keep stronger/longer literal; drop b when a implies b.
        drop: set[int] = set()
        for i, a in enumerate(parts):
            for j, b in enumerate(parts):
                if i != j and isinstance(a, Lit) and isinstance(b, Lit) and _lit_implies(a, b):
                    drop.add(j)
        parts = [p for i, p in enumerate(parts) if i not in drop]
        if not parts:
            return TRUE
        if len(parts) == 1:
            return parts[0]
        return And(tuple(parts))
    if isinstance(node, Or):
        parts = []
        for p in node.parts:
            sp = _simplify(p, max_or=max_or)
            if _is_true(sp):
                return TRUE  # one branch needs nothing → whole OR is unconstrained
            if isinstance(sp, Or):
                parts.extend(sp.parts)
            else:
                parts.append(sp)
        parts = _dedupe(parts)
        # OR: keep weaker/shorter literal; drop a when a implies b (a is stronger).
        drop = set()
        for i, a in enumerate(parts):
            for j, b in enumerate(parts):
                if i != j and isinstance(a, Lit) and isinstance(b, Lit) and _lit_implies(a, b):
                    drop.add(i)
        parts = [p for i, p in enumerate(parts) if i not in drop]
        if not parts:
            return TRUE
        if len(parts) > max_or:
            return TRUE
        if len(parts) == 1:
            return parts[0]
        return Or(tuple(parts))
    raise TypeError(f"unexpected prefilter node: {node!r}")


def _count_literals(node: Any) -> int:
    if _is_true(node):
        return 0
    if isinstance(node, Lit):
        return 1
    if isinstance(node, (And, Or)):
        return sum(_count_literals(p) for p in node.parts)
    raise TypeError(f"unexpected prefilter node: {node!r}")


def build_regex_prefilter(
    pattern: str,
    flags: int = 0,
    *,
    min_literal_len: int = 3,
    max_literals: int = 12,
    max_or: int = 12,
) -> And | Or | Lit | None:
    """Extract a sound necessary-condition predicate plan from a regex pattern.

    Returns a predicate tree (``And``/``Or``/``Lit``) that is a *necessary
    condition* for the pattern to match.  Returns ``None`` when no useful plan
    can be derived (pattern has no required literals, or too many).

    Soundness: if ``build_regex_prefilter`` returns a plan P and the plan
    evaluates to False for a string S, then the regex cannot match S.  The
    converse is NOT guaranteed: the plan may pass strings the regex rejects.

    Derivation rules (conservative — when in doubt, return TRUE):

    - LITERAL runs concatenate into a Lit; emit only if length >= min_literal_len.
    - SUBPATTERN: recurse with flag adjustments.
    - BRANCH (alternation): OR of per-branch plans.  If ANY branch yields TRUE,
      the whole OR is TRUE.
    - MAX_REPEAT / MIN_REPEAT / POSSESSIVE_REPEAT: if lo == 0 the body is
      optional → TRUE.  If lo >= 1 the body is required → recurse.
    - ATOMIC_GROUP: recurse.
    - ASSERT / ASSERT_NOT (lookaround): TRUE — excluded for soundness.
    - GROUPREF_EXISTS (conditional): OR(yes-branch, else-branch-or-TRUE).
    - Everything else (character classes, categories, dot, anchors, group refs):
      TRUE — no safe literal can be extracted.
    """
    tree = _sre.parse(pattern, flags)
    POSSESSIVE_REPEAT = getattr(_sre, "POSSESSIVE_REPEAT", None)
    ATOMIC_GROUP = getattr(_sre, "ATOMIC_GROUP", None)
    GROUPREF_EXISTS_OP = getattr(_sre, "GROUPREF_EXISTS", object())
    repeat_ops: set[object] = {_sre.MAX_REPEAT, _sre.MIN_REPEAT}
    if POSSESSIVE_REPEAT is not None:
        repeat_ops.add(POSSESSIVE_REPEAT)

    def make_lit(chars: list[int], cur_flags: int) -> Any:
        if len(chars) < min_literal_len:
            return TRUE
        text = "".join(chr(c) for c in chars)
        lit_flags = cur_flags & _FLAG_MASK
        if not (lit_flags & re.IGNORECASE):
            lit_flags = 0
        lit_flags &= ~re.LOCALE  # LOCALE is bytes-only; strip for str patterns
        return Lit(text, int(lit_flags))

    def build_seq(sub: Any, cur_flags: int) -> Any:
        parts: list[Any] = []
        run: list[int] = []
        run_flags: int | None = None

        def flush() -> None:
            nonlocal run, run_flags
            if run:
                parts.append(make_lit(run, run_flags or 0))
                run, run_flags = [], None

        for op, arg in getattr(sub, "data", sub):
            if op == _sre.LITERAL:
                lf = cur_flags & _FLAG_MASK
                if run and run_flags != lf:
                    flush()
                run_flags = lf
                run.append(arg)
                continue
            flush()
            parts.append(build_token(op, arg, cur_flags))
        flush()
        return _simplify(And(tuple(parts)), max_or=max_or)

    def build_token(op: object, arg: Any, cur_flags: int) -> Any:
        if op == _sre.LITERAL:
            return make_lit([arg], cur_flags)
        if op == _sre.SUBPATTERN:
            _g, add_f, del_f, child = arg
            return build_seq(child, (cur_flags | add_f) & ~del_f)
        if op == _sre.BRANCH:
            _n, branches = arg
            return _simplify(
                Or(tuple(build_seq(b, cur_flags) for b in branches)),
                max_or=max_or,
            )
        if op in repeat_ops:
            lo, _hi, child = arg
            if lo == 0:
                return TRUE
            return build_seq(child, cur_flags)
        if ATOMIC_GROUP is not None and op == ATOMIC_GROUP:
            return build_seq(arg, cur_flags)
        if op in (_sre.ASSERT, _sre.ASSERT_NOT):
            return TRUE  # lookaround excluded — unsound to use as segment precondition
        if op == GROUPREF_EXISTS_OP:
            _g, yes_b, no_b = arg
            branches = [build_seq(yes_b, cur_flags)]
            branches.append(TRUE if no_b is None else build_seq(no_b, cur_flags))
            return _simplify(Or(tuple(branches)), max_or=max_or)
        return TRUE  # character classes, categories, dot, anchors, group refs

    plan = _simplify(build_seq(tree, tree.state.flags), max_or=max_or)
    if _is_true(plan):
        return None
    if _count_literals(plan) > max_literals:
        return None
    return plan  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# PrefilteredPattern wrapper
# ---------------------------------------------------------------------------

# Per-literal IGNORECASE regex cache: maps (literal, flags) → compiled pattern.
_IGNORECASE_LIT_CACHE: dict[tuple[str, int], re.Pattern[str]] = {}


def _get_ic_pattern(text: str, flags: int) -> re.Pattern[str]:
    """Return a cached tiny regex for IGNORECASE literal search."""
    key = (text, flags)
    if key not in _IGNORECASE_LIT_CACHE:
        _IGNORECASE_LIT_CACHE[key] = re.compile(re.escape(text), flags & re.IGNORECASE)
    return _IGNORECASE_LIT_CACHE[key]


class PrefilteredPattern:
    """Wraps a compiled ``re.Pattern`` with a sound prefilter plan.

    Before calling the full regex engine, ``search``/``match``/``fullmatch``/
    ``finditer``/``findall`` evaluate the plan against the text.  If the plan
    fails the call is short-circuited and the appropriate empty value returned.

    The plan is a NECESSARY CONDITION only — it never produces false negatives.
    False positives (plan passes but regex doesn't match) are fine.

    Attributes mirror ``re.Pattern``: ``pattern``, ``flags``, ``groups``,
    ``groupindex``.  Unknown attribute access delegates to the underlying
    pattern object via ``__getattr__``.
    """

    def __init__(self, rx: "re.Pattern[str]", plan: Any) -> None:
        self._rx = rx
        self._plan = plan
        # Expose the standard Pattern attributes directly.
        self.pattern: str = rx.pattern
        self.flags: int = rx.flags
        self.groups: int = rx.groups
        self.groupindex: dict[str, int] = dict(rx.groupindex)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._rx, name)

    def __repr__(self) -> str:
        return f"PrefilteredPattern({self._rx!r}, plan={self._plan!r})"

    def _literal_present(self, lit: Lit, text: str, pos: int, endpos: int) -> bool:
        """Check whether lit.text is present in text[pos:endpos]."""
        if lit.flags & re.IGNORECASE:
            # Use a tiny cached regex bounded to the segment.
            return _get_ic_pattern(lit.text, lit.flags).search(text, pos, endpos) is not None
        return text.find(lit.text, pos, endpos) != -1

    def _plan_passes(self, plan: Any, text: str, pos: int, endpos: int) -> bool:
        """Evaluate the plan against text[pos:endpos]."""
        if _is_true(plan):
            return True
        if isinstance(plan, Lit):
            return self._literal_present(plan, text, pos, endpos)
        if isinstance(plan, And):
            return all(self._plan_passes(p, text, pos, endpos) for p in plan.parts)
        if isinstance(plan, Or):
            return any(self._plan_passes(p, text, pos, endpos) for p in plan.parts)
        return True  # unknown node type — conservative pass

    def search(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "re.Match[str] | None":
        ep = len(string) if endpos is None else endpos
        if not self._plan_passes(self._plan, string, pos, ep):
            return None
        return self._rx.search(string, pos, ep)

    def match(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "re.Match[str] | None":
        ep = len(string) if endpos is None else endpos
        if not self._plan_passes(self._plan, string, pos, ep):
            return None
        return self._rx.match(string, pos, ep)

    def fullmatch(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "re.Match[str] | None":
        ep = len(string) if endpos is None else endpos
        if not self._plan_passes(self._plan, string, pos, ep):
            return None
        return self._rx.fullmatch(string, pos, ep)

    def finditer(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "Iterable[re.Match[str]]":
        ep = len(string) if endpos is None else endpos
        if not self._plan_passes(self._plan, string, pos, ep):
            return iter([])
        return self._rx.finditer(string, pos, ep)

    def findall(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> list[Any]:
        ep = len(string) if endpos is None else endpos
        if not self._plan_passes(self._plan, string, pos, ep):
            return []
        return self._rx.findall(string, pos, ep)


# ---------------------------------------------------------------------------
# Prefilter stats / telemetry
# ---------------------------------------------------------------------------


@dataclass
class RegexPrefilterStats:
    """Per-classifier prefilter telemetry.

    Attributes:
        checked:   Total calls where the prefilter was evaluated.
        rejected:  Calls where the plan failed (regex not run).
        passed:    Calls where the plan passed (regex was run).
        regex_ran: Alias for passed (regex_ran == passed).
    """

    checked: int = field(default=0)
    rejected: int = field(default=0)
    passed: int = field(default=0)

    @property
    def regex_ran(self) -> int:
        return self.passed


# Module-level telemetry dict keyed by classifier_id.
_PREFILTER_TELEMETRY: dict[str, RegexPrefilterStats] = {}


def dump_prefilter_stats() -> dict[str, dict[str, int]]:
    """Return a snapshot of per-classifier prefilter telemetry.

    Returns a dict mapping classifier_id to a dict with keys:
    ``checked``, ``rejected``, ``passed``, ``regex_ran``.
    """
    return {
        cid: {
            "checked": s.checked,
            "rejected": s.rejected,
            "passed": s.passed,
            "regex_ran": s.regex_ran,
        }
        for cid, s in sorted(_PREFILTER_TELEMETRY.items())
    }


# ---------------------------------------------------------------------------
# compile_classifier_regex — the primary compilation path
# ---------------------------------------------------------------------------


def compile_classifier_regex(
    pattern: str,
    flags: int = 0,
    *,
    classifier_id: str,
    enable_prefilter: bool = True,
) -> "re.Pattern[str] | PrefilteredPattern":
    """Compile a classifier regex with safety lint and an optional prefilter.

    Steps:

    1. Run ``lawvm_regex_risks`` — raises ``ValueError`` if any backtracking
       risks are detected.  This gate is MANDATORY and cannot be bypassed.
    2. Compile the pattern via ``re.compile``.
    3. If ``enable_prefilter`` is True and ``build_regex_prefilter`` produces a
       plan, wrap the compiled pattern in ``PrefilteredPattern``.
    4. Register the classifier in ``_PREFILTER_TELEMETRY`` if a plan was built.

    New hot-path classifiers should prefer ``compile_classifier_regex`` over
    hand-written substring guards (AGENTS.md §1.11).

    Args:
        pattern: The regex pattern string.
        flags: Optional ``re`` flags.
        classifier_id: Human-readable identifier for error messages and telemetry.
        enable_prefilter: Whether to build and attach the prefilter plan.
            Defaults to True.  Set to False only when the caller has a specific
            reason (e.g., testing the raw regex).

    Raises:
        ValueError: If ``lawvm_regex_risks()`` detects backtracking risks.

    Returns:
        A ``PrefilteredPattern`` if a plan was derived, otherwise a plain
        ``re.Pattern``.
    """
    risks = lawvm_regex_risks(pattern, flags)
    if risks:
        joined = "\n  - ".join(risks)
        raise ValueError(
            f"unsafe classifier regex {classifier_id!r}: {pattern!r}\n"
            f"  - {joined}"
        )
    rx = re.compile(pattern, flags)
    if not enable_prefilter:
        return rx
    plan = build_regex_prefilter(pattern, flags)
    if plan is None:
        return rx
    if classifier_id not in _PREFILTER_TELEMETRY:
        _PREFILTER_TELEMETRY[classifier_id] = RegexPrefilterStats()
    return PrefilteredPattern(rx, plan)


# ---------------------------------------------------------------------------
# Self-validation guardrail (NOT proof of soundness — the AST analysis is proof)
# ---------------------------------------------------------------------------


def assert_prefilter_no_false_negatives(
    pattern: str,
    flags: int = 0,
    *,
    samples: list[str],
) -> None:
    """Assert that the prefilter introduces no false negatives on the given samples.

    For each sample string, if the bare regex matches but the wrapped pattern
    does not, raise AssertionError.

    This is a guardrail test — the soundness guarantee comes from the AST
    derivation rules in ``build_regex_prefilter``, not from this function.
    Use in CI test suites with representative and adversarial sample sets.

    Args:
        pattern: Regex pattern to test.
        flags: Optional ``re`` flags.
        samples: List of strings to test against.

    Raises:
        AssertionError: If any sample triggers a false negative.
        ValueError: If ``lawvm_regex_risks`` detects backtracking risks.
    """
    bare = re.compile(pattern, flags)
    plan = build_regex_prefilter(pattern, flags)
    if plan is None:
        # No plan — prefilter is a pass-through; no false negatives possible.
        return
    wrapped = PrefilteredPattern(bare, plan)
    for s in samples:
        bare_match = bare.search(s)
        wrapped_match = wrapped.search(s)
        if bare_match is not None and wrapped_match is None:
            raise AssertionError(
                f"prefilter false negative on {pattern!r}:\n"
                f"  sample:  {s!r}\n"
                f"  plan:    {plan!r}\n"
                f"  bare matched at {bare_match.span()}"
            )


# ---------------------------------------------------------------------------
# Module-level CATEGORY → frozenset[int] mapping (ASCII approximation).
#
# Populated lazily at first use via _build_category_char_sets().
# Keys are the CATEGORY_* constants from re._constants (NamedIntConstant).
# Values are frozenset of ord() values that the category can match (ASCII only).
# NOT-* variants map to None — they're too broad to enumerate usefully.
# ---------------------------------------------------------------------------
_CATEGORY_CHAR_SETS: dict[object, frozenset[int] | None] = {}
_CATEGORY_SETS_BUILT = False


def _build_category_char_sets() -> None:
    """Populate _CATEGORY_CHAR_SETS on first call.  Idempotent."""
    global _CATEGORY_SETS_BUILT
    if _CATEGORY_SETS_BUILT:
        return

    try:
        from re import _constants  # type: ignore[attr-defined]  # ty: ignore[unresolved-import]
    except ImportError:
        try:
            import sre_constants as _constants  # type: ignore[no-redef]
        except ImportError:
            _CATEGORY_SETS_BUILT = True
            return

    # ASCII digit set: 0-9
    _digits = frozenset(range(ord("0"), ord("9") + 1))
    # ASCII space set: space, tab, newline, carriage-return, vertical-tab, form-feed
    _spaces = frozenset(ord(c) for c in " \t\n\r\x0b\x0c")
    # ASCII word set: a-z, A-Z, 0-9, _
    _word = frozenset(
        set(range(ord("a"), ord("z") + 1))
        | set(range(ord("A"), ord("Z") + 1))
        | set(range(ord("0"), ord("9") + 1))
        | {ord("_")}
    )
    # Linebreak set: \n and \r
    _linebreak = frozenset({ord("\n"), ord("\r")})

    def _get(name: str) -> object | None:
        return getattr(_constants, name, None)

    pairs: list[tuple[str, frozenset[int] | None]] = [
        # Positive sets
        ("CATEGORY_DIGIT", _digits),
        ("CATEGORY_UNI_DIGIT", _digits),
        ("CATEGORY_SPACE", _spaces),
        ("CATEGORY_UNI_SPACE", _spaces),
        ("CATEGORY_WORD", _word),
        ("CATEGORY_UNI_WORD", _word),
        ("CATEGORY_LOC_WORD", _word),
        ("CATEGORY_LINEBREAK", _linebreak),
        ("CATEGORY_UNI_LINEBREAK", _linebreak),
        # NOT-* variants: too broad — leave as None (conservative)
        ("CATEGORY_NOT_DIGIT", None),
        ("CATEGORY_UNI_NOT_DIGIT", None),
        ("CATEGORY_NOT_SPACE", None),
        ("CATEGORY_UNI_NOT_SPACE", None),
        ("CATEGORY_NOT_WORD", None),
        ("CATEGORY_UNI_NOT_WORD", None),
        ("CATEGORY_LOC_NOT_WORD", None),
        ("CATEGORY_NOT_LINEBREAK", None),
        ("CATEGORY_UNI_NOT_LINEBREAK", None),
    ]
    for name, charset in pairs:
        const = _get(name)
        if const is not None:
            _CATEGORY_CHAR_SETS[const] = charset

    _CATEGORY_SETS_BUILT = True


def regex_risks(pattern: str, flags: int = 0) -> list[str]:
    """Detect nested/nullable/ambiguous backtracking risks in a regex pattern.

    Returns a sorted list of human-readable risk descriptions, or ``[]`` if the
    pattern is clean.  Checks:

    - nested backtracking quantifiers (e.g. ``(a+)+``)
    - repeated subpattern that can match empty (e.g. ``(a?)+``)
    - ambiguous alternation inside unbounded repeat (e.g. ``(a|ab)*``)
    - backreferences or conditional groups

    Source: ChatGPT Pro draft, 2026-05-29.
    """
    try:
        from re import _parser as sre_parse  # type: ignore[attr-defined]  # ty: ignore[unresolved-import]
    except ImportError:
        import sre_parse  # type: ignore[no-redef]  # older Python fallback

    MAXREPEAT = sre_parse.MAXREPEAT
    MAX_REPEAT = sre_parse.MAX_REPEAT
    MIN_REPEAT = sre_parse.MIN_REPEAT
    SUBPATTERN = sre_parse.SUBPATTERN
    BRANCH = sre_parse.BRANCH
    ASSERT = sre_parse.ASSERT
    ASSERT_NOT = sre_parse.ASSERT_NOT
    AT = sre_parse.AT
    LITERAL = sre_parse.LITERAL
    NOT_LITERAL = sre_parse.NOT_LITERAL
    IN = sre_parse.IN
    ANY = sre_parse.ANY
    RANGE = sre_parse.RANGE
    CATEGORY = sre_parse.CATEGORY
    GROUPREF = sre_parse.GROUPREF
    GROUPREF_EXISTS = sre_parse.GROUPREF_EXISTS

    POSSESSIVE_REPEAT = getattr(sre_parse, "POSSESSIVE_REPEAT", None)
    ATOMIC_GROUP = getattr(sre_parse, "ATOMIC_GROUP", None)

    BACKTRACKING_REPEATS = {MAX_REPEAT, MIN_REPEAT}
    ALL_REPEATS = set(BACKTRACKING_REPEATS)
    if POSSESSIVE_REPEAT is not None:
        ALL_REPEATS.add(POSSESSIVE_REPEAT)

    def seq(x):  # type: ignore[no-untyped-def]
        return getattr(x, "data", x)

    def walk(sub):  # type: ignore[no-untyped-def]
        for op, arg in seq(sub):
            yield op, arg
            if op == SUBPATTERN:
                yield from walk(arg[-1])
            elif op == BRANCH:
                for branch in arg[1]:
                    yield from walk(branch)
            elif op in ALL_REPEATS:
                yield from walk(arg[2])
            elif op == ATOMIC_GROUP:
                yield from walk(arg)
            elif op in (ASSERT, ASSERT_NOT):
                yield from walk(arg[1])
            elif op == GROUPREF_EXISTS:
                yield from walk(arg[1])
                if arg[2] is not None:
                    yield from walk(arg[2])

    def nullable(sub):  # type: ignore[no-untyped-def]
        for op, arg in seq(sub):
            if op == AT:
                continue
            if op == SUBPATTERN:
                if not nullable(arg[-1]):
                    return False
            elif op == BRANCH:
                if not any(nullable(branch) for branch in arg[1]):
                    return False
            elif op in ALL_REPEATS:
                min_, _max, body = arg
                if min_ == 0:
                    continue
                if not nullable(body):
                    return False
            elif op in (ASSERT, ASSERT_NOT):
                continue
            elif op == GROUPREF_EXISTS:
                yes = nullable(arg[1])
                no = True if arg[2] is None else nullable(arg[2])
                if not (yes or no):
                    return False
            else:
                return False
        return True

    def has_backtracking_repeat(sub):  # type: ignore[no-untyped-def]
        return any(op in BACKTRACKING_REPEATS for op, _ in walk(sub))

    def first_chars(sub):  # type: ignore[no-untyped-def]
        _build_category_char_sets()
        out: set[int] = set()
        for op, arg in seq(sub):
            if op == AT or op in (ASSERT, ASSERT_NOT):
                continue
            if op == LITERAL:
                out.add(arg)
                return False, out
            if op in (NOT_LITERAL, ANY):
                return False, None
            if op == CATEGORY:
                cat_chars = _CATEGORY_CHAR_SETS.get(arg)
                return False, set(cat_chars) if cat_chars is not None else None
            if op == IN:
                chars: set[int] = set()
                known = True
                for iop, iarg in arg:
                    if iop == LITERAL:
                        chars.add(iarg)
                    elif iop == RANGE:
                        lo, hi = iarg
                        if hi - lo <= 256:
                            chars.update(range(lo, hi + 1))
                        else:
                            known = False
                    elif iop == CATEGORY:
                        cat_chars = _CATEGORY_CHAR_SETS.get(iarg)
                        if cat_chars is not None:
                            chars.update(cat_chars)
                        else:
                            known = False
                    else:
                        known = False
                return False, chars if known else None
            if op == SUBPATTERN:
                n, s = first_chars(arg[-1])
            elif op == BRANCH:
                nullable_any = False
                chars2: set[int] = set()
                known2 = True
                for branch in arg[1]:
                    bn, bs = first_chars(branch)
                    nullable_any = nullable_any or bn
                    if bs is None:
                        known2 = False
                    elif known2:
                        chars2.update(bs)
                return nullable_any, chars2 if known2 else None
            elif op in ALL_REPEATS:
                min_, _max, body = arg
                n, s = first_chars(body)
                return (min_ == 0) or n, s
            else:
                return True, None
            if s is None:
                return n, None
            out.update(s)
            if not n:
                return False, out
        return True, out

    def ambiguous_branch_inside_repeat(sub):  # type: ignore[no-untyped-def]
        for op, arg in walk(sub):
            if op == BRANCH:
                seen: set[int] = set()
                for branch in arg[1]:
                    _n, s = first_chars(branch)
                    if _n or s is None or (seen & s):
                        return True
                    seen |= s
        return False

    tree = sre_parse.parse(pattern, flags)
    risks: list[str] = []

    for op, arg in walk(tree):
        if op in (GROUPREF, GROUPREF_EXISTS):
            risks.append("uses backreferences or conditional groups")
        if op in BACKTRACKING_REPEATS:
            _min, max_, body = arg
            if nullable(body):
                risks.append("repeats a subpattern that can match empty")
            if has_backtracking_repeat(body):
                risks.append("has nested backtracking quantifiers")
            if max_ == MAXREPEAT and ambiguous_branch_inside_repeat(body):
                risks.append("has ambiguous alternation inside an unbounded repeat")

    return sorted(set(risks))


def adjacent_repeat_risks(pattern: str, flags: int = 0) -> list[str]:
    """Detect adjacent variable backtracking repeats with overlapping first-char sets.

    This catches the LawVM-specific bug class: patterns like ``.+.+``,
    ``(?:.+)(?:.+)``, ``[a-z]+[a-z]+``, etc. where two adjacent unbounded
    (or variable-length) backtracking repeats can consume the same characters,
    causing catastrophic backtracking.

    Conservative note: CATEGORY escapes (``\\d``, ``\\w``, ``\\s``) are treated as
    "unknown" first-char sets and will trigger this check even when the CATEGORY
    and its neighbor are disjoint in practice (e.g. ``\\d+[a-z]*``).  The gate
    test allowlist covers all pre-existing patterns of this type.

    Source: ChatGPT Pro draft, 2026-05-29.
    """
    try:
        from re import _parser as sre  # type: ignore[attr-defined]  # ty: ignore[unresolved-import]
    except ImportError:
        import sre_parse as sre  # type: ignore[no-redef]

    MAXREPEAT = sre.MAXREPEAT
    BACKTRACKING_REPEAT = {sre.MAX_REPEAT, sre.MIN_REPEAT}

    POSSESSIVE_REPEAT = getattr(sre, "POSSESSIVE_REPEAT", None)
    ALL_REPEAT = set(BACKTRACKING_REPEAT)
    if POSSESSIVE_REPEAT is not None:
        ALL_REPEAT.add(POSSESSIVE_REPEAT)

    ATOMIC_GROUP = getattr(sre, "ATOMIC_GROUP", None)
    ANY_ALL = getattr(sre, "ANY_ALL", None)

    def data(sub):  # type: ignore[no-untyped-def]
        return getattr(sub, "data", sub)

    def first_chars(sub):  # type: ignore[no-untyped-def]
        _build_category_char_sets()
        out: set[int] = set()
        for op, arg in data(sub):
            if op in (sre.AT, sre.ASSERT, sre.ASSERT_NOT):
                continue
            if op == sre.LITERAL:
                out.add(arg)
                return False, out
            if op in (sre.NOT_LITERAL, sre.ANY) or (
                ANY_ALL is not None and op == ANY_ALL
            ):
                return False, None
            if op == sre.CATEGORY:
                cat_chars = _CATEGORY_CHAR_SETS.get(arg)
                return False, set(cat_chars) if cat_chars is not None else None
            if op == sre.IN:
                chars: set[int] = set()
                known = True
                for iop, iarg in arg:
                    if iop == sre.NEGATE:
                        return False, None
                    if iop == sre.LITERAL:
                        chars.add(iarg)
                    elif iop == sre.RANGE:
                        lo, hi = iarg
                        if hi - lo > 512:
                            known = False
                        else:
                            chars.update(range(lo, hi + 1))
                    elif iop == sre.CATEGORY:
                        cat_chars = _CATEGORY_CHAR_SETS.get(iarg)
                        if cat_chars is not None:
                            chars.update(cat_chars)
                        else:
                            known = False
                    else:
                        known = False
                return False, chars if known else None
            if op == sre.SUBPATTERN:
                nullable, chars2 = first_chars(arg[-1])
            elif op == sre.BRANCH:
                nullable_any = False
                chars3: set[int] = set()
                for branch in arg[1]:
                    b_nullable, b_chars = first_chars(branch)
                    nullable_any = nullable_any or b_nullable
                    if b_chars is None:
                        return nullable_any, None
                    chars3.update(b_chars)
                return nullable_any, chars3
            elif op in ALL_REPEAT:
                lo, _hi, body = arg
                nullable, chars2 = first_chars(body)
                return (lo == 0) or nullable, chars2
            elif ATOMIC_GROUP is not None and op == ATOMIC_GROUP:
                nullable, chars2 = first_chars(arg)
            else:
                return False, None
            if chars2 is None:
                return nullable, None
            out.update(chars2)
            if not nullable:
                return False, out
        return True, out

    def flatten_concat(sub):  # type: ignore[no-untyped-def]
        flat = []
        for op, arg in data(sub):
            if op == sre.SUBPATTERN:
                flat.extend(flatten_concat(arg[-1]))
            else:
                flat.append((op, arg))
        return flat

    def is_zero_width(tok):  # type: ignore[no-untyped-def]
        op, _arg = tok
        return op in (sre.AT, sre.ASSERT, sre.ASSERT_NOT)

    def repeat_sig(tok):  # type: ignore[no-untyped-def]
        op, arg = tok
        if op not in BACKTRACKING_REPEAT:
            return None
        lo, hi, body = arg
        if lo == hi:
            return None
        _nullable, chars = first_chars(body)
        return {"lo": lo, "hi": hi, "first": chars}

    def overlaps(a, b):  # type: ignore[no-untyped-def]
        return a is None or b is None or bool(a & b)

    tree = sre.parse(pattern, flags)
    risks: list[str] = []

    def scan(sub, where: str = "$") -> None:  # type: ignore[no-untyped-def]
        flat = [
            (i, tok)
            for i, tok in enumerate(flatten_concat(sub))
            if not is_zero_width(tok)
        ]
        for (i, left), (j, right) in zip(flat, flat[1:], strict=False):
            lsig = repeat_sig(left)
            rsig = repeat_sig(right)
            if not lsig or not rsig:
                continue
            if not overlaps(lsig["first"], rsig["first"]):
                continue
            if lsig["hi"] == MAXREPEAT or rsig["hi"] == MAXREPEAT:
                risks.append(
                    f"{where}: adjacent variable backtracking repeats "
                    f"at items {i},{j} have overlapping starts"
                )
        for idx, (op, arg) in enumerate(data(sub)):
            child_where = f"{where}/{idx}:{op}"
            if op == sre.SUBPATTERN:
                scan(arg[-1], child_where)
            elif op == sre.BRANCH:
                for bidx, branch in enumerate(arg[1]):
                    scan(branch, f"{child_where}|{bidx}")
            elif op in ALL_REPEAT:
                scan(arg[2], child_where)
            elif ATOMIC_GROUP is not None and op == ATOMIC_GROUP:
                scan(arg, child_where)
            elif op in (sre.ASSERT, sre.ASSERT_NOT):
                scan(arg[1], child_where)

    scan(tree)
    return sorted(set(risks))


def lawvm_regex_risks(pattern: str, flags: int = 0) -> list[str]:
    """Combined check: ``regex_risks()`` + ``adjacent_repeat_risks()``.

    Returns a sorted, deduplicated list of risk strings.  Returns ``[]`` if the
    pattern passes both checks.

    This is the primary public API for the CI gate.
    """
    risks = []
    risks.extend(regex_risks(pattern, flags))
    risks.extend(adjacent_repeat_risks(pattern, flags))
    return sorted(set(risks))


def safe_compile_classifier(
    pattern: str, flags: int = 0, *, classifier_id: str = "<unknown>"
) -> re.Pattern[str]:
    """Compile a regex pattern, raising ``ValueError`` if it has known risks.

    Intended for use in module-scope classifier definitions during development
    to catch unsafe patterns at import time.  In production, the CI gate
    (``tests/test_regex_perf_gate.py``) provides the same check via AST scan
    without requiring code changes.

    Args:
        pattern: The regex pattern string.
        flags: Optional ``re`` flags.
        classifier_id: Human-readable name for error messages.

    Raises:
        ValueError: If ``lawvm_regex_risks()`` returns any risks.

    Returns:
        A compiled ``re.Pattern``.
    """
    risks = lawvm_regex_risks(pattern, flags)
    if risks:
        joined = "\n  - ".join(risks)
        raise ValueError(
            f"unsafe classifier regex {classifier_id}: {pattern!r}\n"
            f"  - {joined}"
        )
    return re.compile(pattern, flags)
