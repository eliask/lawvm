"""Inessive / locative body-citation forms in the sub-reference grammar.

Body citations are inessive (``1 momentissa``, ``4 kohdassa``, ``§:ssä``) while
amendment johtolauses are genitive/illative (``6 §:n 1 momentin 4 kohtaa``). The
sub-reference recognizer in ``grammar.sections`` reuses the nominative MOMENTTI /
KOHTA branches for the inessive leaf forms, so the only thing the inessive forms
need is a lexicon classification (inessive leaf == nominative leaf address).

These tests pin which inessive leaf forms reach a ``SubRef`` today and assert the
genitive amendment path is byte-identical (additivity guard at the unit level —
the corpus-scale guard is the swap-readiness census).

Recorded blocker — ``momentissa``: promoting inessive ``momentissa`` to a
structural MOMENTTI token is NOT additive. Finnish amendment johtolauses embed
``N §:n M momentissa tarkoitettu ...`` relative clauses inside *statute names*
(e.g. 2013/1324, asetus 1423/2006). Classifying ``momentissa`` as MOMENTTI makes
the section-ref scanner mis-extract those name-internal references as amendment
targets, changing an amendment parse. ``momentissa`` therefore stays a WORD here;
the test below pins that current behavior so a future, context-guarded attempt
has a regression witness.
"""

from __future__ import annotations

from lawvm.finland.johtolause.grammar import sections as S
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.lexer import tokenize


def _sub_ref_after_pykala(text: str):
    """Tokenize ``text``, position just past the first §, run ``_sub_ref``."""
    toks = tokenize(text)
    pyk = next((i for i, t in enumerate(toks) if t.cat == "PYKALA"), None)
    assert pyk is not None, f"no § in {text!r}"
    scan = S._Scan(Cursor(toks, pyk + 1))
    return S._sub_ref(scan)


def _sub_ref_from(text: str, start: int):
    toks = tokenize(text)
    scan = S._Scan(Cursor(toks, start))
    return S._sub_ref(scan)


def _cat_of(text: str, lemma: str) -> str:
    toks = tokenize(text)
    tok = next(t for t in toks if t.text.lower() == lemma)
    return tok.cat


# ── alakohdassa — the additive lexicon completion ──────────────────────────


def test_alakohdassa_classifies_as_alakohta() -> None:
    # Inessive ``alakohdassa`` was absent; it now mirrors the existing
    # ``alakohta`` (NOM) / ``alakohdan`` (GEN). ALAKOHTA has no SubRef branch, so
    # this is a pure token-classification completion (no parse perturbation).
    assert _cat_of("muutetaan 5 §:n 1 momentin 2 alakohdassa", "alakohdassa") == "ALAKOHTA"


# ── inessive leaf forms that already reach a SubRef via the NOM branches ────


def test_inessive_kohta_yields_item_subref() -> None:
    # ``kohdassa`` (inessive) is already mapped to KOHTA/NOM; the bare
    # ``N kohdassa`` reaches the nominative KOHTA branch -> item sub-ref.
    subs = _sub_ref_from("muutetaan 4 kohdassa", 1)
    assert subs == [S.SubRef(momentti=0, item="4")]


def test_inessive_kohta_after_genitive_momentti() -> None:
    # ``6 §:n 1 momentin 4 kohdassa`` -> momentti=1, item=4 (inessive leaf item
    # on a genitive momentti descent).
    subs = _sub_ref_after_pykala("muutetaan 6 §:n 1 momentin 4 kohdassa")
    assert subs == [S.SubRef(momentti=1, item="4")]


def test_inessive_section_has_no_subref() -> None:
    # ``13—16 §:ssä`` — the inessive § range is captured at the section level;
    # there is no momentti/kohta after it, so the sub-ref slot is empty.
    assert _sub_ref_after_pykala("muutetaan 13—16 §:ssä") is None


# ── recorded blocker: momentissa is NOT promoted (statute-name collision) ───


def test_momentissa_stays_word_subref_lost() -> None:
    toks = tokenize("muutetaan 6 §:n 1 momentissa")
    tok = next(t for t in toks if t.text.lower() == "momentissa")
    assert tok.cat == "WORD", "momentissa must stay WORD — promoting it breaks additivity"
    # With momentissa a bare WORD, the momentti is not recovered.
    assert _sub_ref_after_pykala("muutetaan 6 §:n 1 momentissa") is None


# ── genitive amendment control — unchanged by the lexicon completion ────────


def test_genitive_kohta_range_control_unchanged() -> None:
    subs = _sub_ref_after_pykala("muutetaan 2 §:n 1 momentin 4—6 kohdan")
    assert subs == [
        S.SubRef(momentti=1, item="4"),
        S.SubRef(momentti=1, item="5"),
        S.SubRef(momentti=1, item="6"),
    ]


def test_genitive_momentti_elative_control_unchanged() -> None:
    # ``10 §:n 3 momentista`` (elative, GEN-keyed) still yields momentti=3.
    assert _sub_ref_after_pykala("muutetaan 10 §:n 3 momentista") == [S.SubRef(momentti=3)]
