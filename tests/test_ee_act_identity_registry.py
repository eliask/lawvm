from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lawvm.estonia.act_identity_registry import EEActIdentityRecord, lookup_ee_act_identity
from lawvm.estonia import grafter
from lawvm.estonia import target_resolution
from lawvm.estonia.grafter import (
    _matches_target_statute_header,
    _is_omnibus_amendment,
    _para_contains_direct_target_clause,
    _parse_muutmisseadus_ops,
    parse_ee_amendment_ops,
)
from lawvm.estonia.ee_instruction_waist import read_sentence_target_meta


def test_lookup_ee_act_identity_finds_exact_akt_viide_and_alias() -> None:
    by_id = lookup_ee_act_identity(akt_viide="ee/104072013003")
    by_alias = lookup_ee_act_identity(alias="Ehitusseadus")

    assert by_id is not None
    assert by_id is by_alias
    assert by_id.akt_viide == "ee/104072013003"
    assert "Ehitusseadus" in by_id.aliases


def test_target_resolution_module_uses_registry_evidence_before_title_heuristics(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/target_resolution_module",
        canonical_title="Seadus A",
        title_variants=("Seadus A",),
        aliases=("Määrus B",),
        source_family="target_resolution",
    )
    monkeypatch.setattr(target_resolution, "lookup_ee_act_identity", lambda **_kwargs: record)

    assert target_resolution.title_matches_para("Seadus A", "Määrus B") is True
    assert target_resolution.strict_title_match_para("Seadus A", "Määrus B") is True
    assert target_resolution.matches_target_statute_header("Seadus A", "Määrus B") is True


def test_target_resolution_matches_adjective_genitive_titles() -> None:
    assert target_resolution.title_matches_para(
        "Korruptsioonivastane seadus",
        "korruptsioonivastase seaduse",
    ) is True


def test_registry_evidence_wins_for_single_target_preambul_when_title_heuristic_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <sisu>
        <preambul>
          <tavatekst><b>Ehitusseaduse</b> § 5, 6, 7, 8, 9 ja 10 muutmine</tavatekst>
        </preambul>
        <sisuTekst>
          <tavatekst>Ehitusseaduse § 5, 6, 7, 8, 9 ja 10 muudatused:</tavatekst>
          <HTMLKonteiner><![CDATA[
            <p><b>1)</b> § 5 jäetakse välja.</p>
            <p><b>2)</b> § 6 asendatakse sõnaga "kattega".</p>
            <p><b>3)</b> § 7 jäetakse välja.</p>
            <p><b>4)</b> § 8 asendatakse sõnaga "kattega".</p>
            <p><b>5)</b> § 9 jäetakse välja.</p>
            <p><b>6)</b> § 10 asendatakse sõnaga "kattega".</p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/104072013003",
        target_title="Ehitusseadus",
    )

    assert len(ops) == 6
    section_actions = {
        dict(op.target.path)["section"]: op.action.value for op in ops if "section" in dict(op.target.path)
    }
    assert section_actions == {
        "5": "repeal",
        "6": "text_replace",
        "7": "repeal",
        "8": "text_replace",
        "9": "repeal",
        "10": "text_replace",
    }


def test_registry_evidence_wins_for_self_referential_amendment_guard_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/self_referential_guard",
        canonical_title="Ehitusseaduse muutmise seadus",
        title_variants=("Ehitusseaduse muutmise seadus",),
        aliases=("Ehitusseadust",),
        source_family="self_referential_amendment_act",
    )
    monkeypatch.setattr(grafter, "_matches_target_statute_header", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", lambda **_kwargs: record)

    assert grafter._looks_like_self_referential_amendment_act_para(
        "Ehitusseadust",
        "Ehitusseaduse muutmise seadus",
        "Ehitusseaduse muutmise seaduse muutmine",
    ) is True


def test_self_referential_guard_does_not_skip_target_title_with_internal_ja() -> None:
    from lawvm.estonia.fetch import _DEFAULT_RT_DB
    from farchive import Farchive

    act = "103072014017"
    url = f"https://www.riigiteataja.ee/akt/{act}.xml"
    try:
        raw = Farchive(_DEFAULT_RT_DB, readonly=True).get(url)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"EE archive unavailable in this environment: {exc}")
    assert raw is not None

    ops = parse_ee_amendment_ops(
        raw,
        source_id=act,
        target_title="Teadus- ja arendustegevuse korralduse seadus",
    )

    assert any(op.target.path == (("section", "9_2"),) for op in ops)
    assert any(op.target.path == (("section", "7"), ("subsection", "2_2")) for op in ops)
    assert all(op.source is not None for op in ops)


def test_registry_evidence_wins_for_matches_target_statute_header_when_heuristics_fail(
    monkeypatch,
) -> None:
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "_strict_title_match_para", lambda *_args, **_kwargs: False)

    assert _matches_target_statute_header(
        "Ehitusseadus",
        "Ehitusseaduse muutmise seadus",
    ) is True


def test_registry_evidence_wins_for_strict_title_match_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/strict_title_match",
        canonical_title="Määrus X",
        title_variants=("Määrus X",),
        aliases=("Ehitusseadus",),
        source_family="strict_title_match",
    )
    monkeypatch.setattr(target_resolution, "lookup_ee_act_identity", lambda **_kwargs: record)

    assert grafter._strict_title_match_para(
        "Ehitusseadus",
        "Määrus X",
    ) is True


def test_registry_evidence_wins_for_old_format_section_header_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/old_format_section_header",
        canonical_title="§ 1. Määrus X seaduses",
        title_variants=("§ 1. Määrus X seaduses",),
        aliases=("§ 1. Määrus X seaduses", "Ehitusseadus"),
        source_family="old_format_section_header",
    )
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", lambda **_kwargs: record)

    xml = """
    <oigusakt>
      <sisu>
        <HTMLKonteiner><![CDATA[
          <p><b>§ 1. Määrus X seaduses</b></p>
          <p><b>1)</b> Ehitusseaduse § 5 lõiget 2 muudetakse ja sõnastatakse järgmiselt: "tekst".</p>
        ]]></HTMLKonteiner>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = grafter._parse_old_format_amendment_ops(
        ET.fromstring(xml),
        "ee/test/old_format_section_header",
        target_title="Ehitusseadus",
    )

    assert len(ops) == 1
    assert dict(ops[0].target.path)["section"] == "5"
    assert ops[0].action.value == "replace"


def test_registry_evidence_does_not_admit_unrelated_old_format_header() -> None:
    target = (
        "Põllu- ja metsamajanduse taristu arendamise ning hoiu investeeringutoetus "
        "Maaeluministeeriumi valitsemisala riigiasutustele"
    )

    assert target_resolution.old_format_section_matches_target(
        target,
        "§ 1. Keskkonnaministri 16. juuni 2021. a määruse nr 32 "
        "„Aadressiandmete süsteem” muutmine",
    ) is False
    assert target_resolution.old_format_section_matches_target(
        target,
        "§ 26. Maaeluministri 7. mai 2018. a määruse nr 26 "
        "„Põllu- ja metsamajanduse taristu arendamise ning hoiu investeeringutoetus "
        "Regionaal- ja Põllumajandusministeeriumi valitsemisala riigiasutustele” muutmine",
    ) is True
    assert target_resolution.old_format_section_matches_target(
        target,
        "§ 25. Maaeluministri 29. juuli 2015. a määruse nr 76 "
        "„Põllu- ja metsamajanduse taristu arendamise ja hoiu investeeringutoetus” muutmine",
    ) is False


def test_old_format_title_alias_routes_only_target_section_for_2024_013() -> None:
    from lawvm.estonia.fetch import fetch_rt_xml, open_rt_archive

    archive = open_rt_archive(readonly=True)
    source_xml = fetch_rt_xml("128122024013", archive=archive)
    target = (
        "Põllu- ja metsamajanduse taristu arendamise ning hoiu investeeringutoetus "
        "Maaeluministeeriumi valitsemisala riigiasutustele"
    )

    ops = parse_ee_amendment_ops(source_xml, "ee/128122024013", target_title=target)

    assert len(ops) == 2
    assert all("old_format_amendment_section:26" in op.provenance_tags for op in ops)
    assert all("old_format_amendment_section:25" not in op.provenance_tags for op in ops)
    assert ops[0].witness_rule_id == "ee_statute_title_text_delete"
    assert ops[1].witness_rule_id == "ee_old_format_wrapper_scope_inherited"
    assert ops[1].target.path == ()
    assert ops[1].payload is not None
    assert ops[1].payload.attrs["old_text"] == "Põllumajandus- ja Toiduamet"


def test_registry_evidence_wins_for_title_match_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/title_match",
        canonical_title="Ehitusseadus",
        title_variants=("Ehitusseadus",),
        aliases=("Ehitusseaduse muutmise seadus",),
        source_family="title_match",
    )
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", lambda **_kwargs: record)

    assert grafter._title_matches_para(
        "Ehitusseadus",
        "Ehitusseaduse muutmise seadus",
    ) is True


def test_title_match_does_not_cross_match_kohtute_and_kohtutaituri() -> None:
    assert target_resolution.title_matches_para(
        "Kohtutäituri seadus",
        "Kohtute seaduse muutmine",
    ) is False


def test_title_match_does_not_cross_match_main_law_and_rakendamise_law() -> None:
    assert target_resolution.title_matches_para(
        "Kaitseväeteenistuse seadus",
        "Kaitseväeteenistuse seaduse rakendamise seaduse muutmine",
    ) is False
    assert target_resolution.strict_title_match_para(
        "Kaitseväeteenistuse seadus",
        "Kaitseväeteenistuse seaduse rakendamise seaduse muutmine",
    ) is False


def test_registry_evidence_wins_for_direct_target_clause_when_heuristics_fail(
    monkeypatch,
) -> None:
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <sisu>
        <paragrahv>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> Ehitusseaduse § 5 lõiget 2 muudetakse ja § 6 tunnistatakse kehtetuks.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)

    para = next(root.iterfind(".//{akt_1_10.06.2010}paragrahv"))

    assert _para_contains_direct_target_clause(para, "akt_1_10.06.2010", "Ehitusseadus") is True


def test_registry_evidence_wins_for_direct_target_clause_via_fragment_alias_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/direct_target_clause_fragment_alias",
        canonical_title="Ehitusseadus",
        title_variants=("Ehitusseadus",),
        aliases=("Ehitusseaduse",),
        source_family="direct_target_clause_fragment_alias",
    )
    calls: list[dict[str, str]] = []

    def fake_lookup(**kwargs: str) -> EEActIdentityRecord | None:
        calls.append(kwargs)
        if (kwargs.get("alias") or "").startswith("Ehitusseaduse"):
            return record
        return None

    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", fake_lookup)

    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <sisu>
        <paragrahv>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> Ehitusseaduse § 5 lõiget 2 muudetakse ja § 6 tunnistatakse kehtetuks.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)

    para = next(root.iterfind(".//{akt_1_10.06.2010}paragrahv"))

    assert _para_contains_direct_target_clause(para, "akt_1_10.06.2010", "Ehitusseadus") is True
    assert any((call.get("alias") or "").startswith("Ehitusseaduse") for call in calls)


def test_direct_target_clause_does_not_match_generic_seadust_wrapper() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvPealkiri>Konkurentsiseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> seadust täiendatakse §-dega 73 10–73 20 järgmises sõnastuses: "tekst".</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)
    para = next(root.iterfind(".//{muutmisseadus_1_10.02.2010}paragrahv"))

    assert _para_contains_direct_target_clause(
        para,
        "muutmisseadus_1_10.02.2010",
        "Väärteomenetluse seadustik",
    ) is False


def test_nested_direct_target_law_clause_replays_owned_sentence_replace() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>Majandustegevuse seadustiku üldosa seaduses tehakse järgmised muudatused:</tavatekst>
            <HTMLKonteiner><![CDATA[
              <p><b>59)</b> seadust täiendatakse §-ga 89<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 89<sup>1</sup>. Kalapüügiseaduse muutmine</b></p>
              <p>Kalapüügiseaduse § 17<sup>1</sup> lõike 1 esimene lause muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„Kala esmakokkuostuga tohib tegeleda äriregistris registreeritud ettevõtja, kellele on antud tegevusluba.”;”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <paragrahvPealkiri>Korrakaitseseaduse muutmise ja rakendamise seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 1 lõige 1 muudetakse ja sõnastatakse järgmiselt: „võõras tekst.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test/nested_direct_target_law_clause",
        target_title="Kalapüügiseadus",
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action.value == "replace"
    assert op.target.path == (("section", "17_1"), ("subsection", "1"))
    assert op.payload is not None
    assert op.payload.text == (
        "Kala esmakokkuostuga tohib tegeleda äriregistris registreeritud ettevõtja, "
        "kellele on antud tegevusluba."
    )
    sentence_meta = read_sentence_target_meta(op.payload)
    assert sentence_meta is not None
    assert sentence_meta.sentence_indexes == (0,)
    assert "ee_nested_direct_target_law_clause" in op.provenance_tags
    assert "old_format_amendment_item:59" in op.provenance_tags


def test_registry_evidence_wins_for_omnibus_filter_when_strict_title_match_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(grafter, "_strict_title_match_para", lambda *_args, **_kwargs: False)
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <sisu>
        <paragrahv>
          <paragrahvPealkiri>Ehitusseaduse muutmise seadus</paragrahvPealkiri>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)

    assert _is_omnibus_amendment(root, "akt_1_10.06.2010", "Ehitusseadus") is False


def test_registry_evidence_wins_for_omnibus_target_paragraph_without_keywords_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/omnibus_target_paragraph_without_keywords",
        canonical_title="Ehitusseadus",
        title_variants=("Ehitusseadus",),
        aliases=("Ehitusseaduse erisätted",),
        source_family="omnibus_target_paragraph_without_keywords",
    )
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "_strict_title_match_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", lambda **_kwargs: record)

    xml = """
    <oigusakt xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvPealkiri>Ehitusseaduse erisätted</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> § 5 jäetakse välja.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvPealkiri>Muu seaduse muutmine</paragrahvPealkiri>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)

    ops = _parse_muutmisseadus_ops(
        root,
        "tyviseadus_1_10.02.2010",
        "tyviseadus_1_10.02.2010",
        "Ehitusseadus",
    )

    assert len(ops) == 1
    assert dict(ops[0].target.path)["section"] == "5"
    assert ops[0].action.value == "repeal"


def test_registry_evidence_wins_for_untitled_omnibus_attachment_when_heuristics_fail(
    monkeypatch,
) -> None:
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "_is_omnibus_amendment", lambda *_args, **_kwargs: True)
    xml = """
    <oigusakt xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>296</paragrahvNr>
          <sisuTekst>
            <tavatekst><b>Ehitusseaduse</b> § 5, 6, 7, 8, 9 ja 10 muutmine</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> § 5 jäetakse välja.</p>
              <p><b>2)</b> § 6 asendatakse sõnaga "kattega".</p>
              <p><b>3)</b> § 7 jäetakse välja.</p>
              <p><b>4)</b> § 8 asendatakse sõnaga "kattega".</p>
              <p><b>5)</b> § 9 jäetakse välja.</p>
              <p><b>6)</b> § 10 asendatakse sõnaga "kattega".</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)

    ops = _parse_muutmisseadus_ops(root, "akt_1_10.06.2010", "tyviseadus_1_10.02.2010", "Ehitusseadus")

    assert len(ops) == 6
    section_actions = {
        dict(op.target.path)["section"]: op.action.value for op in ops if "section" in dict(op.target.path)
    }
    assert section_actions == {
        "5": "repeal",
        "6": "text_replace",
        "7": "repeal",
        "8": "text_replace",
        "9": "repeal",
        "10": "text_replace",
    }


def test_registry_evidence_wins_for_constitutional_review_when_title_heuristic_fails(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/constitutional_review",
        canonical_title="Ehitusseadus",
        title_variants=("Ehitusseadus",),
        aliases=("Ehitusseaduse",),
        source_family="constitutional_review",
    )
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", lambda **_kwargs: record)

    xml = """
    <oigusakt xmlns="riigikohtu_otsus">
      <body>
        <p>põhiseaduspärasuse kontroll</p>
        <p>Tunnistada Ehitusseaduse § 5 lõige 2 põhiseadusega vastuolus olevaks ja kehtetuks.</p>
      </body>
    </oigusakt>
    """.encode("utf-8")

    ops = grafter._parse_constitutional_review_ops(
        xml,
        source_id="ee/test/constitutional_review",
        target_title="Ehitusseadus",
    )

    assert len(ops) == 1
    assert ops[0].source is not None
    assert ops[0].source.title == "põhiseaduspärasuse kontroll"


def test_registry_evidence_wins_for_untitled_non_omnibus_attachment_when_heuristics_fail(
    monkeypatch,
) -> None:
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "_is_omnibus_amendment", lambda *_args, **_kwargs: False)
    xml = """
    <oigusakt xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <sisuTekst>
            <tavatekst><b>Ehitusseaduse</b> § 5, 6, 7, 8, 9 ja 10 muutmine</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> § 5 jäetakse välja.</p>
              <p><b>2)</b> § 6 asendatakse sõnaga "kattega".</p>
              <p><b>3)</b> § 7 jäetakse välja.</p>
              <p><b>4)</b> § 8 asendatakse sõnaga "kattega".</p>
              <p><b>5)</b> § 9 jäetakse välja.</p>
              <p><b>6)</b> § 10 asendatakse sõnaga "kattega".</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """
    root = ET.fromstring(xml)

    ops = _parse_muutmisseadus_ops(root, "tyviseadus_1_10.02.2010", "tyviseadus_1_10.02.2010", "Ehitusseadus")

    assert len(ops) == 6
    section_actions = {
        dict(op.target.path)["section"]: op.action.value for op in ops if "section" in dict(op.target.path)
    }
    assert section_actions == {
        "5": "repeal",
        "6": "text_replace",
        "7": "repeal",
        "8": "text_replace",
        "9": "repeal",
        "10": "text_replace",
    }


def test_registry_evidence_wins_for_old_format_clause_filter_when_heuristics_fail(
    monkeypatch,
) -> None:
    record = EEActIdentityRecord(
        akt_viide="ee/test/old_format_clause_filter",
        canonical_title="Ehitusseadus",
        title_variants=("Ehitusseadus",),
        aliases=("Ehitusseaduse",),
        source_family="old_format_clause_filter",
    )
    monkeypatch.setattr(grafter, "_title_matches_para", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(grafter, "lookup_ee_act_identity", lambda **_kwargs: record)

    xml = """
    <oigusakt>
      <sisu>
        <HTMLKonteiner><![CDATA[
          <p><b>§ 1. Ehitusseaduse muutmine</b> (RT I 2003, 45, 123)</p>
          <p><b>1)</b> Ehitusseaduse § 5 lõiget 2 muudetakse ja sõnastatakse järgmiselt: "tekst".</p>
        ]]></HTMLKonteiner>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = grafter._parse_old_format_amendment_ops(
        ET.fromstring(xml),
        "ee/test/old_format_clause_filter",
        target_title="Ehitusseadus",
    )

    assert len(ops) == 1
    assert dict(ops[0].target.path)["section"] == "5"
    assert ops[0].action.value == "replace"
