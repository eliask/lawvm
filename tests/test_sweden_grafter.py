from __future__ import annotations

import json
import subprocess
from typing import Mapping

from lawvm.core.semantic_types import IRNodeKind
from lawvm.sweden.grafter import (
    SESourceConfidence,
    enrich_se_source_record_with_doc_page,
    parse_se_amendment_register,
    parse_se_official_pdf_url,
    parse_se_source_record,
    parse_se_statute,
    se_official_doc_url,
    se_pdf_bytes_to_text,
)

_SOURCE_DOC = {
    "beteckning": "2010:800",
    "rubrik": "Skollag (2010:800)",
    "ikraftDateTime": "2010-08-01T00:00:00",
    "ikraftOvergangsbestammelse": True,
    "upphavdDateTime": None,
    "organisation": {
        "namn": "Utbildningsdepartementet",
        "namnOchEnhet": "Utbildningsdepartementet",
    },
    "forfattningstypNamn": "Lag",
    "register": {
        "forarbeten": "Prop. 2009/10:165, bet. 2009/10:UbU21, rskr. 2009/10:370",
    },
    "fulltext": {
        "utfardadDateTime": "2010-06-23T00:00:00",
        "andringInford": "t.o.m. SFS 2025:730",
        "forfattningstext": "1 kap. Inledande bestämmelser\n\n1 § Denna lag innehåller bestämmelser om skolväsendet.",
    },
    "publiceradDateTime": "2025-06-24T08:54:42.723153",
    "andringsforfattningar": [
        {
            "beteckning": "2022:115",
            "anteckningar": "ändr. 26 kap. 10, 15, 17, 18 §§",
            "ikraftDateTime": "2022-07-01T00:00:00",
            "ikraftOvergangsbestammelse": True,
            "forarbeten": "Prop. 2021/22:45, bet. 2021/22:UbU10, rskr. 2021/22:136",
            "rubrik": "Lag (2022:115) om ändring i skollagen (2010:800)",
        },
        {
            "beteckning": "2023:951",
            "anteckningar": "ändr. 1 kap. 3 §",
            "ikraftDateTime": "2024-07-01T00:00:00",
            "ikraftOvergangsbestammelse": False,
            "forarbeten": "Prop. 2023/24:21, bet. 2023/24:UbU6, rskr. 2023/24:105",
            "rubrik": "Lag (2023:951) om ändring i skollagen (2010:800)",
        },
    ],
}

_SIMPLE_STATUTE_DOC = {
    "beteckning": "2025:399",
    "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
    "ikraftDateTime": "2025-07-01T00:00:00",
    "ikraftOvergangsbestammelse": False,
    "organisation": {
        "namn": "Socialdepartementet",
        "namnOchEnhet": "Socialdepartementet",
    },
    "forfattningstypNamn": "Förordning",
    "register": {"forarbeten": None},
    "fulltext": {
        "utfardadDateTime": "2025-05-22T00:00:00",
        "andringInford": None,
        "forfattningstext": "/Träder i kraft I:2025-07-01/\n1 § Riktåldern för pension enligt 2 kap. 10 a-10 c §§ socialförsäkringsbalken ska vara 67 år för år 2031.",
    },
    "publiceradDateTime": "2025-05-26T08:54:29.3888676",
    "andringsforfattningar": [],
}

_LIST_STATUTE_DOC = {
    "beteckning": "2024:1274",
    "rubrik": "Förordning (2024:1274) om statsbidrag",
    "ikraftDateTime": "2025-01-15T00:00:00",
    "ikraftOvergangsbestammelse": True,
    "organisation": {
        "namn": "Socialdepartementet",
        "namnOchEnhet": "Socialdepartementet",
    },
    "forfattningstypNamn": "Förordning",
    "register": {"forarbeten": None},
    "fulltext": {
        "utfardadDateTime": "2024-12-12T00:00:00",
        "andringInford": None,
        "forfattningstext": (
            "Inledande bestämmelser\n\n"
            "1 § Denna förordning innehåller bestämmelser om statsbidrag.\n\n"
            "Förutsättningar för bidrag\n\n"
            "4 § Bidrag får lämnas till regioner för att\n\n"
            "1. öka antalet rådgivande samtal,\n\n"
            "2. öka följsamheten till skriftlig ordination,\n\n"
            "3. samverka med elevhälsan,\n\n"
            "4. samverka med andra aktörer, och\n\n"
            "5. förbättra och utveckla verksamhet.\n\n"
            "Övergångsbestämmelser\n\n"
            "2024:1274\n\n"
            "1. Denna förordning träder i kraft den 15 januari 2025.\n\n"
            "2. En redovisning enligt 15 § ska första gången lämnas senast den 30 juni 2026."
        ),
    },
    "publiceradDateTime": "2025-01-16T15:23:10.1022315",
    "andringsforfattningar": [],
}


def _doc_bytes(doc: Mapping[str, object]) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def test_parse_se_source_record_builds_sweden_source_layer_metadata() -> None:
    record = parse_se_source_record(_doc_bytes(_SOURCE_DOC))

    assert record.sfs_id == "2010:800"
    assert record.title == "Skollag (2010:800)"
    assert record.act_type == "lag"
    assert record.department == "Utbildningsdepartementet"
    assert record.issued_date == "2010-06-23"
    assert record.amended_through_sfs == "2025:730"
    assert record.source_confidence is SESourceConfidence.CURRENT_TEXT_PLUS_REGISTER
    assert record.source_urls.official_sfs_doc_url == "https://svenskforfattningssamling.se/doc/2010800.html"
    assert record.source_urls.rk_sfst_url == "https://rkrattsbaser.gov.se/sfst?bet=2010:800"
    assert record.parliamentary_links[0].prop_id == "2009/10:165"
    assert record.parliamentary_links[0].bet_id == "2009/10:UbU21"
    assert record.parliamentary_links[0].rskr_id == "2009/10:370"


def test_parse_se_official_pdf_url_resolves_relative_href_and_enriches_record() -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    html = """
    <html>
      <body>
        <a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>
      </body>
    </html>
    """
    pdf_url = parse_se_official_pdf_url(html, doc_url)
    assert pdf_url == "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"

    record = parse_se_source_record(_doc_bytes(_SIMPLE_STATUTE_DOC))
    enriched = enrich_se_source_record_with_doc_page(record, html, doc_url=doc_url)
    assert se_official_doc_url("2026:286") == doc_url
    assert enriched.source_urls.official_sfs_pdf_url == pdf_url


def test_parse_se_amendment_register_extracts_scope_and_preparatory_works() -> None:
    entries = parse_se_amendment_register(_doc_bytes(_SOURCE_DOC))

    assert entries
    first = entries[0]
    assert first.base_sfs_id == "2010:800"
    assert first.amending_sfs_id == "2022:115"
    assert first.effective_date == "2022-07-01"
    assert first.has_overgangsbestammelser is True
    assert "26 kap. 10" in first.scope_text
    assert first.parliamentary_links[0].prop_id == "2021/22:45"


def test_parse_se_statute_preserves_simple_section_and_temporal_marker() -> None:
    statute = parse_se_statute(_doc_bytes(_SIMPLE_STATUTE_DOC))

    assert statute.statute_id == "2025:399"
    assert statute.body.children[0].kind == IRNodeKind.SECTION
    assert statute.body.children[0].label == "1"
    assert statute.body.children[0].attrs["RestrictStartDate"] == "2025-07-01"
    assert statute.body.children[0].children[0].text.startswith(
        "Riktåldern för pension enligt 2 kap. 10 a-10 c §§"
    )


def test_parse_se_statute_captures_lists_and_transition_block() -> None:
    statute = parse_se_statute(_doc_bytes(_LIST_STATUTE_DOC))

    section_four = next(child for child in statute.body.children if child.kind == IRNodeKind.SECTION and child.label == "4")
    subsection = section_four.children[0]
    assert subsection.kind == IRNodeKind.SUBSECTION
    assert [item.label for item in subsection.children] == ["1", "2", "3", "4", "5"]

    transition = next(
        child for child in statute.body.children if child.attrs.get("role") == "transition"
    )
    transition_labels = [
        item.label
        for child in transition.children
        if child.kind == IRNodeKind.SUBSECTION
        for item in child.children
        if item.kind == IRNodeKind.ITEM
    ]
    assert transition_labels == ["1", "2"]


def test_se_pdf_bytes_to_text_uses_pdftotext_subprocess(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], capture_output: bool, timeout: int) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        assert capture_output is True
        assert timeout == 30
        return subprocess.CompletedProcess(cmd, 0, stdout="PDF text".encode("utf-8"), stderr=b"")

    monkeypatch.setattr("lawvm.sweden.grafter.subprocess.run", fake_run)
    text = se_pdf_bytes_to_text(b"%PDF-1.7 fake bytes")

    assert text == "PDF text"
    assert calls
    assert calls[0][0] == "pdftotext"
    assert calls[0][-1] == "-"
