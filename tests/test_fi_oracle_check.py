from types import SimpleNamespace
from datetime import date
import json
import sqlite3

import pytest
from lxml import etree

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalAddress
from lawvm.core.ir import ProvisionTimeline
from lawvm.core.ir import ProvisionVersion
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
from lawvm.tools.editorial_hygiene import (
    is_presentation_structural_diff,
    normalize_finlex_oracle_comparison_text,
    strip_editorial_annotations,
    strip_temporary_residue_annotations,
)
from lawvm.tools.classify_result import ClassifyResult
from lawvm.tools.oracle_check import (
    _build_blame_map,
    _classify_statute,
    _classify_statute_sync,
    _corpus_selection_detail,
    _diagnose,
    _diagnose_oracle_repeal_stub,
    _recodification_blame_frame_diagnosis,
    _source_pathology_diagnosis_for_blame,
    _attachment_body_text_ir,
    _attachment_body_text_oracle,
    _el_text,
    _extract_attachment_info_ir,
    _ir_node_has_repeal_placeholder,
    _replay_has_active_tombstoned_ancestor,
    main,
    _print_corpus_summary,
    _print_statute_summary,
    _lookup_blame_op,
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


def test_diagnose_treats_trailing_figure_legend_as_editorial_convention() -> None:
    # Oracle renders a source <block name="image"> legend as trailing
    # "N Marking-name" caption paragraphs; replay carries the image (no text).
    # Real shape: 1982/182 chapter:5/section:34.
    replay = (
        "34 § Sulkuviiva on yhtenäinen ajokaistojen välissä oleva keltainen tai "
        "valkoinen viiva. Sulkuviiva on keltainen, kun se erottaa vastakkaiset "
        "ajosuunnat toisistaan ja muulloin valkoinen. Ajoneuvo ei saa ylittää "
        "ajosuunnalleen tarkoitettua sulkuviivaa eikä ajaa sen päällä."
    )
    oracle = replay + " 1 Keskiviiva 3 Keltainen sulkuviiva 6 Ajoradan reunaviiva"

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_unblamed_high_overlap_truncation_as_source_pathology() -> None:
    # Real shape: 1989/573 §14. Both surfaces carry the same base provision and
    # no amendment touches it, but the consolidated witness drops/mangles a
    # phrase. This must not be reported as replay emitting an extra unit.
    replay = (
        "14 § Uudistaminen ja kumoaminen Ennen tämän lain voimaantuloa annetut "
        "määräykset ja ohjeet on saatettava 4§:n sekä tämän lain nojalla "
        "annettujen määräysten mukaisiksi 31 päivään joulukuuta 1990 mennessä. "
        "Ennen tämän lain voimaantuloa annetut määräykset on julkaistava ja "
        "rekisteröitävä viimeistään 31 päivänä joulukuuta 1990 tai kumottava "
        "vuoden 1991 alusta lukien. Samasta ajankohdasta lukien on niin ikään "
        "kumottava ohjeet, joita ei ole rekisteröity viimeistään 31 päivänä "
        "joulukuuta 1990."
    )
    oracle = (
        "14 § Uudistaminen ja kumoaminen Ennen tämän lain voimaantuloa annetut "
        "määräykset ja ohjeet on annettujen määräysten mukaisiksi 31 päivään "
        "joulukuuta 1990 mennessä. Ennen tämän lain voimaantuloa annetut "
        "määräykset on julkaistava ja rekisteröitävä viimeistään 31 päivänä "
        "joulukuuta1lukien. Samasta ajankohdasta lukien on niin ikään kumottava "
        "ohjeet, joita ei ole rekisteröity viimeistään 31 päivänä joulukuuta 1990."
    )

    assert _diagnose(replay, oracle, None) == "SOURCE_PATHOLOGY"


def test_diagnose_treats_replay_only_section_heading_as_editorial_convention() -> None:
    # Real shape: 1993/1501 §41. Replay carries a heading facet in section text;
    # the Finlex comparison surface omits it while preserving the body.
    replay = "41 § Rahoituspalvelut Veroa ei suoriteta rahoituspalvelun myynnistä."
    oracle = "41 § Veroa ei suoriteta rahoituspalvelun myynnistä."

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_does_not_drop_leading_substantive_sentence_as_heading() -> None:
    replay = (
        "41 § Rahoituspalvelut ovat verottomia. "
        "Veroa ei suoriteta rahoituspalvelun myynnistä."
    )
    oracle = "41 § Veroa ei suoriteta rahoituspalvelun myynnistä."

    assert _diagnose(replay, oracle, None) != "EDITORIAL_CONVENTION"


def test_diagnose_treats_unblamed_tiny_base_text_corruption_as_source_pathology() -> None:
    # Real shape: 1966/232 chapter 4 section 14. The base XML witness has two
    # tiny OCR/source corruptions ("12 a 13", "toiston") while the oracle has
    # the coherent legal text ("12 ja 13", "toisten"). No amendment touches
    # the section, so this is source pathology, not replay mutation drift.
    replay = (
        "Tiedoksianto muulle julkisoikeudelliselle yhdyskunnalle kuin 12 a 13 "
        "§:ssä mainitulle on toimitettava sen hallinnon puheenjohtajalle tai "
        "sille, jolla on oikeus edustaa yhdyskuntaa. Tiedoksianto yhtiölle, "
        "osuuskunnalle, yhdistykselle tai muulle yhtymälle taikka laitokselle "
        "tai säätiölle toimitetaan henkilölle, jolla yksin tai yhdessä toiston "
        "kanssa on oikeus sitä edustaa, taikka, jos edustajaa ei ole, "
        "yleistiedoksiannolla. Tiedoksianto kuolinpesälle voidaan toimittaa "
        "myös toimitsijalle, jonka hallussa pesä on."
    )
    oracle = (
        "Tiedoksianto muulle julkisoikeudelliselle yhdyskunnalle kuin 12 ja 13 "
        "§:ssä mainitulle on toimitettava sen hallinnon puheenjohtajalle tai "
        "sille, jolla on oikeus edustaa yhdyskuntaa. Tiedoksianto yhtiölle, "
        "osuuskunnalle, yhdistykselle tai muulle yhtymälle taikka laitokselle "
        "tai säätiölle toimitetaan henkilölle, jolla yksin tai yhdessä toisten "
        "kanssa on oikeus sitä edustaa, taikka, jos edustajaa ei ole, "
        "yleistiedoksiannolla. Tiedoksianto kuolinpesälle voidaan toimittaa "
        "myös toimitsijalle, jonka hallussa pesä on."
    )

    assert _diagnose(replay, oracle, None) == "SOURCE_PATHOLOGY"


def test_diagnose_keeps_replay_missing_when_drop_exceeds_figure_legend() -> None:
    # A genuine mid-text drop beyond the trailing legend must stay flagged: the
    # self-validating gate only reclassifies when replay matches oracle minus
    # the legend.  Real shape: 1982/182 chapter:5/section:35 (replay also drops
    # "merkitä valkoisella katkoviivalla. Viivan").
    oracle = (
        "35 § Ajoradan reunaviiva on yhtenäinen valkoinen viiva, joka osoittaa "
        "ajoradan reunaa. Reunaviivan jatke voidaan risteyksien ja ajoradasta "
        "erotettujen pysäkkien tai vastaavien alueiden kohdalla merkitä "
        "valkoisella katkoviivalla. Viivan ja välin suhde on tällöin 1:1. "
        "1 Keskiviiva 3 Keltainen sulkuviiva 5 Varoitusviiva 6 Ajoradan reunaviiva"
    )
    replay = (
        "35 § Ajoradan reunaviiva on yhtenäinen valkoinen viiva, joka osoittaa "
        "ajoradan reunaa. Reunaviivan jatke voidaan risteyksien ja ajoradasta "
        "erotettujen pysäkkien tai vastaavien alueiden kohdalla ja välin suhde "
        "on tällöin 1:1."
    )

    assert _diagnose(replay, oracle, None) == "REPLAY_MISSING"


def test_diagnose_treats_bare_oracle_stub_as_editorial_convention() -> None:
    replay = "5 a § Jos vakuutusyhdistys purkautuu, selvitystila pannaan alulle."
    oracle = "5 a §"

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_replay_tombstoned_ancestor_marks_oracle_descendant_stale() -> None:
    chapter = LegalAddress(path=(("part", "2"), ("chapter", "14a")))
    master = SimpleNamespace(
        products=SimpleNamespace(
            materialization_spec=SimpleNamespace(as_of="2026-01-01", query_type="governing"),
            timelines={
                chapter: ProvisionTimeline(
                    address=chapter,
                    versions=[
                        ProvisionVersion(
                            effective="2004-01-01",
                            enacted="2003-12-30",
                            content=IRNode(kind=IRNodeKind.CHAPTER, label="14a"),
                        ),
                        ProvisionVersion(
                            effective="2025-01-01",
                            enacted="2024-06-28",
                            content=None,
                        ),
                    ],
                )
            },
        )
    )

    assert _replay_has_active_tombstoned_ancestor(
        master,
        "part:2/chapter:14a/section:149c",
    )
    assert not _replay_has_active_tombstoned_ancestor(
        master,
        "part:2/chapter:14/section:149c",
    )


def test_diagnose_treats_legacy_roman_division_heading_as_editorial_convention() -> None:
    # Real shape: 1922/148 §1. The source witness projects the division title
    # "I. Yleisiä säännöksiä." into the following section; Finlex's consolidated
    # section text omits that presentation heading.
    replay = (
        "1 § I. Yleisiä säännöksiä. Tuomioistuimissa ja muissa valtion "
        "viranomaisissa on käytettävä maan kansalliskieltä."
    )
    oracle = (
        "1 § Tuomioistuimissa ja muissa valtion viranomaisissa on käytettävä "
        "maan kansalliskieltä."
    )

    assert _diagnose(replay, oracle, {"action": "INSERT", "source_statute": "1991/517"}) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_legacy_numbered_section_heading_as_editorial_convention() -> None:
    # Real shape: 1932/242 §67. The source witness projects the numbered
    # subdivision title "2. Vekselinjäljennökset." into the section; Finlex's
    # consolidated section text omits that presentation heading.
    replay = (
        "67 § 2. Vekselinjäljennökset. Jokaisella vekselin haltijalla on "
        "oikeus ottaa siitä jäljennöksiä."
    )
    oracle = "67 § Jokaisella vekselin haltijalla on oikeus ottaa siitä jäljennöksiä."

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_oracle_subsection_ordinals_as_editorial_convention() -> None:
    # Real shape: 1992/1702 §5. Finlex projects subsection ordinals ("1.",
    # "2.", "3.") into the paragraph text; LawVM carries subsection identity as
    # structure and renders the same body without those display prefixes.
    replay = (
        "5 § Ajoneuvon, järjestelmän, osan tai teknisen yksikön valmistaja ja valmistajan edustaja "
        "Ajoneuvon valmistajalla tarkoitetaan valmistajaa. "
        "Ajoneuvovalmistajan edustajalla tarkoitetaan edustajaa. "
        "Piensarjatyyppikatsastuksessa valmistajaksi rinnastetaan muuttaja."
    )
    oracle = (
        "5 § Ajoneuvon, järjestelmän, osan tai teknisen yksikön valmistaja ja valmistajan edustaja "
        "1. Ajoneuvon valmistajalla tarkoitetaan valmistajaa. "
        "2. Ajoneuvovalmistajan edustajalla tarkoitetaan edustajaa. "
        "3. Piensarjatyyppikatsastuksessa valmistajaksi rinnastetaan muuttaja."
    )

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_blame_owned_extra_insert_as_oracle_stale() -> None:
    # Real shape: 1974/1086 §12b. Replay carries text inserted by 1981/935 that
    # the selected oracle omits; this is an oracle/source mismatch, not an
    # unowned replay extra.
    replay = (
        "12 b § Tavarankuljetustukea voidaan myöntää. "
        "Asetuksella annetaan tarkempia säännöksiä ehdoista."
    )
    oracle = "12 b § Tavarankuljetustukea voidaan myöntää."

    assert (
        _diagnose(replay, oracle, {"action": "insert", "source_statute": "1981/935"})
        == "ORACLE_STALE"
    )


def test_diagnose_treats_promulgation_closure_as_editorial_convention() -> None:
    # Real shape: 1922/148 §26. The final promulgation closure is source-side
    # formula text, not consolidated provision body text.
    replay = (
        "26 § Tämä laki tulee voimaan 1 päivänä tammikuuta 1923. "
        "Tätä kaikki asianomaiset noudattakoot."
    )
    oracle = "26 § Tämä laki tulee voimaan 1 päivänä tammikuuta 1923."

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_source_pathology_demotes_absent_subsection_target_without_failed_op() -> None:
    # Real family: 1922/148 §7. Base XML collapsed the historical second moment
    # into subsection 1; 1975/10 explicitly repeals 7 §:n 2 momentti, so replay
    # has source-pathology ownership even when no coarse failed-op row is present.
    master = SimpleNamespace(
        findings=(),
        source_pathology_rows=lambda: [
            {
                "source_statute": "1975/10",
                "code": "SUBSECTION_TARGET_ABSENT",
                "target_label": "7 § 2 mom",
                "detail": {"target_section": "7", "target_paragraph": "2"},
            }
        ],
    )
    blame_op = {
        "source_statute": "1975/10",
        "target_norm": "7",
        "target_paragraph": "2",
    }

    assert _source_pathology_diagnosis_for_blame(master, blame_op) == "SOURCE_PATHOLOGY"


def test_source_pathology_demotes_section_level_destructive_shape_loss() -> None:
    # Real family: 1993/1709 §1 / 2000/882. The sparse schedule merge records a
    # section-level DESTRUCTIVE_SHAPE_LOSS_RISK row with target_label="1"; the
    # divergence blame row points at the same source and section.
    master = SimpleNamespace(
        findings=(),
        source_pathology_rows=lambda: [
            {
                "source_statute": "2000/882",
                "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                "target_unit_kind": "section",
                "target_label": "1",
                "detail": {},
            }
        ],
    )
    blame_op = {
        "source_statute": "2000/882",
        "target_norm": "1",
        "target_unit_kind": "section",
    }

    assert _source_pathology_diagnosis_for_blame(master, blame_op) == "SOURCE_PATHOLOGY"


def test_diagnose_treats_old_code_reference_marker_as_editorial() -> None:
    replay = "5 § - - - - - - - - - - - - - -"
    oracle = "5 § 5 §:n sijasta ks. L velkojien maksunsaantijärjestyksestä 1578/1992."

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_old_code_reference_marker_structural_diff_is_presentation_only() -> None:
    events = [
        {
            "kind": "wording_text_changed",
            "left_text": (
                "[Jos jollakulla on sellainen piilukirja, kuin Merilaissa on "
                "selitetty, olkoon hänellä etuoikeus laivaan ja tavaraan.]"
            ),
            "right_text": "7 §:n sijasta ks. MeriL 674/1994 4 luku 4 §.",
        }
    ]

    assert is_presentation_structural_diff({"label": 0}, events) is True


def test_old_code_reference_marker_requires_obsolete_replay_shape() -> None:
    events = [
        {
            "kind": "wording_text_changed",
            "left_text": "Pantinhaltija saa myydä pantin ja ottaa saatavansa kauppahinnasta.",
            "right_text": "2 §:n sijasta ks. L velkojien maksunsaantijärjestyksestä 1578/1992.",
        }
    ]

    assert is_presentation_structural_diff({"label": 0}, events) is False


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
    replay = pinned_replay("1974/258", mode="official_consolidation", quiet=True)
    assert replay.materialized_state.find_section("15") is None

    result = _classify_statute("1974/258", "official_consolidation", replay_result=replay)

    assert result is not None
    row = next(item for item in result.section_results if item["section"] == "section:15")
    assert row["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_1988_451_subsection_repeal_not_extra() -> None:
    """1988/451 17 §: 2011/590 replaces momentti 2 and repeals momentti 3.

    The whole-section timeline snapshot for that amendment must not carry the
    repealed momentti 3's pre-amendment content forward when it rebases onto the
    latest exact whole-section snapshot.  Before the rebase honored same-group
    subsection REPEAL ops, the stale momentti 3 survived and the section
    classified as REPLAY_EXTRA against the oracle's repeal tombstone.  The
    repealed momentti now resolves as the oracle's editorial repeal note.
    """
    result = _classify_statute("1988/451", "official_consolidation")

    assert result is not None
    row = next(item for item in result.section_results if item["section"] == "section:17")
    assert row["diagnosis"] != "REPLAY_EXTRA"
    assert row["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_1989_573_unblamed_text_corruption_is_not_replay_extra() -> None:
    result = _classify_statute("1989/573", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["section:11"]["diagnosis"] == "SOURCE_PATHOLOGY"
    assert by_section["section:14"]["diagnosis"] == "SOURCE_PATHOLOGY"


def test_classify_statute_1988_852_unblamed_base_text_corruption_is_source_pathology() -> None:
    result = _classify_statute("1988/852", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["section:2"]["diagnosis"] == "SOURCE_PATHOLOGY"


def test_classify_statute_1979_130_unblamed_base_text_gap_is_source_pathology() -> None:
    result = _classify_statute("1979/130", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["section:6"]["diagnosis"] == "SOURCE_PATHOLOGY"


def test_classify_statute_1966_232_unblamed_base_text_typo_is_source_pathology() -> None:
    result = _classify_statute("1966/232", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["chapter:4/section:14"]["diagnosis"] == "SOURCE_PATHOLOGY"


def test_source_pathology_diagnosis_maps_recodification_omission_shell_to_source_incomplete() -> None:
    master = SimpleNamespace(
        source_pathology_rows=lambda: [
            {
                "code": "RECODIFICATION_OMISSION_ONLY_SECTION_SHELL",
                "source_statute": "2019/371",
                "target_label": "4 luku 210 §",
                "detail": {
                    "target_chapter": "4",
                    "target_section": "210",
                    "destination_target_norm": "210",
                },
            }
        ],
        findings=(),
    )
    diagnosis = _source_pathology_diagnosis_for_blame(
        master,
        {
            "source_statute": "2019/371",
            "target_norm": "210",
            "target_chapter": "4",
        },
    )
    assert diagnosis == "SOURCE_INCOMPLETE"


def test_source_pathology_diagnosis_maps_recodification_source_chain_gap_to_source_incomplete() -> None:
    master = SimpleNamespace(
        source_pathology_rows=lambda: [
            {
                "code": "RECODIFICATION_SOURCE_CHAIN_GAP",
                "source_statute": "2019/371",
                "target_label": "2 luku 7 §",
                "detail": {
                    "target_chapter": "2",
                    "diagnostic_reason": "target_leaf_absent_under_existing_parent",
                },
            }
        ],
        findings=(),
    )
    diagnosis = _source_pathology_diagnosis_for_blame(
        master,
        {
            "source_statute": "2019/371",
            "target_norm": "7",
            "target_chapter": "2",
        },
    )
    assert diagnosis == "SOURCE_INCOMPLETE"


def test_diagnose_oracle_repeal_stub_source_limit_when_out_of_window() -> None:
    # Repealing statute not in the applicable amendment set → unreachable or
    # out-of-window → replay could not apply it → source-limit, not a bug.
    assert (
        _diagnose_oracle_repeal_stub(
            "1994/1218",
            applicable_amendment_ids={"2002/1071"},
            contingent_effective_sources=set(),
        )
        == "SOURCE_INCOMPLETE"
    )


def test_diagnose_oracle_repeal_stub_source_limit_when_contingent_effective() -> None:
    # In-window but its effective date is contingent/decree-set → replay could
    # not pin when it took effect → source-limit.
    assert (
        _diagnose_oracle_repeal_stub(
            "1994/1218",
            applicable_amendment_ids={"1994/1218"},
            contingent_effective_sources={"1994/1218"},
        )
        == "SOURCE_INCOMPLETE"
    )


def test_diagnose_oracle_repeal_stub_real_bug_when_available_and_in_window() -> None:
    # Reachable, in-window, effective date resolvable, yet the section survived →
    # a genuine missed-repeal bug, surfaced rather than hidden under source-limit.
    assert (
        _diagnose_oracle_repeal_stub(
            "2002/1071",
            applicable_amendment_ids={"2002/1071"},
            contingent_effective_sources=set(),
        )
        == "REPLAY_UNREPEALED"
    )


@pytest.mark.slow
def test_classify_statute_1993_1501_eu_accession_repeal_stubs_are_source_limit() -> None:
    # 1993/1501 ch.4 §47-54 and ch.10 §107-109 are oracle repeal stubs from the
    # contingent-effective EU-accession restructure 1994/1218 (entry into force
    # could not be pinned), so replay legitimately kept them: source-limit, not a
    # replay bug.  §46/§55/§68a are repealed by "kumotaan N § ja sen edellä oleva
    # väliotsikko" clauses; the heading op no longer masks the section repeal, so
    # replay now tombstones the section to match the oracle repeal stub.
    result = _classify_statute_sync("1993/1501", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}

    for label in (
        "part:1/chapter:4/section:47",
        "part:1/chapter:4/section:48",
        "part:1/chapter:4/section:54",
        "part:1/chapter:10/section:107",
        "part:1/chapter:10/section:109",
    ):
        assert by_section[label]["diagnosis"] == "SOURCE_INCOMPLETE"

    for label in (
        "part:1/chapter:4/section:46",
        "part:1/chapter:4/section:55",
        "part:1/chapter:5/section:68a",
    ):
        assert by_section[label]["diagnosis"] != "REPLAY_UNREPEALED"


def test_classify_statute_1992_1702_empty_operative_body_wave_is_source_incomplete() -> None:
    result = _classify_statute("1992/1702", "official_consolidation")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["chapter:5/section:24"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:5/section:25a"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:8/section:33"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:10/section:46b"]["diagnosis"] == "SOURCE_INCOMPLETE"
    assert by_section["chapter:8/section:39a"]["diagnosis"] == "ORACLE_STALE"
    assert by_section["chapter:8/section:42"]["diagnosis"] == "ORACLE_STALE"


def test_classify_statute_2015_1480_pdf_only_amendment_is_source_incomplete() -> None:
    result = _classify_statute("2015/1480", "official_consolidation")

    assert result is not None
    assert {
        (row.get("source_statute"), row.get("code"))
        for row in result.source_pathologies
    } >= {("2021/1209", "EMPTY_OPERATIVE_BODY")}

    by_section = {item["section"]: item for item in result.section_results}
    for label in ("section:6", "section:8", "section:13", "section:20"):
        assert by_section[label]["diagnosis"] == "SOURCE_INCOMPLETE"


def test_classify_statute_2005_347_truncated_base_section_is_source_incomplete() -> None:
    result = _classify_statute("2005/347", "official_consolidation")

    assert result is not None
    section_2 = next(item for item in result.section_results if item["section"] == "section:2")
    assert section_2["diagnosis"] == "SOURCE_INCOMPLETE"


def test_classify_statute_1987_322_repealed_stubs_are_editorial_convention() -> None:
    # Sections 10a-10f appear in the oracle as "kumottu" repeal stubs.
    # They are correctly absent from the replay (repealed), and the oracle
    # stub is an editorial rendering of that state — so EDITORIAL_CONVENTION.
    result = _classify_statute("1987/322", "official_consolidation")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    for suffix in ("10a", "10b", "10c", "10d", "10e", "10f"):
        assert by_section[f"section:{suffix}"]["diagnosis"] == "EDITORIAL_CONVENTION"


def test_classify_statute_1990_1295_abridged_chapter_missing_is_source_incomplete() -> None:
    # 1990/1295 ships an abridged base witness whose chapters 7-10 are replaced by
    # a "Puuttuu luvut 7-11" notice (the span runs up to, but excludes, the present
    # chapter 11) and never restated in full by any amendment body, so replay emits
    # SOURCE.ABRIDGED_BASE_CHAPTER_UNRECONSTRUCTABLE for those chapters. Sections
    # the oracle places under them cannot be materialized from replay inputs — they
    # are a source limit, so they must classify as SOURCE_INCOMPLETE rather than
    # MISSING / REPLAY_MISSING / REPLAY_EXTRA (which the ledger counts as real-bug
    # suspects).
    result = _classify_statute("1990/1295", "official_consolidation")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    # Sections inside the abridged chapter span are reclassified off MISSING when
    # replay never built them at all.
    for label in (
        "chapter:7/section:34",
        "chapter:8/section:36",
        "chapter:8/section:39",
        "chapter:8/section:40",
        "chapter:9/section:47",
        "chapter:9/section:48",
    ):
        assert by_section[label]["diagnosis"] == "SOURCE_INCOMPLETE"

    # Chapter 10 is inside the span too. Amendment 1997/29 carries a "10 luku"
    # body holding only a newly *added* section (54 a §); seeding it does not make
    # the chapter reconstructable. Sections 49/50/52/54 are delta-touched by later
    # amendments, so replay builds fragments whose text diverges from the oracle's
    # full bodies — raw REPLAY_MISSING (49/52/54) and REPLAY_EXTRA (50). These are
    # a source limit, not a replay fault, so they reclassify to SOURCE_INCOMPLETE.
    for label in (
        "chapter:10/section:49",
        "chapter:10/section:50",
        "chapter:10/section:51",
        "chapter:10/section:52",
        "chapter:10/section:53",
        "chapter:10/section:54",
    ):
        assert by_section[label]["diagnosis"] == "SOURCE_INCOMPLETE", (
            label,
            by_section[label]["diagnosis"],
        )

    # Chapter 11 is outside the abridged span (it is the present chapter that
    # bounds the span), so its genuine drops stay MISSING — real-bug suspects.
    for label in (
        "chapter:11/section:56",
        "chapter:11/section:58",
        "chapter:11/section:60",
    ):
        assert by_section[label]["diagnosis"] == "MISSING"


def test_classify_statute_1982_182_implicit_abridged_chapter_gap_is_source_incomplete() -> None:
    # 1982/182 base XML silently jumps from chapter 3 to chapter 8. Amendment
    # bodies later restate chapters inside that gap, proving the base witness is
    # abridged, but no amendment body carries chapter 4. Sections the oracle
    # still places under chapter 4 cannot be reconstructed from replay inputs.
    result = _classify_statute("1982/182", "official_consolidation")

    assert result is not None

    by_section = {item["section"]: item for item in result.section_results}
    for label in (
        "chapter:4/section:24",
        "chapter:4/section:25",
        "chapter:4/section:29",
        "chapter:4/section:30",
    ):
        assert by_section[label]["diagnosis"] == "SOURCE_INCOMPLETE"


def test_classify_statute_1994_1466_repealed_sections_are_editorial_not_missing() -> None:
    # 1994/1466 has many sections the oracle keeps only as a one-line repeal
    # tombstone ("N § on kumottu L:lla MMMM/NN"); the replay correctly
    # materializes them as fully repealed (no node).  These must classify as
    # EDITORIAL_CONVENTION (the tombstone is editorial rendering of the same
    # repealed state), not MISSING (which the ledger counts as a real-bug suspect).
    result = _classify_statute("1994/1466", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}
    for label in ("section:21", "section:43a", "section:48"):
        assert by_section[label]["diagnosis"] == "EDITORIAL_CONVENTION"
    # No section should remain a MISSING real-bug suspect — they are all repeal
    # tombstones the replay correctly repealed.
    assert not [
        item for item in result.section_results if item["diagnosis"] == "MISSING"
    ]


def test_extract_attachment_info_ir_counts_materialized_annex() -> None:
    """An operative Liite annex materialized into the replay IR must be counted.

    ``IRNode.kind`` is an ``IRNodeKind`` enum; comparing it to the bare string
    ``"hcontainer"`` always failed, so the helper reported zero attachments and
    the classifier raised a spurious ``LIITE_DIFF`` against an oracle that
    carried the same annex.  This pins the enum-based comparison: the fee-table
    annex is counted and its title is recovered from the CONTENT body, while
    non-operative trailing matter (signatures / conclusions) is not counted.
    """
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "statuteProvisionsWrapper"},
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="1 § Operative."),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "conclusions"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "signatures"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Helsingissä 2015"),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "attachments"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "attachment"},
                        children=(
                            IRNode(kind=IRNodeKind.CONTENT, text="Liite Maksutaulukko"),
                        ),
                    ),
                ),
            ),
        ),
    )

    count, titles = _extract_attachment_info_ir(body)

    assert count == 1
    assert titles == ["Liite Maksutaulukko"]


def test_extract_attachment_info_ir_ignores_non_attachment_containers() -> None:
    """Signatures / conclusions hcontainers must never be counted as annexes."""
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "conclusions"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "signatures"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Allekirjoitus"),),
                    ),
                ),
            ),
        ),
    )

    count, titles = _extract_attachment_info_ir(body)

    assert count == 0
    assert titles == []


def _ir_body_with_annex(annex_body_text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.HCONTAINER,
                attrs={"name": "attachments"},
                children=(
                    IRNode(
                        kind=IRNodeKind.HCONTAINER,
                        attrs={"name": "attachment"},
                        children=(IRNode(kind=IRNodeKind.CONTENT, text=annex_body_text),),
                    ),
                ),
            ),
        ),
    )


def test_attachment_body_text_ir_counts_nested_attachment_once() -> None:
    """An ``attachment`` nested in an outer ``attachments`` container is read once.

    Counting both the outer container and the inner attachment would double the
    annex body and manufacture a spurious divergence (the text-vs-text-doubled
    ``lev≈0.667`` artifact).
    """
    body = _ir_body_with_annex("Liite Maksutaulukko 10 euroa")

    assert _attachment_body_text_ir(body) == "liitemaksutaulukko10euroa"


def test_attachment_body_text_oracle_prefers_outer_container() -> None:
    """Oracle annex body is read from the outer ``attachments`` container once."""
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<body>"
        '<hcontainer name="attachments">'
        '<hcontainer name="attachment"><heading>Liite</heading>'
        "<p>Maksutaulukko 10 euroa</p></hcontainer>"
        "</hcontainer>"
        "</body></akomaNtoso>"
    )
    root = etree.fromstring(xml.encode())

    assert _attachment_body_text_oracle(root) == "liitemaksutaulukko10euroa"


def test_attachment_body_text_matches_between_ir_and_oracle_when_equal() -> None:
    """Identical annex bodies clean to the same string on both sides."""
    body = _ir_body_with_annex("Liite: Maksutaulukko")
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        '<body><hcontainer name="attachments">'
        '<hcontainer name="attachment"><p>Liite: Maksutaulukko</p></hcontainer>'
        "</hcontainer></body></akomaNtoso>"
    )
    root = etree.fromstring(xml.encode())

    assert _attachment_body_text_ir(body) == _attachment_body_text_oracle(root)


def test_attachment_body_text_diverges_when_replay_drops_table() -> None:
    """Replay carrying only the annex heading diverges from an oracle table body.

    This is the structurally-invisible case the body-level LIITE comparison is
    meant to surface: the annex count matches but replay dropped the table body.
    """
    replay = _ir_body_with_annex("Liite: Kalat")
    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        '<body><hcontainer name="attachments">'
        '<hcontainer name="attachment"><heading>Liite: Kalat</heading>'
        "<p>1 silakka 2 hauki 3 ahven 4 made 5 kuha</p></hcontainer>"
        "</hcontainer></body></akomaNtoso>"
    )
    root = etree.fromstring(xml.encode())

    r_body = _attachment_body_text_ir(replay)
    o_body = _attachment_body_text_oracle(root)
    assert r_body != o_body
    assert len(o_body) > len(r_body)


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
        assert mode == "official_consolidation"
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
        "official_consolidation",
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
        assert mode == "official_consolidation"
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
        "official_consolidation",
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
    result = _classify_statute("2001/1047", "official_consolidation")

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


def test_normalize_finlex_oracle_comparison_text_removes_shared_presentation_residue() -> None:
    text = (
        "5 § 5 § on kumottu L:lla 13.11.1992/1015. "
        "Tätä lakia sovelletaan. (9.7.1982/540) Aiempi sanamuoto kuuluu:"
    )

    normalized = normalize_finlex_oracle_comparison_text(text)

    assert "kumottu" not in normalized
    assert "9.7.1982/540" not in normalized
    assert "Aiempi sanamuoto kuuluu" not in normalized
    assert "Tätä lakia sovelletaan." in normalized


def test_normalize_finlex_oracle_comparison_text_preserves_non_stub_kumottu_prose() -> None:
    text = (
        "Jos muussa lainsäädännössä viitataan tällä lailla kumottujen lakien "
        "säännöksiin, viittauksen on katsottava tarkoittavan vastaavaa lainkohtaa."
    )

    assert normalize_finlex_oracle_comparison_text(text) == text


def test_normalize_finlex_oracle_comparison_text_normalizes_chemical_list_spacing() -> None:
    text = "Safroli;  Isosafroli\n        Piperonaali"

    normalized = normalize_finlex_oracle_comparison_text(text)

    assert normalized == "Safroli; Isosafroli\n; Piperonaali"


def test_normalize_finlex_oracle_comparison_text_normalizes_embedded_five_ocr() -> None:
    text = "kuluttaja-arvostelujen tai sosiaal5sen median suosittelujen vääristeleminen"

    normalized = normalize_finlex_oracle_comparison_text(text)

    assert normalized == (
        "kuluttaja-arvostelujen tai sosiaalisen median suosittelujen vääristeleminen"
    )


def test_presentation_structural_diff_treats_embedded_five_ocr_as_oracle_pathology() -> None:
    events = [
        {
            "kind": "wording_text_changed",
            "left_text": "sosiaalisen median suosittelujen vääristeleminen",
            "right_text": "sosiaal5sen median suosittelujen vääristeleminen",
        }
    ]

    assert is_presentation_structural_diff({"label": 0}, events)


def test_normalize_finlex_oracle_comparison_text_can_opt_into_full_editorial_cleanup() -> None:
    text = "A:lla 123/2020 muutettu 1 momentti tuli voimaan 1.1.2021. Pysyvä teksti."

    normalized = normalize_finlex_oracle_comparison_text(text, strip_editorial=True)

    assert normalized == "Pysyvä teksti."


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


def test_diagnose_does_not_treat_substantive_high_similarity_as_editorial() -> None:
    # Real shape: 2009/1182 fin@20110753 metadata says 2011/250 changed § 3,
    # but the consolidated body still carries the pre-2011 ministry name.
    replay = (
        "3 § Muut maksulliset suoritteet Valtion maksuperustelain 7 §:ssä "
        "tarkoitettuja suoritteita, jotka opetus- ja kulttuuriministeriö "
        "hinnoittelee liiketaloudellisin perustein, ovat seuraavat tilauksesta "
        "toimitetut suoritteet: 1) opetus- ja kulttuuriministeriön hallinnassa "
        "olevien toimitilojen ja laitteiden käyttö; 5) selvitykset, arvioinnit "
        "ja tutkimukset."
    )
    oracle = (
        "3 § Muut maksulliset suoritteet Valtion maksuperustelain 7 §:ssä "
        "tarkoitettuja suoritteita, jotka opetusministeriö hinnoittelee "
        "liiketaloudellisin perustein, ovat seuraavat tilauksesta toimitetut "
        "suoritteet: 1) opetusministeriön hallinnassa olevien toimitilojen ja "
        "laitteiden käyttö; 5) selvitykset ja tutkimukset."
    )

    assert _diagnose(replay, oracle, {"action": "replace", "source_statute": "2011/250"}) == "UNKNOWN"


def test_classify_statute_2009_1182_ministry_rename_is_stale_oracle() -> None:
    result = _classify_statute("2009/1182", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}
    assert by_section["section:1"]["diagnosis"] == "ORACLE_STALE"
    assert by_section["section:3"]["diagnosis"] == "ORACLE_STALE"


def test_diagnose_treats_temporary_stub_over_substantive_replay_as_editorial() -> None:
    replay = (
        "13 a § Vanhusten ja muiden asiakasryhmien tarpeita vastaavien "
        "sosiaali- ja terveydenhuollon palvelukokonaisuuksien muodostamiseksi "
        "kunta voi kokeilla tehtävien järjestämistä."
    )
    oracle = "13 a § 13 a § oli väliaikaisesti voimassa 1.1.2005-31.12.2022 L:lla 1429/2004."

    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


def test_diagnose_treats_blamed_tiny_source_text_corruption_as_source_pathology() -> None:
    # Real shape: 1983/683 section 7 carries source XML typos ("siitä>" and
    # "päilidehuolto") while the oracle silently corrects them and adds an
    # amendment-date heading annotation.
    replay = (
        "7 § Palvelujen kehittäminen ja kasvatuksen tukeminen "
        "Kunnan on sosiaali- ja terveydenhuoltoa, koulutointa sekä muita "
        "lapsille, nuorille ja lapsiperheille tarkoitettuja palveluja "
        "kehittäessään pidettävä huolta myös siitä> että näiden palvelujen "
        "avulla tuetaan huoltajia lasten kasvatuksessa. Kun aikuiselle "
        "annetaan sosiaali- ja terveydenhuollon, kuten päilidehuolto- ja "
        "mielenterveyspalveluja, on otettava huomioon myös lapsen tuen tarve."
    )
    oracle = (
        "7 § Palvelujen kehittäminen ja kasvatuksen tukeminen (9.2.1990/139) "
        "Kunnan on sosiaali- ja terveydenhuoltoa, koulutointa sekä muita "
        "lapsille, nuorille ja lapsiperheille tarkoitettuja palveluja "
        "kehittäessään pidettävä huolta myös siitä, että näiden palvelujen "
        "avulla tuetaan huoltajia lasten kasvatuksessa. Kun aikuiselle "
        "annetaan sosiaali- ja terveydenhuollon, kuten päihdehuolto- ja "
        "mielenterveyspalveluja, on otettava huomioon myös lapsen tuen tarve."
    )

    assert _diagnose(replay, oracle, {"action": "insert", "source_statute": "1990/139"}) == "SOURCE_PATHOLOGY"


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


def test_diagnose_treats_repealed_temporary_residue_stub_as_editorial() -> None:
    replay = ""
    oracle = (
        "13 h § 13 h § on kumottu L:lla "
        "9.8.2019/931, väliaikaisesti voimassa 1.1.2005–31.12.2019 "
        "L:lla 1429/2004, 1106/2008, 1315/2010, 1219/2014, 1080/2016, 1300/2018."
    )

    stripped = strip_temporary_residue_annotations(oracle)

    assert stripped.strip() == "13 h §"
    assert _diagnose(replay, oracle, None) == "EDITORIAL_CONVENTION"


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
        assert mode == "official_consolidation"
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
        "official_consolidation",
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
    from lawvm.finland.corpus import get_ground_truth_tree

    replay = pinned_replay("2016/258", mode="official_consolidation", quiet=True)
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
        assert mode == "official_consolidation"
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

    result = _classify_statute("2014/1429", "official_consolidation")
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
        assert mode == "official_consolidation"
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

    result = _classify_statute("1999/488", "official_consolidation")
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


def test_blame_map_attributes_chapter_scope_to_descendant_section() -> None:
    compiled_ops = [
        {
            "action": "seed",
            "source_statute": "1991/1",
            "target_unit_kind": "chapter",
            "target_norm": "7",
            "target_chapter": "",
            "target_part": "",
            "witness_rule_id": "fi_chapter_seed_inserted_from_amendment_body",
        }
    ]

    blame_map = _build_blame_map(compiled_ops)
    blame_op = _lookup_blame_op(blame_map, "chapter:7/section:36")

    assert blame_op["source_statute"] == "1991/1"
    assert blame_op["witness_rule_id"] == "fi_chapter_seed_inserted_from_amendment_body"


def test_blame_map_prefers_unique_matching_chapter_over_unscoped_section() -> None:
    compiled_ops = [
        {
            "action": "replace",
            "source_statute": "1977/604",
            "target_unit_kind": "section",
            "target_norm": "31",
            "target_chapter": "",
            "target_part": "",
            "witness_rule_id": "fi.section_ref",
        },
        {
            "action": "replace",
            "source_statute": "1968/493",
            "target_unit_kind": "section",
            "target_norm": "31",
            "target_chapter": "4",
            "target_part": "",
            "witness_rule_id": "fi_body_chapter_scope_from_source_body",
        },
    ]

    blame_map = _build_blame_map(compiled_ops)
    blame_op = _lookup_blame_op(blame_map, "part:2/chapter:4/section:31")

    assert blame_op["source_statute"] == "1968/493"
    assert blame_op["witness_rule_id"] == "fi_body_chapter_scope_from_source_body"


def test_blame_map_includes_restructure_snapshot_lo_ops() -> None:
    compiled_ops: list[dict[str, object]] = []
    lo_ops = [
        SimpleNamespace(
            op_id="snapshot_section_209_restructure_2019/371",
            sequence=0,
            action=SimpleNamespace(value="insert"),
            target="part:5/chapter:4/section:209",
            source=SimpleNamespace(
                statute_id="2019/371",
                title="Laki liikenteen palveluista annetun lain muuttamisesta",
            ),
            witness_rule_id="fi.restructure.relabel_section_snapshot",
        )
    ]

    blame_map = _build_blame_map(compiled_ops, lo_ops=lo_ops)
    blame_op = _lookup_blame_op(blame_map, "part:5/chapter:4/section:209")

    assert blame_op["source_statute"] == "2019/371"
    assert blame_op["witness_rule_id"] == "fi.restructure.relabel_section_snapshot"


def test_blame_lookup_follows_restructure_renumber_migration_lineage() -> None:
    compiled_ops: list[dict[str, object]] = []
    lo_ops = [
        SimpleNamespace(
            op_id="snapshot_section_210_restructure_2019/371",
            sequence=0,
            action=SimpleNamespace(value="insert"),
            target="part:5/chapter:4/section:210",
            source=SimpleNamespace(
                statute_id="2019/371",
                title="Laki liikenteen palveluista annetun lain muuttamisesta",
            ),
            witness_rule_id="fi.restructure.relabel_section_snapshot",
        )
    ]
    migration_events = (
        SimpleNamespace(
            kind="renumber",
            from_address=LegalAddress(path=(("part", "5"), ("chapter", "4"))),
            to_address=LegalAddress(path=(("part", "5"), ("chapter", "25"))),
            source_statute="2020/1256",
        ),
    )

    blame_map = _build_blame_map(compiled_ops, lo_ops=lo_ops)
    blame_op = _lookup_blame_op(
        blame_map,
        "part:5/chapter:25/section:210",
        migration_events=migration_events,
    )

    assert blame_op["source_statute"] == "2019/371"
    assert blame_op["witness_rule_id"] == "fi.restructure.relabel_section_snapshot"


def test_fi_ledger_inputs_attributes_restructure_blame_via_migration_lineage(
    monkeypatch,
) -> None:
    from lawvm.tools.spec_ledger import fi_ledger_inputs

    lo_ops = [
        SimpleNamespace(
            op_id="snapshot_section_209_restructure_2019/371",
            sequence=0,
            action=SimpleNamespace(value="insert"),
            target="part:5/chapter:4/section:209",
            source=SimpleNamespace(
                statute_id="2019/371",
                title="Laki liikenteen palveluista annetun lain muuttamisesta",
            ),
            witness_rule_id="fi.restructure.relabel_section_snapshot",
        )
    ]
    migration_events = (
        SimpleNamespace(
            kind="renumber",
            from_address=LegalAddress(path=(("part", "5"), ("chapter", "4"))),
            to_address=LegalAddress(path=(("part", "5"), ("chapter", "25"))),
            source_statute="2020/1256",
        ),
    )
    fake_result = ClassifyResult(
        sid="2017/320",
        section_results=[
            {
                "section": "part:5/chapter:25/section:209",
                "diagnosis": "SOURCE_INCOMPLETE",
                "blame_source": "2019/371",
            }
        ],
        compiled_ops=[],
        lo_ops=lo_ops,
        replay_result=SimpleNamespace(migration_events=migration_events),
    )
    monkeypatch.setattr(
        "lawvm.tools.oracle_check._classify_statute_sync",
        lambda _sid, _mode: fake_result,
    )

    inputs = list(fi_ledger_inputs(["2017/320"], "official_consolidation"))

    assert len(inputs) == 1
    assert len(inputs[0].divergences) == 1
    row = inputs[0].divergences[0]
    assert row.rule_id == "fi.restructure.relabel_section_snapshot"
    assert row.blame_source == "2019/371"
    assert row.diagnosis == "SOURCE_INCOMPLETE"


@pytest.mark.slow
def test_classify_statute_2017_320_recodification_omission_shell_at_chapter_25_is_source_incomplete() -> None:
    """2019/371 relabel snapshots for §209-210 omit operative bodies; 2020/1256 renumbers ch.4→ch.25."""
    result = _classify_statute_sync("2017/320", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}

    for label in (
        "part:5/chapter:25/section:209",
        "part:5/chapter:25/section:210",
    ):
        row = by_section[label]
        assert row["diagnosis"] == "SOURCE_INCOMPLETE", (label, row["diagnosis"])
        assert row["blame_source"] == "2019/371"


def test_recodification_blame_frame_diagnosis_maps_relabel_snapshot_frame_mismatch() -> None:
    diagnosis = _recodification_blame_frame_diagnosis(
        {
            "witness_rule_id": "fi.restructure.relabel_section_snapshot",
            "target_unit_kind": "section",
            "target_norm": "13",
            "target_chapter": "1",
            "target_part": "2",
            "source_statute": "2019/371",
        },
        "part:2/chapter:2/section:13",
    )
    assert diagnosis == "SOURCE_INCOMPLETE"


def test_recodification_blame_frame_diagnosis_maps_section_renumber_frame_mismatch() -> None:
    diagnosis = _recodification_blame_frame_diagnosis(
        {
            "witness_rule_id": "fi.section_renumber",
            "target_unit_kind": "section",
            "target_norm": "9",
            "target_chapter": "3",
            "target_part": "3",
            "source_statute": "2019/371",
        },
        "part:4/chapter:3/section:9",
    )
    assert diagnosis == "SOURCE_INCOMPLETE"


def test_recodification_blame_frame_diagnosis_maps_structural_extra_renumber_shell() -> None:
    diagnosis = _recodification_blame_frame_diagnosis(
        {
            "witness_rule_id": "fi.section_renumber",
            "target_unit_kind": "section",
            "target_norm": "1",
            "target_chapter": "4",
            "target_part": "4",
            "source_statute": "2019/371",
        },
        "part:4/chapter:4/section:1",
        replay_text="1 § Example heading\nBody text retained by replay.",
        oracle_text="",
    )
    assert diagnosis == "SOURCE_INCOMPLETE"


def test_recodification_blame_frame_diagnosis_maps_section_renumber_heading_swap() -> None:
    diagnosis = _recodification_blame_frame_diagnosis(
        {
            "witness_rule_id": "fi.section_renumber",
            "target_unit_kind": "section",
            "target_norm": "5",
            "target_chapter": "2",
            "target_part": "2",
            "source_statute": "2019/371",
        },
        "part:2/chapter:2/section:5",
        replay_text="5 § Liikenteestä vastaava henkilö Taksi- ja henkilöliikenneluvan haltijalla on oltava",
        oracle_text="5 §                             Henkilö- ja tavaraliikenneluvan myöntäminen",
    )
    assert diagnosis == "SOURCE_INCOMPLETE"


@pytest.mark.slow
def test_classify_statute_2017_320_recodification_frame_wave_is_source_incomplete() -> None:
    """2019/371 relabel snapshots compare at pre-migration frames; ch.2/ch.4 waves are source limits."""
    result = _classify_statute_sync("2017/320", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}

    for label in (
        "part:2/chapter:2/section:4",
        "part:2/chapter:2/section:5",
        "part:2/chapter:2/section:6",
        "part:2/chapter:2/section:9",
        "part:2/chapter:2/section:11",
        "part:2/chapter:2/section:13",
        "part:2/chapter:2/section:14",
        "part:2/chapter:2/section:16",
        "part:2/chapter:3/section:22",
        "part:2/chapter:4/section:25",
        "part:2/chapter:4/section:27",
        "part:2/chapter:4/section:30",
        "part:2/chapter:4/section:31",
        "part:2/chapter:4/section:36",
        "part:5/chapter:25/section:211",
        "part:6/chapter:28/section:230",
        "part:7/chapter:31/section:247",
        "part:7/chapter:32/section:264",
    ):
        row = by_section[label]
        assert row["diagnosis"] == "SOURCE_INCOMPLETE", (label, row["diagnosis"])
        assert row["blame_source"] == "2019/371"


@pytest.mark.slow
def test_classify_statute_2017_320_recodification_extra_shells_are_source_incomplete() -> None:
    """2019/371 renumber shells present only in replay must not stay bare EXTRA."""
    result = _classify_statute_sync("2017/320", "official_consolidation")

    assert result is not None
    by_section = {item["section"]: item for item in result.section_results}

    for label in (
        "part:2/chapter:14/section:4",
        "part:4/chapter:3/section:9",
        "part:4/chapter:4/section:1",
        "part:5/chapter:1/section:1",
        "part:7/chapter:2/section:2",
    ):
        row = by_section[label]
        assert row["diagnosis"] == "SOURCE_INCOMPLETE", (label, row["diagnosis"])
        assert row["blame_source"] == "2019/371"

    extra_blamed_2019_371 = [
        item
        for item in result.section_results
        if item["diagnosis"] == "EXTRA" and item.get("blame_source") == "2019/371"
    ]
    assert extra_blamed_2019_371 == []


def test_blame_map_keeps_ambiguous_section_suffix_unattributed() -> None:
    compiled_ops = [
        {
            "action": "replace",
            "source_statute": "1990/1",
            "target_unit_kind": "section",
            "target_norm": "5",
            "target_chapter": "1",
            "target_part": "",
            "witness_rule_id": "fi.section_ref",
        },
        {
            "action": "replace",
            "source_statute": "1990/2",
            "target_unit_kind": "section",
            "target_norm": "5",
            "target_chapter": "2",
            "target_part": "",
            "witness_rule_id": "fi.section_ref",
        },
    ]

    blame_map = _build_blame_map(compiled_ops)

    assert _lookup_blame_op(blame_map, "part:1/chapter:9/section:5") == {}


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
    result = _classify_statute("2016/768", "official_consolidation")

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


def test_classify_statute_2015_364_keeps_item_repeal_from_expiring_whole_section() -> None:
    """2017/1153 repeals 9 § 4 kohta, not all of 9 §."""
    result = _classify_statute("2015/364", "official_consolidation")

    assert result is not None
    sec9 = next(sec for sec in result.section_results if sec["section"] == "section:9")
    assert sec9["diagnosis"] != "REPLAY_MISSING"
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
    result = _classify_statute("2012/916", "official_consolidation")

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
                mode="official_consolidation",
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
            mode="official_consolidation",
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


def test_classify_statute_suppresses_raw_replay_failed_chatter(capsys, monkeypatch) -> None:
    class FakeMaster:
        title = "Test statute"
        materialized_state = SimpleNamespace(
            ir=IRNode(kind=IRNodeKind.BODY, children=()),
        )
        source_adjudication = SimpleNamespace(source_pathologies=[])
        findings = ()

        def source_pathology_rows(self):
            return ()

        def serialize_text(self) -> str:
            return ""

    def fake_replay_xml(_sid: str, mode: str, compiled_ops_out=None, failed_ops_out=None, quiet=False, **_kwargs):
        assert mode == "legal_pit"
        if not quiet:
            print("REPLACE 10 luku otsikko → FAILED")
            print("INSERT 10 luku 16 § 2 mom → FAILED")
        return FakeMaster()

    monkeypatch.setattr("lawvm.tools.oracle_check.replay_xml", fake_replay_xml)
    monkeypatch.setattr(
        "lawvm.tools.oracle_check.get_consolidated_oracle_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            locator="",
            cutoff_date=None,
            oracle_version_amendment_id="",
            selector_mode="latest_cached_editorial",
        ),
    )
    monkeypatch.setattr("lawvm.tools.oracle_check.get_ground_truth_tree", lambda _sid: etree.fromstring("<act><body /></act>"))
    monkeypatch.setattr("lawvm.tools.oracle_check.get_consolidated_meta", lambda _sid: (None, ""))
    monkeypatch.setattr(
        "lawvm.tools.audit._audit_html_one",
        lambda _sid: SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )

    result = _classify_statute("1990/100", "legal_pit")
    out = capsys.readouterr().out

    assert result is not None
    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out


# ---------------------------------------------------------------------------
# Tests for the FI presentation structural diff detector (used by bench
# _structural_sim to avoid counting Finlex list/table formatting as "err").
# These are synthetic to cover the families that drove high-err / low-lev
# cases in oracle comparison (value tables with dots, chem lists, geo name
# lists, list ordinal prefixes, Liite/amend notes).
# ---------------------------------------------------------------------------


def test_is_presentation_structural_diff_value_table_dots_and_bare_numbers() -> None:
    # From 1994/290 style fee table: unit rows have name + dots + bare value num.
    # Header row has mk without every line having currency.
    sd = {"label": ""}
    events = [
        {"kind": "unit_missing_left", "left_text": None, "right_text": "Vahvistettu päiväpalkka mk/päivä"},
        {"kind": "unit_missing_left", "left_text": None, "right_text": "korkeakoulututkinnon suorittanut henkilö........... 1 260"},
        {"kind": "unit_missing_left", "left_text": None, "right_text": "maanmittausteknikko tai muu vastaava henkilö....... 850"},
        {"kind": "facet_added", "left_text": None, "right_text": "Päiväpalkat työaikakorvauksen laskemista varten toimitukseen tai tehtävään käytetyltä päivältä ovat henkilökuntaryhmittäin:"},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_geo_municipality_name_list() -> None:
    # 1997/746 style: hundreds of municipality names as units under region facets.
    # Plain names (no ; dot mk wrap) but grouped list presentation.
    sd = {"label": ""}
    events = [
        {"kind": "facet_removed", "left_text": "Rannikon metsäkeskus", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Dragsfjärd", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Espoo", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Hanko", "right_text": None},
        {"kind": "wording_text_changed", "left_text": "Metsäkeskuksien toimipaikat ovat seuraavissa kunnissa: Rannikon...", "right_text": None},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_list_item_prefix_only() -> None:
    # 1997/122 style: "6) " prefix only in one side's wording (list label presentation).
    sd = {"label": ""}
    events = [
        {"kind": "wording_text_changed", "left_text": "6) Kaakkois-Suomen työvoima- ja elinkeinokeskus muodostuu Kymenlaakson liiton ja Etelä-Karjalan liiton toimialueista;", "right_text": "Kaakkois-Suomen työvoima- ja elinkeinokeskus muodostuu Kymenlaakson liiton ja Etelä-Karjalan liiton toimialueista;"},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_liite_coord_table_with_amend_note() -> None:
    # 2000/1125 style long coordinate list with "Liite N" and occasional (date/num) amendment notes.
    sd = {"label": ""}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "1. Haapasaaren suoja-alue 60 15,08 27 04,50 Liite 1 ...",
         "right_text": "1. Haapasaaren suoja-alue 60 15,08 27 04,50 Liite 1 (8.12.2011/1214) ..."},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_chem_list_wrapup_and_names() -> None:
    # 1993/1709 style chem convention lists: long IUPAC names as units, Greek, wrapup facet.
    sd = {"label": ""}
    events = [
        {"kind": "facet_removed", "left_text": "tässä luettelossa mainittuja aineita sisältävät valmisteet.", "right_text": None},
        {"kind": "unit_missing_left", "left_text": None, "right_text": "Alfametyylifentanyyli (N-[1-(α-metyylifenetyyli)-4-piperidyyli] -propionianilidi)"},
        {"kind": "unit_missing_left", "left_text": None, "right_text": "Heroiini (diasetyylimorfiini)"},
        {"kind": "wording_text_changed", "left_text": "Vuoden 1961 ... luettelo IV Alfametyyli...", "right_text": "Vuoden 1961 ... luettelo IV Alfa-metyylitio..."},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_item_suffix_vs_wrapup_owner_projection() -> None:
    # 1998/417 § 3 style: same legal text, but Finlex owns the final unnumbered
    # paragraph as loppukappale while replay has already absorbed it into item 2.
    sd = {"label": ""}
    tail = (
        "tämän päätöksen liitteessä mainittujen toimitusajan ja palvelun laadun "
        "mittarien suoritustason osalta soveltaen liitteessä mainittuja "
        "määritelmiä ja mittausmenetelmiä."
    )
    events = [
        {
            "kind": "facet_added",
            "semantic_path": ["section:3", "subsection:1", "wrapUp"],
            "right_badge": "loppukappale",
            "left_text": None,
            "right_text": tail,
        },
        {
            "kind": "wording_text_changed",
            "semantic_path": ["section:3", "subsection:1", "item:2"],
            "left_text": f"ylläpidettävä ajantasaista luetteloa {tail}",
            "right_text": "ylläpidettävä ajantasaista luetteloa",
        },
    ]

    assert is_presentation_structural_diff(sd, events) is True

    changed_events = [
        events[0],
        {
            **events[1],
            "left_text": "ylläpidettävä ajantasaista luetteloa muuta oikeudellista tekstiä.",
        },
    ]
    assert is_presentation_structural_diff(sd, changed_events) is False


def test_is_presentation_structural_diff_wrapup_shifted_subsection_projection() -> None:
    # 2021/487 § 10 style: source/replay owns a post-list penalty tail as the
    # next subsection, while Finlex projects it as wrapUp under the list
    # subsection and shifts the following subsection ordinal up.
    sd = {"label": ""}
    tail = (
        "on tuomittava, jollei teosta muualla laissa säädetä ankarampaa "
        "rangaistusta, luonnontuotteita keräävien ulkomaalaisten "
        "oikeudellisesta asemasta annetun lain rikkomuksesta sakkoon."
    )
    following = (
        "Rangaistusvastuun kohdentumiseen luonnontuotekeruualan toimijan ja "
        "tämän edustajan kesken sovelletaan, mitä rikoslain 47 luvun 7 §:ssä "
        "säädetään."
    )
    events = [
        {
            "kind": "facet_added",
            "unit_kind": "wrapUp",
            "semantic_path": ["section:10", "subsection:1", "wrapUp"],
            "left_text": None,
            "right_text": tail,
            "right_badge": "loppukappale",
        },
        {
            "kind": "wording_text_changed",
            "unit_kind": "subsection",
            "unit_label": "2",
            "semantic_path": ["section:10", "subsection:2"],
            "left_text": tail,
            "right_text": following,
        },
        {
            "kind": "unit_missing_right",
            "unit_kind": "subsection",
            "unit_label": "3",
            "semantic_path": ["section:10", "subsection:3"],
            "left_text": following,
            "right_text": None,
        },
    ]

    assert is_presentation_structural_diff(sd, events) is True

    changed_events = [
        events[0],
        events[1],
        {
            **events[2],
            "left_text": following.replace("rikoslain", "muun lain"),
        },
    ]
    assert is_presentation_structural_diff(sd, changed_events) is False


def test_is_presentation_structural_diff_wrapup_projected_as_last_item_subitem() -> None:
    # 2014/387 § 46 at the 2022/16 snapshot: replay owns the final penalty
    # sentence as subsection wrapUp, while Finlex projects it as item 8's first
    # subitem and item 8's text as an intro.  This is comparison-only and must
    # require exact conservation of both texts.
    sd = {"label": ""}
    item_text = "laiminlyö 38 §:n mukaisen avunantovelvollisuutensa,"
    penalty = (
        "on tuomittava, jollei teosta muualla laissa säädetä ankarampaa "
        "rangaistusta, eläinten lääkitsemisrikkomuksesta sakkoon."
    )
    events = [
        {
            "kind": "facet_removed",
            "unit_kind": "wrapUp",
            "facet_kind": "wrapUp",
            "semantic_path": ["section:46", "subsection:1", "wrapUp"],
            "left_text": penalty,
            "left_badge": "loppukappale",
        },
        {
            "kind": "facet_added",
            "unit_kind": "intro",
            "facet_kind": "intro",
            "semantic_path": ["section:46", "subsection:1", "item:8", "intro"],
            "right_text": item_text,
            "right_badge": "johdanto",
        },
        {
            "kind": "wording_text_changed",
            "unit_kind": "item",
            "unit_label": "8",
            "semantic_path": ["section:46", "subsection:1", "item:8"],
            "left_text": item_text,
            "left_badge": "8 kohta",
            "right_badge": "8 kohta",
        },
        {
            "kind": "unit_missing_left",
            "unit_kind": "subitem",
            "unit_label": "1",
            "semantic_path": ["section:46", "subsection:1", "item:8", "subitem:1"],
            "right_text": penalty,
            "right_badge": "1 alakohta",
        },
    ]

    assert is_presentation_structural_diff(sd, events) is True

    changed_events = [
        *events[:-1],
        {
            **events[-1],
            "right_text": penalty.replace("sakkoon", "vankeuteen"),
        },
    ]
    assert is_presentation_structural_diff(sd, changed_events) is False


def test_is_presentation_structural_diff_value_table_subsections_vs_items() -> None:
    # 2020/82 § 4 style: source/replay owns two unlabeled table blocks as
    # subsection siblings, while Finlex projects the same blocks as list items.
    sd = {"label": ""}
    intro = (
        "Sovellettaessa verontilityslain 5 a §:ssä tarkoitettua takuutilitystä "
        "verovuodelta 2020 suoritettavissa mainitun lain 5 §:n mukaisissa "
        "tilityksissä käytetään seuraavia työnantajasuoritusten vähimmäismääriä:"
    )
    table_a = (
        "Ennakonpidätysten vähimmäismäärä Vuosi Tilityskuukausi Euroa "
        "2020 Toukokuu 2 347 000 000 Kesäkuu 2 212 000 000 "
        "2021 Tammikuu 2 702 000 000"
    )
    table_b = (
        "Työnantajan sairausvakuutusmaksun vähimmäismäärä Vuosi "
        "Tilityskuukausi Euroa 2020 Toukokuu 101 210 000 Kesäkuu "
        "99 200 000 2021 Tammikuu 96 880 000"
    )
    events = [
        {
            "kind": "facet_added",
            "semantic_path": ["section:4", "subsection:1", "intro"],
            "right_badge": "johdanto",
            "left_text": None,
            "right_text": intro,
        },
        {
            "kind": "wording_text_changed",
            "semantic_path": ["section:4", "subsection:1"],
            "left_text": intro,
            "right_text": None,
        },
        {"kind": "unit_missing_left", "left_text": None, "right_text": table_a},
        {"kind": "unit_missing_left", "left_text": None, "right_text": table_b},
        {"kind": "unit_missing_right", "left_text": table_a, "right_text": None},
        {"kind": "unit_missing_right", "left_text": table_b, "right_text": None},
    ]

    assert is_presentation_structural_diff(sd, events) is True

    changed_events = [
        *events[:-1],
        {"kind": "unit_missing_right", "left_text": table_b.replace("96 880 000", "96 999 000"), "right_text": None},
    ]
    assert is_presentation_structural_diff(sd, changed_events) is False


def test_is_presentation_structural_diff_intro_vs_wording_owner_projection() -> None:
    # 1980/552 § 1 style: identical text is an intro facet on one side and
    # plain subsection wording on the other.
    sd = {"label": ""}
    text = (
        "Kauppahintarekisterin ja siitä annettavan tietopalvelun tarkoituksena "
        "on palvella kiinteistön arvon määrittämistä lunastustoimituksissa."
    )
    events = [
        {
            "kind": "facet_removed",
            "semantic_path": ["section:1", "subsection:2", "intro"],
            "left_badge": "johdanto",
            "left_text": text,
            "right_text": None,
        },
        {
            "kind": "wording_text_changed",
            "semantic_path": ["section:1", "subsection:2"],
            "left_text": None,
            "right_text": text,
        },
    ]

    assert is_presentation_structural_diff(sd, events) is True

    changed_events = [
        events[0],
        {**events[1], "right_text": text.replace("lunastustoimituksissa", "verotuksessa")},
    ]
    assert is_presentation_structural_diff(sd, changed_events) is False


def test_is_presentation_structural_diff_lettered_subitems_flattened_to_items() -> None:
    # 1993/91 § 4 style: source/replay nests lettered subitems under item 3,
    # while Finlex projects the same rows as sibling lettered items.
    sd = {"label": ""}
    intro = (
        "muuta omaisuusvahingon, varallisuusvahingon tai "
        "vahingonkorvausvastuun varalle otettua vakuutusta, jos "
        "vakuutuksenottaja täyttää ainakin kaksi seuraavasta kolmesta "
        "tunnusmerkistä:"
    )
    row_a = "vakuutuksenottajan taseen loppusumma on yli 37,2 miljoonaa markkaa;"
    row_b = "vakuutuksenottajan liikevaihto on yli 76,8 miljoonaa markkaa;"
    events = [
        {
            "kind": "facet_removed",
            "facet_kind": "intro",
            "unit_kind": "intro",
            "semantic_path": ["section:4", "subsection:1", "item:3", "intro"],
            "left_text": intro,
            "right_text": None,
        },
        {
            "kind": "wording_text_changed",
            "unit_kind": "item",
            "unit_label": "3",
            "semantic_path": ["section:4", "subsection:1", "item:3"],
            "left_text": None,
            "right_text": intro,
        },
        {
            "kind": "unit_missing_right",
            "unit_kind": "subitem",
            "unit_label": "a",
            "left_text": row_a,
            "right_text": None,
        },
        {
            "kind": "unit_missing_right",
            "unit_kind": "subitem",
            "unit_label": "b",
            "left_text": row_b,
            "right_text": None,
        },
        {
            "kind": "unit_missing_left",
            "unit_kind": "item",
            "unit_label": "a",
            "left_text": None,
            "right_text": row_a,
        },
        {
            "kind": "unit_missing_left",
            "unit_kind": "item",
            "unit_label": "b",
            "left_text": None,
            "right_text": row_b,
        },
    ]

    assert is_presentation_structural_diff(sd, events) is True

    changed_row_a = "alkuperäinen oikeudellinen vaatimus on täytettävä;"
    changed_row_b = "muutettu oikeudellinen vaatimus on täytettävä;"
    changed_events = [
        {
            **events[0],
            "left_text": "seuraavien tunnusmerkkien perusteella:",
        },
        {
            **events[1],
            "right_text": "seuraavien tunnusmerkkien perusteella:",
        },
        {
            **events[2],
            "left_text": changed_row_a,
        },
        {
            **events[4],
            "right_text": changed_row_b,
        },
    ]
    assert is_presentation_structural_diff(sd, changed_events) is False


def test_is_presentation_structural_diff_negative_real_content_change() -> None:
    # A real wording change (not prefix, not artifact) must not be treated as presentation.
    sd = {"label": ""}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "Tämä laki tulee voimaan 1 päivänä toukokuuta 1994.",
         "right_text": "Tämä laki tulee voimaan 15 päivänä kesäkuuta 1995."},
    ]
    assert is_presentation_structural_diff(sd, events) is False

    # Mixed: a non-pres unit among pres units should fail (conservative).
    # Use Finnish prose containing a verb like "on" so the FI-only guard works cleanly.
    # Do not mix English test prose into the production normalization heuristics.
    sd2 = {"label": ""}
    events2 = [
        {"kind": "unit_missing_left", "left_text": None, "right_text": "kunta........... 123"},
        {"kind": "unit_missing_left", "left_text": None, "right_text": "Tämä on todellinen oikeudellinen vaatimus jostakin asiasta."},
    ]
    assert is_presentation_structural_diff(sd2, events2) is False


def test_is_presentation_structural_diff_geo_list_with_mlk_and_group_labels() -> None:
    # 1997/746 style remaining: region "Foo metsäkeskus" facets + town units
    # including "Mikkelin mlk.", "Loimaan kunta" etc. + one-sided wording for concat list.
    sd = {"label": ""}
    events = [
        {"kind": "facet_removed", "left_text": "Rannikon metsäkeskus", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Mikkelin mlk.", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Loimaan kunta", "right_text": None},
        {"kind": "wording_text_changed", "left_text": None, "right_text": "1. Rannikon metsäkeskus"},
        {"kind": "unit_missing_right", "left_text": "Dragsfjärd", "right_text": None},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_office_schedule_grouped_vs_split_rows() -> None:
    # 1998/132 style: LawVM preserves individual office rows while Finlex groups
    # them under hovioikeuspiiri rows. The text stream is the same schedule; the
    # structural mismatch is presentation, not replay authority.
    sd = {"label": ""}
    events = [
        {
            "kind": "wording_text_changed",
            "left_text": "Turun hovioikeuspiiri",
            "right_text": (
                "Turun hovioikeuspiiri Ahvenanmaa (Maarianhaminassa) Forssa "
                "Hämeenlinna Kankaanpää Ikaalinen (st) Kokemäki Loimaa"
            ),
        },
        {
            "kind": "wording_text_changed",
            "left_text": "Forssa",
            "right_text": (
                "Itä-Suomen hovioikeuspiiri Iisalmi Joensuu Joensuunseutu "
                "(Joensuussa) Ilomantsi (st) Kitee Kajaani"
            ),
        },
        {"kind": "unit_missing_right", "left_text": "Loimaa", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Tampereenseutu (Tampereella)", "right_text": None},
        {"kind": "unit_missing_right", "left_text": "Kannus (st)", "right_text": None},
    ]
    assert is_presentation_structural_diff(sd, events) is True


def test_is_presentation_structural_diff_minor_admin_rephrasing_and_table_header() -> None:
    # 2012/960 style: wording diffs on standard FI delegation boilerplate ("säädetään valtioneuvoston asetuksella",
    # "tarkempia säännöksiä") that the pres normalizer equalizes. Low lev overall.
    sd = {"label": ""}
    events = [
        {"kind": "wording_text_changed",
         "left_text": "Valtioneuvoston asetuksella säädetään asetuksesta. Valtioneuvoston asetuksella voidaan antaa tarkempia säännöksiä bar baz.",
         "right_text": "Valtioneuvoston asetuksella säädetään asetuksesta. bar baz."},
    ]
    assert is_presentation_structural_diff(sd, events) is True

    # table header rephrase (pinta-ala schedule) — value table look skips counting the diff.
    sd2 = {"label": ""}
    events2 = [
        {"kind": "wording_text_changed",
         "left_text": "Pinta-alakorvaus ... ha mk mk 0,5 3 000 ...",
         "right_text": "Pinta-alakorvaus ... ha Pinta-alakorvaus mk Ilman ... 0,5 3 000 ..."},
    ]
    assert is_presentation_structural_diff(sd2, events2) is True


def test_strip_editorial_note_containers_catches_block_form_noteAuthorial_in_oracle() -> None:
    """Oracle XML can represent authorial notes as <block name="noteAuthorial" outline="huomautus">
    (not only hcontainer). These must be stripped *first* in oracle tree/text paths
    used for comparison so their editorial content ("kumoutunut", "oikaistu" etc) does
    not leak into section text, lev, or semantic events as if it were law.
    """
    from lxml import etree
    from lawvm.finland.corpus import _strip_editorial_note_containers

    xml = '''<body xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
                   xmlns:finlex="http://data.finlex.fi/schema/finlex">
      <section eId="sec_1"><num>1 §</num><content><p>Real law text here.</p></content></section>
      <block eId="note_1" finlex:outline="huomautus" name="noteAuthorial">
        Tämä päätös on kumoutunut 1.1.2009 alkaen. Ks. L 123/2008.
      </block>
      <block name="noteAuthorial"><p>Another note.</p></block>
    </body>'''
    root = etree.fromstring(xml.encode("utf-8"))
    xpath_result = root.xpath('.//*[local-name()="block" and @name="noteAuthorial"]')
    assert isinstance(xpath_result, list)
    assert len(xpath_result) == 2
    _strip_editorial_note_containers(root)
    remaining = root.xpath('.//*[local-name()="block" and @name="noteAuthorial"]')
    assert isinstance(remaining, list)
    assert len(remaining) == 0
    # Real content survives
    assert "Real law text here" in etree.tostring(root, method="text", encoding="unicode")
    # Note text gone
    full_text = etree.tostring(root, method="text", encoding="unicode")
    assert "kumoutunut" not in full_text
