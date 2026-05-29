"""Regex safety lint for LawVM classifier patterns.

Purpose:
    Static AST-based lint for module-scope ``_*_RE`` / ``_*_PATTERN`` constants
    in ``src/lawvm/``.  Catches catastrophic-backtracking regex patterns before
    they reach production.  This is a CI lint only — not a runtime monkey-patch.

Reference: AGENTS.md §1.11 (Hot-path performance discipline).
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
"""

from __future__ import annotations

import re

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
