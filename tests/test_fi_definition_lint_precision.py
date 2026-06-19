"""Precision regression tests for the FI definition lints.

A corpus surface-lints report once showed a single statute with 138
``definition.used_before_definition`` lints for the term ``'auto'`` (Finnish for
"car", a common word): the binder bound common words / referential idioms as
defined terms, and every prior occurrence flooded USED_BEFORE_DEFINITION. These
tests pin the conservative fix:

  * the REFERENTIAL ``tarkoitetaan`` idiom (``…, jota / N momentissa / N §:ssä
    tarkoitetaan`` = "referred to in …") binds NOTHING — only the DEFINITIONAL
    adessive idiom (``X:llä tarkoitetaan Y``) introduces a term;
  * the ``jäljempänä`` adverbial idiom (``jäljempänä säädetään`` = "hereinafter
    provided") binds NOTHING — only an alias for a CITED act does;
  * a common word DEFINED in a definitions section (``autolla tarkoitetaan``) and
    used throughout the operative provisions does NOT flood
    USED_BEFORE_DEFINITION (normal Finnish drafting, not an order violation);
  * a genuine ALIAS used before its parenthetical binding STILL fires the lint
    (the canonical true positive is preserved).
"""
from __future__ import annotations

from lawvm.finland.references.defined_terms import (
    BINDING_TARKOITETAAN,
    recognize_defined_term_bindings,
)
from lawvm.finland.references.definition_graph import (
    LINT_USED_BEFORE_DEFINITION,
    build_definition_graph,
)

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(*paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f'<akomaNtoso xmlns="{_NS}"><act><body>{body}</body></act></akomaNtoso>'
    ).encode("utf-8")


def _ubd(graph) -> list:
    return [li for li in graph.lints if li.kind == LINT_USED_BEFORE_DEFINITION]


# ── Binder: referential idioms bind nothing ──────────────────────────────────


def test_referential_tarkoitetaan_idiom_binds_nothing() -> None:
    # "X, jota N momentissa tarkoitetaan" = "X, referred to in subsection N" — a
    # cross-reference, NOT a definition. None of jota / momentissa / luvussa /
    # §:ssä is a definiendum.
    for text in (
        "Maaraysta, jota 1 momentissa tarkoitetaan, sovelletaan.",
        "muutoksesta, jota tarkoitetaan taman luvun 3 kohdassa.",
        "sellaista hyotya kuin 6 luvun 16 §:ssa tarkoitetaan.",
        "Tassa laissa tarkoitetaan seuraavaa.",
    ):
        bindings = recognize_defined_term_bindings(text, source_file="x")
        tk = [b for b in bindings if b.binding_kind == BINDING_TARKOITETAAN]
        assert tk == [], f"referential idiom should bind nothing: {text!r} -> {tk}"


def test_definitional_adessive_tarkoitetaan_still_binds() -> None:
    # "X:llä tarkoitetaan Y" (adessive) IS a definition and must still bind.
    bindings = recognize_defined_term_bindings(
        "Autolla tarkoitetaan henkiloiden kuljetukseen valmistettua ajoneuvoa.",
        source_file="x",
    )
    tk = [b for b in bindings if b.binding_kind == BINDING_TARKOITETAAN]
    assert len(tk) == 1
    assert tk[0].term.lower().startswith("auto")


def test_jaljempana_adverbial_idiom_binds_nothing() -> None:
    # "jäljempänä säädetään" / "jäljempänä on säädetty" = "hereinafter provided" —
    # adverbial, not an alias for a cited act, so it binds nothing.
    bindings = recognize_defined_term_bindings(
        "Aloitteentekijan tulee, silla tavoin kuin jaljempana saadetaan, toimia.",
        source_file="x",
    )
    assert bindings == []


# ── Lint precision: common-word flood vs genuine alias true positive ─────────


def test_common_word_definition_does_not_flood_used_before_definition() -> None:
    # A common word ("auto") used MANY times in the operative provisions, then
    # DEFINED in a definitions section via the adessive idiom. The binding is
    # genuine, but the prior occurrences are the ordinary word — they must NOT
    # flood USED_BEFORE_DEFINITION.
    body = (
        "Auto on moottorikayttoinen ajoneuvo. "
        "Auton kuljettaja vastaa. Autoa ei saa pysakoida. "
        "Auto katsastetaan. Autolle myonnetaan lupa. Autossa on rekisterikilpi. "
        "Auton omistaja ilmoittaa. Autoa kaytetaan liikenteessa."
    )
    definition = "Autolla tarkoitetaan henkiloiden kuljetukseen valmistettua ajoneuvoa."
    graph = build_definition_graph(_xml(body, definition), "auto-statute")
    # The definition IS recognised (binding not silently dropped).
    assert any(
        b.binding_kind == BINDING_TARKOITETAAN and b.term.lower().startswith("auto")
        for b in graph.bindings
    )
    # But the common-word occurrences before it must NOT flood used-before.
    assert _ubd(graph) == [], (
        "common-word definitions-section term flooded USED_BEFORE_DEFINITION: "
        f"{[li.term for li in _ubd(graph)]}"
    )


def test_genuine_alias_used_before_definition_still_fires() -> None:
    # The canonical TRUE POSITIVE: an ALIAS used before its parenthetical binding
    # IS a genuine order violation and must still fire.
    graph = build_definition_graph(
        _xml(
            "Tama sivutuoteasetuksella sailoo.",
            "Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) annetaan.",
        ),
        "alias-statute",
    )
    ubd = _ubd(graph)
    assert len(ubd) == 1
    assert ubd[0].term == "sivutuoteasetus"
    assert "sivutuoteasetuksella" in ubd[0].message
