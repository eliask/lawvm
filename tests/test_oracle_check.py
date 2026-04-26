from types import SimpleNamespace
from datetime import date
import json
import sqlite3

from lxml import etree

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.divergence_heuristics import (
    blame_source_postdates_oracle_version,
    looks_like_bare_section_stub,
    oracle_has_future_repeal_overlay,
    oracle_has_repeal_banner_with_prior_wording,
    oracle_section_duplicates_adjacent_section,
    oracle_text_has_removable_duplicate_sentence,
    oracle_text_reduces_to_replay_by_dropping_sentences,
    replay_section_matches_text_at_cutoff,
)
from lawvm.tools.editorial_hygiene import strip_editorial_annotations
from lawvm.tools.classify_result import ClassifyResult
from lawvm.tools.oracle_check import (
    _classify_statute,
    _classify_statute_sync,
    _corpus_selection_detail,
    _diagnose,
    _el_text,
    _ir_node_has_repeal_placeholder,
    main,
    _print_corpus_summary,
    _print_statute_summary,
    _write_db,
)
from lawvm.tools.section_keys import extract_oracle_sections
from lawvm.finland.replay_products import fi_label_norm
from tests.corpus_pin_helpers import pinned_replay


def test_diagnose_treats_inline_future_effective_change_notes_as_editorial() -> None:
    replay = (
        "2 § Määritelmät Tässä laissa tarkoitetaan: 8) vartijalla "
        "poliisilaitoksen palveluksessa olevaa virkasuhteista vartijaa ja "
        "ylivartijaa; 9) etävalvonnalla teknistä valvontaa."
    )
    oracle = (
        "2 § Määritelmät Tässä laissa tarkoitetaan: 8) vartijalla "
        "poliisilaitoksen palveluksessa olevaa virkasuhteista vartijaa ja "
        "ylivartijaa; L:lla 1086/2015 muutettu 8 kohta tulee voimaan 1.1.2017. "
        "Aiempi sanamuoto kuuluu: 8) vartijalla poliisilain 1 luvun 10 §:ssä "
        "tarkoitettua ylivartijaa ja vartijaa. 9) etävalvonnalla teknistä "
        "valvontaa. L:lla 1086/2015 lisätty 9 kohta tulee voimaan 1.1.2017."
    )

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_bare_oracle_stub_as_editorial_convention() -> None:
    replay = "5 a § Jos vakuutusyhdistys purkautuu, selvitystila pannaan alulle."
    oracle = "5 a §"

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_multiline_aiempi_change_note_as_editorial() -> None:
    replay = (
        "5 § Turvallinen miehitys Alus on miehitettävä siten, ettei alusta, "
        "laivaväkeä, matkustajia, lastia, muuta omaisuutta tai ympäristöä "
        "saateta tarpeettomasti vaaralle alttiiksi. Liikenteen "
        "turvallisuusvirasto voi antaa tarkempia määräyksiä huvialuksen, "
        "vuokraveneen ja kotimaanliikenteessä liikennöivän aluksen miehityksestä."
    )
    oracle = (
        "5 § Turvallinen miehitys Alus on miehitettävä siten, ettei alusta, "
        "laivaväkeä, matkustajia, lastia, muuta omaisuutta tai ympäristöä "
        "saateta tarpeettomasti vaaralle alttiiksi. Liikenteen "
        "turvallisuusvirasto voi antaa tarkempia määräyksiä huvialuksen, "
        "vuokraveneen ja kotimaanliikenteessä liikennöivän aluksen miehityksestä. "
        "L:lla \n332/2018\n muutettu 4 momentti tulee voimaan 1.7.2018. "
        "Aiempi sanamuoto kuuluu: Liikenteen turvallisuusvirasto voi antaa "
        "tarkempia määräyksiä huvialuksen, vuokraveneen ja "
        "kotimaanliikenteessä liikennöivän aluksen miehityksestä ja siihen "
        "liittyvistä laivaväen pätevyysvaatimuksista."
    )

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_classify_statute_1974_258_repeal_stub_is_editorial_convention() -> None:
    """1974/258 15 § should replay as absent, not as live stale text."""
    replay = pinned_replay("1974/258", mode="finlex_oracle", quiet=True)
    assert replay.materialized_state.find_section("15") is None

    result = _classify_statute("1974/258", "finlex_oracle")

    assert result is not None
    row = next(item for item in result.section_results if item["section"] == "section:15")
    assert row["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_1992_1702_empty_operative_body_wave_is_source_incomplete() -> None:
    result = _classify_statute("1992/1702", "finlex_oracle")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["chapter:5/section:24"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:5/section:25a"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:8/section:33"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:10/section:46b"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:8/section:39a"]["diagnosis"] == "ORACLE_STALE"
    assert by_section["chapter:8/section:42"]["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_1987_322_repealed_stubs_are_editorial_convention() -> None:
    # Sections 10a-10f appear in the oracle as "kumottu" repeal stubs.
    # They are correctly absent from the replay (repealed), and the oracle
    # stub is an editorial rendering of that state — so EDITORIAL_CONVENTION.
    result = _classify_statute("1987/322", "finlex_oracle")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    for suffix in ("10a", "10b", "10c", "10d", "10e", "10f"):
        assert by_section[f"section:{suffix}"]["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_1901_15_001_raw_master_gap_wave_is_source_incomplete() -> None:
    # 1901/15-001 has part/chapter structure so section keys have full paths.
    # Sections with raw-text gaps introduced by 1975/351 amendments land in
    # SOURCE_INCOMPLETE (partially present but unresolvable content).
    result = _classify_statute_sync("1901/15-001", "legal_pit")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    for label in ("part:1/chapter:1/section:1", "part:1/chapter:2/section:8"):
        assert by_section[label]["diagnosis"] == "SOURCE_INCOMPLETE"
        assert by_section[label]["blame_source"] == "1975/351"

    for label in ("part:1/chapter:2/section:4", "part:1/chapter:2/section:5"):
        assert by_section[label]["diagnosis"] == "REPLAY_MISSING"
        assert by_section[label]["blame_source"] == "1975/351"


def test_classify_statute_demotes_unknown_to_source_pathology_when_blame_is_already_owned(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="11",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="11 § Replay wording."),),
                    ),
                ),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = (
            Finding(
                kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
                role="obligation",
                stage="grafter_uncovered",
                detail={"amendment_id": "2023/371"},
                source_statute="2023/371",
                blocking=True,
            ),
            Finding(
                kind="APPLY.FAILED_OPERATION",
                role="obligation",
                stage="apply",
                detail={
                    "amendment_id": "2023/371",
                    "reason_code": "no_deterministic_path",
                    "target_section": "11",
                    "target_chapter": "",
                },
                source_statute="2023/371",
                blocking=True,
            ),
        )

        def serialize_text(self) -> str:
            return "11 § Replay wording."

        def source_pathology_rows(self):
            return (
                {
                    "code": "ITEM_TARGET_STRUCTURE_ABSENT",
                    "message": "Target item structure is absent from the source payload.",
                    "source_statute": "2023/371",
                    "target_unit_kind": "item",
                    "target_label": "11 § 1 mom 2 kohta",
                    "detail": {
                        "target_section": "11",
                        "target_paragraph": "1",
                        "target_item": "2",
                    },
                },
            )

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "finlex_oracle"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "replace",
                    "source_statute": "2023/371",
                    "source_title": "Laki testisäädöksen muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "11",
                    "target_chapter": "",
                    "target_paragraph": "1",
                    "target_item": "2",
                }
            )
        return FakeMaster()

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check._diagnose", lambda *_args, **_kwargs: "UNKNOWN")
    monkeypatch.setattr("lawvm.tools.oracle_check._batch_pre_blame_sections", lambda _sid, _sources, _mode: {})

    result = _classify_statute(
        "2012/916",
        "finlex_oracle",
        oracle_root=etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_11">
                  <num>11 §</num>
                  <content><p>11 § Oracle wording.</p></content>
                </section>
              </body>
            </act>
            """
        ),
        html_audit_result=SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )

    assert result is not None
    sec11 = next(sec for sec in result.section_results if sec["section"] == "section:11")
    assert sec11["diagnosis"] == "SOURCE_PATHOLOGY"


def test_classify_statute_keeps_unknown_when_source_pathology_lacks_apply_or_coverage_ownership(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="11",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="11 § Replay wording."),),
                    ),
                ),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "11 § Replay wording."

        def source_pathology_rows(self):
            return (
                {
                    "code": "ITEM_TARGET_STRUCTURE_ABSENT",
                    "message": "Target item structure is absent from the source payload.",
                    "source_statute": "2023/371",
                    "target_unit_kind": "item",
                    "target_label": "11 § 1 mom 2 kohta",
                    "detail": {
                        "target_section": "11",
                        "target_paragraph": "1",
                        "target_item": "2",
                    },
                },
            )

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "finlex_oracle"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "replace",
                    "source_statute": "2023/371",
                    "source_title": "Laki testisäädöksen muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "11",
                    "target_chapter": "",
                    "target_paragraph": "1",
                    "target_item": "2",
                }
            )
        return FakeMaster()

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check._diagnose", lambda *_args, **_kwargs: "UNKNOWN")
    monkeypatch.setattr("lawvm.tools.oracle_check._batch_pre_blame_sections", lambda _sid, _sources, _mode: {})

    result = _classify_statute(
        "2012/916",
        "finlex_oracle",
        oracle_root=etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_11">
                  <num>11 §</num>
                  <content><p>11 § Oracle wording.</p></content>
                </section>
              </body>
            </act>
            """
        ),
        html_audit_result=SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )

    assert result is not None
    sec11 = next(sec for sec in result.section_results if sec["section"] == "section:11")
    assert sec11["diagnosis"] == "UNKNOWN"


def test_classify_statute_2001_1047_future_parent_repeal_is_oracle_stale() -> None:
    # Chapter 3 sections (14, 14c, 16, 16a, 12, etc.) are ORACLE_STALE because
    # the oracle reflects a future-effective state beyond the replay's cutoff.
    result = _classify_statute("2001/1047", "finlex_oracle")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["chapter:3/section:14"]["diagnosis"] == "ORACLE_STALE"


def test_diagnose_treats_repeal_note_with_aiempi_block_as_editorial() -> None:
    replay = "16 § 16 § on kumottu L:lla 4.5.2018/332."
    oracle = (
        "16 § 16 § on kumottu L:lla 4.5.2018/332, joka tulee voimaan 1.7.2018. "
        "Aiempi sanamuoto kuuluu:"
    )

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_multiline_repeal_note_with_spaced_comma_as_editorial() -> None:
    replay = "6 § 6 § on kumottu L:lla 18.5.2018/375."
    oracle = (
        "6 §\n"
        "                                \n"
        "                            \n"
        "                                6 § on kumottu L:lla \n"
        "                                18.5.2018/375\n"
        "                                , joka tulee voimaan 1.1.2019. "
        "Aiempi sanamuoto kuuluu:"
    )

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_repeal_note_with_effective_date_before_citation_as_editorial() -> None:
    replay = "5 § 5 § on kumottu L:lla 13.11.1992/1015."
    oracle = "5 § 5 § on kumottu 1.1.1993 L:lla 13.11.1992/1015."

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_inline_aiempi_block_with_tuli_voimaan_as_editorial() -> None:
    replay = (
        "2 a § Euroopan talousalueella tai verosopimusvaltiossa asuvia yhteisöjä koskevat "
        "tarkemmat määräykset Tätä lakia ei sovelleta ulkomaiseen yhteisöön, jonka kotipaikka "
        "on Euroopan talousalueeseen kuuluvassa tai 2 §:n 3 momentin 2 kohdassa tarkoitetussa "
        "valtiossa, jos hallinnollisesta yhteistyöstä verotuksen alalla ja direktiivin 77/799/ETY "
        "kumoamisesta annettu neuvoston direktiivi 2011/16/EU koskee kyseistä valtiota."
    )
    oracle = (
        "2 a § Euroopan talousalueella tai verosopimusvaltiossa asuvia yhteisöjä koskevat "
        "tarkemmat määräykset Tätä lakia ei sovelleta ulkomaiseen yhteisöön, jonka kotipaikka "
        "on Euroopan talousalueeseen kuuluvassa tai 2 §:n 3 momentin 2 kohdassa tarkoitetussa "
        "valtiossa, jos hallinnollisesta yhteistyöstä verotuksen alalla ja direktiivin 77/799/ETY "
        "kumoamisesta annettu neuvoston direktiivi 2011/16/EU koskee kyseistä valtiota. "
        "L:lla 1491/2016 muutettu 1 momentti tuli voimaan 1.1.2017. "
        "Aiempi sanamuoto kuuluu: Tätä lakia ei sovelleta ..."
    )

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_expired_temporary_residue_as_oracle_stale() -> None:
    replay = (
        "4 § Viivekorko Maksuunpannulle ja maksettavaksi erääntyneelle maksamattomalle verolle "
        "lasketaan viivekorko, joka on määrältään kutakin kalenterivuotta edeltävän puolivuotiskauden "
        "korkolain 12 §:ssä tarkoitettu viitekorko lisättynä kuudella prosenttiyksiköllä, yhteensä "
        "kuitenkin vähintään kolme euroa. Edellä 1 momentissa säädettyä ei sovelleta veronkantolain "
        "soveltamisalaan kuuluvaan veroon."
    )
    oracle = (
        replay
        + " 3 momentti oli väliaikaisesti voimassa 1.5.2020–31.8.2020 L:lla 294/2020.."
        + " 4 momentti oli väliaikaisesti voimassa 1.5.2020–31.8.2020 L:lla 294/2020.."
    )

    assert _diagnose(replay, oracle, None) == "ORACLE_STALE"


def test_diagnose_treats_bench_comparable_temporary_residue_stub_as_editorial() -> None:
    replay = "3 b § Perintäkulut"
    oracle = "3 b § 3 b § oli väliaikaisesti voimassa 1.7.2021–30.4.2022 L:lla 539/2021."

    assert _diagnose(
        replay,
        oracle,
        None,
        oracle_selector_mode="bench_comparable",
    ) == "EDITORIAL_CONVENTION"


def test_strip_editorial_annotations_strips_temporary_residue_without_case_suffix() -> None:
    text = "21 b § 21 b § oli väliaikaisesti voimassa 24.11.2021–30.1.2022 L 984/2021."

    stripped = strip_editorial_annotations(text)

    assert stripped.strip() == "21 b §"
    assert looks_like_bare_section_stub(stripped)


def test_strip_editorial_annotations_strips_temporary_residue_without_valiaikaisesti() -> None:
    text = "3 b § 3 b § oli voimassa 1.10.2021–30.4.2022 L:lla 18.6.2021/540."

    stripped = strip_editorial_annotations(text)

    assert stripped.strip() == "3 b §"
    assert looks_like_bare_section_stub(stripped)


def test_future_repeal_overlay_detection_matches_future_effective_repeal_banner() -> None:
    oracle = (
        "11 § 11 § on kumottu L:lla 5.12.2025/1159, joka tulee voimaan 1.5.2026. "
        "Aiempi sanamuoto kuuluu:"
    )

    assert oracle_has_future_repeal_overlay(oracle) is True


def test_classify_statute_treats_future_effective_repeal_overlay_as_oracle_stale(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(
                        kind=IRNodeKind.SECTION,
                        label="11",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="11 § Voimassa oleva sisältö."),),
                    ),),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "11 § Voimassa oleva sisältö."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "replace",
                    "source_statute": "2023/707",
                    "source_title": "Laki testilain 11 §:n muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "11",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_11">
                  <num>11 §</num>
                  <content>
                    <p>11 § on kumottu L:lla 5.12.2025/1159, joka tulee voimaan 1.5.2026. Aiempi sanamuoto kuuluu:</p>
                  </content>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check._batch_pre_blame_sections", lambda _sid, _sources, _mode: {})

    result = _classify_statute(
        "2019/552",
        "legal_pit",
        oracle_root=fake_ground_truth_tree("2019/552"),
        html_audit_result=SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )
    assert result is not None
    assert result.section_results[0]["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_treats_oracle_version_mid_future_effective_as_oracle_stale(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Tämä asetus tulee voimaan 1 päivänä toukokuuta 2016 ja on voimassa vuoden 2021 loppuun.",
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Tämä asetus tulee voimaan 1 päivänä tammikuuta 2020.",
                            ),
                        ),
                    ),
                ),
            )
        )
        source_adjudication = SimpleNamespace(
            source_pathologies=[],
            oracle_suspect="2021/1199 eff 2021-12-31 > cutoff 2021-12-17",
        )
        findings = ()

        def serialize_text(self) -> str:
            return (
                "8 § Voimaantulo Tämä asetus tulee voimaan 1 päivänä toukokuuta 2016 "
                "ja on voimassa vuoden 2021 loppuun. Tämä asetus tulee voimaan 1 päivänä tammikuuta 2020."
            )

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "finlex_oracle"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "replace",
                    "source_statute": "2021/1199",
                    "source_title": "Sisäministeriön asetus Rajavartiolaitoksen suoritteiden maksuista annetun sisäministeriön asetuksen muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "8",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_8v20211199">
                  <num>8 §</num>
                  <heading>Voimaantulo</heading>
                  <subsection>
                    <content>
                      <p>Tämä asetus tulee voimaan 1 päivänä toukokuuta 2016 ja on voimassa vuoden 2023 loppuun.</p>
                    </content>
                  </subsection>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check._batch_pre_blame_sections", lambda _sid, _sources, _mode: {})
    monkeypatch.setattr("lawvm.tools.oracle_check.get_consolidated_meta", lambda _sid: (date(2021, 12, 17), "2021/1199"))

    result = _classify_statute(
        "2016/258",
        "finlex_oracle",
        oracle_root=fake_ground_truth_tree("2016/258"),
        html_audit_result=SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )

    assert result is not None
    sec = next(row for row in result.section_results if row["section"] == "section:8")
    assert sec["diagnosis"] == "ORACLE_STALE"
    assert sec["oracle_version_amendment_id"] == "2021/1199"


def test_cutoff_witness_matches_mixed_oracle_section_for_2016_258() -> None:
    from tests.corpus_pin_helpers import pinned_replay
    from lawvm.finland.grafter import get_ground_truth_tree

    replay = pinned_replay("2016/258", mode="finlex_oracle", quiet=True)
    oracle_root = get_ground_truth_tree("2016/258")
    assert oracle_root is not None
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_el = oracle_sections["section:3"]

    assert replay_section_matches_text_at_cutoff(
        replay,
        "section:3",
        _el_text(oracle_el),
        "2021-12-17",
        statute_id="2016/258",
        title=replay.title,
        label_norm=fi_label_norm,
    ) is True


def test_classify_statute_treats_future_dated_replay_version_as_oracle_stale(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="5a",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="29e",
                                children=(IRNode(kind=IRNodeKind.CONTENT, text="29 e § Future text."),),
                            ),
                        ),
                    ),
                ),
            )
        )
        timelines = {
            LegalAddress(path=(("chapter", "5a"), ("section", "29e"))): SimpleNamespace(
                versions=(
                    SimpleNamespace(effective="2026-01-01"),
                )
            )
        }
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "29 e § Future text."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "finlex_oracle"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <chapter>
                  <num>5 a luku</num>
                  <section eId="chp_5a__sec_29e">
                    <num>29 e §</num>
                    <content>
                      <p>29 e § Vanha teksti.</p>
                    </content>
                  </section>
                </chapter>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_consolidated_meta", lambda _sid: (date(2025, 5, 27), "2025/1497"))

    result = _classify_statute("2014/1429", "finlex_oracle")
    assert result is not None
    sec = next((s for s in result.section_results if s["section"] == "chapter:5a/section:29e"), None)
    assert sec is not None
    assert sec["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_marks_content_absent_on_empty_oracle_extra(monkeypatch) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="1 § Voimassa oleva sisältö."),),
                    ),),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "1 § Voimassa oleva sisältö."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
              <body>
                <hcontainer name="contentAbsent"/>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_meta",
        lambda _sid: (None, ""),
    )

    result = _classify_statute("1993/1229", "legal_pit")
    assert result is not None
    extra = next(sec for sec in result.section_results if sec["diagnosis"] == "EXTRA")
    assert extra["oracle_text"] == ""
    assert extra["oracle_content_absent"] is True


def test_diagnose_without_preblame_context_keeps_stub_vs_full_text_as_replay_missing() -> None:
    replay = "28 §"
    oracle = (
        "28 § Tulliviranomaisella on oikeus saada tietoja. "
        "Tulliviranomaisella on lisäksi oikeus saada yhteystiedot."
    )
    blame_op = {"action": "REPEAL", "source_statute": "2015/640"}

    assert _diagnose(replay, oracle, blame_op) == "REPLAY_MISSING"


def test_strip_editorial_annotations_collapses_repeal_only_oracle_section_to_heading_stub() -> None:
    oracle = (
        "28 § 1 momentti on kumottu L:lla 22.5.2015/640, joka tuli voimaan 1.6.2015. "
        "Aiempi sanamuoto kuuluu: 2 momentti on kumottu L:lla 22.5.2015/640, joka tuli voimaan 1.6.2015. "
        "Aiempi sanamuoto kuuluu:"
    )

    stripped = strip_editorial_annotations(oracle)
    assert looks_like_bare_section_stub(stripped)


def test_strip_editorial_annotations_handles_formatted_repeal_stub_trailing_whitespace() -> None:
    oracle = (
        "2 a §\n"
        "                        \n"
        "                            \n"
        "                                2 a § on kumottu L:lla \n"
        "                                27.6.2014/491\n"
        "                                ."
    )

    stripped = strip_editorial_annotations(oracle)
    assert looks_like_bare_section_stub(stripped)


def test_strip_editorial_annotations_handles_decision_style_repeal_stub() -> None:
    oracle = "25 § on kumottu P:llä 8.11.2013/415 v. 2014."

    stripped = strip_editorial_annotations(oracle)
    assert looks_like_bare_section_stub(stripped)


def test_classify_statute_treats_missing_temporary_insert_as_oracle_stale(monkeypatch) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(ir=IRNode(kind=IRNodeKind.BODY, children=()))
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return ""

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "insert",
                    "source_statute": "2021/539",
                    "source_title": (
                        "Laki saatavien perinnästä annetun lain "
                        "väliaikaisesta muuttamisesta"
                    ),
                    "target_unit_kind": "section",
                    "target_norm": "3b",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_3b">
                  <num>3 b §</num>
                  <content>Temporary text still present in oracle snapshot.</content>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)

    result = _classify_statute("1999/513", "legal_pit")
    assert result is not None
    assert result.section_results[0]["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_treats_bare_temporary_stub_as_editorial_in_finlex_oracle(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(ir=IRNode(kind=IRNodeKind.BODY, children=()))
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return ""

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "finlex_oracle"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_21b">
                  <num>21 b §</num>
                  <content>
                    <p>21 b § oli väliaikaisesti voimassa 24.11.2021–30.1.2022 L 984/2021.</p>
                  </content>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)

    result = _classify_statute("1999/488", "finlex_oracle")
    assert result is not None
    sec = next((s for s in result.section_results if s["section"] == "section:21b"), None)
    assert sec is not None
    assert sec["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_matches_unique_unscoped_blame_to_chapter_scoped_section(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(IRNode(
                                kind=IRNodeKind.SECTION,
                                label="5",
                                children=(IRNode(kind=IRNodeKind.NUM, text="5 §"),
                                    IRNode(kind=IRNodeKind.HEADING, text="Veron määrä"),
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="1",
                                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Base text."),),
                                    ),),
                            ),),
                    ),),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "5 § Veron määrä Base text."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "replace",
                    "source_statute": "2014/1215",
                    "source_title": "Laki rataverolain 5 ja 7 §:n väliaikaisesta muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "5",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <chapter>
                  <num>2 luku</num>
                  <section eId="sec_5">
                    <num>5 §</num>
                    <heading>Veron määrä</heading>
                    <subsection>
                      <content>Base text.</content>
                    </subsection>
                    <subsection>
                      <content>Poiketen siitä, mitä 1 momentissa säädetään, vuosina 2015–2017 veroa ei peritä.</content>
                    </subsection>
                  </section>
                </chapter>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)

    result = _classify_statute("2003/605", "legal_pit")
    assert result is not None
    assert result.section_results[0]["section"] == "chapter:2/section:5"
    assert result.section_results[0]["blame_source"] == "2014/1215"
    assert result.section_results[0]["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_treats_repeal_that_moves_replay_closer_to_oracle_as_oracle_stale(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="4", text="4 § oracle target text after repeal"),),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "4 § oracle target text after repeal"

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "repeal",
                    "source_statute": "2020/162",
                    "source_title": "Laki testisäädöksen muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "4",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_4">
                  <num>4 §</num>
                  <content>4 § oracle target text</content>
                </section>
              </body>
            </act>
            """
        )

    def fake_get_pre_blame_sections(_sid: str, stop_before_source: str, mode: str):
        assert stop_before_source == "2020/162"
        assert mode == "legal_pit"
        return (
            {"section:4": IRNode(kind=IRNodeKind.SECTION, label="4", text="4 § unrelated earlier wording")},
            "2019/1568",
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check._get_pre_blame_sections",
        fake_get_pre_blame_sections,
    )

    result = _classify_statute("1995/1552", "legal_pit")
    assert result is not None
    assert result.section_results[0]["diagnosis"] == "ORACLE_STALE"
    assert result.section_results[0]["oracle_version"] == "2019/1568"


def test_classify_statute_surfaces_html_topology_mismatch(monkeypatch) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(ir=IRNode(kind=IRNodeKind.BODY, children=()))
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return ""

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring("<act><body /></act>")

    class FakeHtmlAudit:
        missing_from_xml = ["4 a §"]
        extra_in_xml = []
        html_error = ""
        noncommensurable_reason = ""

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr("lawvm.tools.audit._audit_html_one", lambda sid: FakeHtmlAudit())

    result = _classify_statute("1994/1205", "legal_pit")

    assert result is not None
    assert result.html_topology == {
        "mismatch": True,
        "missing_from_xml": ["4 a §"],
        "extra_in_xml": [],
        "html_error": "",
        "noncommensurable_reason": "",
    }


def test_classify_statute_surfaces_html_noncommensurable_reason(monkeypatch) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(ir=IRNode(kind=IRNodeKind.BODY, children=()))
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return ""

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring("<act><body /></act>")

    class FakeHtmlAudit:
        missing_from_xml = []
        extra_in_xml = []
        html_error = ""
        noncommensurable_reason = "duplicate_unscoped_oracle_labels:section:1"

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr("lawvm.tools.audit._audit_html_one", lambda sid: FakeHtmlAudit())

    result = _classify_statute("1995/540", "legal_pit")

    assert result is not None
    assert result.html_topology == {
        "mismatch": False,
        "missing_from_xml": [],
        "extra_in_xml": [],
        "html_error": "",
        "noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:1",
    }


def test_oracle_section_duplicates_adjacent_section_detects_neighbor_copy() -> None:
    assert oracle_section_duplicates_adjacent_section(
        "section:13",
        "13 § Avustuksen hakeminen Avustusta haetaan rahoituskeskukselta.",
        {
            "section:12": "12 § Avustuksen hakeminen Avustusta haetaan rahoituskeskukselta.",
            "section:13": "13 § Avustuksen hakeminen Avustusta haetaan rahoituskeskukselta.",
        },
    ) is True


def test_oracle_text_has_removable_duplicate_sentence_detects_same_section_residue() -> None:
    replay = (
        "10 § Kohdeyhtiön julkistamisvelvollisuus Kun kohdeyhtiö saa liputusilmoituksen, "
        "sen on ilman aiheetonta viivytystä julkistettava liputusilmoituksessa olevat tiedot. "
        "Kohdeyhtiöllä ei ole julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. "
        "Julkistettaessa on myös mainittava, jos kohdeyhtiön tiedossa ei ole kaikkia "
        "liputusilmoituksen säädettyjä tietoja. Jos liputusilmoituksessa on lisäksi annettu "
        "muita tietoja, nämäkin tiedot on julkistettava samassa yhteydessä. Kohdeyhtiön on "
        "julkistettava liputusilmoitukseen sisältyvät tiedot sen oman omistus- tai ääniosuuden "
        "muutoksista 5–7 §:ssä tarkoitetulla tavalla ilman aiheetonta viivytystä."
    )
    oracle = (
        "10 § Kohdeyhtiön julkistamisvelvollisuus Kun kohdeyhtiö saa liputusilmoituksen, "
        "sen on ilman aiheetonta viivytystä julkistettava liputusilmoituksessa olevat tiedot. "
        "Kohdeyhtiöllä ei ole julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. "
        "Julkistettaessa on myös mainittava, jos kohdeyhtiön tiedossa ei ole kaikkia "
        "liputusilmoituksen säädettyjä tietoja. Jos liputusilmoituksessa on lisäksi annettu "
        "muita tietoja, nämäkin tiedot on julkistettava samassa yhteydessä. Kohdeyhtiöllä ei ole "
        "julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. Kohdeyhtiön on "
        "julkistettava liputusilmoitukseen sisältyvät tiedot sen oman omistus- tai ääniosuuden "
        "muutoksista 5–7 §:ssä tarkoitetulla tavalla ilman aiheetonta viivytystä."
    )

    assert oracle_text_has_removable_duplicate_sentence(replay, oracle) is True


def test_oracle_text_reduces_to_replay_by_dropping_sentences_detects_superseded_residue() -> None:
    replay = (
        "55 § Päätöksen sisältö Päätöksestä on käytävä ilmi päätöksen tehnyt viranomainen "
        "yhteystietoineen, verovelvollisen yksilöintitiedot, päätöksen perustelut ja tieto siitä, "
        "miten asia on ratkaistu. Päätökseen sovelletaan lisäksi, mitä hallintolain 53 g §:n "
        "1 momentissa säädetään automaattisesta ratkaisemisesta ilmoittamisesta. Päätös voidaan "
        "jättää perustelematta silloin, kun perusteleminen on ilmeisen tarpeetonta."
    )
    oracle = (
        "55 § Päätöksen sisältö Päätöksestä on käytävä ilmi päätöksen tehnyt viranomainen "
        "yhteystietoineen, verovelvollisen yksilöintitiedot, päätöksen perustelut ja tieto siitä, "
        "miten asia on ratkaistu. Päätökseen sovelletaan lisäksi, mitä hallintolain 53 g §:n "
        "1 momentissa säädetään automaattisesta ratkaisemisesta ilmoittamisesta. Päätöksestä on "
        "käytävä ilmi päätöksen tehnyt viranomainen yhteystietoineen, verovelvollisen "
        "yksilöintitiedot, päätöksen perustelut ja tieto siitä, miten asia on ratkaistu. "
        "Päätös voidaan jättää perustelematta silloin, kun perusteleminen on ilmeisen tarpeetonta."
    )

    assert oracle_text_reduces_to_replay_by_dropping_sentences(replay, oracle) is True


def test_oracle_has_repeal_banner_with_prior_wording_detects_editorial_repeal_overlay() -> None:
    oracle = (
        "53 § 53 § on kumottu L:lla 14.4.2023/661, joka tuli voimaan 1.1.2024. "
        "Aiempi sanamuoto kuuluu:"
    )

    assert oracle_has_repeal_banner_with_prior_wording(oracle) is True


def test_diagnose_treats_same_section_oracle_duplicate_sentence_as_oracle_stale() -> None:
    replay = (
        "10 § Kohdeyhtiön julkistamisvelvollisuus Kun kohdeyhtiö saa liputusilmoituksen, "
        "sen on ilman aiheetonta viivytystä julkistettava liputusilmoituksessa olevat tiedot. "
        "Kohdeyhtiöllä ei ole julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. "
        "Julkistettaessa on myös mainittava, jos kohdeyhtiön tiedossa ei ole kaikkia "
        "liputusilmoituksen säädettyjä tietoja. Jos liputusilmoituksessa on lisäksi annettu "
        "muita tietoja, nämäkin tiedot on julkistettava samassa yhteydessä. Kohdeyhtiön on "
        "julkistettava liputusilmoitukseen sisältyvät tiedot sen oman omistus- tai ääniosuuden "
        "muutoksista 5–7 §:ssä tarkoitetulla tavalla ilman aiheetonta viivytystä."
    )
    oracle = (
        "10 § Kohdeyhtiön julkistamisvelvollisuus Kun kohdeyhtiö saa liputusilmoituksen, "
        "sen on ilman aiheetonta viivytystä julkistettava liputusilmoituksessa olevat tiedot. "
        "Kohdeyhtiöllä ei ole julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. "
        "Julkistettaessa on myös mainittava, jos kohdeyhtiön tiedossa ei ole kaikkia "
        "liputusilmoituksen säädettyjä tietoja. Jos liputusilmoituksessa on lisäksi annettu "
        "muita tietoja, nämäkin tiedot on julkistettava samassa yhteydessä. Kohdeyhtiöllä ei ole "
        "julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. Kohdeyhtiön on "
        "julkistettava liputusilmoitukseen sisältyvät tiedot sen oman omistus- tai ääniosuuden "
        "muutoksista 5–7 §:ssä tarkoitetulla tavalla ilman aiheetonta viivytystä."
    )

    assert _diagnose(replay, oracle, None) == "ORACLE_STALE"


def test_classify_statute_2016_768_reclassifies_oracle_sentence_residue_and_repeal_banner() -> None:
    result = _classify_statute("2016/768", "finlex_oracle")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["chapter:5/section:30"]["diagnosis"] == "ORACLE_STALE"
    assert by_section["chapter:9/section:53"]["diagnosis"] == "EDITORIAL_CONVENTION"
    assert by_section["chapter:7/section:36"]["diagnosis"] == "ORACLE_STALE"


def test_blame_source_postdates_oracle_version_compares_year_num_pairs() -> None:
    assert blame_source_postdates_oracle_version("2021/495", "2018/1024") is True
    assert blame_source_postdates_oracle_version("2017/967", "2018/1024") is False


def test_classify_statute_treats_post_oracle_amendment_divergence_as_oracle_stale(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(
                        kind=IRNodeKind.SECTION,
                        label="7c",
                        text=(
                            "7 c § Rekisterinpitäjä saa salassapitosäännösten estämättä "
                            "luovuttaa metsästäjärekisterin ne tiedot, jotka ovat välttämättömiä."
                        ),
                    ),),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return (
                "7 c § Rekisterinpitäjä saa salassapitosäännösten estämättä "
                "luovuttaa metsästäjärekisterin ne tiedot, jotka ovat välttämättömiä."
            )

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "replace",
                    "source_statute": "2021/495",
                    "source_title": "Laki testisäädöksen 7 c §:n muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "7c",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_7c">
                  <num>7 c §</num>
                  <content>
                    Rekisterinpitäjä saa salassapitosäännösten estämättä luovuttaa
                    metsästäjärekisterin tietoja, jotka ovat tarpeen.
                  </content>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check._batch_pre_blame_sections", lambda _sid, _sources, _mode: {})
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_meta",
        lambda _sid: (None, "2018/1024"),
    )

    result = _classify_statute(
        "1993/616",
        "legal_pit",
        oracle_root=fake_ground_truth_tree("1993/616"),
        html_audit_result=SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )
    assert result is not None
    sec = result.section_results[0]
    assert sec["diagnosis"] == "ORACLE_STALE"
    assert sec["oracle_version_amendment_id"] == "2018/1024"


def test_classify_statute_treats_repealed_section_with_duplicated_adjacent_oracle_text_as_oracle_stale(
    monkeypatch,
) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(
                        kind=IRNodeKind.SECTION,
                        label="12",
                        text=(
                            "12 § Avustuksen hakeminen Avustusta haetaan "
                            "Innovaatiorahoituskeskus Business Finlandilta. "
                            "Hakemus on toimitettava ennen hankkeen aloittamista."
                        ),
                    ),
                    IRNode(kind=IRNodeKind.SECTION, label="13", text="13 § 13 § on kumottu A:lla 28.12.2017/1153."),),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return (
                "12 § Avustuksen hakeminen Avustusta haetaan Innovaatiorahoituskeskus "
                "Business Finlandilta. Hakemus on toimitettava ennen hankkeen aloittamista. "
                "13 § 13 § on kumottu A:lla 28.12.2017/1153."
            )

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "action": "repeal",
                    "source_statute": "2017/1153",
                    "source_title": "Valtioneuvoston asetus testisäädöksen muuttamisesta",
                    "target_unit_kind": "section",
                    "target_norm": "13",
                    "target_chapter": "",
                }
            )
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_12">
                  <num>12 §</num>
                  <content>
                    Avustuksen hakeminen Avustusta haetaan Innovaatiorahoituskeskus
                    Business Finlandilta. Hakemus on toimitettava ennen hankkeen aloittamista.
                  </content>
                </section>
                <section eId="sec_13">
                  <num>13 §</num>
                  <content>
                    Avustuksen hakeminen Avustusta haetaan Innovaatiorahoituskeskus
                    Business Finlandilta. Hakemus on toimitettava ennen hankkeen aloittamista.
                  </content>
                </section>
              </body>
            </act>
            """
        )

    def fake_get_pre_blame_sections(_sid: str, stop_before_source: str, mode: str):
        assert stop_before_source == "2017/1153"
        assert mode == "legal_pit"
        return (
            {"section:13": IRNode(kind=IRNodeKind.SECTION, label="13", text="13 § earlier substantive text")},
            "2015/364",
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check._get_pre_blame_sections",
        fake_get_pre_blame_sections,
    )

    result = _classify_statute("2015/364", "legal_pit")
    assert result is not None
    sec13 = next(sec for sec in result.section_results if sec["section"] == "section:13")
    assert sec13["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_returns_live_source_pathology_codes(monkeypatch) -> None:
    pathology = SourcePathology.from_scope(
        code="CONTAINER_MEMBERSHIP_MISMATCH",
        message="Container payload carries sections outside the target chapter.",
        source_statute="1994/1304",
        target_unit_kind="chapter",
        target_label="4a luku",
        detail={"diagnostic_reason": "shared_heading_tiny_payload"},
    )

    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(ir=IRNode(kind=IRNodeKind.BODY, children=()))
        source_adjudication = SimpleNamespace(source_pathologies=[pathology])
        findings = ()

        def serialize_text(self) -> str:
            return ""

        def source_pathology_rows(self):
            return (
                {
                    "code": pathology.code,
                    "message": pathology.message,
                    "source_statute": pathology.source_statute,
                    "target_unit_kind": pathology.target_unit_kind,
                    "target_label": pathology.target_label,
                    "detail": pathology.detail,
                },
            )

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring("<act><body /></act>")

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)

    result = _classify_statute("1990/1295", "legal_pit")
    assert result is not None
    assert result.source_pathologies == [
        {
            "code": "CONTAINER_MEMBERSHIP_MISMATCH",
            "message": "Container payload carries sections outside the target chapter.",
            "source_statute": "1994/1304",
            "target_unit_kind": "chapter",
            "target_label": "4a luku",
            "detail": {"diagnostic_reason": "shared_heading_tiny_payload"},
        }
    ]


def test_classify_statute_2012_916_demotes_section_1_unknown_to_source_pathology() -> None:
    result = _classify_statute("2012/916", "finlex_oracle")

    assert result is not None
    row = next(sec for sec in result.section_results if sec["section"] == "chapter:13/section:1")
    assert row["diagnosis"] == "SOURCE_PATHOLOGY"


def test_classify_statute_collects_contingent_effective_sources_from_findings(monkeypatch) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(ir=IRNode(kind=IRNodeKind.BODY, children=()))
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = (
            Finding(
                kind="TIME.CONTINGENT_EFFECTIVE_DATE",
                role="obligation",
                stage="process_muutoslaki",
                detail={"message": "Effective date is contingent or decree-set in voimaantulo text."},
                source_statute="2004/542",
                blocking=True,
            ),
            Finding(
                kind="TIME.CONTINGENT_EFFECTIVE_DATE",
                role="obligation",
                stage="process_muutoslaki",
                detail={"message": "Effective date is contingent or decree-set in voimaantulo text."},
                source_statute="2005/544",
                blocking=True,
            ),
            Finding(
                kind="text_duplication_warning",
                role="observation",
                stage="replay_fold",
                detail={"message": "Replay output contains a suspicious duplicated text tract."},
                source_statute="2006/1",
                blocking=False,
            ),
        )

        def serialize_text(self) -> str:
            return ""

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring("<act><body /></act>")

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)

    result = _classify_statute("1990/1295", "legal_pit")

    assert result is not None
    assert result.contingent_effective_sources == ["2004/542", "2005/544"]


def test_write_db_persists_statute_level_signals(tmp_path) -> None:
    db_path = tmp_path / "divergences.db"
    _write_db(
        [
            ClassifyResult(
                sid="1994/1205",
                title="Test statute",
                overall_score=0.83,
                section_score=0.83,
                section_results=[
                    {
                        "section": "section:8a",
                        "diagnosis": "REPLAY_MISSING",
                        "blame_source": "1999/1",
                        "blame_title": "Test amendment",
                        "oracle_version": "",
                        "replay_text": "replay",
                        "oracle_text": "oracle",
                    }
                ],
                source_pathologies=[
                    {
                        "code": "CONTAINER_MEMBERSHIP_MISMATCH",
                        "message": "target container disagrees with source structure",
                        "source_statute": "1990/1295",
                        "target_unit_kind": "section",
                        "target_label": "2 a §",
                    }
                ],
                html_topology={
                    "mismatch": True,
                    "missing_from_xml": ["8 a §"],
                    "extra_in_xml": [],
                    "html_error": "",
                    "noncommensurable_reason": "",
                },
                contingent_effective_sources=["2004/542", "2005/544"],
            )
        ],
        str(db_path),
    )

    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT source_pathology, source_pathology_codes, source_pathology_rows_json, html_topology_mismatch, "
        "html_missing_from_xml, html_extra_in_xml, html_noncommensurable_reason, "
        "contingent_effective_sources "
        "FROM statute_signals WHERE statute_id = ?",
        ("1994/1205",),
    ).fetchone()
    con.close()

    assert row is not None
    assert row[0] == 1
    assert row[1] == "CONTAINER_MEMBERSHIP_MISMATCH"
    assert json.loads(row[2]) == [
        {
            "code": "CONTAINER_MEMBERSHIP_MISMATCH",
            "message": "target container disagrees with source structure",
            "source_statute": "1990/1295",
            "target_label": "2 a §",
            "target_unit_kind": "section",
        }
    ]
    assert row[3:] == (
        1,
        "8 a §",
        "",
        "",
        "2004/542|2005/544",
    )


def test_print_statute_summary_shows_statute_level_signals(capsys) -> None:
    _print_statute_summary(
        ClassifyResult(
            sid="1994/1205",
            overall_score=0.83,
            section_results=[
                {
                    "section": "section:8a",
                    "diagnosis": "REPLAY_MISSING",
                }
            ],
            source_pathologies=[
                {"code": "CONTAINER_MEMBERSHIP_MISMATCH"}
            ],
            html_topology={
                "missing_from_xml": ["8 a §"],
                "extra_in_xml": [],
                "noncommensurable_reason": "",
            },
            contingent_effective_sources=["2004/542"],
        )
    )

    out = capsys.readouterr().out
    assert "source-pathology: CONTAINER_MEMBERSHIP_MISMATCH" in out
    assert "html-topology: missing_from_xml=8 a §" in out
    assert "contingent-effective-date: 2004/542" in out


def test_print_corpus_summary_counts_statute_level_signals(capsys) -> None:
    _print_corpus_summary(
        [
            ClassifyResult(
                sid="1994/1205",
                mode="legal_pit",
                overall_score=0.83,
                section_results=[
                    {"section": "section:8a", "diagnosis": "REPLAY_MISSING"}
                ],
                source_pathologies=[{"code": "CONTAINER_MEMBERSHIP_MISMATCH"}],
                html_topology={
                    "missing_from_xml": ["8 a §"],
                    "extra_in_xml": [],
                    "noncommensurable_reason": "",
                },
                contingent_effective_sources=["2004/542"],
            )
        ],
        None,
    )

    out = capsys.readouterr().out
    assert "Source-pathology    : 1 statutes" in out
    assert "HTML topology       : 1 statutes" in out
    assert "HTML noncommensurable: 0 statutes" in out
    assert "Contingent eff-date : 1 statutes" in out


def test_print_corpus_summary_reports_source_pathology_sections_and_excludes_them_from_adjusted_score(
    capsys,
) -> None:
    _print_corpus_summary(
        [
            ClassifyResult(
                sid="2012/916",
                mode="finlex_oracle",
                overall_score=0.50,
                section_results=[
                    {"section": "chapter:13/section:1", "diagnosis": "SOURCE_PATHOLOGY"},
                    {"section": "chapter:13/section:8", "diagnosis": "REPLAY_MISSING"},
                ],
                source_pathologies=[{"code": "ITEM_TARGET_STRUCTURE_ABSENT"}],
            )
        ],
        None,
    )

    out = capsys.readouterr().out
    assert "SOURCE_PATHOLOGY" in out
    assert "1 sections" in out
    assert "Adjusted score      : 75.00%" in out
    assert "SOURCE_PATHOLOGY" in out.split("Adjusted score      : ", 1)[1]


def test_print_corpus_summary_does_not_print_source_pathology_bucket_without_section_level_rows(
    capsys,
) -> None:
    _print_corpus_summary(
        [
            ClassifyResult(
                sid="1994/1205",
                mode="legal_pit",
                overall_score=0.83,
                section_results=[
                    {"section": "section:8a", "diagnosis": "REPLAY_MISSING"}
                ],
                source_pathologies=[{"code": "CONTAINER_MEMBERSHIP_MISMATCH"}],
            )
        ],
        None,
    )

    out = capsys.readouterr().out
    assert "  SOURCE_PATHOLOGY" not in out


def test_corpus_selection_detail_labels_configured_list_without_alias(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "batch_test_list.csv"
    expanded = tmp_path / "expanded_batch_test_list.csv"
    configured.write_text("1,1990/1\n", encoding="utf-8")
    expanded.write_text("1,1991/2\n", encoding="utf-8")

    monkeypatch.setattr(
        "lawvm.tools.oracle_check._corpus_path",
        lambda full: str(expanded if full else configured),
    )

    assert _corpus_selection_detail(False) == "configured corpus list (batch_test_list.csv)"
    assert _corpus_selection_detail(True) == "expanded corpus list (expanded_batch_test_list.csv)"


def test_corpus_selection_detail_marks_alias_when_lists_match(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "batch_test_list.csv"
    expanded = tmp_path / "expanded_batch_test_list.csv"
    contents = "1,1990/1\n1,1991/2\n"
    configured.write_text(contents, encoding="utf-8")
    expanded.write_text(contents, encoding="utf-8")

    monkeypatch.setattr(
        "lawvm.tools.oracle_check._corpus_path",
        lambda full: str(expanded if full else configured),
    )

    assert _corpus_selection_detail(False) == (
        "configured corpus list (batch_test_list.csv); same rows as --corpus-full on this tree"
    )
    assert _corpus_selection_detail(True) == (
        "expanded corpus list (expanded_batch_test_list.csv); same rows as --corpus on this tree"
    )


def test_main_prints_truthful_corpus_selector_detail(monkeypatch, capsys, tmp_path) -> None:
    configured = tmp_path / "batch_test_list.csv"
    expanded = tmp_path / "expanded_batch_test_list.csv"
    contents = "1,1990/1\n1,1991/2\n"
    configured.write_text(contents, encoding="utf-8")
    expanded.write_text(contents, encoding="utf-8")

    monkeypatch.setattr(
        "lawvm.tools.oracle_check._corpus_path",
        lambda full: str(expanded if full else configured),
    )
    monkeypatch.setattr("lawvm.tools.oracle_check._run_corpus", lambda _sids, _mode, _parallel: [])
    monkeypatch.setattr("lawvm.tools.oracle_check._print_corpus_summary", lambda _results, _save_path: None)

    main(
        SimpleNamespace(
            corpus=True,
            corpus_full=False,
            save=False,
            db=None,
            mode="finlex_oracle",
            parallel=2,
            statute_id=None,
        )
    )

    out = capsys.readouterr().out
    assert "oracle-check: 2 statutes (configured corpus list (batch_test_list.csv); same rows as --corpus-full on this tree, parallel=2, longest-chain-first)" in out


# ---------------------------------------------------------------------------
# REPEAL_NOTICE node-level granularity (PRO_RESPONSE4_2 Q2)
# ---------------------------------------------------------------------------


def test_ir_node_has_repeal_placeholder_top_level() -> None:
    """Section node itself has lawvm_repeal_placeholder=1 → True."""
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        text="5 § on kumottu.",
        attrs={"lawvm_repeal_placeholder": "1"},
    )
    assert _ir_node_has_repeal_placeholder(node) is True


def test_ir_node_has_repeal_placeholder_child_subsection() -> None:
    """Live section with a repealed subsection child → True."""
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Voimassa oleva momenttiteksti.",
            ),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="2",
                text="2 momentti on kumottu.",
                attrs={"lawvm_repeal_placeholder": "1"},
            ),
        ),
    )
    assert _ir_node_has_repeal_placeholder(node) is True


def test_ir_node_has_repeal_placeholder_no_placeholders() -> None:
    """Live section with no repeal placeholders anywhere → False."""
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="3",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Tätä lakia ei sovelleta lakiin, joka on kumottu.",
            ),
        ),
    )
    assert _ir_node_has_repeal_placeholder(node) is False


def test_classify_statute_repeal_notice_at_section_level(monkeypatch) -> None:
    """Section fully repealed (placeholder) + oracle 'on kumottu' → EDITORIAL_CONVENTION.

    The section IR node itself carries lawvm_repeal_placeholder=1, so the
    compared node is a repeal placeholder. Oracle renders the same state with
    editorial attribution text → same legal state, different rendering.
    """
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="7",
                        text="7 § on kumottu.",
                        attrs={"lawvm_repeal_placeholder": "1"},
                    ),
                ),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "7 § on kumottu."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_7">
                  <num>7 §</num>
                  <content>
                    <p>7 § on kumottu L:lla 30.12.2008/1085.</p>
                  </content>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_meta",
        lambda _sid: (None, ""),
    )

    result = _classify_statute("2000/100", "legal_pit")
    assert result is not None
    assert len(result.section_results) == 1
    assert result.section_results[0]["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_repeal_notice_at_subsection_level(monkeypatch) -> None:
    """Live section containing a repealed subsection/momentti → EDITORIAL_CONVENTION.

    The section IR node itself is live, but one of its subsection children
    carries lawvm_repeal_placeholder=1.  The oracle text contains kumottu
    editorial text for that subsection.  The classifier must detect the
    repeal placeholder at the child level and classify as EDITORIAL_CONVENTION,
    not REPLAY_MISSING.
    """
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="3",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Normaali momenttiteksti.",
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="2 momentti on kumottu.",
                                attrs={"lawvm_repeal_placeholder": "1"},
                            ),
                        ),
                    ),
                ),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "3 § Normaali momenttiteksti. 2 momentti on kumottu."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        # Oracle carries kumottu notice with full attribution for subsection 2.
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_3">
                  <num>3 §</num>
                  <subsection>
                    <num>1 mom.</num>
                    <content><p>Normaali momenttiteksti.</p></content>
                  </subsection>
                  <subsection>
                    <num>2 mom.</num>
                    <content>
                      <p>2 momentti on kumottu L:lla 30.12.2008/1085.</p>
                    </content>
                  </subsection>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_meta",
        lambda _sid: (None, ""),
    )

    result = _classify_statute("2001/200", "legal_pit")
    assert result is not None
    sec = next(
        (s for s in result.section_results if s["section"] == "section:3"),
        None,
    )
    assert sec is not None, "section:3 must appear in divergences"
    assert sec["diagnosis"] == "EDITORIAL_CONVENTION", (
        f"Expected EDITORIAL_CONVENTION for live section with repealed subsection, "
        f"got {sec['diagnosis']!r}"
    )


def test_classify_statute_kumottu_in_substantive_text_is_not_repeal_notice(
    monkeypatch,
) -> None:
    """'kumottu' in substantive oracle text with NO repeal placeholder → not EDITORIAL_CONVENTION.

    The oracle text contains 'kumottu' as part of legitimate law (e.g. a
    provision referencing another statute that was repealed).  The replay IR
    has no repeal-placeholder nodes anywhere in the tree.  The classifier must
    NOT suppress this divergence as EDITORIAL_CONVENTION — it is a genuine
    divergence between replay and oracle that requires investigation.
    """
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="12",
                        text="12 § Soveltamisala Tässä laissa säädettyjä menettelyjä sovelletaan.",
                    ),
                ),
            )
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def serialize_text(self) -> str:
            return "12 § Soveltamisala Tässä laissa säädettyjä menettelyjä sovelletaan."

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        return FakeMaster()

    def fake_ground_truth_tree(_sid: str):
        # Oracle has "kumottu" in substantive text: references a repealed law.
        return etree.fromstring(
            """
            <act>
              <body>
                <section eId="sec_12">
                  <num>12 §</num>
                  <heading>Soveltamisala</heading>
                  <content>
                    <p>Tässä laissa säädettyjä menettelyjä sovelletaan toimintaan,
                    johon ei sovelleta kumottua lakia 123/1990.</p>
                  </content>
                </section>
              </body>
            </act>
            """
        )

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", fake_ground_truth_tree)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_meta",
        lambda _sid: (None, ""),
    )

    result = _classify_statute("2002/300", "legal_pit")
    assert result is not None
    sec = next(
        (s for s in result.section_results if s["section"] == "section:12"),
        None,
    )
    assert sec is not None, "section:12 must appear in divergences"
    assert sec["diagnosis"] != "EDITORIAL_CONVENTION", (
        f"'kumottu' in substantive text without repeal placeholder must not be "
        f"EDITORIAL_CONVENTION, got {sec['diagnosis']!r}"
    )


def test_classify_statute_replays_quietly(monkeypatch) -> None:
    quiet_calls: list[bool] = []

    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(kind=IRNodeKind.BODY, children=()),
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def source_pathology_rows(self):
            return ()

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, quiet=False, **_kwargs):
        quiet_calls.append(quiet)
        return FakeMaster()

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", lambda _sid: etree.fromstring("<act><body /></act>"))
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_meta",
        lambda _sid: (None, ""),
    )

    result = _classify_statute("1990/100", "legal_pit")

    assert result is not None
    assert quiet_calls == [True]


def test_classify_statute_suppresses_raw_replay_failed_chatter_for_1978_38(capsys) -> None:
    result = _classify_statute("1978/38", "legal_pit")
    out = capsys.readouterr().out

    assert result is not None
    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out
