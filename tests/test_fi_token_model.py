"""Basic construction and invariant tests for the Phase 2 token model types."""

from __future__ import annotations

import pytest
from typing import Any, cast

from lawvm.finland.johtolause.token_model import (
    Annotation,
    AnnotationSet,
    Lexeme,
    StructuralTokenView,
    TokenTape,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lex(
    text: str, lemma: str, cat: str, case: str = "", verb_code: str = "", start: int = -1, end: int = -1
) -> Lexeme:
    return Lexeme(
        text=text, lemma=lemma, category=cat, gram_case=case, verb_code=verb_code, char_start=start, char_end=end
    )


def _sample_tape() -> TokenTape:
    """A minimal three-token tape for testing."""
    return TokenTape(
        source_text="muutetaan 5 \u00a7",
        lexemes=(
            _lex("muutetaan", "muuttaa", "VERB", verb_code="M", start=0, end=9),
            _lex("5", "5", "NUM", start=10, end=11),
            _lex("\u00a7", "\u00a7", "PYKALA", "NOM", start=12, end=13),
        ),
    )


# ---------------------------------------------------------------------------
# Lexeme
# ---------------------------------------------------------------------------


class TestLexeme:
    def test_construction(self) -> None:
        lex = _lex("muutetaan", "muuttaa", "VERB")
        assert lex.text == "muutetaan"
        assert lex.lemma == "muuttaa"
        assert lex.category == "VERB"
        assert lex.gram_case == ""

    def test_frozen(self) -> None:
        lex = _lex("muutetaan", "muuttaa", "VERB")
        with pytest.raises(AttributeError):
            cast(Any, lex).text = "changed"

    def test_equality(self) -> None:
        a = _lex("5", "5", "NUM", start=10, end=11)
        b = _lex("5", "5", "NUM", start=10, end=11)
        assert a == b

    def test_gram_case(self) -> None:
        lex = _lex("\u00a7:n", "\u00a7", "PYKALA", "GEN", start=0, end=3)
        assert lex.gram_case == "GEN"


# ---------------------------------------------------------------------------
# TokenTape
# ---------------------------------------------------------------------------


class TestTokenTape:
    def test_construction(self) -> None:
        tape = _sample_tape()
        assert tape.source_text == "muutetaan 5 \u00a7"
        assert len(tape) == 3
        assert tape.lexemes[0].category == "VERB"
        assert tape.lexemes[2].category == "PYKALA"

    def test_frozen(self) -> None:
        tape = _sample_tape()
        with pytest.raises(AttributeError):
            cast(Any, tape).source_text = "changed"

    def test_empty(self) -> None:
        tape = TokenTape(source_text="", lexemes=())
        assert len(tape) == 0


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------


class TestAnnotation:
    def test_construction(self) -> None:
        ann = Annotation(kind="citation_span", start=0, end=3)
        assert ann.kind == "citation_span"
        assert ann.start == 0
        assert ann.end == 3
        assert len(ann) == 3
        assert dict(ann.detail) == {}

    def test_detail(self) -> None:
        ann = Annotation(kind="citation_span", start=0, end=2, detail={"cite_text": "2024/123"})
        assert ann.detail["cite_text"] == "2024/123"

    def test_covers(self) -> None:
        ann = Annotation(kind="provenance_span", start=1, end=4)
        assert not ann.covers(0)
        assert ann.covers(1)
        assert ann.covers(3)
        assert not ann.covers(4)

    def test_frozen(self) -> None:
        ann = Annotation(kind="citation_span", start=0, end=1)
        with pytest.raises(AttributeError):
            cast(Any, ann).kind = "changed"

    def test_invalid_start(self) -> None:
        with pytest.raises(ValueError, match="start must be >= 0"):
            Annotation(kind="x", start=-1, end=0)

    def test_invalid_end_before_start(self) -> None:
        with pytest.raises(ValueError, match="end .* must be >= start"):
            Annotation(kind="x", start=3, end=1)

    def test_zero_length(self) -> None:
        ann = Annotation(kind="x", start=2, end=2)
        assert len(ann) == 0
        assert not ann.covers(2)


# ---------------------------------------------------------------------------
# AnnotationSet
# ---------------------------------------------------------------------------


class TestAnnotationSet:
    def test_construction(self) -> None:
        anns = AnnotationSet(
            annotations=(
                Annotation(kind="citation_span", start=0, end=2),
                Annotation(kind="provenance_span", start=3, end=5),
            )
        )
        assert len(anns) == 2

    def test_empty(self) -> None:
        anns = AnnotationSet(annotations=())
        assert len(anns) == 0
        assert anns.covering(0) == ()
        assert anns.of_kind("citation_span") == ()

    def test_covering(self) -> None:
        a1 = Annotation(kind="citation_span", start=0, end=3)
        a2 = Annotation(kind="provenance_span", start=2, end=5)
        anns = AnnotationSet(annotations=(a1, a2))
        # index 2 is covered by both
        assert anns.covering(2) == (a1, a2)
        # index 4 is covered only by a2
        assert anns.covering(4) == (a2,)
        # index 5 is covered by neither
        assert anns.covering(5) == ()

    def test_of_kind(self) -> None:
        a1 = Annotation(kind="citation_span", start=0, end=2)
        a2 = Annotation(kind="provenance_span", start=3, end=5)
        a3 = Annotation(kind="citation_span", start=6, end=8)
        anns = AnnotationSet(annotations=(a1, a2, a3))
        assert anns.of_kind("citation_span") == (a1, a3)
        assert anns.of_kind("provenance_span") == (a2,)
        assert anns.of_kind("heading_placement") == ()


# ---------------------------------------------------------------------------
# StructuralTokenView
# ---------------------------------------------------------------------------


class TestStructuralTokenView:
    def test_construction(self) -> None:
        tape = _sample_tape()
        anns = AnnotationSet(annotations=())
        view = StructuralTokenView(
            tape=tape,
            annotations=anns,
            visible_indices=(0, 1, 2),
        )
        assert len(view) == 3
        assert view.lexeme_at(0).category == "VERB"
        assert view.lexeme_at(2).category == "PYKALA"
        assert view.tape_index(1) == 1

    def test_hidden_span(self) -> None:
        tape = _sample_tape()
        # Annotation covers token 0 (the verb) -- view hides it.
        ann = Annotation(kind="citation_span", start=0, end=1)
        anns = AnnotationSet(annotations=(ann,))
        view = StructuralTokenView(
            tape=tape,
            annotations=anns,
            visible_indices=(1, 2),
        )
        assert len(view) == 2
        assert view.lexeme_at(0).text == "5"
        assert view.tape_index(0) == 1

    def test_empty_view(self) -> None:
        tape = _sample_tape()
        anns = AnnotationSet(annotations=())
        view = StructuralTokenView(
            tape=tape,
            annotations=anns,
            visible_indices=(),
        )
        assert len(view) == 0

    def test_frozen(self) -> None:
        tape = _sample_tape()
        anns = AnnotationSet(annotations=())
        view = StructuralTokenView(tape=tape, annotations=anns, visible_indices=(0,))
        with pytest.raises(AttributeError):
            cast(Any, view).tape = tape


# ---------------------------------------------------------------------------
