"""combinators — the transactional parser substrate (legally dumb).

This is the architecture-neutral plumbing the rewritten parser is built on: a
cursor over the filtered token stream, a transactional ``ParseResult`` that makes
backtracking explicit (replacing the old parser's 472 manual
``save()``/``restore()``/``pos +=`` sites), and the standard combinators
(``seq``/``choice``/``optional``/``many``/``commit``).

It knows NOTHING about Finnish, sections, verbs, or witnesses. It only knows
tokens, positions, spans, success/failure, and diagnostics. The grammar's legal
meaning lives entirely in the recognizers and the discourse transducer built on
top of this.

Design (per the rewrite contract):
  * ``ParseResult[T]`` is either a success (value, next cursor, committed flag,
    diagnostics) or a failure (expected, position, committed flag, diagnostics).
  * A RECOVERABLE failure rewinds — the caller may try an alternative.
  * A COMMITTED failure does NOT rewind — once a production has committed (passed
    a ``commit`` point), failing is a hard error that propagates, so we get
    precise "expected X at position N" diagnostics instead of a silent backtrack
    that loses the whole clause. This is the substrate-level "no silent drop".
  * Diagnostics survive backtracking: a recoverable failure still carries the
    furthest-reach expectation so the caller can report the best error.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence, TypeVar, cast

from lawvm.finland.johtolause.lexicon import Token

T = TypeVar("T")
U = TypeVar("U")


# ---------------------------------------------------------------------------
# Span — a half-open token-index range, the unit of provenance.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Span:
    """A half-open [start, end) range of token indices in the filtered stream."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"Span end {self.end} < start {self.start}")

    @property
    def is_empty(self) -> bool:
        return self.end == self.start

    def join(self, other: "Span") -> "Span":
        """The smallest span covering both (ignoring empty spans)."""
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        return Span(min(self.start, other.start), max(self.end, other.end))


# ---------------------------------------------------------------------------
# Diagnostic — a furthest-reach expectation, for precise error reporting.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Expectation:
    """What a production expected, and where, when it failed."""

    pos: int
    expected: str


# ---------------------------------------------------------------------------
# Cursor — an immutable position over the token stream.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Cursor:
    """An immutable read position over the filtered token stream.

    All advancement returns a NEW cursor; nothing mutates. This is what lets
    backtracking be "drop the new cursor, keep the old one" instead of manual
    ``save()``/``restore()``.
    """

    tokens: Sequence[Token]
    pos: int = 0

    @property
    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self, offset: int = 0) -> Optional[Token]:
        i = self.pos + offset
        if 0 <= i < len(self.tokens):
            return self.tokens[i]
        return None

    def advance(self, n: int = 1) -> "Cursor":
        return replace(self, pos=min(self.pos + n, len(self.tokens)))

    def span_to(self, other: "Cursor") -> Span:
        return Span(self.pos, other.pos)


# ---------------------------------------------------------------------------
# ParseResult — the transactional success/failure value.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ParseResult[T]:
    """The outcome of running a parser at a cursor.

    Exactly one of (``ok`` true / false) holds. On success ``value`` and ``next``
    are set. On failure ``value``/``next`` are None and ``expectation`` says what
    was wanted. ``committed`` means a ``commit`` point was passed; a committed
    FAILURE must not be recovered by ``choice``/``optional`` — it propagates.
    ``furthest`` carries the deepest expectation seen (even across a recovered
    failure) so the top level can report the best diagnostic.
    """

    ok: bool
    committed: bool = False
    value: Optional[T] = None
    next: Optional[Cursor] = None
    expectation: Optional[Expectation] = None
    furthest: Optional[Expectation] = None

    # -- constructors --
    @staticmethod
    def success(value: T, next: Cursor, *, committed: bool = False,
                furthest: Optional[Expectation] = None) -> "ParseResult[T]":
        # cast: ``value: Optional[T] = None`` makes the checker bind T=None through
        # the dataclass constructor; the value passed IS the T this result carries.
        return cast(
            "ParseResult[T]",
            ParseResult(ok=True, committed=committed, value=value, next=next, furthest=furthest),
        )

    @staticmethod
    def failure(pos: int, expected: str, *, committed: bool = False,
                furthest: Optional[Expectation] = None) -> "ParseResult[T]":
        exp = Expectation(pos=pos, expected=expected)
        return ParseResult(
            ok=False, committed=committed, expectation=exp,
            furthest=_deeper(furthest, exp),
        )

    def with_furthest(self, other: Optional[Expectation]) -> "ParseResult[T]":
        return replace(self, furthest=_deeper(self.furthest, other))

    def unwrap(self) -> T:
        """The value of a SUCCESS, typed as ``T``.

        ``value`` is declared ``Optional[T]`` because a failure carries none; on a
        success the field holds the produced ``T`` (which may itself be ``None`` when
        ``T`` is nullable). Centralizes that narrowing so call sites stay clean.
        """
        assert self.ok, "unwrap() on a failed ParseResult"
        return cast(T, self.value)


def _deeper(a: Optional[Expectation], b: Optional[Expectation]) -> Optional[Expectation]:
    """Keep the expectation that reached furthest into the stream."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a.pos >= b.pos else b


# A Parser[T] is a function from a cursor to a ParseResult[T].
Parser = Callable[[Cursor], ParseResult[T]]


# ---------------------------------------------------------------------------
# Primitive combinators.
# ---------------------------------------------------------------------------
def token(predicate: Callable[[Token], bool], expected: str) -> Parser[Token]:
    """Consume one token satisfying ``predicate``; recoverable failure otherwise."""

    def run(c: Cursor) -> ParseResult[Token]:
        t = c.peek()
        if t is not None and predicate(t):
            return ParseResult.success(t, c.advance())
        return ParseResult.failure(c.pos, expected)

    return run


def cat(category: str) -> Parser[Token]:
    """Consume one token of the given category (e.g. ``"PYKALA"``)."""
    return token(lambda t: t.cat == category, f"cat:{category}")


def cat_case(category: str, case: str) -> Parser[Token]:
    """Consume one token of a category AND grammatical case (e.g. PYKALA/GEN)."""
    return token(lambda t: t.cat == category and t.case == case, f"cat:{category}/{case}")


def map_(parser: Parser[T], fn: Callable[[T], U]) -> Parser[U]:
    """Transform a parser's value on success."""

    def run(c: Cursor) -> ParseResult[U]:
        r = parser(c)
        if r.ok:
            assert r.next is not None
            return ParseResult.success(fn(r.unwrap()), r.next, committed=r.committed, furthest=r.furthest)
        return ParseResult(ok=False, committed=r.committed, expectation=r.expectation, furthest=r.furthest)

    return run


def seq(*parsers: Parser) -> Parser[list]:
    """Run parsers in order, collecting values; fail (propagating commit) on any miss.

    Once any sub-parser succeeds with ``committed=True`` (it passed a ``commit``
    point), a later failure is returned as committed — the enclosing ``choice``
    will not silently try another branch.
    """

    def run(c: Cursor) -> ParseResult[list]:
        values: list = []
        cur = c
        committed = False
        furthest: Optional[Expectation] = None
        for p in parsers:
            r = p(cur)
            furthest = _deeper(furthest, r.furthest)
            if not r.ok:
                return ParseResult(ok=False, committed=committed or r.committed,
                                   expectation=r.expectation, furthest=furthest)
            assert r.next is not None
            values.append(r.value)
            cur = r.next
            committed = committed or r.committed
        return ParseResult.success(values, cur, committed=committed, furthest=furthest)

    return run


def choice(*parsers: Parser[T]) -> Parser[T]:
    """First successful alternative wins. A COMMITTED failure stops the search.

    This is the substrate's "no silent drop": if a branch commits and then fails,
    we do not quietly fall through to another branch (which is how the old parser
    lost whole clauses) — we surface the committed failure.
    """

    def run(c: Cursor) -> ParseResult[T]:
        furthest: Optional[Expectation] = None
        for p in parsers:
            r = p(c)
            furthest = _deeper(furthest, r.furthest)
            if r.ok:
                return r.with_furthest(furthest)
            if r.committed:
                return replace(r, furthest=furthest)
        return ParseResult(ok=False, committed=False,
                           expectation=furthest, furthest=furthest)

    return run


def optional(parser: Parser[T]) -> Parser[Optional[T]]:
    """Succeed with the value or with None; a committed failure still propagates."""

    def run(c: Cursor) -> ParseResult[Optional[T]]:
        r = parser(c)
        if r.ok:
            assert r.next is not None
            return ParseResult.success(r.value, r.next, committed=r.committed, furthest=r.furthest)
        if r.committed:
            return ParseResult(ok=False, committed=True, expectation=r.expectation, furthest=r.furthest)
        return ParseResult.success(None, c, furthest=r.furthest)

    return run


def many(parser: Parser[T], *, min_count: int = 0) -> Parser[list[T]]:
    """Zero-or-more (or ``min_count``-or-more) repetitions.

    Stops at the first recoverable failure. A committed failure propagates. A
    parser that succeeds without advancing raises (a guard against infinite
    loops in the grammar).
    """

    def run(c: Cursor) -> ParseResult[list[T]]:
        values: list[T] = []
        cur = c
        furthest: Optional[Expectation] = None
        while True:
            r = parser(cur)
            furthest = _deeper(furthest, r.furthest)
            if not r.ok:
                if r.committed:
                    return ParseResult(ok=False, committed=True, expectation=r.expectation, furthest=furthest)
                break
            assert r.next is not None
            if r.next.pos == cur.pos:
                raise RuntimeError("many(): parser succeeded without consuming input")
            values.append(r.unwrap())
            cur = r.next
        if len(values) < min_count:
            return ParseResult.failure(c.pos, f"at least {min_count} repetitions", furthest=furthest)
        return ParseResult.success(values, cur, furthest=furthest)

    return run


def commit(parser: Parser[T]) -> Parser[T]:
    """Mark everything from here as committed: a later failure won't be recovered.

    Use after the disambiguating prefix of a production ("we've seen the verb,
    we ARE in a verb group now"), so a downstream failure produces a precise
    error rather than a silent fall-through.
    """

    def run(c: Cursor) -> ParseResult[T]:
        r = parser(c)
        return replace(r, committed=True)

    return run


def lazy(thunk: Callable[[], Parser[T]]) -> Parser[T]:
    """Defer parser construction (for recursive grammars)."""

    def run(c: Cursor) -> ParseResult[T]:
        return thunk()(c)

    return run
