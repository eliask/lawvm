"""Tests for the additive provision-boundary substrate (ProvisionIndex).

The body decode flattens the AKN body to ``<p>`` content and DROPS the
``<num>`` markers + container nesting, so the decoded text carries no
provision boundaries. :func:`build_provision_index` re-attaches the §/momentti/
kohta identity as a parallel index over the SAME coordinate space, sourced from
the AKN structure (eId / ``<num>``), never guessed from the text.

Covered: provision recovery on a synthetic AKN body (section / momentti / kohta,
each adjudicated against the known structure), the fail-loud path (a ``<p>`` with
no provision ancestor), the join-drift guard, the span->provision query incl. the
between-paragraph AMBIGUOUS case, and the carrier invariants.
"""
from __future__ import annotations

import hashlib

import pytest

from lawvm.core.legal_surface_tokens import (
    AMBIGUOUS,
    ProvisionIndex,
    ProvisionSpan,
)
from lawvm.finland.legal_surface.bundle import decode_body_text
from lawvm.finland.legal_surface.provision_index import build_provision_index

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


# A synthetic AKN body mirroring the Finlex shape the live corpus emits: a
# chapter, a section with two momentti, a section whose first momentti carries an
# enumerated kohta list, plus a preface <p> with no provision ancestry.
_BODY = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <preface><p>123/2024 Esimerkkilaki</p></preface>
    <body>
      <chapter eId="chp_1">
        <num>1 luku</num>
        <heading>Yleiset säännökset</heading>
        <section eId="chp_1__sec_1">
          <num>1 §</num>
          <heading>Soveltamisala</heading>
          <subsection eId="chp_1__sec_1__subsec_1">
            <content><p>Ensimmäisen momentin teksti.</p></content>
          </subsection>
          <subsection eId="chp_1__sec_1__subsec_2">
            <content><p>Toisen momentin teksti.</p></content>
          </subsection>
        </section>
        <section eId="chp_1__sec_6">
          <num>6 §</num>
          <heading>Luettelo</heading>
          <subsection eId="chp_1__sec_6__subsec_1">
            <intro><p>Seuraavat ryhmat:</p></intro>
            <paragraph eId="chp_1__sec_6__subsec_1__para_1">
              <num>1)</num>
              <content><p>ensimmainen kohta;</p></content>
            </paragraph>
            <paragraph eId="chp_1__sec_6__subsec_1__para_2">
              <num>2)</num>
              <content><p>toinen kohta.</p></content>
            </paragraph>
          </subsection>
        </section>
      </chapter>
    </body>
  </act>
</akomaNtoso>"""


def _index(xml: str) -> tuple[str, ProvisionIndex]:
    xb = xml.encode("utf-8")
    body = decode_body_text(xb)
    th = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return body, build_provision_index(xb, "123/2024#body", body_text=body, text_hash=th)


def _query(body: str, idx: ProvisionIndex, needle: str) -> ProvisionSpan:
    pos = body.find(needle)
    assert pos >= 0, needle
    sp = idx.provision_at(pos, pos + len(needle))
    assert sp is not AMBIGUOUS, needle
    return sp  # type: ignore[return-value]


def test_recovers_momentti_paths() -> None:
    body, idx = _index(_BODY)
    sp1 = _query(body, idx, "Ensimmäisen momentin teksti.")
    assert sp1.provision_path() == "1/1"
    assert sp1.eid == "chp_1__sec_1__subsec_1"
    assert sp1.section_label == "1"
    assert sp1.subsection_num == 1
    assert sp1.chapter_label == "1"

    sp2 = _query(body, idx, "Toisen momentin teksti.")
    assert sp2.provision_path() == "1/2"
    assert sp2.subsection_num == 2


def test_recovers_kohta_path() -> None:
    body, idx = _index(_BODY)
    sp = _query(body, idx, "ensimmainen kohta;")
    assert sp.provision_path() == "6/1/1"
    assert sp.eid == "chp_1__sec_6__subsec_1__para_1"
    assert sp.item_label == "1"

    sp2 = _query(body, idx, "toinen kohta.")
    assert sp2.provision_path() == "6/1/2"
    assert sp2.item_label == "2"


def test_intro_p_inherits_subsection_but_no_kohta() -> None:
    body, idx = _index(_BODY)
    sp = _query(body, idx, "Seuraavat ryhmat:")
    # the chapeau <p> sits in the subsection, above any kohta
    assert sp.section_label == "6"
    assert sp.subsection_num == 1
    assert sp.item_label == ""
    assert sp.provision_path() == "6/1"


def test_preface_p_is_unmapped_failloud_not_fabricated() -> None:
    body, idx = _index(_BODY)
    pos = body.find("123/2024 Esimerkkilaki")
    sp = idx.provision_at(pos, pos + len("123/2024 Esimerkkilaki"))
    assert sp is not AMBIGUOUS
    assert sp.mapped is False
    assert sp.unmapped_reason  # non-empty witness
    assert sp.provision_path() == ""
    assert sp.eid == ""


def test_coverage_census() -> None:
    _, idx = _index(_BODY)
    cov = idx.coverage()
    # 6 <p>: preface(unmapped) + 2 momentti + 1 intro + 2 kohta = 5 mapped, 1 not
    assert cov.total_spans == 6
    assert cov.mapped_spans == 5
    assert cov.unmapped_spans == 1
    assert 0.0 < cov.mapped_fraction < 1.0
    assert cov.mapped_chars + cov.unmapped_chars == sum(
        sp.char_end - sp.char_start for sp in idx.spans
    )


def test_between_paragraph_gap_is_ambiguous() -> None:
    # the newline join chars between two paragraphs are not a provision
    body, idx = _index(_BODY)
    first = body.find("Ensimmäisen momentin teksti.")
    end = first + len("Ensimmäisen momentin teksti.")
    # the char at `end` is the '\n' separator (a gap, not in the index)
    assert body[end] == "\n"
    assert idx.provision_at(end, end + 1) is AMBIGUOUS


def test_spans_are_ordered_and_nonoverlapping() -> None:
    _, idx = _index(_BODY)
    prev = 0
    for sp in idx.spans:
        assert sp.char_start >= prev
        prev = sp.char_end


def test_empty_and_unparseable_bytes_yield_empty_index() -> None:
    assert build_provision_index(b"", "x#body", body_text="", text_hash="h").spans == ()
    assert (
        build_provision_index(b"<<bad", "x#body", body_text="", text_hash="h").spans
        == ()
    )


# ── carrier invariants (fail-loud construction) ───────────────────────────────


def test_mapped_span_rejects_unmapped_reason() -> None:
    with pytest.raises(ValueError):
        ProvisionSpan(char_start=0, char_end=5, mapped=True, unmapped_reason="x")


def test_unmapped_span_requires_reason_and_no_path() -> None:
    with pytest.raises(ValueError):
        ProvisionSpan(char_start=0, char_end=5, mapped=False)
    with pytest.raises(ValueError):
        ProvisionSpan(
            char_start=0,
            char_end=5,
            mapped=False,
            unmapped_reason="r",
            section_label="5",
        )


def test_index_rejects_overlapping_spans() -> None:
    with pytest.raises(ValueError):
        ProvisionIndex(
            source_unit_id="u",
            text_hash="h",
            spans=(
                ProvisionSpan(char_start=0, char_end=10, mapped=False, unmapped_reason="r"),
                ProvisionSpan(char_start=5, char_end=12, mapped=False, unmapped_reason="r"),
            ),
        )
