"""Payload stage of the amendment-IR compare (``diff_op_payloads``).

The op-structure diff proves both witnesses name the SAME provision + the SAME
verb; the PAYLOAD stage proves the replacement BODY TEXT they carry for that
provision is the same too, modulo the legally-inert encoding quotient owned by
:mod:`lawvm.finland.op_equivalence`. These hermetic tests (scripted XML + PDF
body pairs — no vision backend, no farchive) pin the four behaviours:

  (a) inert-encoding differences (soft-hyphen line joins, whitespace, Cf format
      chars) do NOT produce a ``payload_mismatch`` — they fold away;
  (b) a genuine word/number difference DOES produce a ``payload_mismatch``;
  (c) REPEAL ops carry no payload — they are SKIPPED, never compared;
  (d) a target whose body is absent on one witness is TYPE-DEFERRED (counted),
      never forced into a spurious ``payload_mismatch``.

plus the two body-extraction helpers (XML inventory + PDF reading-text segmenter)
and the end-to-end wiring through ``compare_statute`` (XML bytes monkeypatched,
PDF text injected).
"""
from __future__ import annotations

from lawvm.tools import fi_amendment_ir_compare as mod
from lawvm.tools.fi_amendment_ir_compare import (
    CompareResult,
    FlatOp,
    StatuteLocator,
    _pdf_body_payloads,
    _xml_body_payloads,
    amendment_ops_from_clause_text,
    compare_statute,
    diff_op_payloads,
    flatten_clause_ast,
    result_to_json,
)

_JOHTO = (
    "muutetaan sähkön ja eräiden polttoaineiden valmisteverosta annetun lain "
    "(1260/1996) 7 §, 10 § ja 16 § sekä lisätään uusi 2 a § seuraavasti:"
)

# A main.xml amendment: the enacting johtolause (formula) + four section bodies
# (the "new provision" text the payload stage compares).
_XML_BODY = (
    '<?xml version="1.0"?>'
    '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    "<act>"
    '<preface><formula name="enactingClause"><p>' + _JOHTO + "</p></formula></preface>"
    "<body>"
    "<section><num>7 §</num><content><p>Veroprosentti on 5,9.</p></content></section>"
    "<section><num>10 §</num><content><p>Kymmenen teksti.</p></content></section>"
    "<section><num>16 §</num><content><p>Kuustoista teksti.</p></content></section>"
    "<section><num>2 a §</num><content><p>Uusi kaksi a teksti.</p></content></section>"
    "</body></act></akomaNtoso>"
).encode("utf-8")


# --------------------------------------------------------------------------- #
# (a) inert-encoding differences fold away — no payload_mismatch              #
# --------------------------------------------------------------------------- #


def test_soft_hyphen_join_is_not_a_payload_mismatch() -> None:
    # discretionary soft hyphen (U+00AD) at a line break → fused word
    xml_body = {"section:7": "Veroprosentti on kriisinratkaisusta."}
    pdf_body = {"section:7": "Veroprosentti on kriisinrat­\nkaisusta."}
    res = diff_op_payloads(xml_body, pdf_body, (FlatOp("replace", "section:7"),))
    assert res.divergences == ()
    assert res.compared == 1 and res.deferred == 0 and res.skipped == 0


def test_whitespace_and_cf_differences_are_not_payload_mismatches() -> None:
    xml_body = {
        "section:7": "Uusi   teksti\n",          # whitespace run + trailing newline
        "section:10": "Uusi​teksti.",       # ZERO WIDTH SPACE (Cf) join
    }
    pdf_body = {
        "section:7": "Uusi teksti",
        "section:10": "Uusiteksti.",
    }
    ops = (FlatOp("replace", "section:7"), FlatOp("replace", "section:10"))
    res = diff_op_payloads(xml_body, pdf_body, ops)
    assert res.divergences == ()
    assert res.compared == 2


# --------------------------------------------------------------------------- #
# (b) a genuine word/number difference IS a payload_mismatch                  #
# --------------------------------------------------------------------------- #


def test_genuine_number_difference_is_a_payload_mismatch() -> None:
    xml_body = {"section:7": "Veroprosentti on 5,9."}
    pdf_body = {"section:7": "Veroprosentti on 6,5."}  # misread number
    res = diff_op_payloads(xml_body, pdf_body, (FlatOp("replace", "section:7"),))
    assert len(res.divergences) == 1
    d = res.divergences[0]
    assert d.kind == "payload_mismatch"
    assert d.target_ref == "section:7"
    assert d.xml_op == "replace section:7" and d.pdf_op == "replace section:7"
    # detail carries the fold audit + trimmed canon forms for adjudication
    assert "folds fired:" in d.detail
    assert "5,9" in d.detail and "6,5" in d.detail
    assert res.compared == 1 and res.deferred == 0


def test_visible_dash_difference_survives_as_payload_mismatch() -> None:
    # en-dash vs em-dash is inert for PARSE text but NOT speculatively folded for
    # payload body text — it must survive as a residual (the discovery loop judges).
    xml_body = {"section:7": "16 a–b sovelletaan"}
    pdf_body = {"section:7": "16 a—b sovelletaan"}
    res = diff_op_payloads(xml_body, pdf_body, (FlatOp("replace", "section:7"),))
    assert len(res.divergences) == 1
    assert res.divergences[0].kind == "payload_mismatch"


# --------------------------------------------------------------------------- #
# (c) REPEAL ops carry no payload — skipped, never compared                   #
# --------------------------------------------------------------------------- #


def test_repeal_op_is_skipped_even_when_bodies_differ() -> None:
    # A repeal tombstone has no replacement text. Even with (spurious) differing
    # body text under the same address, the payload stage must NOT compare it.
    xml_body = {"section:5": "vanha teksti"}
    pdf_body = {"section:5": "TOTALLY DIFFERENT stray body"}
    res = diff_op_payloads(xml_body, pdf_body, (FlatOp("repeal", "section:5"),))
    assert res.divergences == ()
    assert res.skipped == 1 and res.compared == 0 and res.deferred == 0


# --------------------------------------------------------------------------- #
# (d) not payload-comparable on a witness → TYPE-DEFERRED, never forced       #
# --------------------------------------------------------------------------- #


def test_body_absent_on_one_witness_is_deferred_not_forced() -> None:
    # XML has the body, the PDF reconstruction did not recover it (scanned-thin /
    # table body). This must be DEFERRED (counted), NOT a forced payload_mismatch.
    xml_body = {"section:7": "Uusi teksti."}
    pdf_body: dict[str, str] = {}  # PDF body not segmentable for this target
    res = diff_op_payloads(xml_body, pdf_body, (FlatOp("replace", "section:7"),))
    assert res.divergences == ()
    assert res.deferred == 1 and res.compared == 0


def test_body_absent_on_xml_witness_is_deferred() -> None:
    xml_body: dict[str, str] = {}  # thin table-frame XML: no inventoried body
    pdf_body = {"section:7": "Uusi teksti."}
    res = diff_op_payloads(xml_body, pdf_body, (FlatOp("replace", "section:7"),))
    assert res.divergences == ()
    assert res.deferred == 1


# --------------------------------------------------------------------------- #
# body-extraction helpers                                                     #
# --------------------------------------------------------------------------- #


def test_xml_body_payloads_keys_align_with_op_target_refs() -> None:
    xb = _xml_body_payloads(_XML_BODY)
    assert set(xb) == {"section:7", "section:10", "section:16", "section:2a"}
    # keys match the flattened op target_refs exactly (so payload lookup is direct)
    ops = flatten_clause_ast(amendment_ops_from_clause_text(_JOHTO, statute_id="1994/800"))
    assert {op.target_ref for op in ops} == set(xb)


def test_pdf_body_payloads_last_section_stops_at_appendix_boundary() -> None:
    # The LAST section otherwise runs to EOF and swallows the trailing appendix
    # tables + gazette colophon (confirmed on 1994/800 §7: body over-ran to 3875
    # chars grabbing Liite 1/2). The body must be bounded at the first "Liite"
    # heading line so the appendix junk never enters the payload.
    page = (
        _JOHTO + "\n"
        "7 §\nVeroprosentti on 5,9.\n"
        "Liite 1\n"
        "TAULUKKO | sarake | sarake\n"
        "1 | 2 | 3\n"
        "Liite 2\n"
        "Tämä direktiivi tulee voimaan.\n"
    )
    pb = _pdf_body_payloads(page)
    assert set(pb) == {"section:7"}
    assert pb["section:7"] == "Veroprosentti on 5,9."
    # the appendix tables + directive footer are NOT in the payload
    assert "TAULUKKO" not in pb["section:7"]
    assert "Liite" not in pb["section:7"]
    assert "direktiivi" not in pb["section:7"]


def test_pdf_body_payloads_inline_liite_reference_does_not_clip_body() -> None:
    # A conservative boundary: only a BARE "Liite [N]" heading line truncates. An
    # inflected in-prose reference ("liitteen mukaisesti", "Liite 1 sisältää ...")
    # carries trailing text on its line and must NOT clip genuine body prose.
    page = (
        _JOHTO + "\n"
        "7 §\nTästä on säädetty liitteen mukaisesti tarkemmin ja jatkuu.\n"
    )
    pb = _pdf_body_payloads(page)
    assert pb["section:7"] == "Tästä on säädetty liitteen mukaisesti tarkemmin ja jatkuu."


def test_pdf_body_payloads_segments_after_johtolause() -> None:
    # The "7 §, 10 § ja 16 §" list INSIDE the johtolause must NOT be mistaken for
    # body headers: segmentation starts after "... seuraavasti:".
    page = (
        _JOHTO + "\n"
        "7 §\nUusi seitseman teksti.\n"
        "10 §\nKymmenen teksti.\n"
        "2 a §\nUusi kaksi a teksti.\n"
    )
    pb = _pdf_body_payloads(page)
    assert set(pb) == {"section:7", "section:10", "section:2a"}
    assert pb["section:7"].startswith("Uusi seitseman")
    assert "Uusi kaksi a teksti." in pb["section:2a"]


# --------------------------------------------------------------------------- #
# CompareResult accounting + JSON schema                                      #
# --------------------------------------------------------------------------- #


def test_compare_result_payload_mismatch_defeats_exact_equivalence() -> None:
    pm = mod.OpDivergence(
        "payload_mismatch", "section:7", "replace section:7", "replace section:7", "x"
    )
    ok = mod.OpDivergence("matched", "section:10", "replace section:10", "replace section:10", "")
    result = CompareResult("1994/800", "fin", "compared", (ok, pm), 2, 2, payload_compared=2)
    assert result.typed_divergence_count == 1
    assert result.exact_equivalent is False
    assert result.counts["payload_mismatch"] == 1
    payload = result_to_json(result)
    assert payload["counts"]["payload_mismatch"] == 1
    assert payload["payload_compared"] == 2
    assert payload["payload_deferred"] == 0
    assert payload["payload_skipped"] == 0


# --------------------------------------------------------------------------- #
# end-to-end wiring through compare_statute (hermetic)                        #
# --------------------------------------------------------------------------- #


def _gazette(johto: str, body: str) -> str:
    return "N:o 800\nLaki\n" + johto + "\n" + body


def test_compare_statute_wires_payload_stage_all_equivalent(monkeypatch) -> None:
    # XML bytes served from a fake farchive; PDF reading text injected. The bodies
    # match modulo whitespace → op-structure AND payload both clean → exact.
    monkeypatch.setattr(mod, "_read_farchive", lambda farchive, loc: _XML_BODY)
    body = (
        "7 §\nVeroprosentti on 5,9.\n"
        "10 §\nKymmenen teksti.\n"
        "16 §\nKuustoista teksti.\n"
        "2 a §\nUusi kaksi a teksti.\n"
    )
    result = compare_statute(
        StatuteLocator("1994/800", "fin"),
        farchive="unused",
        pdf_text_fn=lambda: _gazette(_JOHTO, body),
    )
    assert result.compare_status == "compared"
    assert result.counts["matched"] == 4
    assert result.counts["payload_mismatch"] == 0
    assert result.payload_compared == 4
    assert result.exact_equivalent is True


def test_compare_statute_surfaces_payload_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(mod, "_read_farchive", lambda farchive, loc: _XML_BODY)
    # PDF misreads section 7's number: 5,9 -> 5,8 (a genuine body divergence) while
    # the op-structure stays identical.
    body = (
        "7 §\nVeroprosentti on 5,8.\n"
        "10 §\nKymmenen teksti.\n"
        "16 §\nKuustoista teksti.\n"
        "2 a §\nUusi kaksi a teksti.\n"
    )
    result = compare_statute(
        StatuteLocator("1994/800", "fin"),
        farchive="unused",
        pdf_text_fn=lambda: _gazette(_JOHTO, body),
    )
    assert result.compare_status == "compared"
    assert result.counts["matched"] == 4  # op-structure all matched
    assert result.counts["payload_mismatch"] == 1  # but one body diverged
    pm = [d for d in result.divergences if d.kind == "payload_mismatch"]
    assert pm and pm[0].target_ref == "section:7"
    assert result.exact_equivalent is False
