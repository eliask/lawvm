"""Tests for the by-name cross-statute reference recognizer.

Covers the ``[STATUTE_NAME_HEAD]`` family: cross-statute references made by
inflected statute NAME with no ``(NNN/YYYY)`` id anchor. See
``src/lawvm/finland/references/by_name.py``.
"""

from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.by_name import recognize_by_name_refs


def test_name_only_no_tail_emits_statute_level_mention() -> None:
    """``luonnonsuojelulaissa`` (no § tail) -> 1 STATUTE_ONLY cross-statute."""
    mentions = recognize_by_name_refs("luonnonsuojelulaissa säädetään tarkemmin.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:luonnonsuojelulaki"
    # Statute-level: no section path resolved.
    assert m.target_provision_ref.section_label == ""
    # The name surface is carried; no fabricated id, source span deferred.
    assert m.surface_text == "luonnonsuojelulaissa"
    assert m.source_span is None
    assert m.phrase_lemma == "statute_name_head"


def test_name_with_section_tail_carries_section_path() -> None:
    """``ympäristönsuojelulain 5 §:ssä`` -> 1 mention, section 5 + name."""
    mentions = recognize_by_name_refs("ympäristönsuojelulain 5 §:ssä säädetään.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:ympäristönsuojelulaki"
    assert m.target_provision_ref.section_label == "5"
    assert m.target_provision_ref.subsection_num is None


def test_name_with_momentti_tail() -> None:
    """A momentti tail carries through the shared body parser."""
    mentions = recognize_by_name_refs("työsopimuslain 7 §:n 2 momentissa säädetään.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:työsopimuslaki"
    assert m.target_provision_ref.section_label == "7"
    assert m.target_provision_ref.subsection_num == 2


def test_coordinated_compound_modifier_head_detected() -> None:
    """``maankäyttö- ja rakennuslain 132 §`` -> name head detected, section 132.

    The inflection rides the LAST conjunct's head (``rakennus`` + ``lain``); the
    normalized key reattaches the head to that conjunct (tag-don't-guess: no
    per-conjunct id synthesis). The reported surface includes the elided-head
    left conjunct (``maankäyttö- ja``).
    """
    mentions = recognize_by_name_refs("maankäyttö- ja rakennuslain 132 §:ssä")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:rakennuslaki"
    assert m.target_provision_ref.section_label == "132"
    # Full coordinated name surface reported for overlay display.
    assert "maankäyttö- ja rakennuslain" in m.surface_text


def test_id_anchored_reference_emits_nothing() -> None:
    """``jätelain (646/2011) 3 §`` -> NOTHING (id-anchored; plain-text lane)."""
    assert recognize_by_name_refs("jätelain (646/2011) 3 § säädetään.") == []


def test_id_anchored_with_spacing_emits_nothing() -> None:
    """The id-paren exclusion tolerates whitespace before the paren."""
    assert recognize_by_name_refs("jätelain  ( 646/2011 ) 3 §") == []


def test_bare_section_ref_emits_nothing() -> None:
    """``5 §:ssä`` (no name head) -> NOTHING (internal / other lane)."""
    assert recognize_by_name_refs("5 §:ssä säädetään") == []
    assert recognize_by_name_refs("Tämän lain 5 §:ssä säädetään") == []


def test_nominative_bare_head_not_triggered() -> None:
    """An uninflected nominative head is not a by-name citation trigger."""
    assert recognize_by_name_refs("Tämä laki tulee voimaan.") == []
    assert recognize_by_name_refs("Annetaan asetus tarkemmista säännöksistä.") == []


def test_bare_governed_head_not_emitted() -> None:
    """A bare governed head (``valtioneuvoston asetuksessa``) is NOT emitted.

    There is no compound title — ``asetuksessa`` is a generic governed
    instrument, not a resolvable named act. The lane requires a compound title
    (modifier glued to the head), so this is correctly skipped.
    """
    assert (
        recognize_by_name_refs(
            "valtioneuvoston asetuksessa annetaan tarkempia säännöksiä"
        )
        == []
    )


def test_compound_asetus_head() -> None:
    """A compound ``...asetuksen`` head normalizes to its nominative compound."""
    mentions = recognize_by_name_refs("ympäristönsuojeluasetuksen 4 §:ssä")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:ympäristönsuojeluasetus"
    assert m.target_provision_ref.section_label == "4"


def test_section_coordination_expands_per_section() -> None:
    """A coordinated section list expands to one mention per section."""
    mentions = recognize_by_name_refs("rakennuslain 1 ja 3 §:ssä säädetään")
    sections = sorted(
        m.target_provision_ref.section_label
        for m in mentions
        if m.target_provision_ref is not None
    )
    assert sections == ["1", "3"]
    for m in mentions:
        assert m.target_provision_ref is not None
        assert m.target_provision_ref.statute_id == "fi-name:rakennuslaki"
        assert m.cite_confidence is CiteConfidence.STATUTE_ONLY


def test_multiple_distinct_names_in_one_text() -> None:
    """Two distinct by-name references in one text -> two mentions."""
    mentions = recognize_by_name_refs(
        "luonnonsuojelulaissa ja ympäristönsuojelulain 5 §:ssä säädetään."
    )
    names = sorted(
        m.target_provision_ref.statute_id
        for m in mentions
        if m.target_provision_ref is not None
    )
    assert names == [
        "fi-name:luonnonsuojelulaki",
        "fi-name:ympäristönsuojelulaki",
    ]


def test_paatos_compound_head_with_tail_inflected() -> None:
    """A compound ``päätös`` head (weak head) is recognised WHEN it has a tail.

    ``päätös`` is a productive common noun (``lupapäätöksen`` = permit decision),
    so the precision gate requires POSITIVE EVIDENCE that the token is a real act
    reference. A following provision tail (``4 §:ssä``) is that evidence, so the
    weak head still emits here (with vowel harmony preserved in the head form).
    """
    mentions = recognize_by_name_refs("ministeriön työllisyyspäätöksen 4 §:ssä määrätään")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:työllisyyspäätös"
    assert m.target_provision_ref.section_label == "4"


def test_weak_head_no_tail_lowercase_not_emitted() -> None:
    """A weak-head common noun with NO tail and a lowercase modifier -> NOTHING.

    ``työllisyyspäätöksessä`` (no § tail, lowercase) is an ordinary compound
    common noun, not an act reference. With no positive evidence (no provision
    tail, not capitalized mid-sentence) the precision gate suppresses it rather
    than emitting a non-reference. (Previously this over-fired.)
    """
    assert recognize_by_name_refs("ministeriön työllisyyspäätöksessä määrätään") == []


def test_no_partial_word_match() -> None:
    """The head must end a whole token; an embedded substring does not fire.

    ``lainata`` (to borrow) contains ``lain`` but is not a name head — the word
    boundary lookahead forbids a following name character.
    """
    assert recognize_by_name_refs("Voidaan lainata varoja toiselta.") == []


def test_weak_head_vuokrasopimus_no_tail_lowercase_not_emitted() -> None:
    """``vuokrasopimuksen`` (lease agreement; weak head, no tail, lowercase) -> NOTHING.

    ``sopimus`` is a productive common noun; ``vuokrasopimuksen`` is the genitive
    of "lease agreement", not an act title. No provision tail and a lowercase
    modifier mean no positive evidence, so the precision gate suppresses it.
    """
    assert recognize_by_name_refs("Osapuolet allekirjoittivat vuokrasopimuksen.") == []


def test_alainen_adjective_partitive_not_a_laki() -> None:
    """``sellaista`` / ``veronalaista`` are ``-alainen`` adjectives, NOT a ``laki``.

    The adjective partitive ``-laista`` is orthographically identical to the
    ``laki`` elative ``laista``, but an ``-alainen``/``-nainen`` adjective in the
    partitive is never a statute. These are hard-rejected (not even gated on
    evidence) because they are non-references.
    """
    assert recognize_by_name_refs("Tarkoitetaan sellaista toimintaa.") == []
    assert recognize_by_name_refs("kyseessä on veronalaista tuloa") == []


def test_real_laki_elative_with_tail_still_emitted() -> None:
    """``ympäristönsuojelulain 5 §:ssä`` (real act + provision tail) -> STILL emitted.

    A real act name with a provision tail carries the positive evidence the
    precision gate requires, so genuine references are preserved.
    """
    mentions = recognize_by_name_refs("ympäristönsuojelulain 5 §:ssä säädetään")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:ympäristönsuojelulaki"
    assert m.target_provision_ref.section_label == "5"


def test_capitalized_strong_head_still_emitted() -> None:
    """``Kuntalain`` (capitalized strong head ``laki``) -> still emitted.

    Strong heads keep the looser behavior; a capitalized strong-head title
    mid-sentence is a genuine by-name reference even without a provision tail.
    """
    mentions = recognize_by_name_refs("Sovelletaan Kuntalain mukaisesti.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:kuntalaki"


def test_empty_text() -> None:
    assert recognize_by_name_refs("") == []
