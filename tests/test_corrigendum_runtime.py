from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.finland import corrigendum as corr
from lawvm.finland import corrigendum_records
from lawvm.core.semantic_types import StructuralAction


def test_patch_table_loads_from_text_corpus(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivu 1, johtolause",
                "wrong_text": "18 §:n 4 ja 5 momentti ja 31 § ja",
                "correct_text": "18 §:n 4 ja 5 momentti, 31 §:n 1 momentti sekä",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table.amendment_count() == 1
    ops = table._patches["2016/442"]
    assert len(ops) == 1
    assert ops[0].op_id == "corr/442/2016/0"
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "18 §:n 4 ja 5 momentti ja 31 § ja"
    assert ops[0].text_patch.replacement == "18 §:n 4 ja 5 momentti, 31 §:n 1 momentti sekä"
    assert ops[0].text_patch is not None
    assert ops[0].payload is None


def test_patch_table_keeps_johtolauseen_jalkeen_in_body_patch_lane(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "official#0",
                "source_pdf": "x",
                "statute_id": "2011/715",
                "amendment_id": "33/2024",
                "lang": "fi",
                "correction_index": 1,
                "correction_type": "prose",
                "location_desc": "Sivulla 1, johtolauseen jälkeen",
                "wrong_text": "5 b §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
                "correct_text": "5 a §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert "2024/33" not in table._patches
    assert table._body_patches["2024/33"] == [
        (
            "5 b §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "5 a §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "Sivulla 1, johtolauseen jälkeen",
        )
    ]


def test_patch_table_preserves_unsupported_table_corrections(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "akn/fi/x#0",
                "source_pdf": "x",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "table",
                "location_desc": "Sivu 2, taulukko 1",
                "wrong_text": "1 | old",
                "correct_text": "1 | new",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._patches == {}
    assert table._body_patches == {}
    assert len(table._unsupported_patches) == 1
    assert table._unsupported_patches[0]["reason"] == "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED"
    assert table._unsupported_patches[0]["correction_type"] == "table"


def test_load_from_source_routes_prose_johtolause_location_to_johtolause_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "akn/fi/act/statute-consolidated/2012/980/media/corrigenda/sk20220604_1.pdf#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2012/980/media/corrigenda/sk20220604_1.pdf",
                "statute_id": "2012/980",
                "amendment_id": "604/2022",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "prose",
                "location_desc": "Sivulla 1, lain johtolauseessa",
                "wrong_text": "2 §:n 2 momentti ja 9 §",
                "correct_text": "2 §:n 3 momentti ja 9 §",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert "2022/604" in table._patches
    assert "2022/604" not in table._body_patches
    op = table._patches["2022/604"][0]
    assert op.target == corr.LegalAddress(path=(("johtolause", ""),))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "2 §:n 2 momentti ja 9 §"
    assert op.text_patch.replacement == "2 §:n 3 momentti ja 9 §"


def test_load_from_source_skips_manual_expanded_duplicate_johtolause_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records = [
        {
            "stable_id": "official#0",
            "source_pdf": "x",
            "statute_id": "2014/1194",
            "amendment_id": "821/2017",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "johtolause",
            "location_desc": "Sivulla 1, johtolauseessa",
            "wrong_text": "… 6 luvun otsikko, 1 § sekä 1 §:n otsikko ja 1, 2 ja 5 momentti sekä…",
            "correct_text": "… 6 luvun otsikko, 1 §:n otsikko ja 1, 2 ja 5 momentti sekä…",
            "extraction_source": "both+vision",
            "parse_error": None,
        },
        {
            "stable_id": "expanded#3013",
            "source_pdf": "unknown",
            "statute_id": "2014/1194",
            "amendment_id": "2017/821",
            "lang": "fi",
            "correction_index": 3013,
            "correction_type": "johtolause",
            "location_desc": "",
            "wrong_text": "6 luvun otsikko, 1 § sekä 1 §:n otsikko ja 1, 2 ja 5 momentti sekä",
            "correct_text": "6 luvun otsikko, 1 §:n otsikko ja 1, 2 ja 5 momentti sekä",
            "extraction_source": "manual_expanded",
            "parse_error": None,
        },
    ]
    records_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table.amendment_count() == 1
    ops = table._patches["2017/821"]
    assert len(ops) == 1
    assert ops[0].op_id == "corr/821/2017/0"


def test_parse_corrigendum_populates_structured_text_replace_fields() -> None:
    pdf_text = (
        "Oikaisuja Suomen säädöskokoelmaan\n\n"
        "Suomen säädöskokoelmaan n:o 442/2016\n"
        "Sivulla 1, johtolause on:\n"
        "väärä teksti\n"
        "Pitää olla:\n"
        "oikea teksti\n"
    )

    ops = corr.parse_corrigendum(pdf_text, "442/2016")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "väärä teksti"
    assert ops[0].text_patch.replacement == "oikea teksti"
    assert ops[0].text_patch is not None
    assert ops[0].payload is None


def test_parse_corrigendum_preserves_unsupported_add_blocks() -> None:
    pdf_text = (
        "Oikaisuja Suomen säädöskokoelmaan\n\n"
        "Suomen säädöskokoelmaan n:o 442/2016\n"
        "Sivulla 1, johtolauseesta puuttuu virke, joka kuuluu:\n"
        "lisätty teksti\n"
    )

    result = corr.parse_corrigendum(pdf_text, "442/2016")

    assert len(result) == 0
    assert len(result.unsupported_patches) == 1
    assert result.unsupported_patches[0].reason == "FINLAND.CORRIGENDUM_ADD_UNSUPPORTED"
    assert result.unsupported_patches[0].correction_kind == "ADD"
    assert result.unsupported_patches[0].correct_text == "lisätty teksti"


def test_extract_inline_corrections_strips_only_corrigendum_authorial_notes() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <span class="corrigendum">oikea teksti
            <authorialNote>
              <p>Alkuperainen teksti.</p>
              <p>vaara teksti</p>
            </authorialNote>
          </span>
        </content>
      </section>
      <hcontainer name="editorial">
        <authorialNote>
          <p>legitimate note outside corrigendum</p>
        </authorialNote>
      </hcontainer>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")

    ops, cleaned = corr.extract_inline_corrections(xml, "2000/1")

    assert len(ops) == 1
    assert b"vaara teksti" not in cleaned
    assert b"legitimate note outside corrigendum" in cleaned


def test_extract_inline_corrections_records_missing_authorial_note() -> None:
    corr.clear_misapplied_records()
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_1"><content><span class="corrigendum">oikea teksti</span></content></section></body></act></akomaNtoso>"""

    ops, cleaned = corr.extract_inline_corrections(xml, "2000/1")
    records = corr.get_misapplied_records()

    assert ops == []
    assert b"oikea teksti" in cleaned
    assert records[-1]["reason"] == "FINLAND.INLINE_CORRIGENDUM_MISSING_AUTHORIAL_NOTE"


def test_extract_inline_corrections_records_missing_wrong_text() -> None:
    corr.clear_misapplied_records()
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_1"><content><span class="corrigendum">oikea teksti<authorialNote><p>Merkitty kohta oikaistu (v. 2001).</p></authorialNote></span></content></section></body></act></akomaNtoso>"""

    ops, cleaned = corr.extract_inline_corrections(xml, "2000/1")
    records = corr.get_misapplied_records()

    assert ops == []
    assert b"authorialNote" not in cleaned
    assert b"oikea teksti" in cleaned
    assert records[-1]["reason"] == "FINLAND.INLINE_CORRIGENDUM_MISSING_WRONG_TEXT"


def test_apply_text_replace_with_mode_exact() -> None:
    patched, mode = corr._apply_text_replace_with_mode(
        b"<body><p>vaara teksti</p></body>",
        "vaara teksti",
        "oikea teksti",
    )

    assert mode == "exact"
    assert b"oikea teksti" in patched


def test_apply_text_replace_with_mode_tag_tolerant() -> None:
    patched, mode = corr._apply_text_replace_with_mode(
        b"<body><p>alpha <ref>beta</ref> gamma delta</p></body>",
        "alpha beta gamma delta",
        "korjattu teksti",
    )

    assert mode == "tag_tolerant"
    assert b"korjattu teksti" in patched


def test_apply_text_replace_deterministic_does_not_use_fuzzy_recovery() -> None:
    original = b"<body><p>alpha beta gamma delta</p></body>"
    patched, mode = corr._apply_text_replace_deterministic(
        original,
        "alpha beta gammb delta",
        "korjattu teksti",
    )

    assert mode is None
    assert patched == original


def test_load_patch_records_merges_official_and_adjudication_files(tmp_path: Path) -> None:
    official_path = tmp_path / "corrigendum_official_fi.jsonl"
    adjudication_path = tmp_path / "corrigendum_adjudications_fi.jsonl"
    official_path.write_text(
        json.dumps(
            {
                "stable_id": "x#0",
                "source_pdf": "x",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivu 1",
                "wrong_text": "wrong",
                "correct_text": "correct",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    adjudication_path.write_text(
        json.dumps(
            {
                "stable_id": "x#0",
                "verified_in_source": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = corrigendum_records.load_patch_records(official_path)

    assert len(rows) == 1
    assert rows[0]["stable_id"] == "x#0"
    assert rows[0]["verified_in_source"] == 0
    assert rows[0]["wrong_text"] == "wrong"


def test_load_patch_records_does_not_implicitly_fallback_to_sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(corrigendum_records, "_OFFICIAL_JSONL", tmp_path / "missing_official.jsonl")
    monkeypatch.setattr(corrigendum_records, "_ADJUDICATIONS_JSONL", tmp_path / "missing_adjudications.jsonl")

    rows = corrigendum_records.load_patch_records()

    assert rows == []


# ---------------------------------------------------------------------------
# _parse_location_section tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("location_desc, expected_sec, expected_subsec", [
    # "2 ja 3 rivi" means rows 2 and 3, not subsection — no 'moment' keyword → subsec=None
    ("Sivulla 2707, 32 §:n 2 ja 3 rivi", "32", None),
    ("Sivulla 4455, 24 §:n 2 momentti", "24", "2"),
    ("Sivulla 12, 15 a §:ssä", "15a", None),
    ("Sivulla 8, 6 §:n 2 momentissa", "6", "2"),
    ("Sivulla 5, 3 §:n 1 momentin 3 kohdassa", "3", "1"),
    ("Sivulla 1772, 5 §:n 4 a kohta", "5", None),  # no 'moment' keyword
    ("Sivulla 3, 17 c §:n 2 momentin riveillä 2-4", "17c", "2"),
    ("Sivulla 4515, 15 b §:n 1 momentti", "15b", "1"),
    # No section at all
    ("Sivulla 1, johtolause", None, None),
])
def test_parse_location_section(
    location_desc: str, expected_sec: str | None, expected_subsec: str | None
) -> None:
    sec, subsec = corr._parse_location_section(location_desc)
    assert sec == expected_sec
    assert subsec == expected_subsec


# ---------------------------------------------------------------------------
# _find_element_range tests
# ---------------------------------------------------------------------------

_SAMPLE_BODY_XML = b"""\
<body>
  <section eId="sec_6">
    <subsection eId="sec_6__subsec_1"><content><p>subsec 1 text</p></content></subsection>
    <subsection eId="sec_6__subsec_2"><content><p>subsec 2 old text</p></content></subsection>
  </section>
  <section eId="sec_15a">
    <subsection eId="sec_15a__subsec_1"><content><p>15a content</p></content></subsection>
  </section>
  <section eId="chp_3__sec_10">
    <subsection eId="chp_3__sec_10__subsec_1"><content><p>chapter-prefixed content</p></content></subsection>
  </section>
</body>"""


def test_find_element_range_section_only() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "15a", None)
    assert result is not None
    start, end = result
    chunk = _SAMPLE_BODY_XML[start:end]
    assert b"15a content" in chunk
    assert b"sec_6" not in chunk


def test_find_element_range_section_with_subsec() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "6", "2")
    assert result is not None
    start, end = result
    chunk = _SAMPLE_BODY_XML[start:end]
    assert b"subsec 2 old text" in chunk
    assert b"subsec 1 text" not in chunk


def test_find_element_range_chapter_prefixed() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "10", "1")
    assert result is not None
    start, end = result
    chunk = _SAMPLE_BODY_XML[start:end]
    assert b"chapter-prefixed content" in chunk


def test_find_element_range_missing_returns_none() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "99", None)
    assert result is None


# ---------------------------------------------------------------------------
# patch_source_body_xml blocked location-scoped retry test
# ---------------------------------------------------------------------------

def test_patch_source_body_xml_blocks_location_scoped_retry(tmp_path: Path, monkeypatch) -> None:
    """Failed full-body replace must stay failed instead of retrying on a guessed section."""
    xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body>
  <section eId="sec_6">
    <subsection eId="sec_6__subsec_1"><content><p>other content</p></content></subsection>
    <subsection eId="sec_6__subsec_2"><content><p>old text here</p></content></subsection>
  </section>
  <section eId="sec_7">
    <subsection eId="sec_7__subsec_1"><content><p>irrelevant</p></content></subsection>
  </section>
</body></act></akomaNtoso>"""

    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps({
            "stable_id": "x#0",
            "source_pdf": "x",
            "statute_id": "2005/671",
            "amendment_id": "671/2000",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 1772, 6 §:n 2 momentissa",
            "wrong_text": "old text here",
            "correct_text": "new text here",
            "parse_error": None,
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)
    corr.clear_misapplied_records()

    original_apply = corr._apply_text_replace
    calls = {"n": 0}

    def _patched_apply(xml_bytes: bytes, wrong: str, correct: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return xml_bytes, False
        return original_apply(xml_bytes, wrong, correct)

    monkeypatch.setattr(corr, "_apply_text_replace", _patched_apply)

    # Confirm location_desc stored in tuple
    patches = table._body_patches.get("2000/671", [])
    assert len(patches) == 1
    assert patches[0][2] == "Sivulla 1772, 6 §:n 2 momentissa"

    patched, applied = table.patch_source_body_xml(xml, "2000/671")
    records = corr.get_misapplied_records()
    assert calls["n"] == 1
    assert applied == []
    assert patched == xml
    blocked = next(
        record for record in records if record["reason"] == "FINLAND.CORRIGENDUM_BODY_LOCATION_FALLBACK_BLOCKED"
    )
    assert blocked["section"] == "6"
    assert blocked["subsection"] == "2"


def test_patch_source_body_xml_full_body_success_still_applies(tmp_path: Path, monkeypatch) -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_6"><subsection eId="sec_6__subsec_2"><content><p>old text here</p></content></subsection></section></body></act></akomaNtoso>"""
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps({
            "stable_id": "x#0",
            "source_pdf": "x",
            "statute_id": "2005/671",
            "amendment_id": "671/2000",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 1772, 6 §:n 2 momentissa",
            "wrong_text": "old text here",
            "correct_text": "new text here",
            "parse_error": None,
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)
    corr.clear_misapplied_records()

    patched, applied = table.patch_source_body_xml(xml, "2000/671")

    assert applied == ["body_patch/2000/671/0"]
    assert b"new text here" in patched
    assert b"old text here" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_records_invalid_candidate_xml() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2016/442"] = "2013/23"
    table._patches["2016/442"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/442/2016/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text="vaara teksti",
            correct_text="<broken",
            source=corr.OperationSource(
                statute_id="corr/442/2016",
                raw_text="Sivulla 1, johtolause",
                corrected_by="442/2016",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><preface><preamble><block name="substitutions"><p>vaara teksti</p></block></preamble></preface><body><section eId="sec_1"><num>1 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2016/442")
    records = corr.get_misapplied_records()

    assert patched == xml
    assert applied == []
    assert records[-1]["reason"] == "post_patch_xml_invalid"
    assert records[-1]["op_id"] == "corr/442/2016/0"


def test_patch_source_xml_recovers_with_single_text_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2013/426"] = "2010/297"
    table._patches["2013/426"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/426/2013/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseen rivillä 2", "johtolause"),
            wrong_text="muutetaan maksulaitoslain (297/2010) 21 a §, 37 §:n 2 momentti, 46 §:n 3 momentti ja",
            correct_text="muutetaan maksulaitoslain (297/2010) 21 a §, 37 §:n 3 momentti, 46 §:n 3 momentti ja",
            source=corr.OperationSource(
                statute_id="corr/426/2013",
                raw_text="Sivulla 1, johtolauseen rivillä 2",
                corrected_by="426/2013",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="substitutions"><i>muutetaan</i> maksulaitoslain (<affectedDocument href="/akn/fi/act/statute/2010/297">297/2010</affectedDocument>) 21 a \xc2\xa7, 37 \xc2\xa7:n 2 momentti, 46 \xc2\xa7:n 3 momentti ja 47 \xc2\xa7:n 2 momentti, sellaisena kuin niista on 21 a \xc2\xa7 laeissa 899/2011 ja 764/2012, seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_37"><num>37 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2013/426")

    assert applied == ["corr/426/2013/0"]
    assert b"37 \xc2\xa7:n 3 momentti" in patched
    assert b"37 \xc2\xa7:n 2 momentti" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_with_whitespace_tolerant_single_tail_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2022/642"] = "2005/390"
    table._patches["2022/642"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/642/2022/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text=(
                "muutetaan vaarallisten kemikaalien ja räjähteiden käsittelyn turvallisuudesta annetun lain (390/2005) 6\n"
                "§:n 21 kohta sekä 126 ja 131 §, sellaisina kuin niistä ovat 6 §:n 21 kohta laissa 358/2015 ja 126 § laissa\n"
                "795/2020, seuraavasti:"
            ),
            correct_text=(
                "muutetaan vaarallisten kemikaalien ja räjähteiden käsittelyn turvallisuudesta annetun lain (390/2005) 6\n"
                "§:n 21 kohta ja 131 §, sellaisena kuin niistä on 6 §:n 21 kohta laissa 358/2015, sekä\n"
                "lisätään 126 §:ään, sellaisena kuin se on laissa 795/2020, uusi 2 ja 3 momentti seuraavasti:"
            ),
            source=corr.OperationSource(
                statute_id="corr/642/2022",
                raw_text="Sivulla 1, johtolause",
                corrected_by="642/2022",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="substitutions"><i>muutetaan</i>
 vaarallisten kemikaalien ja rajahteiden kasittelyn turvallisuudesta annetun lain (<affectedDocument href="/akn/fi/act/statute/2005/390">390/2005</affectedDocument>)
 ) 6 \xc2\xa7:n 21 kohta sek\xc3\xa4 126 ja 131 \xc2\xa7, sellaisina kuin niist\xc3\xa4 ovat 6 \xc2\xa7:n 21 kohta laissa 358/2015 ja 126 \xc2\xa7 laissa 795/2020, seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_126"><num>126 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2022/642")

    assert applied == ["corr/642/2022/0"]
    assert b"lis\xc3\xa4t\xc3\xa4\xc3\xa4n 126 \xc2\xa7:\xc3\xa4\xc3\xa4n" in patched
    assert b"sek\xc3\xa4 126 ja 131 \xc2\xa7" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_insertion_only_single_text_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2019/979"] = "2017/519"
    table._patches["2019/979"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/979/2019/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseessa", "johtolause"),
            wrong_text=(
                "muutetaan 14, 20, 28, 29 ja 52 §, näistä 28, 29 ja 52 § "
                "sellaisina kuin ne ovat asetuksessa 1158/2017,"
            ),
            correct_text=(
                "muutetaan 14, 15, 20, 28, 29 ja 52 §, näistä 28, 29 ja 52 § "
                "sellaisina kuin ne ovat asetuksessa 1158/2017,"
            ),
            source=corr.OperationSource(
                statute_id="corr/979/2019",
                raw_text="Sivulla 1, johtolauseessa",
                corrected_by="979/2019",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Sosiaali- ja terveysministeri\xc3\xb6n p\xc3\xa4\xc3\xa4t\xc3\xb6ksen mukaisesti</p><blockContainer><block name="insertions"><i>lis\xc3\xa4t\xc3\xa4\xc3\xa4n</i> lakiiin uusi 10 \xc2\xa7, jolloin nykyinen 10 \xc2\xa7 siirtyy 10 a \xc2\xa7:ksi, sek\xc3\xa4</block></blockContainer><blockContainer><block name="substitutions"><i>muutetaan</i> 14, 20, 28, 29 ja 52 \xc2\xa7, n\xc3\xa4ist\xc3\xa4 28, 29 ja 52 \xc2\xa7 sellaisina kuin ne ovat asetuksessa 1158/2017, seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_14"><num>14 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2019/979")

    assert applied == ["corr/979/2019/0"]
    assert b"14, 15, 20, 28, 29 ja 52" in patched
    assert b"14, 20, 28, 29 ja 52" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_with_visible_text_delta_single_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2024/33"] = "2011/715"
    table._patches["2024/33"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/33/2024/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text=(
                "lisätään luvan saaneista oikeudenkäyntiavustajista annettuun lakiin "
                "(715/2011) uusi 5 b § seuraavasti:"
            ),
            correct_text=(
                "lisätään luvan saaneista oikeudenkäyntiavustajista annettuun lakiin "
                "(715/2011) uusi 5 a § seuraavasti:"
            ),
            source=corr.OperationSource(
                statute_id="corr/33/2024",
                raw_text="Sivulla 1, johtolause",
                corrected_by="33/2024",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="insertions"><i>lisataan</i> luvan saaneista oikeudenkayntiavustajista annettuun lakiin (<affectedDocument href="/akn/fi/act/statute/2011/715">715/2011</affectedDocument>) uusi 5 b \xc2\xa7 seuraavasti:</block></blockContainer></formula></preamble><body><section><num>5 b \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2024/33")

    assert applied == ["corr/33/2024/0"]
    assert b"uusi 5 a \xc2\xa7 seuraavasti:" in patched
    assert b"uusi 5 b \xc2\xa7 seuraavasti:" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_preserves_later_insertions_when_inserting_into_johtolause() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2022/283"] = "2016/549"
    wrong = (
        "muutetaan tupakkalain (549/2016) 95 §, 96 §:n 1 momentti, "
        "97 §:n 1 momentin 8 kohta ja 117 §,"
    )
    correct = (
        "muutetaan tupakkalain (549/2016) 95 §, 96 §:n otsikko ja 1 momentti, "
        "97 §:n 1 momentin 8 kohta ja 117 §,"
    )
    table._patches["2022/283"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/283/2022/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseessa", "johtolause"),
            wrong_text=wrong,
            correct_text=correct,
            source=corr.OperationSource(
                statute_id="corr/283/2022",
                raw_text="Sivulla 1, johtolauseessa",
                corrected_by="283/2022",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><blockContainer><block name="substitutions">muutetaan tupakkalain (<affectedDocument href="/akn/fi/act/statute/2016/549">549/2016</affectedDocument>) 95 \xc2\xa7, 96 \xc2\xa7:n 1 momentti, 97 \xc2\xa7:n 1 momentin 8 kohta ja 117 \xc2\xa7,</block></blockContainer><blockContainer><block name="insertions">lis\xc3\xa4t\xc3\xa4\xc3\xa4n 32 \xc2\xa7:\xc3\xa4\xc3\xa4n uusi 4 ja 5 momentti sek\xc3\xa4 lakiin uusi 35 a \xc2\xa7 seuraavasti:</block></blockContainer></formula></preamble><body/></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2022/283")

    assert applied == ["corr/283/2022/0"]
    assert b"96 \xc2\xa7:n otsikko ja 1 momentti" in patched
    assert b"uusi 4 ja 5 momentti" in patched
    assert b"uusiotsikko" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_single_ellipsis_witness_against_visible_johtolause() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2025/854"] = "2013/599"
    table._patches["2025/854"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/854/2025/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseessa", "johtolause"),
            wrong_text="lisätään 5 §:n 1 momenttiin … uusi 1 kohta seuraavasti:",
            correct_text="lisätään 5 §:n 1 momenttiin … uusi 17 kohta seuraavasti:",
            source=corr.OperationSource(
                statute_id="corr/854/2025",
                raw_text="Sivulla 1, johtolauseessa",
                corrected_by="854/2025",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="substitutions"><i>muutetaan</i> kemikaalilain (<affectedDocument href="/akn/fi/act/statute/2013/599">599/2013</affectedDocument>) 5 \xc2\xa7:n 1 momentin 16 kohta, sellaisena kuin se on laissa 193/2025, ja</block></blockContainer><blockContainer><block name="insertions"><i>lis\xc3\xa4t\xc3\xa4\xc3\xa4n</i> 5 \xc2\xa7:n 1 momenttiin, sellaisena kuin se on osaksi laeissa 554/2014, 711/2020, 547/2023 ja 193/2025, uusi 1 kohta seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_5"><num>5 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2025/854")

    assert applied == ["corr/854/2025/0"]
    assert b"uusi 17 kohta seuraavasti:" in patched
    assert b"uusi 1 kohta seuraavasti:" not in patched
    assert corr.get_misapplied_records() == []


def test_apply_visible_text_delta_multi_slot_recovers_two_slot_johtolause_corrigendum() -> None:
    fragment = b"""
<p>Eduskunnan paatoksen mukaisesti</p>
<blockContainer>
  <block name="repeals"><i>kumotaan</i> ik\xc3\xa4\xc3\xa4ntyneen v\xc3\xa4est\xc3\xb6n toimintakyvyn tukemisesta sek\xc3\xa4 i\xc3\xa4kk\xc3\xa4iden sosiaali- ja terveyspalveluista annetun lain (<affectedDocument href="/akn/fi/act/statute/2012/980">980/2012</affectedDocument>) 2 \xc2\xa7:n 2 momentti ja 9 \xc2\xa7, </block>
  <block name="repeals-originals">sellaisena kuin niist\xc3\xa4 on 2 \xc2\xa7:n 2 momentti laissa 267/2015,</block>
</blockContainer>
"""
    wrong = (
        "kumotaan ikääntyneen väestön toimintakyvyn tukemisesta sekä iäkkäiden sosiaali- ja\n"
        "terveyspalveluista annetun lain (980/2012) 2 §:n 2 momentti ja 9 §,\n"
        "sellaisena kuin niistä on 2 §:n 2 momentti laissa 267/2015"
    )
    correct = (
        "kumotaan ikääntyneen väestön toimintakyvyn tukemisesta sekä iäkkäiden sosiaali- ja\n"
        "terveyspalveluista annetun lain (980/2012) 2 §:n 3 momentti ja 9 §,\n"
        "sellaisena kuin niistä on 2 §:n 3 momentti laissa 267/2015"
    )

    patched, ok = corr._apply_visible_text_delta_multi_slot(fragment, wrong, correct)

    assert ok is True
    assert b"2 \xc2\xa7:n 3 momentti ja 9 \xc2\xa7" in patched
    assert b"2 \xc2\xa7:n 3 momentti laissa 267/2015" in patched
    assert b"2 \xc2\xa7:n 2 momentti ja 9 \xc2\xa7" not in patched


def test_patch_source_body_xml_records_invalid_candidate_xml() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2000/671"] = "2005/671"
    table._body_patches["2000/671"] = [("old text here", "<broken", "Sivulla 1772, 6 §:n 2 momentissa")]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_6"><subsection eId="sec_6__subsec_2"><content><p>old text here</p></content></subsection></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_body_xml(xml, "2000/671")
    records = corr.get_misapplied_records()

    assert patched == xml
    assert applied == []
    assert records[-1]["reason"] == "post_patch_xml_invalid"
    assert records[-1]["op_id"] == "body_patch/2000/671/0"


def test_patch_source_body_xml_recovers_with_visible_text_delta_single_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2024/33"] = "2011/715"
    table._body_patches["2024/33"] = [
        (
            "5 b §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "5 a §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "Sivulla 1, johtolauseen jälkeen",
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body><section><num>5 b \xc2\xa7</num>
<heading>Oikeudenk\xc3\xa4yntiavustajalautakunnan henkil\xc3\xb6st\xc3\xb6</heading></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_body_xml(xml, "2024/33")

    assert applied == ["body_patch/2024/33/0"]
    assert b"<num>5 a \xc2\xa7</num>" in patched
    assert b"<num>5 b \xc2\xa7</num>" not in patched
    assert corr.get_misapplied_records() == []


def test_load_from_source_skips_duplicate_manual_body_patch_family(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text(
        "- amendment_id: 2018/541\n"
        "  correction_type: body_text\n"
        "  wrong_text: |\n"
        "    Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
        "    varhaiskasvatuksesta voidaan maarata kuukausimaksu.\n"
        "  correct_text: |\n"
        "    Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
        "    varhaiskasvatuksesta voidaan maarata kuukausimaksu. Maksu voidaan peri\u00e4 enint\u00e4\u00e4n yhdelt\u00e4toista\n"
        "    kalenterikuukaudelta toimintavuoden aikana.\n",
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "official#0",
                "source_pdf": "x",
                "statute_id": "2016/1503",
                "amendment_id": "541/2018",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "prose",
                "location_desc": "Sivulla 1, 4 §:n 1 momentti",
                "wrong_text": (
                    "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja "
                    "perhepaivahoitona jarjestettavasta\n"
                    "varhaiskasvatuksesta voidaan maarata kuukausimaksu."
                ),
                "correct_text": (
                    "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja "
                    "perhepaivahoitona jarjestettavasta\n"
                    "varhaiskasvatuksesta voidaan maarata kuukausimaksu. Maksu voidaan peri\u00e4 "
                    "enint\u00e4\u00e4n yhdelt\u00e4toista\n"
                    "kalenterikuukaudelta toimintavuoden aikana."
                ),
                "llm_confidence": "high",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._body_patches["2018/541"] == [
        (
            "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
            "varhaiskasvatuksesta voidaan maarata kuukausimaksu.",
            "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
            "varhaiskasvatuksesta voidaan maarata kuukausimaksu. Maksu voidaan periä enintään yhdeltätoista\n"
            "kalenterikuukaudelta toimintavuoden aikana.",
            "Sivulla 1, 4 §:n 1 momentti",
        )
    ]


def test_load_from_source_skips_near_duplicate_body_patch_variant_for_same_location(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    manual_path.write_text("[]\n", encoding="utf-8")
    records = [
        {
            "stable_id": "official#0",
            "source_pdf": "x",
            "statute_id": "2021/616",
            "amendment_id": "616/2021",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 26, 69 §",
            "wrong_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 3 § tulee kuitenkin voimaanvasta 1 päivänä\n"
                "tammikuuta 2023."
            ),
            "correct_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 2 § tulee kuitenkin voimaanvasta 1 päivänä\n"
                "tammikuuta 2023."
            ),
            "parse_error": None,
        },
        {
            "stable_id": "official#1",
            "source_pdf": "x",
            "statute_id": "2021/616",
            "amendment_id": "616/2021",
            "lang": "fi",
            "correction_index": 1,
            "correction_type": "prose",
            "location_desc": "Sivulla 26, 69 §",
            "wrong_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 3 § tulee kuitenkin voimaavasta 1 päivänä tammikuuta 2023."
            ),
            "correct_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 2 § tulee kuitenkin voimaavasta 1 päivänä tammikuuta 2023."
            ),
            "parse_error": None,
        },
    ]
    records_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._body_patches["2021/616"] == [
        (
            "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
            "Lain 3 § tulee kuitenkin voimaanvasta 1 päivänä\n"
            "tammikuuta 2023.",
            "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
            "Lain 2 § tulee kuitenkin voimaanvasta 1 päivänä\n"
            "tammikuuta 2023.",
            "Sivulla 26, 69 §",
        )
    ]


def test_load_from_source_records_manual_yaml_failure(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    manual_path = tmp_path / "corrigendum_manual.yaml"
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "x#0",
                "source_pdf": "x",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivu 1, johtolause",
                "wrong_text": "wrong",
                "correct_text": "correct",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    manual_path.write_text(":\n", encoding="utf-8")
    corr.clear_misapplied_records()

    monkeypatch.setattr(corr, "_MANUAL_YAML", manual_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)
    records = corr.get_misapplied_records()

    assert table._loaded is True
    assert records[-1]["reason"] == "FINLAND.CORRIGENDUM_MANUAL_YAML_LOAD_FAILED"
    assert records[-1]["fallback"] == "db_only"
