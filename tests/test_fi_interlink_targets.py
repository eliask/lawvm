from __future__ import annotations

import json

from lawvm.finland.interlink_targets import build_fi_interlink_target_row
from lawvm.tools.transition_graph_interlinks import LawvmInterlinkTargetRef


class _Corpus:
    def read_oracle(self, statute_id: str) -> bytes | None:
        assert statute_id == "2023/9"
        return """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <meta><identification><FRBRWork><FRBRsubtype value="statute-consolidated"/></FRBRWork></identification></meta>
    <preface><docTitle>Luonnonsuojelulaki</docTitle></preface>
    <body>
      <chapter eId="chp_4">
        <num>4 luku</num>
        <heading>Luonnonsuojelun menettelyt</heading>
        <section eId="chp_4__sec_35">
          <num>35 §</num>
          <heading>Hankkeiden ja suunnitelmien arviointi</heading>
          <subsection eId="chp_4__sec_35__subsec_1">Arviointi tehdään asianmukaisella tavalla.</subsection>
        </section>
      </chapter>
    </body>
  </act>
</akomaNtoso>""".encode("utf-8")

    def read_source(self, _statute_id: str) -> bytes | None:
        return None


def test_fi_interlink_target_row_has_urls_and_oracle_preview() -> None:
    ref = LawvmInterlinkTargetRef(
        key="fi|normative_act|9/2023|section:35",
        jurisdiction="fi",
        work_kind="normative_act",
        local_id="9/2023",
        work_id="fi:normative_act:9/2023",
        locator="section:35",
    )

    row = build_fi_interlink_target_row(ref, corpus=_Corpus())

    assert row.target_url == "https://www.finlex.fi/fi/lainsaadanto/2023/9"
    assert json.loads(row.target_links_json) == [
        {
            "label": "Finlex",
            "rel": "canonical",
            "url": "https://www.finlex.fi/fi/lainsaadanto/2023/9",
        },
        {
            "label": "Säädöskokoelma",
            "rel": "source_publication",
            "url": "https://www.finlex.fi/fi/lainsaadanto/saadoskokoelma/2023/9",
        },
    ]
    assert row.preview_status == "resolved_latest_local_oracle_preview"
    assert row.title == "Luonnonsuojelulaki"
    assert row.locator_label == "35 §"
    assert "Arviointi tehdään" in row.preview_text
    hierarchy = json.loads(row.hierarchy_json)
    assert hierarchy == [
        {"kind": "chapter", "label": "4", "title": "Luonnonsuojelun menettelyt"},
        {"kind": "section", "label": "35 §", "title": "Hankkeiden ja suunnitelmien arviointi"},
    ]


def test_fi_interlink_target_row_rejects_unstatute_target_id() -> None:
    ref = LawvmInterlinkTargetRef(
        key="fi|normative_act|unknown|",
        jurisdiction="fi",
        work_kind="normative_act",
        local_id="unknown",
        work_id="fi:normative_act:unknown",
        locator=None,
    )

    row = build_fi_interlink_target_row(ref, corpus=_Corpus())

    assert row.preview_status == "unsupported_fi_target_id"
    assert row.target_url is None
    assert row.target_links_json == "[]"
