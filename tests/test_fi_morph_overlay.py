"""Tests for the Finland MorphOverlay (Phase 7 reverse-morphology view).

The overlay is a SPARSE per-token annotation over a :class:`TokenTape`: for each
``word`` token whose surface inverts (via the M1-inverting lemma index) to a
known head lemma, it records that lemma at the token's index. Out-of-vocabulary
tokens get NO annotation (honest unknown), and ambiguous surfaces carry all
matching lemmas with ``unique=False``.
"""
from __future__ import annotations

import pytest

from lawvm.core.legal_surface_tokens import (
    MorphAnnotation,
    MorphOverlay,
    Token,
    TokenTape,
)
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.tokenize import (
    build_morph_overlay,
    build_token_tape,
)
from lawvm.finland.morphology.lemma_index import LemmaIndex, build_lemma_index

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A snippet that inflects several known heads: laki (laissa), asetus (asetuksen),
# pykälä (pykälän), momentti (momentissa). "robotti" is an ordinary noun NOT in
# the closed head inventory => must NOT be annotated.
_SNIPPET = "Laissa ja asetuksen pykälän momentissa robotti."

_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><content>
    <p>{_SNIPPET}</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")


def _tape() -> TokenTape:
    return build_token_tape("u#body", _SNIPPET)


def test_overlay_annotates_known_heads_with_correct_lemmas() -> None:
    tape = _tape()
    overlay = build_morph_overlay(tape)
    index = build_lemma_index()

    # Map normalized surface -> expected single head lemma.
    expected = {
        "laissa": "laki",
        "asetuksen": "asetus",
        "pykälän": "pykälä",
        "momentissa": "momentti",
    }
    seen: dict[str, str] = {}
    for index_, ann in overlay.annotations.items():
        tok = tape.tokens[ann.token_index]
        # token_index alignment: the annotation key IS the tape index.
        assert ann.token_index == index_
        assert tok.category == "word"
        # round-trip: the token's surface inverts through the lemma index to
        # exactly the lemmas the annotation carries.
        assert index.analyze(tok.normalized) == ann.lemmas
        if ann.unique:
            seen[tok.normalized] = ann.lemmas[0]

    for surface, lemma in expected.items():
        assert seen.get(surface) == lemma, f"{surface!r} -> {lemma!r}"


def test_out_of_vocab_token_has_no_annotation() -> None:
    tape = _tape()
    overlay = build_morph_overlay(tape)
    # "robotti" is a word token but is not a known head => no annotation.
    robotti_idx = next(
        i for i, t in enumerate(tape.tokens) if t.normalized == "robotti"
    )
    assert robotti_idx not in overlay.annotations
    # and the lemma index agrees it is out of vocabulary.
    assert build_lemma_index().analyze("robotti") == ()


def test_ambiguous_surface_is_surfaced_not_resolved() -> None:
    # The default head inventory has no colliding surfaces, so use a synthetic
    # index that maps a single surface to two lemmas to exercise unique=False.
    synthetic = LemmaIndex(
        _map={"kohta": ("alpha", "beta")},
        lemmas=("alpha", "beta"),
    )
    tape = build_token_tape("u#body", "kohta")
    overlay = build_morph_overlay(tape, lemma_index=synthetic)
    word_idx = next(
        i for i, t in enumerate(tape.tokens) if t.category == "word"
    )
    ann = overlay.annotations[word_idx]
    assert ann.unique is False
    assert ann.lemmas == ("alpha", "beta")


def test_bundle_integration_populates_morph_overlay() -> None:
    bundle = build_surface_bundle(_XML, "123/2020")
    unit = bundle.units[0]
    assert isinstance(unit.morph_overlay, MorphOverlay)
    overlay = unit.morph_overlay
    assert overlay.source_unit_id == unit.source_unit_id
    # anchored to the same text the tape was built over.
    assert isinstance(unit.token_tape, TokenTape)
    assert overlay.text_hash == unit.token_tape.text_hash
    # at least the four known heads from the snippet are annotated.
    lemmas = {
        lemma
        for ann in overlay.annotations.values()
        for lemma in ann.lemmas
    }
    assert {"laki", "asetus", "pykälä", "momentti"} <= lemmas


def test_overlay_build_is_deterministic() -> None:
    tape = _tape()
    a = build_morph_overlay(tape)
    b = build_morph_overlay(tape)
    assert a == b
    # and through the bundle path.
    u1 = build_surface_bundle(_XML, "123/2020").units[0]
    u2 = build_surface_bundle(_XML, "123/2020").units[0]
    assert u1.morph_overlay == u2.morph_overlay


def test_morph_annotation_fail_loud_invariants() -> None:
    with pytest.raises(ValueError):
        MorphAnnotation(token_index=-1, lemmas=("laki",), unique=True)
    with pytest.raises(ValueError):
        MorphAnnotation(token_index=0, lemmas=(), unique=False)
    with pytest.raises(ValueError):
        # unique must equal (len(lemmas) == 1)
        MorphAnnotation(token_index=0, lemmas=("a", "b"), unique=True)
    with pytest.raises(ValueError):
        MorphAnnotation(token_index=0, lemmas=("a",), unique=False)


def test_morph_overlay_key_must_match_token_index() -> None:
    ann = MorphAnnotation(token_index=3, lemmas=("laki",), unique=True)
    with pytest.raises(ValueError):
        MorphOverlay(source_unit_id="u", text_hash="h", annotations={5: ann})


def test_overlay_ignores_non_word_tokens() -> None:
    # "5 §:n laissa" — number, section colon_suffix, punctuation must never be
    # annotated even if they were somehow in the index.
    tape = build_token_tape("u#body", "5 §:n laissa")
    overlay = build_morph_overlay(tape)
    for idx in overlay.annotations:
        assert tape.tokens[idx].category == "word"


def test_token_unaffected_by_overlay() -> None:
    # sanity: a Token still round-trips its span (overlay does not mutate tape).
    tape = _tape()
    for tok in tape.tokens:
        assert _SNIPPET[tok.char_start : tok.char_end] == tok.text
    assert isinstance(tape.tokens[0], Token)
