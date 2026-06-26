"""Tests for the H6 surface exception/condition cue recognizer.

The recognizer records SURFACE FACTS ONLY. These tests assert the surface shapes
(cue_kind / marker_text / scope_hint bounding / spans / status), the
no-boundable-scope -> None behaviour, the jos-inside-a-word precision guard, and
explicitly assert that NO legal conclusion vocabulary is ever produced.
"""
from __future__ import annotations

import dataclasses
from dataclasses import fields

import pytest

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references.exception_condition import (
    ExceptionConditionCue,
    recognize_exception_condition_cues,
)


def _by_marker(cues, marker: str) -> ExceptionConditionCue:
    for c in cues:
        if c.marker_text == marker:
            return c
    raise AssertionError(
        f"no cue for marker {marker!r}; got {[c.marker_text for c in cues]}"
    )


def _scope_text(text: str, cue: ExceptionConditionCue) -> str:
    sh = cue.scope_hint
    assert sh is not None
    return text[sh.byte_offset : sh.byte_offset + sh.byte_len]


# ---------------------------------------------------------------------------
# Exception markers
# ---------------------------------------------------------------------------


def test_exception_ei_kuitenkaan() -> None:
    text = "Säännöstä ei kuitenkaan sovelleta alle 15-vuotiaisiin."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "ei kuitenkaan")
    assert c.cue_kind == "EXCEPTION"
    assert c.exception_status == "surface_fact_only"


def test_exception_poiketen_siita_mita_multiword() -> None:
    text = "Poiketen siitä mitä 5 §:ssä säädetään, lupa voidaan myöntää."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "Poiketen siitä mitä")
    assert c.cue_kind == "EXCEPTION"


def test_exception_lukuun_ottamatta() -> None:
    text = "Laki koskee kaikkia, lukuun ottamatta valtion virastoja."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "lukuun ottamatta")
    assert c.cue_kind == "EXCEPTION"
    assert "valtion virastoja" in _scope_text(text, c)


def test_exception_sen_estamatta() -> None:
    text = "Sen estämättä mitä edellä säädetään, asia ratkaistaan heti."
    cues = recognize_exception_condition_cues(text)
    # marker_text is verbatim — sentence-initial cue keeps its capital.
    c = _by_marker(cues, "Sen estämättä")
    assert c.cue_kind == "EXCEPTION"


def test_exception_jollei_paitsi_ellei() -> None:
    text = "Lupa myönnetään, jollei estettä ole. Kielto on voimassa, ellei toisin määrätä."
    cues = recognize_exception_condition_cues(text)
    assert _by_marker(cues, "jollei").cue_kind == "EXCEPTION"
    assert _by_marker(cues, "ellei").cue_kind == "EXCEPTION"


def test_exception_paitsi() -> None:
    text = "Kaikki asiakirjat ovat julkisia, paitsi salassa pidettävät."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "paitsi")
    assert c.cue_kind == "EXCEPTION"


# ---------------------------------------------------------------------------
# Condition markers
# ---------------------------------------------------------------------------


def test_condition_jos_clause_initial() -> None:
    text = "Lupa peruutetaan, jos edellytykset eivät enää täyty."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "jos")
    assert c.cue_kind == "CONDITION"
    assert "edellytykset eivät enää täyty" in _scope_text(text, c)


def test_condition_kun_clause_initial() -> None:
    text = "Maksu peritään, kun päätös on annettu."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "kun")
    assert c.cue_kind == "CONDITION"


def test_condition_mikali() -> None:
    text = "Mikäli hakemus on puutteellinen, hakijaa kehotetaan täydentämään sitä."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "Mikäli")  # verbatim case preserved
    assert c.cue_kind == "CONDITION"


def test_condition_silta_osin_kuin() -> None:
    text = "Säännöksiä sovelletaan siltä osin kuin muualla ei toisin säädetä."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "siltä osin kuin")
    assert c.cue_kind == "CONDITION"


def test_condition_edellyttaen_etta_and_silla_edellytyksella() -> None:
    text = (
        "Tuki myönnetään edellyttäen että ehdot täyttyvät. "
        "Lupa annetaan sillä edellytyksellä että maksu suoritetaan."
    )
    cues = recognize_exception_condition_cues(text)
    assert _by_marker(cues, "edellyttäen että").cue_kind == "CONDITION"
    assert _by_marker(cues, "sillä edellytyksellä että").cue_kind == "CONDITION"


# ---------------------------------------------------------------------------
# scope_hint bounding
# ---------------------------------------------------------------------------


def test_scope_hint_bounded_to_next_clause_boundary() -> None:
    text = "Lupa peruutetaan, jos edellytykset puuttuvat; muutoin se pysyy voimassa."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "jos")
    scope = _scope_text(text, c)
    # Bounded at the ';' — must not run into the following clause.
    assert scope == "edellytykset puuttuvat"
    assert "muutoin" not in scope


def test_scope_hint_spans_round_trip_to_source() -> None:
    text = "Maksu peritään, kun päätös on annettu."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "kun")
    s = c.source_span
    assert text[s.byte_offset : s.byte_offset + s.byte_len] == "kun"
    assert _scope_text(text, c) == "päätös on annettu"


def test_marker_with_no_boundable_scope_yields_none() -> None:
    # Cue is the last token before end-of-text/boundary: nothing to bound.
    text = "Sovelletaan, paitsi."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "paitsi")
    assert c.scope_hint is None  # still a valid cue, scope just not boundable


# ---------------------------------------------------------------------------
# Precision guards for the common jos/kun
# ---------------------------------------------------------------------------


def test_jos_inside_a_word_does_not_match() -> None:
    # "jossa", "josta", "jostakin" all contain "jos" as a substring.
    text = "Asiassa, jossa on useita osapuolia, sovelletaan erityissäännöksiä."
    cues = recognize_exception_condition_cues(text)
    assert all(c.marker_text != "jos" for c in cues), (
        f"'jos' must not match inside 'jossa'; got {[c.marker_text for c in cues]}"
    )


def test_kun_inside_a_word_does_not_match() -> None:
    # "kunta", "kunnes" contain "kun" as a substring.
    text = "Kunta vastaa palveluista kunnes toisin päätetään."
    cues = recognize_exception_condition_cues(text)
    assert all(c.marker_text != "kun" for c in cues), (
        f"'kun' must not match inside 'kunta'/'kunnes'; got "
        f"{[c.marker_text for c in cues]}"
    )


def test_jos_mid_clause_is_suppressed_for_precision() -> None:
    # A 'jos' that is NOT clause-initial-ish is deliberately skipped.
    text = "Tämä tarkoittaa sitä jos asiaa tarkastellaan tarkemmin."
    cues = recognize_exception_condition_cues(text)
    assert all(c.marker_text != "jos" for c in cues), (
        "mid-clause 'jos' must be suppressed (precision over recall)"
    )


def test_kun_clause_initial_after_period_matches() -> None:
    text = "Päätös tehdään. Kun määräaika on kulunut, asia raukeaa."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "Kun")  # verbatim case preserved
    assert c.cue_kind == "CONDITION"


# ---------------------------------------------------------------------------
# No legal conclusion / surface-only invariants
# ---------------------------------------------------------------------------


def test_no_legal_conclusion_vocabulary_ever() -> None:
    text = (
        "Säännöstä ei kuitenkaan sovelleta, jos ehdot täyttyvät. "
        "Lupa annetaan, paitsi erityisestä syystä. "
        "Poiketen siitä mitä säädetään, mikäli hakemus puuttuu, asia raukeaa."
    )
    cues = recognize_exception_condition_cues(text)
    assert cues, "expected several cues in the combined text"
    banned = {
        "override",
        "overridden",
        "overrides",
        "invalid",
        "unenforceable",
        "void",
        "limitation",
        "derogation",
        "exempt",
        "exemption",
        "kumoaa",
        "pätemätön",
        "syrjäyttää",
    }
    for cue in cues:
        assert cue.exception_status == "surface_fact_only"
        assert cue.cue_kind in ("EXCEPTION", "CONDITION")
        for f in fields(cue):
            val = getattr(cue, f.name)
            assert val not in banned


def test_frozen_type() -> None:
    text = "Lupa peruutetaan, jos ehdot puuttuvat."
    cues = recognize_exception_condition_cues(text)
    c = _by_marker(cues, "jos")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.marker_text = "x"  # ty: ignore[invalid-assignment]


def test_source_file_propagated_to_spans() -> None:
    text = "Maksu peritään, kun päätös on annettu."
    cues = recognize_exception_condition_cues(text, source_file="711/2022")
    c = _by_marker(cues, "kun")
    assert c.source_span.source_file == "711/2022"
    assert c.scope_hint is not None
    assert c.scope_hint.source_file == "711/2022"


def test_empty_when_no_guard_present() -> None:
    text = "Tämä on tavallinen virke ilman ehtoja tai poikkeuksia."
    cues = recognize_exception_condition_cues(text)
    assert cues == []


def test_document_order() -> None:
    text = "Lupa annetaan, jos ehto täyttyy, mutta ei kuitenkaan poikkeustapauksissa."
    cues = recognize_exception_condition_cues(text)
    offsets = [c.source_span.byte_offset for c in cues]
    assert offsets == sorted(offsets)
    # Both kinds present and in order.
    kinds = [c.cue_kind for c in cues]
    assert "CONDITION" in kinds and "EXCEPTION" in kinds


def test_cues_are_typed_dataclass() -> None:
    text = "Sovelletaan, mikäli ehdot täyttyvät."
    cues = recognize_exception_condition_cues(text)
    assert all(isinstance(c, ExceptionConditionCue) for c in cues)
    assert all(isinstance(c.source_span, SourceSpan) for c in cues)


# ---------------------------------------------------------------------------
# Hand-sample coverage tally
# ---------------------------------------------------------------------------

_HAND_SAMPLE: tuple[tuple[str, str, str], ...] = (
    # (clause, expected marker_text, expected cue_kind)
    ("Säännöstä ei kuitenkaan sovelleta tähän.", "ei kuitenkaan", "EXCEPTION"),
    ("Kaikki ovat julkisia, paitsi salaiset.", "paitsi", "EXCEPTION"),
    ("Lupa myönnetään, jollei estettä ole.", "jollei", "EXCEPTION"),
    ("Sovelletaan siltä osin kuin tarpeen.", "siltä osin kuin", "CONDITION"),
    ("Asia raukeaa, mikäli hakemus puuttuu.", "mikäli", "CONDITION"),
    ("Maksu peritään, kun päätös annetaan.", "kun", "CONDITION"),
)


def test_hand_sample_coverage_tally() -> None:
    covered = 0
    for clause, marker, kind in _HAND_SAMPLE:
        cues = recognize_exception_condition_cues(clause)
        matched = [c for c in cues if c.marker_text == marker and c.cue_kind == kind]
        assert matched, (
            f"clause not covered: {clause!r}; got "
            f"{[(c.marker_text, c.cue_kind) for c in cues]}"
        )
        covered += 1
    assert covered == len(_HAND_SAMPLE)
