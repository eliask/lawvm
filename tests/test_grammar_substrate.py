"""Unit tests for the rewritten-parser substrate (combinators + evidence ledger).

Tests parser MECHANICS only — no Finnish, no legal semantics. Per the rewrite
plan: recoverable failure rewinds, committed failure does not, spans are exact,
diagnostics survive backtracking, the ledger accounts every token.
"""

from __future__ import annotations

import pytest

from lawvm.finland.johtolause.grammar.combinators import (
    Cursor,
    Span,
    cat,
    choice,
    commit,
    many,
    map_,
    optional,
    seq,
)
from lawvm.finland.johtolause.grammar.evidence import Disposition, EvidenceLedger
from lawvm.finland.johtolause.lexicon import Token


def _tok(cat: str, text: str = "", case: str = "") -> Token:
    return Token(text=text or cat, lemma=text or cat, cat=cat, case=case, verb_code=None)


def _cur(*cats: str) -> Cursor:
    return Cursor(tokens=[_tok(c) for c in cats])


# --- Span ---
def test_span_join_and_empty() -> None:
    assert Span(2, 5).join(Span(7, 9)) == Span(2, 9)
    assert Span(3, 3).is_empty
    assert Span(3, 3).join(Span(7, 9)) == Span(7, 9)  # empty ignored
    with pytest.raises(ValueError):
        Span(5, 2)


# --- token / cat ---
def test_token_success_advances_one() -> None:
    r = cat("NUM")(_cur("NUM", "PYKALA"))
    assert r.ok and r.next is not None and r.next.pos == 1
    assert r.value.cat == "NUM"


def test_token_recoverable_failure_does_not_advance() -> None:
    c = _cur("PYKALA")
    r = cat("NUM")(c)
    assert not r.ok and not r.committed
    assert r.expectation is not None and r.expectation.pos == 0


# --- seq ---
def test_seq_collects_in_order() -> None:
    r = seq(cat("NUM"), cat("PYKALA"))(_cur("NUM", "PYKALA"))
    assert r.ok and [t.cat for t in r.value] == ["NUM", "PYKALA"]
    assert r.next.pos == 2


def test_seq_failure_is_recoverable_before_commit() -> None:
    r = seq(cat("NUM"), cat("PYKALA"))(_cur("NUM", "COMMA"))
    assert not r.ok and not r.committed  # no commit point passed


# --- choice + commit: the no-silent-drop contract ---
def test_choice_first_match_wins() -> None:
    r = choice(cat("NUM"), cat("PYKALA"))(_cur("PYKALA"))
    assert r.ok and r.value.cat == "PYKALA"


def test_choice_recoverable_failure_tries_next() -> None:
    # first branch fails recoverably -> second is tried
    r = choice(seq(cat("NUM"), cat("LUKU")), cat("PYKALA"))(_cur("PYKALA"))
    assert r.ok and r.value.cat == "PYKALA"


def test_committed_failure_is_not_recovered_by_choice() -> None:
    # Branch A commits after NUM, then requires LUKU. Input is NUM COMMA, so A
    # fails AFTER commit -> choice must NOT fall through to branch B.
    branch_a = seq(cat("NUM"), commit(cat("LUKU")))
    branch_b = seq(cat("NUM"), cat("COMMA"))
    r = choice(branch_a, branch_b)(_cur("NUM", "COMMA"))
    assert not r.ok and r.committed  # surfaced, not silently recovered


# --- optional ---
def test_optional_present_and_absent() -> None:
    present = optional(cat("NUM"))(_cur("NUM"))
    assert present.ok and present.value is not None and present.next.pos == 1
    absent = optional(cat("NUM"))(_cur("PYKALA"))
    assert absent.ok and absent.value is None and absent.next.pos == 0


def test_optional_propagates_committed_failure() -> None:
    inner = seq(cat("NUM"), commit(cat("LUKU")))
    r = optional(inner)(_cur("NUM", "COMMA"))
    assert not r.ok and r.committed


# --- many ---
def test_many_collects_until_failure() -> None:
    r = many(cat("NUM"))(_cur("NUM", "NUM", "NUM", "PYKALA"))
    assert r.ok and len(r.value) == 3 and r.next.pos == 3


def test_many_min_count_enforced() -> None:
    r = many(cat("NUM"), min_count=2)(_cur("NUM", "PYKALA"))
    assert not r.ok


def test_many_guards_against_nonconsuming_loop() -> None:
    nonconsuming = optional(cat("NUM"))  # always succeeds, may not advance
    with pytest.raises(RuntimeError):
        many(nonconsuming)(_cur("PYKALA"))


# --- furthest diagnostic survives backtracking ---
def test_furthest_expectation_survives_recovered_branch() -> None:
    # Branch A reaches pos 2 before failing recoverably; branch B fails at pos 0.
    # The furthest expectation (pos 2) must survive for diagnostics.
    branch_a = seq(cat("NUM"), cat("PYKALA"), cat("LUKU"))
    branch_b = cat("OSA")
    r = choice(branch_a, branch_b)(_cur("NUM", "PYKALA", "COMMA"))
    assert not r.ok
    assert r.furthest is not None and r.furthest.pos == 2


# --- map ---
def test_map_transforms_value() -> None:
    r = map_(cat("NUM"), lambda t: t.cat.lower())(_cur("NUM"))
    assert r.ok and r.value == "num"


# --- span_to ---
def test_cursor_span_to() -> None:
    c0 = _cur("NUM", "PYKALA", "COMMA")
    c2 = c0.advance(2)
    assert c0.span_to(c2) == Span(0, 2)


# --- EvidenceLedger: token accounting ---
def test_ledger_accounts_and_reports_unaccounted() -> None:
    led = EvidenceLedger(n_tokens=4)
    led.account(Span(0, 2), Disposition.NODE, rule="fi.section_ref")
    led.account(Span(2, 3), Disposition.TRIVIA, rule="sep")
    # token 3 left unaccounted
    assert led.unaccounted() == [3]
    led.account(Span(3, 4), Disposition.IGNORED, rule="end_sentinel")
    assert led.unaccounted() == []


def test_ledger_witnesses_for_span_containment() -> None:
    led = EvidenceLedger(n_tokens=6)
    led.witness("fi.section_ref", Span(0, 2))
    led.witness("fi.insertion_section", Span(3, 5))
    assert led.witnesses_for(Span(0, 3)) == ["fi.section_ref"]
    assert set(led.witnesses_for(Span(0, 6))) == {"fi.section_ref", "fi.insertion_section"}
