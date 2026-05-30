"""Regex safety lint and sound prefilter for LawVM classifier patterns.

Purpose:
    Static AST-based lint for module-scope ``_*_RE`` / ``_*_PATTERN`` constants
    in ``src/lawvm/``.  Catches catastrophic-backtracking regex patterns before
    they reach production.  This is a CI lint only — not a runtime monkey-patch.

    Also provides ``compile_classifier_regex`` and ``build_regex_prefilter`` for
    classifier patterns that benefit from a sound necessary-condition prefilter.

Reference: AGENTS.md §1.11, §1.13 (Hot-path performance discipline; regex vs bespoke).
Used by: ``tests/test_regex_perf_gate.py``.

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

CATEGORY first-char sets (A18; flag-aware hardening 2026-05-30):
    ``first_chars()`` resolves the standard CATEGORY escapes (``\\d``, ``\\w``,
    ``\\s``) to ASCII code-point sets.  Because Python 3 str patterns default to
    Unicode semantics, that ASCII set *under-approximates* the real membership
    (``\\w`` matches ``ä``; ``\\d`` matches non-ASCII digits) unless ``re.ASCII``
    is set.  ``_resolve_category()`` therefore tags such a set as a Unicode
    approximation, and the overlap test treats it as overlapping whenever the
    *other* operand carries a non-ASCII char — so ``\\w+[ä]+`` is correctly
    flagged while genuinely-disjoint pairs like ``\\d``/``\\s`` are not.

    Under ``re.IGNORECASE`` literal/range char sets are widened to their case
    variants (``_case_expand``), so ``[a-z]+[A-Z]+`` and ``a+A+`` are flagged.
    Flags are threaded per-subpattern, so scoped inline flags like ``(?i:a+)A+``
    resolve correctly.

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
from functools import lru_cache
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

# Per-literal IGNORECASE regex cache, bounded so the wrapper stays well-behaved
# if extracted from LawVM's finite owned-pattern corpus into a general library.
@lru_cache(maxsize=4096)
def _get_ic_pattern(text: str, flags: int) -> re.Pattern[str]:
    """Return a cached tiny regex for IGNORECASE literal search."""
    return re.compile(re.escape(text), flags & re.IGNORECASE)


class PrefilteredPattern:
    """Wraps a compiled ``re.Pattern`` with a sound prefilter plan.

    Before calling the full regex engine, ``search``/``match``/``fullmatch``/
    ``finditer``/``findall`` evaluate the plan against the text.  If the plan
    fails the call is short-circuited and the appropriate empty value returned.
    Only these five lookup methods are prefiltered; ``sub``/``subn``/``split``
    and any other attribute access delegate straight to the underlying pattern
    (``__getattr__``) and bypass the prefilter.

    The plan is a NECESSARY CONDITION only — it never produces false negatives.
    False positives (plan passes but regex doesn't match) are fine.

    Attributes mirror ``re.Pattern``: ``pattern``, ``flags``, ``groups``,
    ``groupindex``.  Unknown attribute access delegates to the underlying
    pattern object via ``__getattr__``.
    """

    def __init__(
        self,
        rx: "re.Pattern[str]",
        plan: Any,
        stats: "RegexPrefilterStats | None" = None,
    ) -> None:
        self._rx = rx
        self._plan = plan
        self._stats = stats
        # Expose the standard Pattern attributes directly.
        self.pattern: str = rx.pattern
        self.flags: int = rx.flags
        self.groups: int = rx.groups
        self.groupindex: dict[str, int] = dict(rx.groupindex)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._rx, name)

    def __repr__(self) -> str:
        return f"PrefilteredPattern({self._rx!r}, plan={self._plan!r})"

    @staticmethod
    def _bounds(string: str, pos: int, endpos: int | None) -> tuple[int, int]:
        """Clamp pos/endpos the way ``re.Pattern`` does (to ``[0, len]``).

        ``str.find`` interprets negative bounds as slice-style indices, which
        diverges from ``re``'s positional clamping and could otherwise let the
        prefilter scan a different region than the engine — a false-negative
        risk.  Normalizing both to ``re`` semantics keeps the prefilter region
        identical to the region the regex will actually search.
        """
        n = len(string)
        if pos < 0:
            pos = 0
        elif pos > n:
            pos = n
        if endpos is None or endpos > n:
            endpos = n
        elif endpos < 0:
            endpos = 0
        return pos, endpos

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

    def _prefilter_ok(self, string: str, pos: int, endpos: int | None) -> tuple[bool, int, int]:
        """Normalize bounds, evaluate the plan once, and record telemetry."""
        npos, nep = self._bounds(string, pos, endpos)
        passed = self._plan_passes(self._plan, string, npos, nep)
        if self._stats is not None:
            self._stats.checked += 1
            if passed:
                self._stats.passed += 1
            else:
                self._stats.rejected += 1
        return passed, npos, nep

    def search(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "re.Match[str] | None":
        ok, pos, ep = self._prefilter_ok(string, pos, endpos)
        if not ok:
            return None
        return self._rx.search(string, pos, ep)

    def match(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "re.Match[str] | None":
        ok, pos, ep = self._prefilter_ok(string, pos, endpos)
        if not ok:
            return None
        return self._rx.match(string, pos, ep)

    def fullmatch(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "re.Match[str] | None":
        ok, pos, ep = self._prefilter_ok(string, pos, endpos)
        if not ok:
            return None
        return self._rx.fullmatch(string, pos, ep)

    def finditer(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> "Iterable[re.Match[str]]":
        ok, pos, ep = self._prefilter_ok(string, pos, endpos)
        if not ok:
            return iter([])
        return self._rx.finditer(string, pos, ep)

    def findall(
        self,
        string: str,
        pos: int = 0,
        endpos: int | None = None,
    ) -> list[Any]:
        ok, pos, ep = self._prefilter_ok(string, pos, endpos)
        if not ok:
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
    stats = _PREFILTER_TELEMETRY.setdefault(classifier_id, RegexPrefilterStats())
    return PrefilteredPattern(rx, plan, stats)


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
# _CATEGORY_CHAR_SETS values are frozensets of ASCII code-points the category
# matches; NOT-* variants map to None (too broad to enumerate).
# _CATEGORY_TESTERS maps each positive category to a compiled Unicode regex that
# matches exactly one char of that category, so the overlap test can decide
# membership of a specific non-ASCII char precisely (\\d does NOT match ``ä``;
# \\w does) instead of conservatively assuming every non-ASCII char overlaps.
# ---------------------------------------------------------------------------
_CATEGORY_CHAR_SETS: dict[object, frozenset[int] | None] = {}
_CATEGORY_TESTERS: dict[object, re.Pattern[str] | None] = {}
_CATEGORY_SETS_BUILT = False


def _build_category_char_sets() -> None:
    """Populate _CATEGORY_CHAR_SETS / _CATEGORY_TESTERS on first call.  Idempotent."""
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

    # Unicode membership testers: match exactly one char of the category.
    _t_word = re.compile(r"\w")
    _t_digit = re.compile(r"\d")
    _t_space = re.compile(r"\s")
    _t_linebreak = re.compile("[\n\r\x0b\x0c\x1c\x1d\x1e\x85  ]")

    def _get(name: str) -> object | None:
        return getattr(_constants, name, None)

    pairs: list[tuple[str, frozenset[int] | None, re.Pattern[str] | None]] = [
        # Positive sets (ascii charset, unicode membership tester)
        ("CATEGORY_DIGIT", _digits, _t_digit),
        ("CATEGORY_UNI_DIGIT", _digits, _t_digit),
        ("CATEGORY_SPACE", _spaces, _t_space),
        ("CATEGORY_UNI_SPACE", _spaces, _t_space),
        ("CATEGORY_WORD", _word, _t_word),
        ("CATEGORY_UNI_WORD", _word, _t_word),
        ("CATEGORY_LOC_WORD", _word, _t_word),
        ("CATEGORY_LINEBREAK", _linebreak, _t_linebreak),
        ("CATEGORY_UNI_LINEBREAK", _linebreak, _t_linebreak),
        # NOT-* variants: too broad — leave as None (conservative)
        ("CATEGORY_NOT_DIGIT", None, None),
        ("CATEGORY_UNI_NOT_DIGIT", None, None),
        ("CATEGORY_NOT_SPACE", None, None),
        ("CATEGORY_UNI_NOT_SPACE", None, None),
        ("CATEGORY_NOT_WORD", None, None),
        ("CATEGORY_UNI_NOT_WORD", None, None),
        ("CATEGORY_LOC_NOT_WORD", None, None),
        ("CATEGORY_NOT_LINEBREAK", None, None),
        ("CATEGORY_UNI_NOT_LINEBREAK", None, None),
    ]
    for name, charset, tester in pairs:
        const = _get(name)
        if const is not None:
            _CATEGORY_CHAR_SETS[const] = charset
            _CATEGORY_TESTERS[const] = tester

    _CATEGORY_SETS_BUILT = True


def _case_expand(chars: set[int], flags: int) -> set[int]:
    """Under IGNORECASE, widen a char set to include its case variants.

    Sound for the overlap test: adding more code-points can only make two sets
    *more* likely to be judged overlapping (a conservative direction in a safety
    gate).  Handles ASCII and simple single-char Unicode folds; multi-char folds
    (e.g. ``ß`` → ``ss``) are skipped, which is safe for first-char analysis.
    """
    if not (flags & re.IGNORECASE):
        return set(chars)
    out = set(chars)
    for c in chars:
        ch = chr(c)
        for variant in (ch.lower(), ch.upper()):
            if len(variant) == 1:
                out.add(ord(variant))
    return out


def _resolve_category(const: object, flags: int) -> tuple[set[int] | None, "re.Pattern[str] | None"]:
    """Resolve a CATEGORY constant to (ascii_charset, unicode_membership_tester).

    The ASCII charset is the slice of the category within ASCII.  Without
    ``re.ASCII`` the real (Unicode) membership is larger — ``\\w`` matches ``ä``,
    ``\\d`` matches non-ASCII digits — so the second element is a tester that
    decides whether a *specific* non-ASCII char belongs to the category.  The
    overlap test uses it to flag ``\\w`` vs ``[ä]`` while leaving ``\\d`` vs
    ``[ä]`` (a digit class vs a letter) correctly disjoint.  Under ``re.ASCII``
    the ASCII set is exact, so the tester is ``None``.
    """
    _build_category_char_sets()
    cs = _CATEGORY_CHAR_SETS.get(const)
    if cs is None:
        return None, None
    tester = None if (flags & re.ASCII) else _CATEGORY_TESTERS.get(const)
    return set(cs), tester


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
        # Do NOT descend into ATOMIC_GROUP: an atomic group discards its inner
        # backtracking points, so (?>a+)+ is not a nested-backtracking risk.
        def walk_no_atomic(s):  # type: ignore[no-untyped-def]
            for op, arg in seq(s):
                if op == ATOMIC_GROUP:
                    continue
                yield op, arg
                if op == SUBPATTERN:
                    yield from walk_no_atomic(arg[-1])
                elif op == BRANCH:
                    for branch in arg[1]:
                        yield from walk_no_atomic(branch)
                elif op in ALL_REPEATS:
                    yield from walk_no_atomic(arg[2])
                elif op in (ASSERT, ASSERT_NOT):
                    yield from walk_no_atomic(arg[1])
                elif op == GROUPREF_EXISTS:
                    yield from walk_no_atomic(arg[1])
                    if arg[2] is not None:
                        yield from walk_no_atomic(arg[2])

        return any(op in BACKTRACKING_REPEATS for op, _ in walk_no_atomic(sub))

    def first_chars(sub, cur_flags):  # type: ignore[no-untyped-def]
        """Return (nullable, charset_or_None, testers) — see adjacent_repeat_risks."""
        out: set[int] = set()
        testers: set[Any] = set()
        for op, arg in seq(sub):
            if op == AT or op in (ASSERT, ASSERT_NOT):
                continue
            if op == LITERAL:
                out.update(_case_expand({arg}, cur_flags))
                return False, out, testers
            if op in (NOT_LITERAL, ANY):
                return False, None, set()
            if op == CATEGORY:
                cat_chars, tester = _resolve_category(arg, cur_flags)
                if cat_chars is None:
                    return False, None, set()
                return False, cat_chars, ({tester} if tester is not None else set())
            if op == IN:
                chars: set[int] = set()
                known = True
                in_testers: set[Any] = set()
                for iop, iarg in arg:
                    if iop == LITERAL:
                        chars.update(_case_expand({iarg}, cur_flags))
                    elif iop == RANGE:
                        lo, hi = iarg
                        if hi - lo <= 256:
                            chars.update(_case_expand(set(range(lo, hi + 1)), cur_flags))
                        else:
                            known = False
                    elif iop == CATEGORY:
                        cat_chars, tester = _resolve_category(iarg, cur_flags)
                        if cat_chars is not None:
                            chars.update(cat_chars)
                            if tester is not None:
                                in_testers.add(tester)
                        else:
                            known = False
                    else:
                        known = False
                return False, (chars if known else None), in_testers
            if op == SUBPATTERN:
                _g, add_f, del_f, child = arg
                n, s, t = first_chars(child, (cur_flags | add_f) & ~del_f)
            elif op == BRANCH:
                nullable_any = False
                chars2: set[int] = set()
                known2 = True
                br_testers: set[Any] = set()
                for branch in arg[1]:
                    bn, bs, bt = first_chars(branch, cur_flags)
                    nullable_any = nullable_any or bn
                    if bs is None:
                        known2 = False
                    elif known2:
                        chars2.update(bs)
                        br_testers |= bt
                return nullable_any, (chars2 if known2 else None), br_testers
            elif op in ALL_REPEATS:
                min_, _max, body = arg
                n, s, t = first_chars(body, cur_flags)
                return (min_ == 0) or n, s, t
            else:
                return True, None, set()
            if s is None:
                return n, None, set()
            out.update(s)
            testers |= t
            if not n:
                return False, out, testers
        return True, out, testers

    def _branch_overlap(seen, seen_testers, s, s_testers):  # type: ignore[no-untyped-def]
        if seen & s:
            return True
        if any(t.match(chr(c)) for t in seen_testers for c in s if c > 127):
            return True
        if any(t.match(chr(c)) for t in s_testers for c in seen if c > 127):
            return True
        return False

    def ambiguous_branch_inside_repeat(sub, cur_flags):  # type: ignore[no-untyped-def]
        for op, arg in walk(sub):
            if op == BRANCH:
                seen: set[int] = set()
                seen_testers: set[Any] = set()
                for branch in arg[1]:
                    _n, s, t = first_chars(branch, cur_flags)
                    if _n or s is None or _branch_overlap(seen, seen_testers, s, t):
                        return True
                    seen |= s
                    seen_testers |= t
        return False

    tree = sre_parse.parse(pattern, flags)
    eff_flags = tree.state.flags
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
            if max_ == MAXREPEAT and ambiguous_branch_inside_repeat(body, eff_flags):
                risks.append("has ambiguous alternation inside an unbounded repeat")

    return sorted(set(risks))


def adjacent_repeat_risks(pattern: str, flags: int = 0) -> list[str]:
    """Detect adjacent variable backtracking repeats with overlapping first-char sets.

    This catches the LawVM-specific bug class: patterns like ``.+.+``,
    ``(?:.+)(?:.+)``, ``[a-z]+[a-z]+``, etc. where two variable backtracking
    repeats — adjacent, or separated only by nullable material like ``a+\\s*a+``
    — can consume the same characters, causing catastrophic backtracking.

    First-char analysis is flag-aware:

    - Under ``IGNORECASE`` literal/range char sets are widened to their case
      variants, so ``[a-z]+[A-Z]+`` and ``a+A+`` are correctly flagged.
    - CATEGORY escapes (``\\d``, ``\\w``, ``\\s``) resolve to ASCII sets.  Under
      Unicode semantics (no ``re.ASCII``) that set under-approximates the real
      membership, so a category is treated as overlapping when the *other*
      operand carries a non-ASCII char (the ``\\w`` vs ``[ä]`` case).  Category
      sets still distinguish genuinely-disjoint pairs like ``\\d``/``\\s``.

    Conservative direction: unknown first-char sets (``.``, ``\\D``, negated
    classes) are treated as overlapping.  False positives are acceptable in the
    gate; false negatives are not.

    Source: ChatGPT Pro draft, 2026-05-29; soundness hardening 2026-05-30.
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

    def first_chars(sub, cur_flags):  # type: ignore[no-untyped-def]
        """Return (nullable, charset_or_None, testers).

        ``testers`` is a set of Unicode membership testers for any categories in
        the charset; the charset holds only the ASCII slice, so a tester decides
        whether a specific non-ASCII char belongs (\\w matches ``ä``, \\d does not).
        """
        out: set[int] = set()
        testers: set[Any] = set()
        for op, arg in data(sub):
            if op in (sre.AT, sre.ASSERT, sre.ASSERT_NOT):
                continue
            if op == sre.LITERAL:
                out.update(_case_expand({arg}, cur_flags))
                return False, out, testers
            if op in (sre.NOT_LITERAL, sre.ANY) or (
                ANY_ALL is not None and op == ANY_ALL
            ):
                return False, None, set()
            if op == sre.CATEGORY:
                cat_chars, tester = _resolve_category(arg, cur_flags)
                if cat_chars is None:
                    return False, None, set()
                return False, cat_chars, ({tester} if tester is not None else set())
            if op == sre.IN:
                chars: set[int] = set()
                known = True
                in_testers: set[Any] = set()
                for iop, iarg in arg:
                    if iop == sre.NEGATE:
                        return False, None, set()
                    if iop == sre.LITERAL:
                        chars.update(_case_expand({iarg}, cur_flags))
                    elif iop == sre.RANGE:
                        lo, hi = iarg
                        if hi - lo > 512:
                            known = False
                        else:
                            chars.update(_case_expand(set(range(lo, hi + 1)), cur_flags))
                    elif iop == sre.CATEGORY:
                        cat_chars, tester = _resolve_category(iarg, cur_flags)
                        if cat_chars is not None:
                            chars.update(cat_chars)
                            if tester is not None:
                                in_testers.add(tester)
                        else:
                            known = False
                    else:
                        known = False
                return False, (chars if known else None), in_testers
            if op == sre.SUBPATTERN:
                _g, add_f, del_f, child = arg
                nullable, chars2, t2 = first_chars(child, (cur_flags | add_f) & ~del_f)
            elif op == sre.BRANCH:
                nullable_any = False
                chars3: set[int] = set()
                br_testers: set[Any] = set()
                for branch in arg[1]:
                    b_nullable, b_chars, b_testers = first_chars(branch, cur_flags)
                    nullable_any = nullable_any or b_nullable
                    if b_chars is None:
                        return nullable_any, None, set()
                    chars3.update(b_chars)
                    br_testers |= b_testers
                return nullable_any, chars3, br_testers
            elif op in ALL_REPEAT:
                lo, _hi, body = arg
                nullable, chars2, t2 = first_chars(body, cur_flags)
                return (lo == 0) or nullable, chars2, t2
            elif ATOMIC_GROUP is not None and op == ATOMIC_GROUP:
                nullable, chars2, t2 = first_chars(arg, cur_flags)
            else:
                return False, None, set()
            if chars2 is None:
                return nullable, None, set()
            out.update(chars2)
            testers |= t2
            if not nullable:
                return False, out, testers
        return True, out, testers

    def flatten_concat(sub, cur_flags):  # type: ignore[no-untyped-def]
        """Flatten bare subpatterns into a token list, carrying effective flags.

        Carrying per-token flags is what lets ``(?i:a+)A+`` resolve each repeat
        under the flags actually in force at that position.
        """
        flat = []
        for op, arg in data(sub):
            if op == sre.SUBPATTERN:
                _g, add_f, del_f, child = arg
                flat.extend(flatten_concat(child, (cur_flags | add_f) & ~del_f))
            else:
                flat.append((op, arg, cur_flags))
        return flat

    def is_zero_width(tok):  # type: ignore[no-untyped-def]
        return tok[0] in (sre.AT, sre.ASSERT, sre.ASSERT_NOT)

    def nullable_token(tok):  # type: ignore[no-untyped-def]
        """Conservatively, only ``lo == 0`` repeats can match empty.

        Returning False for anything else stops the look-ahead at the first
        token that must consume input, which avoids excess false positives.
        """
        op, arg, _flags = tok
        return op in ALL_REPEAT and arg[0] == 0

    def repeat_sig(tok):  # type: ignore[no-untyped-def]
        op, arg, tflags = tok
        if op not in BACKTRACKING_REPEAT:
            return None
        lo, hi, body = arg
        if lo == hi:
            return None
        _nullable, chars, testers = first_chars(body, tflags)
        return {"lo": lo, "hi": hi, "first": chars, "testers": testers}

    def overlaps(a, b):  # type: ignore[no-untyped-def]
        ca, cb = a["first"], b["first"]
        if ca is None or cb is None:
            return True
        if ca & cb:
            return True
        # ASCII intersection is exact; a Unicode category can additionally
        # overlap the other operand outside ASCII iff some non-ASCII char there
        # actually belongs to the category (the \\w vs [ä] case, but not \\d).
        if any(t.match(chr(c)) for t in a["testers"] for c in cb if c > 127):
            return True
        if any(t.match(chr(c)) for t in b["testers"] for c in ca if c > 127):
            return True
        return False

    tree = sre.parse(pattern, flags)
    risks: list[str] = []

    def scan(sub, cur_flags, where: str = "$") -> None:  # type: ignore[no-untyped-def]
        flat = [tok for tok in flatten_concat(sub, cur_flags) if not is_zero_width(tok)]
        n = len(flat)
        for i in range(n):
            lsig = repeat_sig(flat[i])
            if not lsig:
                continue
            # Look ahead across nullable tokens: an intervening repeat that can
            # match empty (\\s*, ,?) still leaves the two variable repeats sharing
            # a split boundary, so a+\\s*a+ is the same bug as a+a+.
            k = i + 1
            while k < n:
                rtok = flat[k]
                rsig = repeat_sig(rtok)
                if (
                    rsig
                    and (lsig["hi"] == MAXREPEAT or rsig["hi"] == MAXREPEAT)
                    and overlaps(lsig, rsig)
                ):
                    risks.append(
                        f"{where}: adjacent variable backtracking repeats "
                        f"at items {i},{k} have overlapping starts"
                    )
                    break
                if not nullable_token(rtok):
                    break
                k += 1
        for idx, (op, arg) in enumerate(data(sub)):
            child_where = f"{where}/{idx}:{op}"
            if op == sre.SUBPATTERN:
                _g, add_f, del_f, child = arg
                scan(child, (cur_flags | add_f) & ~del_f, child_where)
            elif op == sre.BRANCH:
                for bidx, branch in enumerate(arg[1]):
                    scan(branch, cur_flags, f"{child_where}|{bidx}")
            elif op in ALL_REPEAT:
                scan(arg[2], cur_flags, child_where)
            elif ATOMIC_GROUP is not None and op == ATOMIC_GROUP:
                scan(arg, cur_flags, child_where)
            elif op in (sre.ASSERT, sre.ASSERT_NOT):
                scan(arg[1], cur_flags, child_where)

    scan(tree, tree.state.flags)
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
