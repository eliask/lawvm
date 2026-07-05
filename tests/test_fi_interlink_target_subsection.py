"""Interlink target previews carry momentti (subsection) / kohta (item) granularity.

Regression coverage for the fix where a locator like
``chapter:1/section:5/subsection:2`` used to collapse to the whole-section
Finlex anchor (``#chp_1__sec_5``) with the whole-section preview, discarding
the momentti. The emitted Finlex fragment must now nest ``subsec_N`` (and
``para_N`` for a kohta), matching the real Finlex AKN eId grammar
(e.g. ``chp_1__sec_5__subsec_2__para_3``), and the preview must narrow to the
cited momentti/kohta when it is locally resolvable — while section-only cites
stay byte-for-byte identical.
"""
from __future__ import annotations

import json

from lawvm.finland.interlink_targets import build_fi_interlink_target_row
from lawvm.tools.transition_graph_interlinks import LawvmInterlinkTargetRef


_MULTI_SUBSEC_XML = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta><identification>
      <FRBRWork><FRBRsubtype value="statute-consolidated"/></FRBRWork>
      <FRBRExpression>
        <FRBRdate date="2026-05-29" name="dateConsolidated"/>
        <FRBRversionNumber value="20260415"/>
      </FRBRExpression>
    </identification></meta>
    <preface><docTitle>Luonnonsuojelulaki</docTitle></preface>
    <body>
      <chapter eId="chp_1">
        <num>1 luku</num>
        <heading>Yleiset saannokset</heading>
        <section eId="chp_1__sec_5">
          <num>5 &#167;</num>
          <heading>Soveltamisala</heading>
          <subsection eId="chp_1__sec_5__subsec_1">Ensimmainen momentti kertoo laista.</subsection>
          <subsection eId="chp_1__sec_5__subsec_2">
            <content>Toinen momentti sisaltaa kohdat:</content>
            <paragraph eId="chp_1__sec_5__subsec_2__para_1"><num>1)</num><content>ensimmainen kohta;</content></paragraph>
            <paragraph eId="chp_1__sec_5__subsec_2__para_2"><num>2)</num><content>toinen kohta.</content></paragraph>
          </subsection>
        </section>
      </chapter>
    </body>
  </act>
</akomaNtoso>"""


class _MultiSubsecCorpus:
    def read_oracle(self, statute_id: str) -> bytes | None:
        assert statute_id == "2023/9"
        return _MULTI_SUBSEC_XML.encode("utf-8")

    def read_source(self, _statute_id: str) -> bytes | None:
        return None


def _row(locator: str):
    ref = LawvmInterlinkTargetRef(
        key=f"fi|normative_act|9/2023|{locator}",
        jurisdiction="fi",
        work_kind="normative_act",
        local_id="9/2023",
        work_id="fi:normative_act:9/2023",
        locator=locator,
    )
    return build_fi_interlink_target_row(ref, corpus=_MultiSubsecCorpus())


def test_subsection_extends_fragment_and_narrows_preview() -> None:
    row = _row("chapter:1/section:5/subsection:2")

    assert row.preview_status == "resolved_latest_local_oracle_preview"
    assert row.target_url == (
        "https://www.finlex.fi/fi/lainsaadanto/2023/9#chp_1__sec_5__subsec_2"
    )
    assert json.loads(row.detail_json)["target_fragment"] == "chp_1__sec_5__subsec_2"
    # Preview narrows to the cited momentti (not the whole section).
    assert "Toinen momentti" in row.preview_text
    assert "Ensimmainen momentti" not in row.preview_text
    hierarchy = json.loads(row.hierarchy_json)
    assert {"kind": "subsection", "label": "2", "title": ""} in hierarchy
    assert row.locator_label == "1 luku › 5 § › 2 mom."


def test_item_kohta_extends_fragment_to_para_and_narrows_preview() -> None:
    row = _row("chapter:1/section:5/subsection:2/paragraph:1")

    assert row.target_url == (
        "https://www.finlex.fi/fi/lainsaadanto/2023/9#chp_1__sec_5__subsec_2__para_1"
    )
    assert (
        json.loads(row.detail_json)["target_fragment"]
        == "chp_1__sec_5__subsec_2__para_1"
    )
    # Preview narrows to the cited kohta (its num is display metadata, dropped).
    assert "ensimmainen kohta" in row.preview_text
    assert "toinen kohta" not in row.preview_text
    hierarchy = json.loads(row.hierarchy_json)
    assert {"kind": "paragraph", "label": "1", "title": ""} in hierarchy


def test_bare_section_cite_is_byte_identical_to_pre_fix() -> None:
    """No subsection/paragraph → whole-section anchor + whole-section preview."""
    row = _row("chapter:1/section:5")

    assert row.target_url == (
        "https://www.finlex.fi/fi/lainsaadanto/2023/9#chp_1__sec_5"
    )
    assert json.loads(row.detail_json)["target_fragment"] == "chp_1__sec_5"
    # Whole section body: both momentit present.
    assert "Ensimmainen momentti" in row.preview_text
    assert "Toinen momentti" in row.preview_text
    hierarchy = json.loads(row.hierarchy_json)
    assert all(seg["kind"] != "subsection" for seg in hierarchy)
    assert all(seg["kind"] != "paragraph" for seg in hierarchy)


def test_unresolvable_subsection_falls_back_to_whole_section_preview() -> None:
    """A momentti absent from the body keeps the fragment but shows full section.

    The fragment is still deep-linked (Finlex may render the anchor even when
    our local oracle snapshot lacks the element), but the preview text falls
    back to the whole section rather than going blank.
    """
    row = _row("chapter:1/section:5/subsection:9")

    assert row.target_url == (
        "https://www.finlex.fi/fi/lainsaadanto/2023/9#chp_1__sec_5__subsec_9"
    )
    assert "Ensimmainen momentti" in row.preview_text
    assert "Toinen momentti" in row.preview_text
