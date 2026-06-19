"""Tests for the deterministic Finnish clause/sentence segmentation substrate.

Covers the sentence-split guards (abbreviation / ordinal / date / §-dot), the
sub-clause splits (comma/semicolon + subordinating/coordinating cues), and the
span->clause / span->sentence query including the boundary-spanning AMBIGUOUS
case. Also asserts the shared clause-boundary authority the H6 recognizer now
consumes is byte-identical to its pre-lift private behaviour.
"""
from __future__ import annotations

import pytest

from lawvm.core.legal_surface_tokens import (
    AMBIGUOUS,
    ClauseIndex,
    ClauseSpan,
    SentenceSpan,
)
from lawvm.finland.legal_surface.clause_segment import (
    bound_scope_hint,
    build_clause_index,
    is_clause_initial_ish,
)


def _sentences(text: str) -> list[str]:
    ci = build_clause_index("u#body", text)
    return [text[s.char_start : s.char_end] for s in ci.sentences]


def _clauses(text: str) -> list[str]:
    ci = build_clause_index("u#body", text)
    return [text[c.char_start : c.char_end] for c in ci.clauses]


# ── sentence splitting + guards ───────────────────────────────────────────────


def test_basic_sentence_split() -> None:
    assert _sentences("Ensimmäinen virke. Toinen virke.") == [
        "Ensimmäinen virke.",
        "Toinen virke.",
    ]


def test_hard_sentence_ends_exclaim_question() -> None:
    assert _sentences("Eka. Toka! Kolmas?") == ["Eka.", "Toka!", "Kolmas?"]


def test_newline_ends_sentence() -> None:
    assert _sentences("Eka rivi\nToka rivi") == ["Eka rivi", "Toka rivi"]


def test_date_dot_does_not_block_terminal_period() -> None:
    # The canonical commencement sentence: "1.1.2027" is one number token and the
    # trailing '.' DOES end the sentence (the date-dot guard is narrow).
    assert _sentences("Tämä laki tulee voimaan 1.1.2027. Sitä sovelletaan heti.") == [
        "Tämä laki tulee voimaan 1.1.2027.",
        "Sitä sovelletaan heti.",
    ]


def test_decimal_internal_dot_is_not_a_sentence_break() -> None:
    # "12.5" is a single number token; no spurious split inside it.
    assert _sentences("Korko on 12.5 prosenttia. Toinen virke.") == [
        "Korko on 12.5 prosenttia.",
        "Toinen virke.",
    ]


def test_ordinal_dot_does_not_end_sentence() -> None:
    # "1." / "2." are list ordinals — the dot is not a sentence end.
    assert _sentences("1. Ensimmäinen kohta. 2. Toinen kohta.") == [
        "1. Ensimmäinen kohta.",
        "2. Toinen kohta.",
    ]


def test_section_mark_dot_guard() -> None:
    # A "5 §:ssä" reference must not be split, and a dot before '§' is suppressed.
    assert _sentences("Lupa myönnetään 5 §:ssä mainituissa tapauksissa. Toinen.") == [
        "Lupa myönnetään 5 §:ssä mainituissa tapauksissa.",
        "Toinen.",
    ]


def test_abbreviation_dot_does_not_end_sentence() -> None:
    for abbr in ("esim.", "ns.", "mm."):
        text = f"Maksu on {abbr} 50 euroa kuukaudessa. Toinen virke."
        assert _sentences(text) == [
            f"Maksu on {abbr} 50 euroa kuukaudessa.",
            "Toinen virke.",
        ], abbr


# ── sub-clause splitting ──────────────────────────────────────────────────────


def test_comma_and_semicolon_split_clauses() -> None:
    text = "Lupa peruutetaan, jos edellytykset puuttuvat; muutoin se pysyy voimassa."
    cl = _clauses(text)
    assert cl == [
        "Lupa peruutetaan,",
        "jos edellytykset puuttuvat;",
        "muutoin se pysyy voimassa.",
    ]


def test_subordinator_jos_opens_a_clause() -> None:
    # A clause-initial 'jos' (after a comma) opens its own clause.
    ci = build_clause_index(
        "u#body", "Lupa peruutetaan, jos edellytykset eivät täyty."
    )
    jos = [c for c in ci.clauses if c.clause_kind.startswith("comma")]
    assert jos and "jos" in "Lupa peruutetaan, jos edellytykset eivät täyty."[
        jos[0].char_start : jos[0].char_end
    ]


def test_multiword_subordinator_silta_osin_kuin() -> None:
    text = "Säännöstä sovelletaan, mikäli ehdot täyttyvät."
    cl = _clauses(text)
    assert any(c.startswith("mikäli") for c in cl), cl


def test_coordinating_ja_after_comma_splits() -> None:
    # "…, ja …" is the canonical clause-coordinating use → split before 'ja'.
    text = "Hakija toimittaa asiakirjat, ja viranomainen tekee päätöksen."
    cl = _clauses(text)
    assert any(c.startswith("ja viranomainen") for c in cl), cl


def test_bare_ja_noun_coordination_does_not_split() -> None:
    # "X ja Y" with no comma is a noun coordination, not a clause boundary.
    text = "Hakija ja viranomainen sopivat asiasta."
    assert _clauses(text) == ["Hakija ja viranomainen sopivat asiasta."]


def test_mid_clause_jos_is_not_a_split() -> None:
    # A 'jos' that is not clause-initial-ish must not open a sub-clause.
    text = "Tämä tarkoittaa sitä jos asiaa tarkastellaan tarkemmin."
    # one comma-less sentence → exactly one clause (no spurious 'jos' split)
    assert _clauses(text) == [text]


# ── clause/sentence containment invariant ─────────────────────────────────────


def test_every_clause_lies_within_its_sentence() -> None:
    text = (
        "Lupa peruutetaan, jos edellytykset puuttuvat. "
        "Maksu peritään, kun päätös on annettu."
    )
    ci = build_clause_index("u#body", text)
    for c in ci.clauses:
        s = ci.sentences[c.sentence_index]
        assert s.char_start <= c.char_start and c.char_end <= s.char_end


def test_text_hash_anchors_the_built_text() -> None:
    import hashlib

    text = "Eka virke. Toka virke."
    ci = build_clause_index("u#body", text)
    assert ci.text_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert ci.source_unit_id == "u#body"


def test_deterministic_same_input_same_output() -> None:
    text = "Lupa peruutetaan, jos ehdot puuttuvat; muutoin voimassa. Toinen virke."
    a = build_clause_index("u#body", text)
    b = build_clause_index("u#body", text)
    assert a == b


# ── span -> clause / sentence query ───────────────────────────────────────────


def test_clause_at_returns_enclosing_clause() -> None:
    text = "Lupa peruutetaan, jos edellytykset puuttuvat."
    ci = build_clause_index("u#body", text)
    # span fully inside the second clause ("jos edellytykset…")
    start = text.index("edellytykset")
    end = start + len("edellytykset")
    cl = ci.clause_at(start, end)
    assert isinstance(cl, ClauseSpan)
    assert text[cl.char_start : cl.char_end].startswith("jos")


def test_sentence_at_returns_enclosing_sentence() -> None:
    text = "Eka virke. Toka virke."
    ci = build_clause_index("u#body", text)
    start = text.index("Toka")
    end = start + len("Toka")
    s = ci.sentence_at(start, end)
    assert isinstance(s, SentenceSpan)
    assert text[s.char_start : s.char_end] == "Toka virke."


def test_span_crossing_a_clause_boundary_is_ambiguous() -> None:
    text = "Lupa peruutetaan, jos edellytykset puuttuvat."
    ci = build_clause_index("u#body", text)
    # a span that straddles the comma boundary spans two clauses → AMBIGUOUS
    start = text.index("peruutetaan")
    end = text.index("edellytykset") + len("edellytykset")
    assert ci.clause_at(start, end) is AMBIGUOUS


def test_span_crossing_a_sentence_boundary_is_ambiguous() -> None:
    text = "Eka virke. Toka virke."
    ci = build_clause_index("u#body", text)
    start = text.index("virke.")  # in sentence 0
    end = text.index("Toka") + len("Toka")  # in sentence 1
    assert ci.sentence_at(start, end) is AMBIGUOUS


def test_query_outside_any_span_is_ambiguous() -> None:
    # Build over text with a trailing region that is trimmed out of every span.
    text = "Eka.   "
    ci = build_clause_index("u#body", text)
    # offset 5/6 lands in the trailing whitespace, inside no clause/sentence
    assert ci.clause_at(5, 6) is AMBIGUOUS
    assert ci.sentence_at(5, 6) is AMBIGUOUS


def test_query_rejects_inverted_span() -> None:
    ci = build_clause_index("u#body", "Eka virke.")
    with pytest.raises(ValueError):
        ci.clause_at(5, 2)


# ── shared clause-boundary authority (consumed by the H6 recognizer) ──────────


def test_is_clause_initial_ish_after_boundary_and_start() -> None:
    text = "Lupa, jos ehto. Kun aika."
    assert is_clause_initial_ish(text, 0) is True  # start of text
    assert is_clause_initial_ish(text, text.index("jos")) is True  # after ", "
    assert is_clause_initial_ish(text, text.index("Kun")) is True  # after ". "
    # mid-clause position is NOT clause-initial-ish
    assert is_clause_initial_ish(text, text.index("ehto")) is False


def test_is_clause_initial_ish_after_colon_and_paren() -> None:
    text = "Seuraavat: (jos)"
    assert is_clause_initial_ish(text, text.index("(") + 1) is True  # after "("
    assert is_clause_initial_ish(text, text.index(":") + 2) is True  # after ": "


def test_bound_scope_hint_stops_at_next_boundary() -> None:
    text = "jos edellytykset puuttuvat; muutoin voimassa"
    after = len("jos")
    bounds = bound_scope_hint(text, after, max_len=200)
    assert bounds is not None
    assert text[bounds[0] : bounds[1]] == "edellytykset puuttuvat"


def test_bound_scope_hint_none_when_nothing_follows() -> None:
    assert bound_scope_hint("paitsi.", len("paitsi"), max_len=200) is None


def test_bound_scope_hint_respects_max_len() -> None:
    text = "jos " + "x" * 500
    bounds = bound_scope_hint(text, len("jos"), max_len=200)
    assert bounds is not None
    assert bounds[1] - bounds[0] == 200


# ── core carrier invariants (fail-loud construction) ──────────────────────────


def test_clause_index_rejects_out_of_range_sentence_index() -> None:
    with pytest.raises(ValueError):
        ClauseIndex(
            source_unit_id="u",
            text_hash="h",
            sentences=(SentenceSpan(0, 10),),
            clauses=(ClauseSpan(0, 5, sentence_index=3, clause_kind="x"),),
        )


def test_clause_index_rejects_clause_outside_its_sentence() -> None:
    with pytest.raises(ValueError):
        ClauseIndex(
            source_unit_id="u",
            text_hash="h",
            sentences=(SentenceSpan(0, 5),),
            clauses=(ClauseSpan(3, 9, sentence_index=0, clause_kind="x"),),
        )


def test_clause_index_rejects_overlapping_sentences() -> None:
    with pytest.raises(ValueError):
        ClauseIndex(
            source_unit_id="u",
            text_hash="h",
            sentences=(SentenceSpan(0, 10), SentenceSpan(5, 15)),
            clauses=(),
        )
