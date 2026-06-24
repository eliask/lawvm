"""Tests for the canonical operative-body editorial filter.

``decode_body_text`` / ``build_provision_index`` flatten a statute's ``<p>`` set
into the analysis coordinate space. Finlex embeds NON-operative editorial
material there (``<authorialNote>`` footnotes/corrigenda, ``noteAuthorial``
version notes, document-tail boilerplate); :mod:`editorial_filter` is the single
definition of "which element is non-operative" both extractors share, so they
cannot drift. These tests pin: (1) the strip set equals the bench-critical
replay path's set; (2) editorial material is dropped while the OPERATIVE
remainder (incl. real ``entryIntoForce`` commencement provisions) is kept;
(3) both extractors stay aligned (the provision-index drift guard holds).
"""
from __future__ import annotations

import hashlib

from lawvm.finland.legal_surface.bundle import decode_body_text
from lawvm.finland.legal_surface.editorial_filter import (
    EDITORIAL_NOTE_NAMES,
    is_editorial_element,
    iter_operative_paragraphs,
    operative_itertext,
)
from lawvm.finland.legal_surface.provision_index import build_provision_index
from lawvm.tools.section_keys import _ORACLE_SECTION_STRIP_NAMES

import xml.etree.ElementTree as ET

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def test_strip_set_matches_replay_path_canonical_set() -> None:
    # The body-text filter and the replay path's oracle-section normaliser must
    # recognise the SAME editorial-note name set, else the two pipelines disagree
    # on what counts as operative text. Pin them equal so neither can silently
    # drift (section_keys may strip whole sections too; the note-name set is the
    # shared core).
    assert EDITORIAL_NOTE_NAMES == _ORACLE_SECTION_STRIP_NAMES


def test_is_editorial_element_recognises_both_forms() -> None:
    assert is_editorial_element("authorialNote", {})
    assert is_editorial_element("hcontainer", {"name": "noteAuthorial"})
    assert is_editorial_element("block", {"name": "signatures"})
    assert is_editorial_element("hcontainer", {"name": "conclusions"})
    # Operative commencement provisions are NOT editorial (a frequent trap: the
    # OPERATIVE "Tämä laki tulee voimaan …" lives in name="entryIntoForce").
    assert not is_editorial_element("hcontainer", {"name": "entryIntoForce"})
    assert not is_editorial_element("p", {})
    assert not is_editorial_element("section", {})


def test_authorial_note_footnote_stripped_operative_remainder_kept() -> None:
    # The real 1993/1071 shape: an <authorialNote> corrigendum footnote nested
    # INSIDE an operative <p>, with operative text on both sides of the note.
    xml = f"""<akomaNtoso xmlns="{_AKN}"><act><body>
      <section eId="sec_2"><num>2 §</num><content>
        <p>Maksu on 700 markkaa ja <span class="corrigendum">150 markkaa.<authorialNote
           marker="1" placement="bottom"><p>Merkitty kohta oikaistu (v. 1993),
           alkuperainen sanamuoto kuului:</p><p>Vanha teksti.</p></authorialNote></span></p>
      </content></section>
    </body></act></akomaNtoso>""".encode()
    body = decode_body_text(xml)
    assert "Merkitty kohta oikaistu" not in body  # footnote prose gone
    assert "Vanha teksti" not in body  # nested footnote <p> gone
    assert "700 markkaa" in body  # operative head kept
    assert "150 markkaa" in body  # operative corrigendum-span text kept


def test_note_authorial_version_note_stripped_commencement_kept() -> None:
    xml = f"""<akomaNtoso xmlns="{_AKN}"><act><body>
      <section eId="sec_1"><num>1 §</num><content>
        <p>Operatiivinen saannos.</p>
      </content></section>
      <hcontainer name="noteAuthorial"><content>
        <p>L:lla 269/2026 muutettu 1 momentti tulee voimaan 1.6.2026. Aiempi sanamuoto kuuluu:</p>
      </content></hcontainer>
      <hcontainer name="entryIntoForce"><content>
        <p>Tama laki tulee voimaan 1 paivana tammikuuta 2020.</p>
      </content></hcontainer>
    </body></act></akomaNtoso>""".encode()
    body = decode_body_text(xml)
    assert "Operatiivinen saannos." in body  # operative provision kept
    assert "Aiempi sanamuoto kuuluu" not in body  # editorial note stripped
    assert "L:lla 269/2026" not in body  # editorial note stripped
    assert "Tama laki tulee voimaan" in body  # operative commencement kept


def test_operative_itertext_keeps_tail_after_editorial_child() -> None:
    el = ET.fromstring(
        f'<p xmlns="{_AKN}">head '
        '<authorialNote><p>note body</p></authorialNote>'
        ' tail operative</p>'
    )
    text = "".join(operative_itertext(el))
    assert "note body" not in text
    assert "head" in text
    assert "tail operative" in text


def test_iter_operative_paragraphs_skips_editorial_p() -> None:
    root = ET.fromstring(
        f'<body xmlns="{_AKN}">'
        '<section eId="sec_1"><content><p>operative</p></content></section>'
        '<hcontainer name="noteAuthorial"><content><p>editorial</p></content></hcontainer>'
        '</body>'
    )
    texts = ["".join(operative_itertext(p)) for p, _ in iter_operative_paragraphs(root)]
    assert texts == ["operative"]


def test_decode_and_provision_index_stay_aligned_with_nested_note() -> None:
    # With editorial material present, decode_body_text and build_provision_index
    # must still produce the identical coordinate space (drift guard holds).
    xml = f"""<akomaNtoso xmlns="{_AKN}"><act><body>
      <section eId="sec_1"><num>1 §</num><content>
        <p>Eka <authorialNote><p>alaviite</p></authorialNote>pykala.</p>
      </content></section>
      <hcontainer name="noteAuthorial"><content><p>Aiempi sanamuoto kuuluu:</p></content></hcontainer>
      <section eId="sec_2"><num>2 §</num><content><p>Toka pykala.</p></content></section>
    </body></act></akomaNtoso>""".encode()
    body = decode_body_text(xml)
    th = hashlib.sha256(body.encode()).hexdigest()
    idx = build_provision_index(xml, "x/1#body", body_text=body, text_hash=th)
    # builds without raising the drift ValueError; only the two operative <p>
    assert len(idx.spans) == 2
    assert "alaviite" not in body
    assert "Aiempi sanamuoto kuuluu" not in body
