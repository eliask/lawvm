from __future__ import annotations

import copy

import lxml.etree as etree
import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.ir import IRStatute
from lawvm.core.ir import LegalAddress
from lawvm.core.ir import OperationSource
from lawvm.core.ir import ProvisionTimeline
from lawvm.core.ir import ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.ir import LegalOperation
from lawvm.core.provenance import MigrationEvent
from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.finland.apply import apply_op
from lawvm.finland.frontend_compile import normalize_and_compile_ops
from lawvm.finland.grafter import compile_amendment_ops, get_corpus, get_johtolause
from lawvm.core.timeline import compile_timelines
from lawvm.core.timeline import materialize_pit_ex
from lawvm.core.timeline import select_active_version
from lawvm.core.timeline import materialize_pit
from lawvm.core.timeline_results import MaterializationLineagePlan
from lawvm.finland.replay_products import ReplayProducts
from lawvm.finland.replay_products import FinlandLineageBridgeClassification
from lawvm.finland.replay_products import _FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE
from lawvm.finland.replay_products import _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR
from lawvm.finland.replay_products import _cleanup_sourceless_base_merge_conflicts
from lawvm.finland.replay_products import _rekey_timelines_with_migration_events
from lawvm.finland.replay_products import _classify_finland_lineage_bridge
from lawvm.finland.replay_products import _select_pit_lineage_inputs
from lawvm.finland.replay_products import _temporal_events_from_lo_ops
from lawvm.finland.replay_products import build_replay_products
from lawvm.finland.replay_products import validate_replay_products
from lawvm.core.timeline_addresses import _retarget_root_node
from lawvm.tools.inspect_amendment import build_amendment_bundle
from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.statute import ReplayState, StatuteContext


def test_replay_xml_exposes_typed_replay_products() -> None:
    replay = pinned_replay("2009/953", mode="legal_pit")

    assert replay.products.replay_fold_state is replay.replay_fold_state
    assert replay.products.materialized_state is replay.materialized_state
    assert replay.products.timelines is replay.timelines
    assert replay.materialization_spec is not None
    assert replay.source_adjudication is not None
    assert replay.materialization_spec.as_of == replay.source_adjudication.cutoff_date
    assert replay.materialization_spec.lineage_mode in {
        "rekeyed_with_migrations",
        "rekeyed_only",
        "raw_with_migrations",
    }
    assert replay.materialization_spec.lineage_plan.mode == replay.materialization_spec.lineage_mode
    assert replay.materialization_spec.lineage_reason in {
        "default_migration_projection",
        "native_rebirth_after_renumber",
        "leaf_stable_scope_renumber",
        "destination_occupancy_collision",
        "scope_changing_migration_fallback",
    }
    assert isinstance(
        replay.materialization_spec.bridge_classification,
        FinlandLineageBridgeClassification,
    )
    assert replay.source_adjudication.statute_id == "2009/953"


def test_replay_xml_2016_258_section_3_matches_oracle_version_anchor() -> None:
    """finlex_oracle should anchor 2016/258 to the oracle-version amendment date.

    Oracle version ``fin@20211199`` is keyed by amendment ``2021/1199``, whose
    own entry-into-force date is ``2021-12-31``. Once that effective date is
    honored correctly, ``finlex_oracle`` materialization for 2016/258 is
    anchored to ``2021-12-31`` rather than the earlier consolidated date. On
    that anchored date the temporary second subsection of 3 § from 2019/1458
    has already expired, so only the first moment remains visible.
    """
    replay = pinned_replay("2016/258", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("3")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2021-12-31"

    assert text.count("Valtion maksuperustelain 7 §:n 2 momentissa") == 1
    assert text.count("Valtion maksuperustelain 6 §:n 1 momentissa") == 0


def test_replay_xml_2022_213_keeps_future_repeal_at_oracle_cutoff() -> None:
    replay = pinned_replay("2022/213", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("3")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2026-01-16"
    assert "kuluttajansuojalain (38/1978) 6 a luvun 11 §:n 2 momenttia;" in text
    assert "3) 4)" not in text


def test_replay_xml_2021_616_applies_corrigendum_without_collapsing_spacing() -> None:
    replay = pinned_replay("2021/616", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_node("section", "69", "chapter", "8")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert "Lain 2 § tulee kuitenkin voimaan vasta 1 päivänä tammikuuta 2023." in text
    assert "voimaavasta" not in text
    assert "voimaanvasta" not in text


def test_replay_xml_1973_36_materializes_live_missing_sections() -> None:
    """1973/36 must retain the live Finland bug-family sections end to end."""
    replay = pinned_replay(
        "1973/36",
        mode="finlex_oracle",
        quiet=True,
    )

    def _section_text(chapter_label: str, section_label: str) -> str:
        section = replay.materialized_state.find_node("section", section_label, "chapter", chapter_label)
        assert section is not None, f"missing chapter {chapter_label} / section {section_label}"
        return " ".join(irnode_to_text(section).split())

    assert _section_text("3", "15").startswith("15 § Yksityisellä lasten päivähoidolla tarkoitetaan")
    assert _section_text("3", "16").startswith("16 § Jollei tässä laissa muuta säädetä")
    assert _section_text("3", "17").startswith("17 § Yksityisen lasten päivähoidon osalta")
    assert _section_text("3", "18").startswith("18 § Sosiaali- ja terveysalan lupa- ja valvontavirasto")
    assert _section_text("4", "27").startswith("27 § Kunnan on huolehdittava siitä")
    assert _section_text("4", "32").startswith("32 § Hallinto-oikeuden päätökseen ei saa hakea muutosta")


def test_replay_xml_1987_1203_preserves_jolloin_section_renumber_chain() -> None:
    replay = pinned_replay("1987/1203", mode="finlex_oracle", quiet=True)

    section_11 = replay.materialized_state.find_section("11")
    section_12 = replay.materialized_state.find_section("12")

    assert section_11 is not None
    assert section_12 is not None

    text_11 = " ".join(irnode_to_text(section_11).split())
    text_12 = " ".join(irnode_to_text(section_12).split())

    assert text_11.startswith("11 § Tarkemmat määräykset")
    assert "valtiovarainministeriö" in text_11
    assert text_12.startswith("12 § Voimaantulo")
    assert "Tämä asetus tulee voimaan 1 päivänä tammikuuta 1988." in text_12


def test_replay_xml_1968_360_handles_temporary_tax_year_window_without_crashing() -> None:
    replay = pinned_replay("1968/360", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("46b")
    assert section is not None
    text = " ".join(irnode_to_text(section).split()).lower()
    assert "vuodelta 1982 toimitettavassa verotuksessa" in text
    assert "vuodelta 1983 toimitettavassa verotuksessa" in text


def test_replay_xml_1987_322_repeals_sections_10a_to_10f_after_2023_741() -> None:
    replay = pinned_replay("1987/322", mode="finlex_oracle", quiet=True)
    for label in ("10a", "10b", "10c", "10d", "10e", "10f"):
        section = replay.materialized_state.find_section(label)
        assert section is not None
        assert section.attrs.get("lawvm_repeal_placeholder") == "1"


def test_replay_xml_1992_772_applies_1994_1281_replace_to_section_6() -> None:
    replay = pinned_replay("1992/772", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("6")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("6 § Terveyshaitan arvioimiseksi tarvittavat lisätiedot")
    assert "terveydensuojelulain (763/94)" in text
    assert "terveydensuojeluasetuksen (1280/94)" in text
    assert "terveydenhoitolain (469/65)" not in text


def test_build_amendment_bundle_2002_1000_does_not_collapse_dotted_kohta_repeal_to_section_repeal() -> None:
    bundle = build_amendment_bundle("2002/1000", "2007/180", "finlex_oracle")
    all_ops = [op for group in bundle["groups"] for op in group["ops_final"]]
    assert "REPEAL 1 §" not in all_ops


def test_replay_xml_2002_1000_keeps_section_1_after_dotted_kohta_repeal_clause() -> None:
    replay = pinned_replay("2002/1000", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("1")
    assert section is not None
    assert section.attrs.get("lawvm_repeal_placeholder") != "1"


def test_build_amendment_bundle_1992_552_keeps_heading_and_subsection_scope_separate() -> None:
    bundle = build_amendment_bundle("1992/552", "2016/784", "finlex_oracle")
    group8 = next(group for group in bundle["groups"] if group["target_norm"] == "8")

    # The johtolause says "8 §:n otsikko ja 3 momentti" — both the heading and
    # the subsection appear as separate ops at the raw stage.
    assert group8["ops_raw"] == ["REPEAL 8 § 1 mom", "REPLACE 8 § otsikko", "REPLACE 8 § 3 mom"]
    assert "REPLACE 8 §" not in group8["ops_final"]
    assert "REPLACE 8 § otsikko" in group8["ops_final"]


def test_replay_xml_1992_552_preserves_section_8_subsection_4() -> None:
    replay = pinned_replay("1992/552", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("8")
    assert section is not None
    subsection_labels = [child.label for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert "4" in subsection_labels


def test_replay_xml_1992_552_updates_section_8_subsection_3_intro_text() -> None:
    replay = pinned_replay("1992/552", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("8")
    assert section is not None
    subsection = next(
        child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "3"
    )
    text = " ".join(irnode_to_text(subsection).split())

    assert text.startswith("Vero kohdistetaan sille kalenterikuukaudelle:")
    assert "Kalenterikuukaudeksi, jolta vero suoritetaan" not in text


def test_build_amendment_bundle_2000_755_rebinds_cited_version_owned_section_paths() -> None:
    bundle = build_amendment_bundle("2000/755", "2018/945", "finlex_oracle")
    all_ops = [op for group in bundle["groups"] for op in group["ops_final"]]

    assert "REPLACE 6 luku 23 §" in all_ops
    assert "REPLACE 6 luku 24c §" in all_ops
    assert "REPLACE 6 luku 30b §" in all_ops
    assert "REPLACE 3 luku 34a §" in all_ops
    assert "REPLACE 30b §" not in all_ops


def test_replay_xml_2000_755_applies_2018_945_to_cited_pending_version_paths() -> None:
    replay = pinned_replay("2000/755", mode="legal_pit", quiet=True, as_of="2020-01-02")

    sec24c = replay.materialized_state.find_node("section", "24c", "chapter", "6")
    sec30b = replay.materialized_state.find_node("section", "30b", "chapter", "6")
    sec34a = replay.materialized_state.find_node("section", "34a", "chapter", "3")

    assert sec24c is not None
    assert sec30b is not None
    assert sec34a is not None

    text24c = " ".join(irnode_to_text(sec24c).split())
    text30b = " ".join(irnode_to_text(sec30b).split())
    text34a = " ".join(irnode_to_text(sec34a).split())

    assert "Liikenne- ja viestintäviraston" in text24c
    assert "Liikenne- ja viestintäviraston" in text30b
    assert "Liikenne- ja viestintäviraston" in text34a
    assert "Liikenneviraston" not in text24c
    assert "Liikenneviraston" not in text30b
    assert "Liikenneviraston" not in text34a

    timeline_24c = replay.timelines[LegalAddress(path=(("chapter", "6"), ("section", "24c")))]
    timeline_30b = replay.timelines[LegalAddress(path=(("chapter", "6"), ("section", "30b")))]
    timeline_34a = replay.timelines[LegalAddress(path=(("chapter", "3"), ("section", "34a")))]

    assert any(
        version.source is not None
        and version.source.statute_id == "2018/945"
        and version.effective == "2019-01-01"
        for version in timeline_24c.versions
    )
    assert any(
        version.source is not None
        and version.source.statute_id == "2018/945"
        and version.effective == "2019-01-01"
        for version in timeline_30b.versions
    )
    assert any(
        version.source is not None
        and version.source.statute_id == "2018/945"
        and version.effective == "2019-01-01"
        for version in timeline_34a.versions
    )

    root_30b = replay.timelines.get(LegalAddress(path=(("section", "30b"),)))
    assert root_30b is None or select_active_version(root_30b, "2020-01-02") is None


def test_replay_xml_2011_1552_composes_pending_amendment_children() -> None:
    replay = pinned_replay("2011/1552", mode="finlex_oracle", quiet=True)

    sec88 = replay.materialized_state.find_section("88")
    sec109 = replay.materialized_state.find_section("109")
    sec126 = replay.materialized_state.find_section("126")

    assert sec88 is not None
    assert sec109 is not None
    assert sec126 is not None

    text88 = " ".join(irnode_to_text(sec88).split())
    text109 = " ".join(irnode_to_text(sec109).split())
    text126 = " ".join(irnode_to_text(sec126).split())

    assert "3 §:n 1 ja 4–6 kohdassa" in text88
    assert "(1301/2014)" not in text88
    assert "mukaiseen" in text88
    assert "1, 2 ja 4–6 kohdassa" in text109
    assert "Rajavartiolaitoksen" in text126


def test_replay_xml_2014_938_composes_pending_amendment_children() -> None:
    replay = pinned_replay("2014/938", mode="finlex_oracle", quiet=True)

    sec29 = replay.materialized_state.find_section("29")
    sec41 = replay.materialized_state.find_section("41")

    assert sec29 is not None
    assert sec41 is not None

    text29 = " ".join(irnode_to_text(sec29).split())
    text41 = " ".join(irnode_to_text(sec41).split())

    assert "8 tai 8 a §:n mukaan" in text29
    assert "tai hän on tullut oikeutetuksi" in text41
    assert "8 §:n 2 momentin tai 8 a §:n" in text41


def test_replay_xml_2014_938_keeps_permanent_section_25_change_after_temporary_51_expires() -> None:
    replay = pinned_replay("2014/938", mode="finlex_oracle", quiet=True)

    sec25 = replay.materialized_state.find_section("25")
    sec51 = replay.materialized_state.find_section("51")

    assert sec25 is not None
    assert sec51 is not None

    text25 = " ".join(irnode_to_text(sec25).split())
    text51 = " ".join(irnode_to_text(sec51).split())

    assert "kokonaan tai osittain" in text25
    assert "vuonna 2023 hyväksytään" not in text51


def test_replay_xml_1940_378_keeps_voimaantulo_section_under_chapter_7_after_1994_318() -> None:
    replay = pinned_replay("1940/378", mode="finlex_oracle", quiet=True)

    moved = replay.materialized_state.find_node("section", "61", "chapter", "7")
    assert moved is not None
    moved_text = " ".join(irnode_to_text(moved).split())
    assert moved_text.startswith("61 § Tämä laki tulee voimaan 1 päivänä elokuuta 1940")
    assert replay.materialized_state.find_section("73") is None


def test_replay_xml_1929_234_materializes_part_v_after_2001_1226() -> None:
    replay = pinned_replay("1929/234", mode="finlex_oracle", quiet=True)

    sec109 = replay.materialized_state.find_section("109")
    sec142 = replay.materialized_state.find_section("142")

    assert sec109 is not None
    assert sec142 is not None
    assert "Suomen viranomainen voi myöntää 9 §:ssä tarkoitetun luvan" in " ".join(irnode_to_text(sec109).split())
    assert "Tarkemmat säännökset tämän osan täytäntöönpanosta" in " ".join(irnode_to_text(sec142).split())


def test_replay_xml_1994_674_keeps_section_1_under_inserted_chapter_11a() -> None:
    replay = pinned_replay("1994/674", mode="finlex_oracle", quiet=True)

    inserted = replay.materialized_state.find_node("section", "1", "chapter", "11a")
    assert inserted is not None
    inserted_text = " ".join(irnode_to_text(inserted).split())
    assert inserted_text.startswith("1 § Nairobin yleissopimuksen soveltaminen Suomessa")


def test_replay_xml_1994_674_replaces_section_6_without_stale_subsection_tail() -> None:
    replay = pinned_replay("1994/674", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_node("section", "6", "chapter", "16")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1"]

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("6 § Pelastuspalkkion suuruuden määrääminen")
    assert "Sama koskee muulla tavalla tehtyä sopimusta" not in text


def test_replay_xml_1994_674_replaces_section_1_without_stale_subsection_tail() -> None:
    replay = pinned_replay("1994/674", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_node("section", "1", "chapter", "16")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1"]

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("1 § Määritelmät")
    assert "Sillä, joka vastoin aluksen päällikön nimenomaista ja oikeutettua kieltoa" not in text
    assert "Pelastuspalkkiota on vaadittaessa suoritettava myös silloin" not in text


def test_replay_xml_1994_674_repeals_section_6_1_second_subsection_without_resurrection() -> None:
    replay = pinned_replay("1994/674", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_node("section", "1", "chapter", "6")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [child.label for child in subsections] == ["1", "2"]
    assert subsections[1].attrs.get("lawvm_repeal_placeholder") == "1"

    text = " ".join(irnode_to_text(section).split())
    assert text.startswith("1 § Päällikön kansalaisuus")
    assert "Päällikön ja muiden päällystöön kuuluvien muista kelpoisuusvaatimuksista säädetään asetuksella." not in text


def test_replay_xml_1965_40_keeps_sections_12a_and_1a_in_their_explicit_insert_chapters() -> None:
    replay = pinned_replay("1965/40", mode="finlex_oracle", quiet=True)

    sec12a = replay.materialized_state.find_node("section", "12a", "chapter", "19")
    sec1a = replay.materialized_state.find_node("section", "1a", "chapter", "25")

    assert sec12a is not None
    assert sec1a is not None
    assert "Jos on syytä epäillä, että pesän varat eivät riitä" in " ".join(irnode_to_text(sec12a).split())
    assert "Tässä luvussa tarkoitetaan:" in " ".join(irnode_to_text(sec1a).split())


def test_replay_xml_1965_40_materializes_sections_20_21_22_under_chapter_19() -> None:
    replay = pinned_replay("1965/40", mode="finlex_oracle", quiet=True)

    sec20 = replay.materialized_state.find_node("section", "20", "chapter", "19")
    sec21 = replay.materialized_state.find_node("section", "21", "chapter", "19")
    sec22 = replay.materialized_state.find_node("section", "22", "chapter", "19")

    assert sec20 is not None
    assert sec21 is not None
    assert sec22 is not None
    assert "Pesänselvittäjällä on oikeus saada pesän varoista" in " ".join(irnode_to_text(sec20).split())
    assert "Testamentin toimeenpanijalla on" in " ".join(irnode_to_text(sec21).split())
    assert "Oikeuden tai tuomarin päätökseen" in " ".join(irnode_to_text(sec22).split())


def test_replay_xml_nests_mixed_single_and_compound_letters_for_1997_1339_section_1() -> None:
    """Regression: the 2015/1752 amendment must not flatten repeated paragraph labels."""
    replay = pinned_replay("1997/1339", mode="finlex_oracle", quiet=True, build_full_products=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        subsection = state.find_node("subsection", "1", "section", "1")
        assert subsection is not None

        paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == [
            "1.",
            "2.",
            "3.",
            "4.",
            "5.",
            "6.",
            "7.",
            "8.",
            "9.",
            "10.",
            "11.",
            "12.",
            "13.",
            "14.",
            "15.",
        ]

        para5 = next(p for p in paragraphs if p.label == "5.")
        assert [sp.label for sp in para5.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "aa", "ab", "b"]

        para6 = next(p for p in paragraphs if p.label == "6.")
        assert [sp.label for sp in para6.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "ba", "bb"]

        para10 = next(p for p in paragraphs if p.label == "10.")
        assert [sp.label for sp in para10.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d", "e", "f", "g"]

        para12 = next(p for p in paragraphs if p.label == "12.")
        assert [sp.label for sp in para12.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c"]


def test_replay_xml_keeps_inserted_moments_separate_for_2005_452_section_6() -> None:
    """Regression: 2012/317 inserts 6 § moments 2 and 3, not item 2 inside moment 1."""
    replay = pinned_replay("2005/452", mode="finlex_oracle", quiet=True)

    section = replay.replay_fold_state.find_section("6", "2")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in subsections] == ["1", "2", "3"]

    second = next(sub for sub in subsections if sub.label == "2")
    second_text = " ".join(irnode_to_text(second).split())
    assert "Tiivistelmässä annettavia keskeisiä tietoja ovat esimerkiksi:" in second_text
    assert "lyhyt kuvaus arvopaperiin tehtävän sijoituksen" in second_text or "lyhyt kuvaus kyseiseen arvopaperiin tehtävän sijoituksen" in second_text


def test_replay_xml_1967_550_section_8_preserves_subsection_1_repeal_in_export() -> None:
    oracle_lo_ops: list[LegalOperation] = []
    legal_lo_ops: list[LegalOperation] = []
    oracle = pinned_replay("1967/550", mode="finlex_oracle", quiet=True, lo_ops_out=oracle_lo_ops)
    legal = pinned_replay("1967/550", mode="legal_pit", quiet=True, lo_ops_out=legal_lo_ops)

    assert any(
        op.action is StructuralAction.REPEAL
        and op.target.path == (("chapter", "2"), ("section", "8"), ("subsection", "1"))
        for op in legal_lo_ops
    )
    assert any(
        op.target.path == (("chapter", "2"), ("section", "8"), ("subsection", "1"))
        and (
            op.action is StructuralAction.REPEAL
            or (op.payload is not None and op.payload.attrs.get("lawvm_repeal_placeholder") == "1")
        )
        for op in oracle_lo_ops
    )

    oracle_section = oracle.materialized_state.find_section("8", "2")
    assert oracle_section is not None
    oracle_subsections = [child for child in oracle_section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in oracle_subsections] == ["1", "2", "3", "4", "5", "6", "7"]
    assert oracle_subsections[0].attrs.get("lawvm_repeal_placeholder") == "1"

    legal_section = legal.materialized_state.find_section("8", "2")
    assert legal_section is not None
    legal_subsections = [child for child in legal_section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in legal_subsections] == ["2", "3", "4", "5", "6", "7"]


def test_replay_xml_1967_550_section_8_keeps_distinct_sparse_tail_moments() -> None:
    replay = pinned_replay("1967/550", mode="legal_pit", quiet=True)

    section = replay.materialized_state.find_section("8", "2")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    sixth = next(sub for sub in subsections if sub.label == "6")
    seventh = next(sub for sub in subsections if sub.label == "7")

    sixth_text = " ".join(irnode_to_text(sixth).split())
    seventh_text = " ".join(irnode_to_text(seventh).split())

    assert "Hakijan on suoritettava vahvistettu hakemusmaksu." in sixth_text
    assert "Hakemuksesta on myös suoritettava vahvistettu vuosimaksu" in sixth_text
    assert "Maksuvuosi lasketaan ensimmäisen kerran siitä päivästä" in seventh_text
    assert sixth_text != seventh_text


def test_replay_xml_1966_657_section_3_keeps_distinct_tail_moments() -> None:
    replay = pinned_replay("1966/657", mode="legal_pit", quiet=True)

    section = replay.materialized_state.find_section("3")
    assert section is not None

    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
    assert [sub.label for sub in subsections] == ["1", "2", "3", "4", "5"]

    third = next(sub for sub in subsections if sub.label == "3")
    fourth = next(sub for sub in subsections if sub.label == "4")
    fifth = next(sub for sub in subsections if sub.label == "5")

    third_text = " ".join(irnode_to_text(third).split())
    fourth_text = " ".join(irnode_to_text(fourth).split())
    fifth_text = " ".join(irnode_to_text(fifth).split())

    assert "rahoituskaudella 2023–2027" in third_text
    assert "rahoituskaudella 2023–2027" not in fourth_text
    assert "maidon viitemäärien ostamiseen" in fourth_text
    assert "Valtioneuvosto voi vastikkeetta luovuttaa" in fifth_text
    assert third_text != fourth_text


def test_replay_xml_recovers_1935_419_full_section_replace_for_1922_312_section_8() -> None:
    """Authority-citation lead-ins must not collapse 1935/419 to a fake 6 § replace."""
    replay = pinned_replay("1922/312", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("8")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())
    subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]

    assert "kutsuntatoimiston sihteeriltä" in text
    assert "kutsuntatoimiston piiripäälliköltä" not in text
    assert "sekä alipäällystöltä" in text
    assert "Pääsemistä varten alipäällystön toimeen" not in text
    assert [sub.label for sub in subsections] == ["1", "2", "3"]

    third = next(sub for sub in subsections if sub.label == "3")
    third_text = " ".join(irnode_to_text(third).split())
    assert "sekä alipäällystöltä" in third_text


def test_replay_xml_preserves_native_same_label_section_after_1958_496_renumber() -> None:
    """1999/1249 must preserve both the migrated 5 c § and the new native 5 b §."""
    replay = pinned_replay("1958/496", mode="legal_pit", quiet=True, stop_before="2004/697")

    section_5b = replay.materialized_state.find_section("5b")
    section_5c = replay.materialized_state.find_section("5c")

    assert section_5b is not None
    assert section_5c is not None

    text_5b = " ".join(irnode_to_text(section_5b).split())
    text_5c = " ".join(irnode_to_text(section_5c).split())

    assert "EU-luettelo" in text_5b
    assert "salassapitovelvollisuuden rikkomisesta" not in text_5b
    assert "salassapitovelvollisuuden rikkomisesta" in text_5c
    assert "EU-luettelo" not in text_5c


def test_replay_xml_keeps_1994_1486_uncovered_sections_under_part_scoped_chapter() -> None:
    """Uncovered-body recovery must not emit bare root chapter:5 extras for 1994/1486."""
    replay = pinned_replay("1993/1501", mode="legal_pit", quiet=True, stop_before="1995/347")

    root_level_extras = {
        str(addr)
        for addr in replay.timelines
        if str(addr) in {
            "chapter:5/section:70",
            "chapter:5/section:74",
            "chapter:5/section:75",
            "chapter:5/section:76",
            "chapter:5/section:78",
            "chapter:5/section:79",
            "chapter:5/section:83",
            "chapter:5/section:88",
        }
    }
    part_scoped_recovered = {
        str(addr)
        for addr in replay.timelines
        if str(addr) in {
            "part:1/chapter:6/section:70",
            "part:1/chapter:7/section:74",
            "part:1/chapter:7/section:75",
            "part:1/chapter:7/section:76",
            "part:1/chapter:7/section:78",
            "part:1/chapter:7/section:79",
            "part:1/chapter:7/section:83",
            "part:1/chapter:9/section:88",
        }
    }

    assert root_level_extras == set()
    assert part_scoped_recovered == {
        "part:1/chapter:6/section:70",
        "part:1/chapter:7/section:74",
        "part:1/chapter:7/section:75",
        "part:1/chapter:7/section:76",
        "part:1/chapter:7/section:78",
        "part:1/chapter:7/section:79",
        "part:1/chapter:7/section:83",
        "part:1/chapter:9/section:88",
    }


def test_replay_xml_1978_38_section_12_1_full_replace_does_not_preserve_stale_list_items() -> None:
    replay = pinned_replay("1978/38", mode="finlex_oracle", quiet=True, stop_before="2022/697")

    section = replay.materialized_state.find_node("section", "1", "chapter", "12")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert "Kulutushyödykkeen välittäjän vastuu" in text
    assert "vastaa hyödykkeen hankkivalle kuluttajalle sopimuksen täyttämisestä" in text
    assert "sen paikkakunnan yleisessä alioikeudessa" not in text
    assert "väestökirjalain" not in text
    assert "Välittäjän vastuu ei rajoita kuluttajan oikeuksia" in text
    assert "Kiinteistönvälittäjän vastuusta on voimassa" in text


def test_replay_xml_1978_38_preserves_chapter_12_sections_1a_and_1b_alongside_new_chapter_7_1a() -> None:
    replay = pinned_replay("1978/38", mode="finlex_oracle", quiet=True)

    chapter7_1a = replay.materialized_state.find_node("section", "1a", "chapter", "7")
    chapter12_1a = replay.materialized_state.find_node("section", "1a", "chapter", "12")
    chapter12_1b = replay.materialized_state.find_node("section", "1b", "chapter", "12")

    assert chapter7_1a is not None
    assert chapter12_1a is not None
    assert chapter12_1b is not None

    chapter7_1a_text = " ".join(irnode_to_text(chapter7_1a).split())
    chapter12_1a_text = " ".join(irnode_to_text(chapter12_1a).split())
    chapter12_1b_text = " ".join(irnode_to_text(chapter12_1b).split())

    assert "Säännösten soveltamisen rajoitukset maksunlykkäyksinä myönnettävissä luotoissa" in chapter7_1a_text
    assert "Vahingonkorvausta koskeva kanneaika eräissä tapauksissa" in chapter12_1a_text
    assert "Suhde Vahingonkorvauslakiin ja muihin lakeihin" in chapter12_1b_text

def test_replay_xml_1962_184_applies_formula_and_body_prose_repeals() -> None:
    replay = pinned_replay(
        "1962/184",
        mode="finlex_oracle",
        quiet=True,
    )

    assert replay.materialized_state.find_section("9") is None
    assert replay.materialized_state.find_section("17") is None


def test_replay_xml_1967_551_strips_inline_corrigendum_note_from_section_2() -> None:
    replay = pinned_replay(
        "1967/551",
        mode="finlex_oracle",
        quiet=True,
    )

    section = replay.materialized_state.find_section("2")
    assert section is not None

    text = " ".join(irnode_to_text(section).split())
    assert "Merkitty kohta oikaistu" not in text
    assert "Euroopan patenttisopimuksessa (SopS 8/96)" in text
    assert "tarkoitettua eurooppapatenttia koskeva hakemus" in text


def test_replay_xml_nests_simple_letter_subparagraphs_for_1997_1339_section_4() -> None:
    """Regression: section 4 must keep its simple letter families nested.

    This is the live 1997/1339 <- 2015/1752 mixed-scope tail family: the
    chapter-scoped 4 § group and the bare 4 § group must not both replay the
    same `7 kohta` tail.
    """
    replay = pinned_replay("1997/1339", mode="finlex_oracle", quiet=True, build_full_products=True)

    section = replay.replay_fold_state.find_section("4", "1")
    assert section is not None

    subsection = next(child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
    paragraphs = [child for child in subsection.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [p.label for p in paragraphs] == ["1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9.", "10."]

    para1 = next(p for p in paragraphs if p.label == "1.")
    assert [sp.label for sp in para1.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d"]

    para3 = next(p for p in paragraphs if p.label == "3.")
    assert [sp.label for sp in para3.children if sp.kind == IRNodeKind.SUBPARAGRAPH] == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
    ]


def test_normalize_and_compile_ops_1997_1339_rejects_ambiguous_unscoped_fallback_insert() -> None:
    base_replay = pinned_replay("1997/1339", mode="legal_pit", stop_before="2015/1752", quiet=True)
    xml_bytes = get_corpus().read_source("2015/1752")
    assert xml_bytes is not None
    muutos_tree = etree.fromstring(xml_bytes)
    johto = get_johtolause(xml_bytes)
    title_el = muutos_tree.find(".//{*}docTitle")
    source_title = (
        etree.tostring(title_el, method="text", encoding="unicode").strip()
        if title_el is not None
        else "Unknown"
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=base_replay.replay_fold_state,
        amendment_id="2015/1752",
        source_title=source_title,
        used_sec1_fallback=False,
        parent_id="1997/1339",
        strict_profile=None,
    )

    descriptions = [op.description() for op in phase2.output]
    assert "INSERT 4 § 1 mom 7 kohta" not in descriptions
    assert "REPLACE 1 luku 4 § 1 mom" in descriptions
    assert any(
        finding.kind == "ELAB.REJECTED_OPERATION"
        and finding.detail.get("reason_code") == "ELAB.AMBIGUOUS_UNSCOPED_FALLBACK_INSERT_MULTI_SCOPE"
        for finding in phase2.finding_ledger
    )


def test_replay_xml_nests_simple_digit_subparagraphs_for_1997_108() -> None:
    """Regression: repeated digit families in 1997/108 must not stay as duplicate labels."""
    replay = pinned_replay("1997/108", mode="finlex_oracle", quiet=True, build_full_products=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        section2 = state.find_section("2")
        assert section2 is not None
        subsection2 = next(child for child in section2.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
        paras2 = [child for child in subsection2.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paras2] == ["1", "2", "3", "4"]
        para4 = next(p for p in paras2 if p.label == "4")
        assert [sp.label for sp in para4.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["1", "2", "3", "4", "5", "6", "7"]

        section3 = state.find_section("3")
        assert section3 is not None
        subsection3 = next(child for child in section3.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1")
        paras3 = [child for child in subsection3.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paras3] == ["1."]
        para1 = paras3[0]
        assert [sp.label for sp in para1.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["2"]


def test_replay_xml_splits_digit_reset_subparagraph_run_for_2000_154() -> None:
    """Regression: 2000/154 must split the buried 5)-reset into its own paragraph."""
    replay = pinned_replay("2000/154", mode="finlex_oracle", quiet=True, build_full_products=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        section = state.find_section("1", "1")
        assert section is not None
        subsection = next(
            child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        paragraphs = [child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == ["1", "2", "3", "4", "5", "6"]

        para4 = next(p for p in paragraphs if p.label == "4")
        para5 = next(p for p in paragraphs if p.label == "5")
        assert [sp.label for sp in para4.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["a", "b", "c", "d", "e"]
        assert [sp.label for sp in para5.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["a", "b"]


def test_replay_xml_nests_repeated_roman_subitems_for_2002_1244_section_21c() -> None:
    """Regression: malformed flat i/ii runs in 2018/1184 must nest under d/e in §21c."""
    replay = pinned_replay("2002/1244", mode="finlex_oracle", quiet=True, build_full_products=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        section = state.find_section("21c", "3")
        assert section is not None
        subsection = next(
            child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        paragraphs = [child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [p.label for p in paragraphs] == ["a", "b", "c", "d", "e"]

        para_d = next(p for p in paragraphs if p.label == "d")
        assert [sp.label for sp in para_d.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]

        para_e = next(p for p in paragraphs if p.label == "e")
        assert [sp.label for sp in para_e.children if sp.kind is IRNodeKind.SUBPARAGRAPH] == ["i", "ii"]


def test_finlex_oracle_replay_uses_cutoff_materialization_spec() -> None:
    replay = pinned_replay("2009/953", mode="finlex_oracle")

    assert replay.materialization_spec is not None
    assert replay.materialization_spec.as_of == "2020-01-01"


def test_replay_products_validate_cleanly_for_known_statute() -> None:
    replay = pinned_replay("2009/953", mode="legal_pit")

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=True,
    )

    assert violations == []


def test_replay_products_validate_cleanly_for_2004_1287_deep_materialization() -> None:
    replay = pinned_replay("2004/1287", mode="finlex_oracle")

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=True,
    )

    assert violations == []


def test_cleanup_sourceless_base_merge_conflicts_keeps_base_and_stronger_later_lineage() -> None:
    versions = [
        ProvisionVersion(
            effective="0000-00-00",
            enacted="0000-00-00",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko"),
            source=None,
        ),
        ProvisionVersion(
            effective="0000-00-00",
            enacted="0000-00-00",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko"),
            source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
        ),
        ProvisionVersion(
            effective="0000-00-00",
            enacted="0000-00-00",
            content=IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                text="5 § Otsikko lisäys Tässä laissa tarkoitetaan jotain enemmän.",
            ),
            source=OperationSource(statute_id="2002/1", effective="2002-01-01"),
        ),
    ]

    cleaned = _cleanup_sourceless_base_merge_conflicts(versions)

    assert _FI_SOURCELESS_BASE_MERGE_CLEANUP_RULE == "fi_sourceless_base_merge_cleanup_v1"
    assert len(cleaned) == 2
    assert cleaned[0].source is None
    assert cleaned[1].source is not None
    assert cleaned[1].source.statute_id == "2002/1"


def test_cleanup_sourceless_base_merge_conflicts_is_noop_without_sourceless_base() -> None:
    versions = [
        ProvisionVersion(
            effective="2001-01-01",
            enacted="2001-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko"),
            source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
        ),
        ProvisionVersion(
            effective="2002-01-01",
            enacted="2002-01-01",
            content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § Otsikko Tässä laissa tarkoitetaan."),
            source=OperationSource(statute_id="2002/1", effective="2002-01-01"),
        ),
    ]

    cleaned = _cleanup_sourceless_base_merge_conflicts(versions)

    assert cleaned == versions


def test_replay_xml_surfaces_migration_events_for_renumbered_statute() -> None:
    replay = pinned_replay("2017/320", mode="legal_pit")

    assert replay.migration_events
    assert any(event.kind == "renumber" for event in replay.migration_events)


def test_replay_xml_can_skip_full_products_for_fast_bench() -> None:
    replay = pinned_replay(
        "2009/953",
        mode="finlex_oracle",
        quiet=True,
        build_full_products=False,
    )

    assert replay.products.replay_fold_state is replay.replay_fold_state
    assert replay.products.materialized_state == replay.replay_fold_state
    assert replay.products.timelines is None
    assert replay.materialization_spec is None


def test_replay_xml_emits_payloaded_part_snapshot_for_2020_1256() -> None:
    lo_ops: list[LegalOperation] = []

    pinned_replay(
        "2017/320",
        quiet=True,
        build_full_products=False,
        lo_ops_out=lo_ops,
    )

    snapshot = next(
        op
        for op in lo_ops
        if op.op_id == "snapshot_part_6"
        and op.source is not None
        and op.source.statute_id == "2020/1256"
    )

    assert snapshot.payload is not None


def test_replay_xml_expires_2021_984_temporary_21b_section() -> None:
    replay = pinned_replay("1999/488", mode="legal_pit", quiet=True)
    addr = LegalAddress(path=(("chapter", "5"), ("section", "21b")))

    assert replay.timelines is not None
    assert addr in replay.timelines
    assert replay.timelines[addr].versions[-1].expires == "2022-01-31"
    assert select_active_version(replay.timelines[addr], "2025-01-01") is None
    assert replay.replay_fold_state.find_section("21b") is not None
    assert replay.materialized_state.find_section("21b") is None


def test_replay_xml_expires_2020_292_temporary_99a_section() -> None:
    replay = pinned_replay("2015/410", mode="legal_pit", quiet=True)
    addr = LegalAddress(path=(("part", "5"), ("chapter", "12"), ("section", "99a")))

    assert replay.timelines is not None
    assert addr in replay.timelines
    assert replay.timelines[addr].versions[-1].expires == "2021-05-31"
    assert select_active_version(replay.timelines[addr], "2025-01-01") is None
    assert replay.replay_fold_state.find_section("99a", "12", "5") is not None
    assert replay.materialized_state.find_section("99a", "12", "5") is None


def test_temporal_events_from_lo_ops_keeps_expire_when_group_has_explicit_commence_only() -> None:
    addr = LegalAddress(path=(("chapter", "5"), ("section", "21b")))
    op = LegalOperation(
        op_id="t21b",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=addr,
        group_id="finland-johto:2021/984:section_commencement",
        source=OperationSource(
            statute_id="2021/984",
            title="Laki lääketieteellisestä tutkimuksesta annetun lain muuttamisesta",
            enacted="2021-11-19",
            effective="2021-11-24",
            expires="2022-01-31",
        ),
    )
    explicit_commence = TemporalEvent(
        event_id="fi-temporal:finland-johto:2021/984:section_commencement",
        kind="commence",
        scope=TemporalScope(target_statute="1999/488", exact_addresses=(addr,)),
        effective="2021-11-24",
        source=op.source,
        group_id="finland-johto:2021/984:section_commencement",
    )

    assert explicit_commence.group_id is not None
    got = _temporal_events_from_lo_ops(
        [op],
        target_statute="1999/488",
        covered_commence_group_ids=frozenset({explicit_commence.group_id}),
        covered_expiry_signatures=frozenset(),
    )

    assert [event.kind for event in got] == ["expire"]
    assert got[0].expires == "2022-01-31"
    assert tuple(got[0].scope.exact_addresses or ()) == (addr,)


def test_replay_xml_expires_2018_11_temporary_content_before_later_permanent_merges() -> None:
    """Later permanent sparse merges must not bake expired 2021/513 content in."""
    replay = pinned_replay("2018/11", mode="legal_pit", quiet=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        sec25 = state.find_section("25", "4")
        assert sec25 is not None
        sec25_mom1 = next(
            child for child in sec25.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        sec25_paragraphs = [child for child in sec25_mom1.children if child.kind is IRNodeKind.PARAGRAPH]
        assert [child.label for child in sec25_paragraphs] == ["1", "2"]
        assert "41 §:n 1 momentin 2 kohdassa tarkoitetun maksujärjestelyn kohteena olevan veron suoritukseksi" not in (
            " ".join(irnode_to_text(sec25_mom1).split())
        )

        sec26 = state.find_section("26", "4")
        assert sec26 is not None
        assert next(
            (
                child
                for child in sec26.children
                if child.kind is IRNodeKind.SUBSECTION and child.label == "5"
            ),
            None,
        ) is None

        sec43 = state.find_section("43", "7")
        assert sec43 is not None
        assert next(
            (
                child
                for child in sec43.children
                if child.kind is IRNodeKind.SUBSECTION and child.label == "6"
            ),
            None,
        ) is None


def test_replay_xml_keeps_2021_984_permanent_inserts_active() -> None:
    """Regression: 2021/984 permanent inserts stay live in the replay products.

    The replay fold already contains the inserted sections. Materialization at a
    later PIT must keep the permanent inserts visible while the temporary
    chapter-5 `21b §` expires.
    """
    replay = pinned_replay("1999/488", mode="legal_pit", quiet=True)

    assert replay.replay_fold_state.find_section("4a", "2") is not None
    assert replay.replay_fold_state.find_section("5a", "2") is not None
    assert replay.replay_fold_state.find_section("7a", "2") is not None
    assert replay.replay_fold_state.find_section("18a", "4") is not None
    assert replay.replay_fold_state.find_section("18b", "4") is not None
    assert replay.replay_fold_state.find_section("18c", "4") is not None
    assert replay.replay_fold_state.find_section("21a", "5") is not None
    assert replay.replay_fold_state.find_section("21b", "5") is not None
    assert replay.replay_fold_state.find_section("21c", "5") is not None
    assert replay.replay_fold_state.find_section("22b", "5") is not None
    assert replay.replay_fold_state.find_section("18a", "3") is None
    assert replay.replay_fold_state.find_section("18b", "3") is None
    assert replay.replay_fold_state.find_section("18c", "3") is None
    assert replay.replay_fold_state.find_section("21a", "3") is None
    assert replay.replay_fold_state.find_section("21b", "3") is None
    assert replay.replay_fold_state.find_section("21c", "3") is None
    assert replay.replay_fold_state.find_section("21a", "4") is None
    assert replay.replay_fold_state.find_section("21b", "4") is None
    assert replay.replay_fold_state.find_section("21c", "4") is None
    assert replay.replay_fold_state.find_section("22b", "3") is None

    assert replay.materialized_state.find_section("4a", "2") is not None
    assert replay.materialized_state.find_section("5a", "2") is not None
    assert replay.materialized_state.find_section("7a", "2") is not None
    assert replay.materialized_state.find_section("18a", "4") is not None
    assert replay.materialized_state.find_section("18b", "4") is not None
    assert replay.materialized_state.find_section("18c", "4") is not None
    assert replay.materialized_state.find_section("21a", "5") is not None
    assert replay.materialized_state.find_section("21b", "5") is None
    assert replay.materialized_state.find_section("21c", "5") is not None
    assert replay.materialized_state.find_section("22b", "5") is not None
    assert replay.materialized_state.find_section("18a", "3") is None
    assert replay.materialized_state.find_section("18b", "3") is None
    assert replay.materialized_state.find_section("18c", "3") is None
    assert replay.materialized_state.find_section("21a", "3") is None
    assert replay.materialized_state.find_section("21b", "3") is None
    assert replay.materialized_state.find_section("21c", "3") is None
    assert replay.materialized_state.find_section("21a", "4") is None
    assert replay.materialized_state.find_section("21b", "4") is None
    assert replay.materialized_state.find_section("21c", "4") is None
    assert replay.materialized_state.find_section("22b", "3") is None


def test_replay_xml_1999_488_places_replaced_section_18_under_chapter_4_after_2021_984() -> None:
    replay = pinned_replay("1999/488", mode="legal_pit", quiet=True)

    sec18 = replay.materialized_state.find_section("18", "4")
    assert sec18 is not None
    text18 = " ".join(irnode_to_text(sec18).split())

    assert "Alueellisen toimikunnan kokoonpano" in text18
    assert "Alueellisessa lääketieteellisessä tutkimuseettisessä toimikunnassa on oltava puheenjohtaja" in text18
    assert replay.materialized_state.find_section("18", "3") is None


def test_replay_xml_2004_1224_keeps_permanent_2016_1100_insert_sections_active() -> None:
    """2016/1100's alpha-suffix chapter-6 inserts must remain live after 2019.

    Regression: the real LISATA verb group was truncated by a false authority-
    lead-in skip at the first provenance CITATION_SPAN. Replay then treated the
    temporary `6 a §` as the only inserted section from that family, leaving the
    permanent `7 b §`, `18 a §`, and `22 b §` missing from the final PIT.
    """
    replay = pinned_replay("2004/1224", mode="finlex_oracle", quiet=True)

    assert replay.materialized_state.find_section("7b", "6") is not None
    assert replay.materialized_state.find_section("18a", "6") is not None
    assert replay.materialized_state.find_section("22b", "6") is not None


def test_replay_xml_applies_2025_1162_sparse_section_replace_to_22a() -> None:
    replay = pinned_replay("1999/488", mode="legal_pit", quiet=True)
    sec22a = replay.materialized_state.find_section("22a", "5")

    assert sec22a is not None
    text = irnode_to_text(sec22a)
    assert (
        "sekä 21 c §:n 1 momentissa tarkoitetun viranomaisen tekemään "
        "rekisteritietojen luovuttamista koskevaan päätökseen"
    ) in text
    assert replay.materialized_state.find_section("21b", "5") is None


def test_replay_xml_applies_2025_1162_21c_then_22a_sequentially_without_staling_22a() -> None:
    """Regression: the later 22a replace must not inherit stale text after 21c.

    The replay compiler emits both 21c and 22a for 2025/1162.  Applying the
    compiled ops sequentially must yield the same 22a text as applying 22a from
    the same pre-amendment state directly.
    """
    base_replay = pinned_replay("1999/488", mode="legal_pit", quiet=True, build_full_products=False)
    xml_bytes = get_corpus().read_source("2025/1162")
    assert xml_bytes is not None
    muutos_tree = etree.fromstring(xml_bytes)
    johto = get_johtolause(xml_bytes)
    title_el = muutos_tree.find(".//{*}docTitle")
    source_title = (
        etree.tostring(title_el, method="text", encoding="unicode").strip()
        if title_el is not None
        else "Unknown"
    )

    phase2 = normalize_and_compile_ops(
        johto=johto,
        muutos_tree=muutos_tree,
        master=base_replay.replay_fold_state,
        amendment_id="2025/1162",
        source_title=source_title,
        used_sec1_fallback=False,
        parent_id="1999/488",
        strict_profile=None,
    )
    resolved = compile_amendment_ops(
        base_replay.replay_fold_state,
        phase2.output,
        muutos_tree,
        johto,
        "legal_pit",
        source_ref="2025/1162",
        source_title=source_title,
        target_statute="1999/488",
    ).output

    relevant = [rop for rop in resolved if rop.resolved_target_label in {"21c", "22a"}]
    # 2025/1162 emits two ops for 22a (heading + subsection body) plus one for 21c
    assert [rop.resolved_target_label for rop in relevant] == ["21c", "22a", "22a"]

    op_21c = relevant[0]
    # The subsection body op carries target_paragraph; use it for the anti-staling check
    op_22a_body = next(
        r for r in relevant
        if r.resolved_target_label == "22a" and r.resolved_target_scope_view.target_paragraph is not None
    )

    seq_state = apply_op(base_replay.replay_fold_state, None, base_replay.ctx, None, rop=op_21c)
    seq_state = apply_op(seq_state, None, base_replay.ctx, None, rop=op_22a_body)
    direct_state = apply_op(base_replay.replay_fold_state, None, base_replay.ctx, None, rop=op_22a_body)

    seq_22a = seq_state.find_section("22a", "5")
    direct_22a = direct_state.find_section("22a", "5")
    assert seq_22a is not None
    assert direct_22a is not None
    assert irnode_to_text(seq_22a) == irnode_to_text(direct_22a)


def test_replay_xml_preserves_2013_393_body_chapter_scope_for_37a() -> None:
    """Regression: §37a must stay in chapter 6, not rehome to the old family."""
    lo_ops: list[LegalOperation] = []
    replay = pinned_replay("2013/393", mode="legal_pit", quiet=True, lo_ops_out=lo_ops)

    snapshot = next(op for op in lo_ops if op.op_id == "snapshot_section_37a")
    assert tuple(snapshot.target.path) == (("chapter", "6"), ("section", "37a"))

    found_paths: list[tuple[tuple[str, str], ...]] = []

    def _walk(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> None:
        next_path = path + ((node.kind.value, node.label or ""),)
        if node.kind == IRNodeKind.SECTION and node.label == "37a":
            found_paths.append(next_path)
        for child in node.children:
            _walk(child, next_path)

    _walk(replay.materialized_state.ir)
    assert any(("chapter", "6") in path for path in found_paths)
    assert not any(("chapter", "5") in path for path in found_paths)


def test_replay_xml_repeals_2021_984_range_sections_10d_to_10i() -> None:
    """Regression: 2021/984 repeals the 10d–10i tail from the live statute."""
    replay = pinned_replay("1999/488", mode="legal_pit", quiet=True)

    for label in ("10d", "10e", "10f", "10g", "10h", "10i"):
        assert replay.materialized_state.find_section(label, "2a") is None


def test_replay_xml_2009_1672_whole_chapter_replace_retires_7a_2abc_from_materialized_state() -> None:
    """Regression: later whole-chapter replace must retire historic non-base child sections."""
    lo_ops: list[LegalOperation] = []
    replay = pinned_replay("2009/1672", mode="finlex_oracle", quiet=True, lo_ops_out=lo_ops)

    for label in ("2a", "2b", "2c"):
        assert replay.replay_fold_state.find_section(label, "7a") is None
        assert replay.materialized_state.find_section(label, "7a") is None

    repeal_targets = {
        tuple(op.target.path)
        for op in lo_ops
        if op.action is StructuralAction.REPEAL
        and op.source is not None
        and op.source.statute_id == "2024/1116"
        and op.target.path[:1] == (("chapter", "7a"),)
    }
    assert (("chapter", "7a"), ("section", "2a")) in repeal_targets
    assert (("chapter", "7a"), ("section", "2b")) in repeal_targets
    assert (("chapter", "7a"), ("section", "2c")) in repeal_targets


def test_replay_xml_2009_1672_keeps_section_2_8_body_when_vts_repeals_only_subsection() -> None:
    """Ambiguous granular voimaantulo repeal must not hijack the parent section snapshot."""
    replay = pinned_replay("2009/1672", mode="finlex_oracle", quiet=True)

    section = replay.replay_fold_state.find_section("8", "2")
    assert section is not None
    text = " ".join(irnode_to_text(section).split())

    assert "Öljyn kuljettaminen sisävesialueella" in text
    assert "Sisävesialueella liikennöivässä öljysäiliöaluksessa" in text
    assert "Muut haitallisten nestemäisten aineiden kuljetuksen todistuskirjat" not in text


def test_replay_xml_2009_1672_sparse_chapter_replace_does_not_drop_section_5() -> None:
    replay = pinned_replay("2009/1672", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_section("5", "1")
    assert section is not None


def test_replay_xml_2009_1672_does_not_import_laivavarustelaki_section_13_11() -> None:
    lo_ops: list[LegalOperation] = []
    replay = pinned_replay("2009/1672", mode="finlex_oracle", quiet=True, lo_ops_out=lo_ops)

    assert replay.replay_fold_state.find_section("11", "13") is None
    assert replay.materialized_state.find_section("11", "13") is None

    culprit_ops = [
        op
        for op in lo_ops
        if op.source is not None
        and op.source.statute_id == "2011/1503"
        and op.target.path == (("chapter", "13"), ("section", "11"))
    ]
    assert culprit_ops
    assert all(
        op.action is StructuralAction.REPEAL and op.payload is None
        for op in culprit_ops
    )
    assert not any(
        op.source is not None
        and op.source.statute_id == "2011/1503"
        and op.action is StructuralAction.INSERT
        and op.target.path[:2] == (("chapter", "13"), ("section", "11"))
        for op in lo_ops
    )


def test_replay_xml_repealed_2009_375_sections_25_26_do_not_revive_live_text() -> None:
    """Regression: repealed 25–26 must not revive stale permanent body text."""
    replay = pinned_replay("1999/488", mode="legal_pit", quiet=True)

    sec25 = replay.materialized_state.find_section("25", "6")
    sec26 = replay.materialized_state.find_section("26", "6")

    assert sec25 is None
    assert sec26 is None


def test_replay_xml_1988_161_pseudo_chapter_marker_moves_section_55_to_7c() -> None:
    """Regression: 1996/473 restructures chapter 7 → 7a/7b/7c via pseudo-markers.

    §55 must be moved from chapter 7 to chapter 7c (not left in chapter 7 nor
    duplicated).  2008/732 later repeals chapter 7; if §55 stays in chapter 7
    it gets wiped and appears MISSING in the final replay.
    """
    replay = pinned_replay("1988/161", mode="legal_pit", quiet=True)

    # §55 must be in chapter 7c
    sec55_in_7c = replay.materialized_state.find_section("55", "7c")
    assert sec55_in_7c is not None, "§55 must be in chapter 7c after pseudo-chapter restructuring"

    # §55 must NOT remain in chapter 7 (it was moved away)
    sec55_in_7 = replay.materialized_state.find_section("55", "7")
    assert sec55_in_7 is None, "§55 must not remain in chapter 7 after move to 7c"


def test_replay_xml_2009_617_moves_sections_39_to_41_into_inserted_chapter_4a() -> None:
    """Regression: 2016/533 splits chapter 4 and moves §§39–41 under 4 a luku.

    Before the fix, replay inserted chapter 4a as an empty shell and left the
    existing section family under chapter 4 because the structural move bridge
    only trusted pseudo-chapter marker sections, not real inserted chapters.
    """
    replay = pinned_replay("2009/617", stop_before="2017/816", mode="legal_pit", quiet=True)

    for label in ("39", "40", "41"):
        sec_in_4a = replay.materialized_state.find_section(label, "4a")
        assert sec_in_4a is not None, f"§{label} must be moved into chapter 4a after 2016/533"

        sec_in_4 = replay.materialized_state.find_section(label, "4")
        assert sec_in_4 is None, f"§{label} must not remain in chapter 4 after move to 4a"


def test_replay_xml_1977_603_top_level_pseudo_chapter_marker_inserts_sections() -> None:
    """Regression: 1996/476 introduces §72a/§72b/§72c under a top-level pseudo-chapter-marker
    '8 a luku' (not inside a <chapter> element).

    grafter_uncovered.py primary coverage path was comparing CoverageUnit.kind (str)
    to IRNodeKind.SECTION (enum) with `is not`, which always evaluated True and skipped
    all sections in the supplemental_candidates loop.  The fix changes to `!= "section"`.
    """
    replay = pinned_replay("1977/603", mode="finlex_oracle", quiet=True)

    # All three sections must be present after the fix
    sec72a = replay.materialized_state.find_section("72a")
    assert sec72a is not None, "§72a must be inserted by 1996/476 (uncovered recovery fix)"
    sec72b = replay.materialized_state.find_section("72b")
    assert sec72b is not None, "§72b must be inserted by 1996/476"
    sec72c = replay.materialized_state.find_section("72c")
    assert sec72c is not None, "§72c must be inserted by 1996/476"


def test_replay_xml_1977_603_realizes_section_72c_only_under_chapter_8a() -> None:
    """Later chapter 8a realization must not leave a standalone §72c timeline bucket."""
    replay = pinned_replay("1977/603", mode="finlex_oracle", quiet=True)

    timeline_keys = {str(address) for address in replay.products.timelines}
    assert "chapter:8a/section:72c" in timeline_keys
    assert "section:72c" not in timeline_keys

    chapter_8a = replay.materialized_state.find_chapter("8a")
    assert chapter_8a is not None
    chapter_section_labels = [child.label for child in chapter_8a.children if child.kind == IRNodeKind.SECTION]
    assert chapter_section_labels == ["72a", "72b", "72c", "72d"]

    root_section_labels = [child.label for child in replay.materialized_state.ir.children if child.kind == IRNodeKind.SECTION]
    assert "72c" not in root_section_labels


def test_replay_xml_1996_1260_orphaned_uusi_multi_target_lisataan() -> None:
    """Regression: 2022/958 lisätään clause with three targets where the first
    sub-target qualifier ('c alakohta') is removed by annotate_qualifiers,
    leaving an orphaned UUSI token immediately before the COMMA separator.

    Surface parse continuation loop was treating UUSI as a failed parse and
    breaking out of the loop instead of skipping the orphaned marker and
    continuing to the next COMMA-separated target (§8b INSERT via DOC:ILL
    Pattern C) and the §20b momentti 2 INSERT after it.
    """
    replay = pinned_replay("1996/1260", mode="finlex_oracle", quiet=True)

    sec8b = replay.materialized_state.find_section("8b")
    assert sec8b is not None, "§8b must be inserted by 2022/958 (orphaned UUSI fix)"

    sec20b = replay.materialized_state.find_section("20b")
    assert sec20b is not None, "§20b must exist"
    from lawvm.core.ir import IRNodeKind
    subs_20b = [c for c in sec20b.children if c.kind == IRNodeKind.SUBSECTION]
    assert any(s.label == "2" for s in subs_20b), "§20b must have momentti 2 inserted by 2022/958"


def test_replay_xml_repealed_2007_435_sections_do_not_revive_live_text() -> None:
    """Whole-section kumotaan repeals must not revive base text in finlex_oracle."""
    replay = pinned_replay("1995/355", mode="finlex_oracle", quiet=True)

    assert replay.replay_fold_state.find_section("8a", "3") is not None
    assert replay.materialized_state.find_section("5", "2") is None
    assert replay.materialized_state.find_section("7", "2") is None
    assert replay.materialized_state.find_section("8a", "3") is None


def test_replay_xml_repealed_2006_764_sections_do_not_revive_live_text() -> None:
    """Zero-day repeal placeholders must not surface stale sections after PIT selection."""
    replay = pinned_replay("2003/343", mode="finlex_oracle", quiet=True)

    assert replay.materialized_state.find_section("32", "5") is None
    assert replay.materialized_state.find_section("35", "5") is None
    assert replay.materialized_state.find_section("40", "5") is None


def test_replay_xml_repealed_2003_750_sections_stay_absent_on_same_day_oracle_horizon() -> None:
    """Same-day permanent repeals must not be ignored under detached horizons."""
    replay = pinned_replay("1998/461", mode="finlex_oracle", quiet=True)

    assert replay.materialized_state.find_section("16") is None
    assert replay.materialized_state.find_section("17") is None
    assert replay.materialized_state.find_section("18") is None
    assert replay.materialized_state.find_section("19") is None


def test_replay_xml_repealed_1974_258_section_15_stays_absent() -> None:
    """A whole-section repeal with a johto commencement date must reach timelines."""
    replay = pinned_replay("1974/258", mode="finlex_oracle", quiet=True)

    assert replay.materialized_state.find_section("15") is None


def test_replay_xml_recycle_rename_kumotaan_muutetaan_preserves_new_section_2010_128() -> None:
    """Recycle-and-rename: section in both kumotaan AND muutetaan must survive as new content.

    2019/1330 repeals old §44 (kumotaan 43 ja 44 §) and simultaneously
    introduces new §44 content (muutetaan ... 44 §). The kumotaan-muutetaan
    recycle guard must exclude §44 from the expiry override so the new §44
    is preserved permanently rather than being converted to a repeal.

    Regression for the bug where _rewrite_kumotaan_snapshot_replaces_to_repeal
    incorrectly converted the new §44 to a REPEAL, leaving it absent from
    the materialized product.
    """
    replay = pinned_replay("2010/128", mode="finlex_oracle", quiet=True)

    # §43 was genuinely repealed by 2019/1330 (not in muutetaan)
    assert replay.materialized_state.find_section("43") is None, "§43 should be repealed"

    # §44 was recycled: old §44 repealed, new §44 introduced via muutetaan
    sec44 = replay.materialized_state.find_section("44")
    assert sec44 is not None, "§44 (new Ahvenanmaa content) must be present after recycle fix"


def test_replay_xml_later_inserted_whole_section_repeal_respects_oracle_horizon() -> None:
    """Oracle PIT extends to the effective date of the latest amendment repeal.

    Oracle fin@20110427 was consolidated around 2011-05-05.  Amendment 2011/427
    repeals §31a with effective date 2011-06-01.  The oracle PIT is extended
    to 2011-06-01 so that the materialized state reflects the completed repeal,
    matching what the Finlex consolidated XML shows.
    """
    replay = pinned_replay("1990/845", mode="finlex_oracle", quiet=True)

    sec31a = replay.materialized_state.find_section("31a")
    assert sec31a is None


def test_replay_xml_retargets_stale_body_chapter_scope_to_live_current_chapter_2016_1285() -> None:
    replay = pinned_replay("2016/1285", mode="finlex_oracle", quiet=True)

    for label in ("17", "18", "19", "20"):
        assert replay.replay_fold_state.find_section(label, "5") is not None
        assert replay.replay_fold_state.find_section(label, "3") is None
        assert replay.materialized_state.find_section(label, "5") is not None
        assert replay.materialized_state.find_section(label, "3") is None

    assert replay.replay_fold_state.find_section("24", "6") is not None
    assert replay.replay_fold_state.find_section("24", "3") is None
    assert replay.materialized_state.find_section("24", "6") is not None
    assert replay.materialized_state.find_section("24", "3") is None


def test_replay_xml_preserves_sparse_insert_before_terminal_voimaantulo_for_2006_766() -> None:
    replay = pinned_replay("2006/766", mode="finlex_oracle", quiet=True)
    body = replay.materialized_state.ir

    top_labels = [
        child.label
        for child in body.children
        if child.kind in {IRNodeKind.SECTION, IRNodeKind.CHAPTER}
    ]
    assert top_labels == ["1", "2", "3", "3a", "4"]

    section_3a = replay.materialized_state.find_section("3a")
    assert section_3a is not None
    heading = next(child for child in section_3a.children if child.kind == IRNodeKind.HEADING)
    assert "Vastuullisuuden huomiointi" in (heading.text or "")

    section_4 = replay.materialized_state.find_section("4")
    assert section_4 is not None
    assert irnode_to_text(section_4).startswith("4 § Voimaantulo")


def test_replay_xml_preserves_inserted_chapter_topology_for_2014_1429() -> None:
    replay = pinned_replay("2014/1429", mode="finlex_oracle", quiet=True)
    body = replay.materialized_state.ir

    chapter_labels = [child.label for child in body.children if child.kind == IRNodeKind.CHAPTER]
    assert "3a" in chapter_labels
    assert "5a" in chapter_labels
    assert "5b" in chapter_labels
    assert "6" in chapter_labels

    def _chapter_section_labels(chapter_label: str) -> list[str]:
        chapter = next(
            child
            for child in body.children
            if child.kind == IRNodeKind.CHAPTER and child.label == chapter_label
        )
        return [child.label for child in chapter.children if child.kind == IRNodeKind.SECTION]

    assert _chapter_section_labels("5a")[:4] == ["29a", "29b", "29c", "29d"]
    assert "29e" in _chapter_section_labels("5b")
    assert "29f" in _chapter_section_labels("5b")
    assert "29g" in _chapter_section_labels("5b")

    chapter_3a = next(
        child for child in body.children if child.kind == IRNodeKind.CHAPTER and child.label == "3a"
    )
    section_18a = next(
        child for child in chapter_3a.children if child.kind == IRNodeKind.SECTION and child.label == "18a"
    )
    assert irnode_to_text(section_18a).startswith("18 a § Pakottavuus")


def test_replay_xml_keeps_2014_1429_18e_as_single_subsection_list_section() -> None:
    replay = pinned_replay("2014/1429", mode="finlex_oracle", quiet=True)

    sec18e = replay.materialized_state.find_section("18e", "3a")
    assert sec18e is not None

    subsection_labels = [child.label for child in sec18e.children if child.kind == IRNodeKind.SUBSECTION]
    assert subsection_labels == ["1"]

    sub1 = next(child for child in sec18e.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1")
    child_kinds = [child.kind for child in sub1.children]
    assert child_kinds[:4] == [
        IRNodeKind.INTRO,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
    ]
    assert child_kinds[4:] == [
        IRNodeKind.CONTENT,
        IRNodeKind.CONTENT,
        IRNodeKind.WRAP_UP,
    ]
    assert "Määräaikaisen sopimuksen ehtoja ei kuitenkaan saa muuttaa" in irnode_to_text(sub1)


def test_replay_xml_keeps_2022_1384_tree_definition_inside_subsection_2() -> None:
    replay = pinned_replay("2022/1384", mode="finlex_oracle", quiet=True)

    sec1 = replay.materialized_state.find_section("1")
    assert sec1 is not None

    subsection_labels = [child.label for child in sec1.children if child.kind == IRNodeKind.SUBSECTION]
    assert subsection_labels == ["1", "2"]

    sub2 = next(child for child in sec1.children if child.kind == IRNodeKind.SUBSECTION and child.label == "2")
    child_kinds = [child.kind for child in sub2.children]
    assert child_kinds[:4] == [
        IRNodeKind.INTRO,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
    ]
    assert child_kinds[4:] == [IRNodeKind.WRAP_UP]
    assert "Tätä asetusta sovelletaan vain puihin" in irnode_to_text(sub2)


def test_replay_xml_drops_tax_year_scoped_temporary_sections_for_1967_543() -> None:
    replay = pinned_replay(
        "1967/543",
        mode="finlex_oracle",
        quiet=True,
    )

    assert replay.materialized_state.find_section("12a") is None
    assert replay.materialized_state.find_section("12b") is None


def test_replay_xml_moves_2014_1429_29e_into_chapter_5b() -> None:
    """29e follows the move clause into chapter 5b at the oracle horizon."""
    replay = pinned_replay("2014/1429", mode="finlex_oracle", quiet=True)

    chapter_5a_29e = replay.materialized_state.find_section("29e", "5a")
    chapter_5b_29e = replay.materialized_state.find_section("29e", "5b")

    assert chapter_5a_29e is None
    assert chapter_5b_29e is not None
    assert "Datakeskuksen hukkalämmön hyödyntäminen" in irnode_to_text(chapter_5b_29e)


def test_replay_xml_applies_2024_483_kieliasu_section_list_for_2008_550() -> None:
    """Language-variant residue must not block the later long section list in 2024/483."""
    replay = pinned_replay("2008/550", mode="finlex_oracle", quiet=True)

    section_10 = replay.materialized_state.find_section("10")
    assert section_10 is not None
    assert irnode_to_text(section_10).startswith("10 § Ministeriön virkamiesjohdon kokous")


def test_replay_xml_applies_2019_511_luvun_insert_chain_for_2012_746() -> None:
    """Anaphoric `luvun` insert continuations in 2019/511 must materialize under the right chapters."""
    replay = pinned_replay("2012/746", mode="finlex_oracle", quiet=True)

    assert replay.materialized_state.find_section("1a", "8") is not None
    assert replay.materialized_state.find_section("5a", "8") is not None
    assert replay.materialized_state.find_section("10", "8") is not None
    assert replay.materialized_state.find_section("1", "10a") is not None
    assert replay.materialized_state.find_section("2", "10a") is not None
    assert replay.materialized_state.find_section("5", "10a") is not None


def test_replay_xml_preserves_explicit_body_chapter_ownership_for_2013_393() -> None:
    """An explicit chapter wrapper in the amendment body must stay on the inserted section."""
    replay = pinned_replay("2013/393", mode="finlex_oracle", quiet=True)

    assert replay.materialized_state.find_section("37a", "6") is not None
    assert replay.materialized_state.find_section("37a", "5") is None


@pytest.mark.parametrize(
    "section_num, chapter_num, expected_labels, expected_snippets",
    [
        (
            "32",
            "3",
            ["1", "2", "3"],
            [
                "Jos ammatillinen kuntoutus keskeytyy yli 30 kalenteripäivän ajaksi",
                "Kuntoutusraha tai kuntoutuskorotus voidaan lakkauttaa",
                "Työntekijällä ei ole oikeutta ilman pätevää syytä työkyvyttömyyseläkkeeseen",
            ],
        ),
        (
            "118",
            "8",
            ["1", "2", "3", "4", "5"],
            [
                "Jos työntekijälle on maksettu sairausvakuutuslain mukaista sairauspäivärahaa",
                "Jos täysi työkyvyttömyyseläke myönnetään takautuvasti 41 §:n 1 momentissa",
                "Jos kuntoutusraha tai -korotus myönnetään takautuvasti",
                "Jos kuntoutusraha, kuntoutuskorotus tai työkyvyttömyyseläke",
                "Jos työuraeläke myönnetään takautuvasti",
            ],
        ),
        (
            "122",
            "8",
            ["1", "2", "3", "4"],
            [
                "Eläkelaitos voi eläkkeensaajan suostumuksella päättää",
                "Esityksen eläkkeen maksamisesta hyvinvointialueelle voi tehdä",
                "Eläkettä ei saa käyttää vastoin eläkkeensaajan suostumusta",
                "Mitä tässä pykälässä säädetään hyvinvointialueesta",
            ],
        ),
            (
                    "205",
                    "14",
                    ["1", "2", "3", "4"],
                    [
                        "Eläkelaitoksella ja Eläketurvakeskuksella on oikeus salassapitosäännösten",
                        "Annettavia tietoja ovat:",
                        "Eläkelaitoksella ja Eläketurvakeskuksella on oikeus antaa 2 momentissa tarkoitettuja tietoja",
                        "Tässä pykälässä tarkoitetuissa tilanteissa ei kuitenkaan saa antaa työntekijän terveydentilaa koskevia tietoja",
                    ],
                ),
            (
                "70",
                "4",
                ["1", "2", "3", "4", "5"],
                [
                    "Eläkkeen perusteena olevaa työansiota määrättäessä otetaan huomioon palkka",
                    "Eläkkeen perusteena olevaan työansioon luetaan myös työstä maksettava vastike, joka on osaksi tai kokonaan sovittu hyvitettäväksi",
                    "Edellä 1 momentissa tarkoitettuna vastikkeena työstä ei pidetä muun muassa",
                    "Edellä 3 momentin 11 kohdassa tarkoitetussa tilanteessa edellytyksenä on lisäksi",
                    "Yleisöltä palvelurahaa saavan työntekijän on ilmoitettava työnantajalleen veron perusteena olevan palvelurahan määrä",
                ],
            ),
    ],
)
def test_replay_xml_preserves_2006_395_targeted_merge_sections(
    section_num: str,
    chapter_num: str,
    expected_labels: list[str],
    expected_snippets: list[str],
) -> None:
    """Replay/product regression for 2006/395 targeted merge semantics."""
    replay = pinned_replay("2006/395", mode="finlex_oracle", quiet=True)

    for state in (replay.replay_fold_state, replay.materialized_state):
        section = state.find_section(section_num, chapter_num)
        assert section is not None
        subsections = [child for child in section.children if child.kind is IRNodeKind.SUBSECTION]
        assert [child.label for child in subsections] == expected_labels
        assert len(subsections) == len(expected_snippets)
        for subsection, snippet in zip(subsections, expected_snippets, strict=True):
            text = " ".join(irnode_to_text(subsection).split())
            assert snippet in text


def test_validate_replay_products_detects_materialized_tree_invariants() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),
            IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    ctx = StatuteContext(
        id="test/1",
        title="Test",
        base_ir=IRNode(kind=IRNodeKind.BODY),
        base_xml_bytes=b"<body/>",
    )
    products = ReplayProducts(
        replay_fold_state=ReplayState(ir=IRNode(kind=IRNodeKind.BODY)),
        materialized_state=ReplayState(ir=body),
        timelines=None,
        materialization_spec=None,
        source_adjudication=None,
    )

    violations = validate_replay_products(
        ctx,
        products,
        deep_materialization_check=False,
    )

    assert "materialized_tree:body: duplicate section:1 (2 times)" in violations


def test_replay_fold_does_not_duplicate_temporary_section_chain_for_1995_1556() -> None:
    replay = pinned_replay("1995/1556", mode="legal_pit", stop_before="2022/439")

    violations = validate_replay_products(
        replay.ctx,
        replay.products,
        deep_materialization_check=False,
    )

    assert "replay_fold_tree:body/hcontainer:?: duplicate section:5e (2 times)" not in violations


def test_replay_fold_splits_sparse_combined_subsection_replace_for_1991_827() -> None:
    replay = pinned_replay("1991/827", mode="legal_pit", stop_before="1995/1387")

    for state in (replay.replay_fold_state, replay.materialized_state):
        sec6 = state.find_section("6")
        assert sec6 is not None
        sec6_text = irnode_to_text(sec6)
        subsections = [child for child in sec6.children if child.kind is IRNodeKind.SUBSECTION]

        assert [child.label for child in subsections] == ["1", "2", "3"]
        assert sec6_text.count(
            "Edellä 1 momentissa mainitun oikeuden, rajoituksen tai toimenpiteen kirjauksessa"
        ) == 1
        assert sec6_text.count("Arvo-osuustilille, jolle jo on kirjattu panttaus") == 1


def test_materialize_pit_preserves_base_schedules() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="body"),))
    schedule = IRNode(kind=IRNodeKind.SCHEDULE, label="1", text="schedule text")
    base = IRStatute(
        statute_id="test/schedules",
        title="Schedules",
        body=body,
        supplements=(schedule,),
    )
    timelines = compile_timelines(base, [], base_date="2020-01-01")
    pit = materialize_pit(timelines, "2025-01-01", base=base)

    assert len(pit.supplements) == 1


def test_materialize_pit_drops_zero_day_repeal_placeholder_under_detached_horizon() -> None:
    def _find_section(node: IRNode, label: str) -> IRNode | None:
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label == label:
                return child
            found = _find_section(child, label)
            if found is not None:
                return found
        return None

    base = IRStatute(
        statute_id="test/zero-day-repeal",
        title="Zero-day repeal",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2019-12-19",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={
                            "lawvm_repeal_placeholder": "1",
                            _MATERIALIZE_AS_ABSENT_UNDER_DETACHED_HORIZON_ATTR: "1",
                        },
                        children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                    ),
                    source=OperationSource(
                        statute_id="2019/1",
                        enacted="2019-12-19",
                        effective="2020-01-01",
                    ),
                ),
            ],
        )
    }

    pit = materialize_pit(
        timelines,
        "9999-12-31",
        base=base,
        expires_as_of="2023-10-01",
    )

    assert _find_section(pit.body, "1") is None


def test_materialize_pit_keeps_non_zero_day_repeal_placeholder_visible_under_detached_horizon() -> None:
    def _find_section(node: IRNode, label: str) -> IRNode | None:
        for child in node.children:
            if child.kind is IRNodeKind.SECTION and child.label == label:
                return child
            found = _find_section(child, label)
            if found is not None:
                return found
        return None

    base = IRStatute(
        statute_id="test/permanent-repeal-placeholder",
        title="Permanent placeholder",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Base 1 §"),
                ),
                ProvisionVersion(
                    effective="2024-01-01",
                    enacted="2023-04-14",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        attrs={"lawvm_repeal_placeholder": "1"},
                        children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),),
                    ),
                    source=OperationSource(
                        statute_id="2023/741",
                        enacted="2023-04-14",
                        effective="2024-01-01",
                    ),
                ),
            ],
        )
    }

    pit = materialize_pit(
        timelines,
        "9999-12-31",
        base=base,
        expires_as_of="2024-01-01",
    )

    section = _find_section(pit.body, "1")
    assert section is not None
    assert section.attrs.get("lawvm_repeal_placeholder") == "1"


def test_build_replay_products_accepts_temporal_events_for_materialization() -> None:
    ctx = StatuteContext(
        id="test/temporal-products",
        title="Temporal replay products",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:fi-replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2005-01-01",
                effective="2005-01-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-products",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="fi:commence",
                group_id="g:fi-replay",
                kind="commence",
                scope=TemporalScope(target_statute="test/temporal-products"),
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-products:source",
                    raw_text="commence",
                    effective="2010-01-01",
                ),
            ),
        ),
    )

    assert products.timelines is not None
    assert len(products.temporal_events) == 1
    assert products.temporal_events[0].source is not None
    assert products.temporal_events[0].source.statute_id == "test/temporal-products:source"
    assert products.temporal_events[0].source.effective == "2010-01-01"
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.effective == "2010-01-01"
    assert products.materialized_state.ir.children[0].text == "Updated"


def test_build_replay_products_requires_explicit_effective_date_for_derived_temporal_events() -> None:
    ctx = StatuteContext(
        id="test/temporal-products-no-fallback",
        title="Temporal replay products without fallback",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:fi-replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2005-01-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-products-no-fallback",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
    )

    assert products.temporal_events == ()
    assert products.materialized_state.ir.children[0].text == "Base"


def test_build_replay_products_merges_existing_temporal_events_with_synthesized_ops() -> None:
    ctx = StatuteContext(
        id="test/temporal-products-merge",
        title="Temporal replay products merge",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:replay",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2009-01-01",
                effective="2010-01-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-products-merge",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        as_of="2011-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="other:set_applicability",
                group_id="g:other",
                kind="set_applicability",
                scope=TemporalScope(target_statute="test/temporal-products-merge"),
                source=OperationSource(statute_id="test/temporal-products-merge:other"),
            ),
        ),
    )

    assert len(products.temporal_events) == 2
    assert products.materialized_state.ir.children[0].text == "Updated"


def test_retarget_root_node_preserves_existing_num_suffix_for_section() -> None:
    source_node = IRNode(
        kind=IRNodeKind.SECTION,
        label="10",
        text="10 § old ten",
        children=(IRNode(kind=IRNodeKind.NUM, text="10 §"),),
    )

    retargeted = _retarget_root_node(
        source_node,
        LegalAddress(path=(("section", "11"),)),
    )

    assert retargeted.label == "11"
    assert retargeted.children[0].text == "11 §"
    assert retargeted.text == "10 § old ten"


def test_build_replay_products_carries_migration_events() -> None:
    ctx = StatuteContext(
        id="test/migration-products",
        title="Migration replay products",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    migration_event = MigrationEvent(
        event_id="mig:test/1:section:1→section:2",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "1"),)),
        to_address=LegalAddress(path=(("section", "2"),)),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/migration-products",
        replay_fold_state=replay_fold_state,
        lo_ops_out=[],
        migration_events=(migration_event,),
    )

    assert products.migration_events == (migration_event,)

def test_rekey_timelines_prefers_destination_native_lineage_over_migrated_source_history() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § old lineage"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § native lineage"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/section:5→section:159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {destination_addr}
    destination_versions = rekeyed[destination_addr].versions
    assert len(destination_versions) == 1
    assert destination_versions[0].content is not None
    assert destination_versions[0].content.text == "159 § native lineage"


def test_rekey_timelines_walks_migration_chains_across_distinct_waves_regardless_of_input_order() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    final_addr = LegalAddress(path=(("section", "159a"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="old lineage"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
    }
    first = MigrationEvent(
        event_id="mig:test/section:5→section:159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )
    second = MigrationEvent(
        event_id="mig:test/section:159→section:159a",
        kind="renumber",
        from_address=destination_addr,
        to_address=final_addr,
        effective="2021-01-01",
        source_statute="2021/1",
    )

    forward = _rekey_timelines_with_migration_events(
        timelines,
        (first, second),
        as_of="2025-01-01",
    )
    reverse = _rekey_timelines_with_migration_events(
        timelines,
        (second, first),
        as_of="2025-01-01",
    )

    assert set(forward) == {final_addr}
    assert set(reverse) == {final_addr}


def test_rekey_timelines_native_rebirth_same_wave_chain_does_not_double_migrate() -> None:
    source_addr = LegalAddress(path=(("section", "10"),))
    destination_addr = LegalAddress(path=(("section", "11"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="10 § old lineage"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="1992-10-01",
                    enacted="1992-10-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="10 § native rebirth"),
                    source=OperationSource(statute_id="1992/878", effective="1992-10-01"),
                ),
            ],
        ),
    }
    same_wave = (
        MigrationEvent(
            event_id="mig:test/9-10",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "9"),)),
            to_address=LegalAddress(path=(("section", "10"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
        MigrationEvent(
            event_id="mig:test/10-11",
            kind="renumber",
            from_address=source_addr,
            to_address=destination_addr,
            effective="1992-10-01",
            source_statute="1992/878",
        ),
        MigrationEvent(
            event_id="mig:test/11-12",
            kind="renumber",
            from_address=destination_addr,
            to_address=LegalAddress(path=(("section", "12"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        same_wave,
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {source_addr, destination_addr}
    destination_versions = rekeyed[destination_addr].versions
    assert len(destination_versions) == 1
    assert destination_versions[0].content is not None
    assert destination_versions[0].content.label == "11"
    assert destination_versions[0].source is None

    source_versions = rekeyed[source_addr].versions
    assert len(source_versions) == 1
    assert source_versions[0].content is not None
    assert source_versions[0].content.label == "10"
    assert source_versions[0].source is not None
    assert source_versions[0].source.statute_id == "1992/878"


def test_rekey_timelines_same_wave_incoming_prefix_does_not_double_migrate_sibling_source() -> None:
    address = LegalAddress(path=(("part", "7"), ("chapter", "32"), ("section", "268")))
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                ProvisionVersion(
                    effective="2020-06-01",
                    enacted="2018-08-10",
                    content=IRNode(kind=IRNodeKind.SECTION, label="268", text="268 §"),
                    source=OperationSource(statute_id="2018/731", effective="2020-06-01"),
                ),
            ],
        ),
    }
    same_wave = (
        MigrationEvent(
            event_id="mig:2019/371:part6-part7",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "6"),)),
            to_address=LegalAddress(path=(("part", "7"),)),
            effective="2019-04-01",
            source_statute="2019/371",
        ),
        MigrationEvent(
            event_id="mig:2019/371:part7-part8",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "7"),)),
            to_address=LegalAddress(path=(("part", "8"),)),
            effective="2019-04-01",
            source_statute="2019/371",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        same_wave,
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {address}


def test_rekey_timelines_post_renumber_descendant_stays_with_native_source_lineage() -> None:
    source_addr = LegalAddress(path=(("section", "10"), ("subsection", "2")))
    destination_addr = LegalAddress(path=(("section", "11"), ("subsection", "2")))
    timelines = {
        LegalAddress(path=(("section", "10"),)): ProvisionTimeline(
            address=LegalAddress(path=(("section", "10"),)),
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="historical section 10"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="1992-10-01",
                    enacted="1992-10-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="10", text="native rebirth section 10"),
                    source=OperationSource(statute_id="1992/878", effective="1992-10-01"),
                ),
            ],
        ),
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="1992-12-09",
                    enacted="1992-12-09",
                    content=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="native descendant"),
                    source=OperationSource(statute_id="1992/1195", effective="1992-12-09"),
                ),
            ],
        ),
    }
    same_wave = (
        MigrationEvent(
            event_id="mig:test/10-11",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "10"),)),
            to_address=LegalAddress(path=(("section", "11"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
        MigrationEvent(
            event_id="mig:test/11-12",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "11"),)),
            to_address=LegalAddress(path=(("section", "12"),)),
            effective="1992-10-01",
            source_statute="1992/878",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        same_wave,
        as_of="2025-01-01",
    )

    assert set(rekeyed) == {
        LegalAddress(path=(("section", "10"),)),
        LegalAddress(path=(("section", "11"),)),
        source_addr,
    }
    versions = rekeyed[source_addr].versions
    assert len(versions) == 1
    assert versions[0].content is not None
    assert versions[0].content.label == "2"
    assert destination_addr not in rekeyed

def test_rekey_timelines_rewrites_root_num_child_for_migrated_section() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="5",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="5 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Heading"),
                        ),
                    ),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/section:5→section:159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    migrated = rekeyed[destination_addr].versions[0].content
    assert migrated is not None
    assert migrated.label == "159"
    assert migrated.children[0].text == "159 §"


def test_rekey_timelines_merges_ancestor_only_migration_into_native_destination_lineage() -> None:
    source_addr = LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "159")))
    destination_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-12-30",
                    enacted="2020-12-30",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="159",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="159 §"),
                            IRNode(kind=IRNodeKind.HEADING, text="Updated heading"),
                        ),
                    ),
                    source=OperationSource(statute_id="2020/1256", effective="2020-12-30"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="native lineage"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/III/2/159→IV/18/159",
        kind="renumber",
        from_address=LegalAddress(path=(("part", "III"), ("chapter", "2"))),
        to_address=LegalAddress(path=(("part", "4"), ("chapter", "18"))),
        effective="2020-12-30",
        source_statute="2020/1256",
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    destination_versions = rekeyed[destination_addr].versions
    assert len(destination_versions) == 2
    assert destination_versions[-1].source is not None
    assert destination_versions[-1].source.statute_id == "2020/1256"


def test_rekey_timelines_same_wave_incoming_section_still_follows_ancestor_migration() -> None:
    source_addr = LegalAddress(path=(("part", "3"), ("chapter", "2"), ("section", "159")))
    destination_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    part_addr = LegalAddress(path=(("part", "3"),))
    timelines = {
        part_addr: ProvisionTimeline(
            address=part_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.PART, label="3", text="part 3 before"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.PART, label="3", text="part 3 same-wave version"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                ),
            ],
        ),
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § migrated same-wave section"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                ),
            ],
        ),
    }
    events = (
        MigrationEvent(
            event_id="mig:2019/371:part3/chapter2/section5-section159",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "3"), ("chapter", "2"), ("section", "5"))),
            to_address=source_addr,
            effective="2019-04-01",
            source_statute="2019/371",
        ),
        MigrationEvent(
            event_id="mig:2019/371:part3-part4",
            kind="renumber",
            from_address=part_addr,
            to_address=LegalAddress(path=(("part", "4"),)),
            effective="2019-04-01",
            source_statute="2019/371",
        ),
        MigrationEvent(
            event_id="mig:2020/1256:part4/chapter2-chapter18",
            kind="renumber",
            from_address=LegalAddress(path=(("part", "4"), ("chapter", "2"))),
            to_address=LegalAddress(path=(("part", "4"), ("chapter", "18"))),
            effective="2021-02-01",
            source_statute="2020/1256",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        events,
        as_of="2026-01-01",
    )

    assert destination_addr in rekeyed
    assert source_addr not in rekeyed
    versions = rekeyed[destination_addr].versions
    assert len(versions) == 1
    assert versions[0].source is not None
    assert versions[0].source.statute_id == "2019/371"


def test_select_pit_lineage_inputs_prefers_rekeyed_native_rebirth_over_scope_changing_migration() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    raw_destination_addr = LegalAddress(path=(("part", "I"), ("chapter", "2"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § historical lineage"),
                    source=None,
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native rebirth"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        raw_destination_addr: ProvisionTimeline(
            address=raw_destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native destination lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/ch1-5→partI-ch2-5",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "I"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert len(migration_event.from_address.path) != len(migration_event.to_address.path)
    assert lineage_decision.timelines is rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == ()
    assert lineage_decision.lineage_plan.mode == "rekeyed_only"
    assert lineage_decision.reason == "native_rebirth_after_renumber"
    assert set(lineage_decision.timelines) == {source_addr, destination_addr}

    source_versions = lineage_decision.timelines[source_addr].versions
    assert len(source_versions) == 1
    assert source_versions[0].source is not None
    assert source_versions[0].source.statute_id == "2020/1"

    destination_versions = lineage_decision.timelines[destination_addr].versions
    assert len(destination_versions) == 2
    assert destination_versions[0].source is None
    assert destination_versions[1].source is not None
    assert destination_versions[1].source.statute_id == "2019/1"


def test_select_pit_lineage_inputs_keeps_rekeyed_with_migrations_for_leaf_stable_scope_renumber() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    raw_destination_addr = LegalAddress(path=(("part", "I"), ("chapter", "2"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § migrated lineage"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        raw_destination_addr: ProvisionTimeline(
            address=raw_destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native destination lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/ch1-5→partI-ch2-5",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "I"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert lineage_decision.timelines is rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "leaf_stable_scope_renumber"
    assert set(lineage_decision.timelines) == {destination_addr}

    active = select_active_version(lineage_decision.timelines[destination_addr], as_of="2025-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2020/1"
    assert active.content is not None
    assert "migrated lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_keeps_rekeyed_with_migrations_for_noncolliding_scope_renumber() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="migrated lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:5->2/7",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    assert lineage_decision.timelines is rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "default_migration_projection"
    assert set(lineage_decision.timelines) == {destination_addr}

    active = select_active_version(lineage_decision.timelines[destination_addr], as_of="2002-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2001/1"
    assert active.content is not None
    assert active.content.label == "7"
    assert "migrated lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_keeps_rekeyed_with_migrations_for_noncolliding_scope_move() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    assert lineage_decision.timelines is rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "default_migration_projection"
    assert set(lineage_decision.timelines) == {destination_addr}

    active = select_active_version(lineage_decision.timelines[destination_addr], as_of="2002-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2001/1"
    assert active.content is not None
    assert active.content.label == "7"
    assert "moved lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_ignores_future_scope_move_when_choosing_current_pit_lineage_inputs() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:future",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2005-01-01",
        source_statute="2005/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2004-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2004-01-01",
    )

    assert lineage_decision.timelines is rekeyed_timelines
    assert lineage_decision.timeline_source == "rekeyed"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "rekeyed_with_migrations"
    assert lineage_decision.reason == "default_migration_projection"
    assert set(lineage_decision.timelines) == {source_addr}

    active = select_active_version(lineage_decision.timelines[source_addr], as_of="2004-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2001/1"
    assert active.content is not None
    assert "moved lineage" in irnode_to_text(active.content)


def test_select_pit_lineage_inputs_reports_destination_occupancy_collision_for_scope_move() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="1999-01-01",
                    enacted="1999-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="native destination lineage"),
                    source=OperationSource(statute_id="1999/1", effective="1999-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:occupied",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    assert lineage_decision.timelines is raw_timelines
    assert lineage_decision.timeline_source == "raw"
    assert lineage_decision.lineage_plan.migration_events == (migration_event,)
    assert lineage_decision.lineage_plan.mode == "raw_with_migrations"
    assert lineage_decision.reason == "destination_occupancy_collision"


def test_classify_finland_lineage_bridge_reports_native_rebirth_after_renumber() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § old lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native rebirth"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(address=destination_addr, versions=[]),
    }
    migration_event = MigrationEvent(
        event_id="mig:test/ch1-5→part1-ch2-5",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "1"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    classification = _classify_finland_lineage_bridge(
        raw_timelines,
        (migration_event,),
        as_of="2025-01-01",
    )

    assert classification == FinlandLineageBridgeClassification(
        native_rebirth_after_renumber=True,
        leaf_stable_scope_renumber=True,
        active_scope_changing=True,
        noncolliding_scope_migrations=False,
        destination_occupancy_collision=True,
    )


def test_replay_xml_exposes_finland_lineage_bridge_classification() -> None:
    replay = pinned_replay("2009/953", mode="legal_pit")

    assert replay.materialization_spec is not None
    assert replay.materialization_spec.bridge_classification == FinlandLineageBridgeClassification(
        native_rebirth_after_renumber=True,
        leaf_stable_scope_renumber=False,
        active_scope_changing=False,
        noncolliding_scope_migrations=False,
        destination_occupancy_collision=False,
    )


def test_materialize_pit_ex_rejects_both_lineage_plan_and_migration_events() -> None:
    addr = LegalAddress(path=(("section", "5"),))
    migration_event = MigrationEvent(
        event_id="mig:test:5->7",
        kind="move",
        from_address=addr,
        to_address=LegalAddress(path=(("section", "7"),)),
        effective="2001-01-01",
        source_statute="2001/1",
    )
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="text"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
    }

    with pytest.raises(ValueError, match="either lineage_plan or migration_events"):
        materialize_pit_ex(
            timelines,
            "2002-01-01",
            migration_events=(migration_event,),
            lineage_plan=MaterializationLineagePlan(
                mode="raw_with_migrations",
                migration_events=(migration_event,),
            ),
        )


def test_lineage_plan_round_trips_core_materialize_for_destination_occupancy_collision() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    raw_timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="1999-01-01",
                    enacted="1999-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="native destination lineage"),
                    source=OperationSource(statute_id="1999/1", effective="1999-01-01"),
                ),
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:occupied",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )
    rekeyed_timelines = _rekey_timelines_with_migration_events(
        raw_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )
    lineage_decision = _select_pit_lineage_inputs(
        raw_timelines,
        rekeyed_timelines,
        (migration_event,),
        as_of="2002-01-01",
    )

    result = materialize_pit_ex(
        lineage_decision.timelines,
        "2002-01-01",
        base=IRStatute(
            statute_id="test/occupancy-roundtrip",
            title="Occupancy roundtrip",
            body=IRNode(
                kind=IRNodeKind.BODY,
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="5", text="base source"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="7", text="base destination"),),
                    ),
                ),
            ),
        ),
        lineage_plan=lineage_decision.lineage_plan,
    )

    assert result.status == "degraded_missing_scope"
    assert result.certificate is not None
    assert result.certificate.ambiguous_address_count == 1


def test_build_replay_products_requires_caller_to_lower_temporal_events() -> None:
    """Callers must lower effect intents before calling build_replay_products.

    The temporal phase boundary is now explicit: the caller owns the lowering
    step; build_replay_products only accepts already-lowered temporal_events.
    """
    import datetime as dt

    from lawvm.core.effect_intent import Commencement
    from lawvm.core.effect_lowering import lower_effect_intents_to_temporal_events

    ctx = StatuteContext(
        id="test/effect-intents-caller-lowers",
        title="Caller lowers effect intents",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    intent = Commencement(
        effective_date=dt.date(2010, 1, 1),
        raw_text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2010.",
    )

    # Caller explicitly lowers before calling build_replay_products
    lowered_events = tuple(
        lower_effect_intents_to_temporal_events(
            [intent],
            source_ref="test/effect-intents-caller-lowers",
            source_title="Caller lowers effect intents",
            source_issue_date=dt.date(2009, 1, 1),
            source_effective_date=dt.date(2010, 1, 1),
            group_id_prefix="explicit-lowering",
            target_statute="test/effect-intents-caller-lowers",
        )
    )

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/effect-intents-caller-lowers",
        replay_fold_state=replay_fold_state,
        lo_ops_out=[],
        as_of="2011-01-01",
        temporal_events=lowered_events,
    )

    assert len(products.temporal_events) == 1
    assert products.temporal_events[0].kind == "commence"
    assert products.temporal_events[0].source is not None
    assert products.temporal_events[0].source.title == "Caller lowers effect intents"
    assert products.temporal_events[0].source.enacted == "2009-01-01"
    assert products.temporal_events[0].source.effective == "2010-01-01"


def test_build_replay_products_can_enforce_strict_johto_temporal_for_mismatch() -> None:
    ctx = StatuteContext(
        id="test/temporal-default-strict",
        title="Temporal default strict",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="finland-johto:1999/1",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2010-01-01",
                effective="2010-06-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-default-strict",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        temporal_events=(
            TemporalEvent(
                event_id="ev:different-group",
                group_id="finland-johto:2000/2",
                kind="commence",
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-default-strict",
                    effective="2010-01-01",
                ),
                scope=TemporalScope(target_statute="test/temporal-default-strict"),
            ),
        ),
    )

    assert products.timelines is not None
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.content is not None
    assert active.content.text == "Updated"
    assert active.effective == "2010-06-01"


def test_build_replay_products_does_not_synthesize_fallback_for_covered_group() -> None:
    ctx = StatuteContext(
        id="test/temporal-covered-group",
        title="Temporal covered group",
        base_ir=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
        base_xml_bytes=b"<body/>",
    )
    replay_fold_state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    lo_ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="finland-johto:1999/1",
            source=OperationSource(
                statute_id="2010/100",
                enacted="2010-01-01",
                effective="2010-06-01",
            ),
        )
    ]

    products = build_replay_products(
        ctx=ctx,
        statute_id="test/temporal-covered-group",
        replay_fold_state=replay_fold_state,
        lo_ops_out=lo_ops,
        temporal_events=(
            TemporalEvent(
                event_id="ev:matching-group",
                group_id="finland-johto:1999/1",
                kind="commence",
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-covered-group",
                    effective="2010-01-01",
                ),
                scope=TemporalScope(target_statute="test/temporal-covered-group"),
            ),
        ),
    )

    assert len(products.temporal_events) == 1
    assert products.temporal_events[0].group_id == "finland-johto:1999/1"
    assert products.timelines is not None
    active = products.timelines[LegalAddress(path=(("section", "1"),))].versions[-1]
    assert active.effective == "2010-01-01"


def test_materialize_pit_overlays_active_schedule_versions() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="body"),))
    schedule = IRNode(
        kind=IRNodeKind.SCHEDULE,
        label="1",
        text="old schedule",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="old paragraph"),),
    )
    base = IRStatute(
        statute_id="test/schedules",
        title="Schedules",
        body=body,
        supplements=(schedule,),
    )
    timelines = compile_timelines(base, [], base_date="2020-01-01")

    schedule_addr = LegalAddress(path=(("schedule", "1"),))
    timelines[schedule_addr] = ProvisionTimeline(
        address=schedule_addr,
        versions=[
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2024-01-01",
                content=IRNode(
                    kind=IRNodeKind.SCHEDULE,
                    label="1",
                    text="new schedule",
                    children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="new paragraph"),),
                ),
                source=OperationSource(statute_id="test/act", effective="2024-01-01"),
            )
        ],
    )

    pit = materialize_pit(timelines, "2025-01-01", base=base)

    assert len(pit.supplements) == 1
    assert pit.supplements[0].text == "new schedule"
    assert pit.supplements[0].children[0].text == "new paragraph"

def test_build_replay_products_rejects_payloadless_replace_timeline_ops() -> None:
    body = IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),))
    ctx = StatuteContext(
        id="test/missing-payload",
        title="Missing payload",
        base_ir=body,
        base_xml_bytes=b"<body/>",
    )
    replay_fold = ReplayState(ir=copy.deepcopy(body))
    op = LegalOperation(
        op_id="test_replace_missing_payload",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=None,
        source=OperationSource(statute_id="2024/1", effective="2024-01-01"),
    )

    with pytest.raises(RuntimeError, match="FI_TIMELINE_PAYLOADLESS_REPLACE"):
        build_replay_products(
            ctx=ctx,
            statute_id="test/missing-payload",
            replay_fold_state=replay_fold,
            lo_ops_out=[op],
        )


@pytest.mark.parametrize(
    "statute_id",
    [
        "1974/412",  # chapter:4 duplicate from INSERT 4 luku onto existing ch4
        "1961/264",  # chapter:12 duplicate
        "1989/495",  # chapter:2 duplicate
        "1997/689",  # chapter:8 duplicate
        "2001/604",  # chapter:4 duplicate
        "2009/1698",  # chapter:9 duplicate
    ],
)
def test_chapter_insert_onto_existing_chapter_produces_no_duplicate_label(statute_id: str) -> None:
    """INSERT chapter:X onto an already-existing base chapter must merge, not duplicate.

    Regression for the legal_pit mode path where `replace_same_numbered_container_insert=False`
    previously fell through to insert_sorted, creating a duplicate chapter label.
    """
    replay = pinned_replay(statute_id, mode="legal_pit", quiet=True)
    viols = [
        dict(getattr(f, "detail", {}) or {}).get("violation", "")
        for f in replay.findings
        if dict(getattr(f, "detail", {}) or {}).get("barrier_code") == "APPLY.TREE_INVARIANT_VIOLATION"
    ]
    chapter_dups = [v for v in viols if "duplicate chapter" in v]
    assert chapter_dups == [], f"{statute_id}: unexpected duplicate chapter: {chapter_dups}"


def test_replay_xml_1974_412_preserves_1979_middle_subsection_through_1991_update() -> None:
    """1979/373 + 1991/1423 must leave chapter 4 / section 2 with moments 1-4.

    1979/373 inserts new 2 and 3 moments under 4 luvun 2 §. 1991/1423 later
    replaces moments 1 and 3 and inserts moment 4. If 1979/373 collapses to a
    chapter-level insert, replay reaches 1991 with only moment 1 and the old
    1979 middle moment disappears permanently.
    """
    replay = pinned_replay("1974/412", mode="finlex_oracle", quiet=True)

    section = replay.materialized_state.find_node("section", "2", "chapter", "4")
    assert section is not None, "chapter 4 / section 2 must exist"

    subsections = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section.children
        if child.kind == IRNodeKind.SUBSECTION and child.label
    }

    assert list(subsections) == ["1", "2", "3", "4"]
    assert subsections["2"].startswith("Asevelvollisuuslain (452/50) nojalla annetun")
    assert subsections["3"].startswith("Sotilasrikossäännösten alainen")
    assert subsections["4"].startswith("Julkisyhteisön luottamushenkilön")


def test_replay_xml_2011_756_inserts_section_8a_into_chapter_5() -> None:
    """Regression for cross-chapter INSERT remap bug.

    Amendment 2022/33 inserts section 8a into chapter 5. The master already has
    section 8a in chapter 2 (from 2016/1115). The chapter-remap logic was wrongly
    remapping the INSERT to chapter 2 because the section label existed there,
    silently dropping the new chapter:5/section:8a.

    Fix: pure-INSERT groups are exempt from the chapter-remap correction because
    they create new sections that don't yet exist in the target chapter.
    """
    replay = pinned_replay("2011/756", mode="legal_pit", quiet=True)
    body = replay.materialized_state.ir

    def _chapter_section_labels(chapter_label: str) -> list[str]:
        chapter = next(
            (child for child in body.children if child.kind.name == "CHAPTER" and child.label == chapter_label),
            None,
        )
        if chapter is None:
            return []
        return [child.label for child in chapter.children if child.kind.name == "SECTION"]

    # Section 8a should be in chapter 2 (from 2016/1115) AND in chapter 5 (from 2022/33)
    assert "8a" in _chapter_section_labels("2"), "chapter 2 must still have section 8a from 2016/1115"
    assert "8a" in _chapter_section_labels("5"), "chapter 5 must have section 8a inserted by 2022/33"


def test_replay_xml_2012_916_keeps_section_8_in_chapter_13() -> None:
    """Whole-chapter REPLACE must not drop new sections shadowed in another chapter.

    Amendment 2022/337 replaces chapter 13 and also separately replaces
    chapter 3 / section 8. The chapter REPLACE payload legitimately includes
    chapter 13 / section 8, and apply must not strip it just because another
    chapter has a same-labeled standalone section op.
    """
    replay = pinned_replay("2012/916", mode="finlex_oracle", quiet=True)

    chapter = next(
        (child for child in replay.materialized_state.ir.children if child.kind.name == "CHAPTER" and child.label == "13"),
        None,
    )
    assert chapter is not None, "chapter 13 must exist"
    section_labels = [child.label for child in chapter.children if child.kind.name == "SECTION"]
    assert "8" in section_labels, "chapter 13 must keep section 8 from 2022/337"


def test_replay_xml_2012_916_keeps_section_1_family_in_chapter_13() -> None:
    """Later degraded subsection inserts must not erase chapter 13 / section 1.

    The current tree carries accepted degraded source lanes for 2022/244 and
    2023/371 that rewrite `13 luku 1 § 1 momentti` as subsection-level insert
    snapshots with flat content rather than preserving the earlier paragraph
    structure from 2022/337. The durable replay-products contract here is that
    chapter 13 / section 1 stays in place with its subsection family intact,
    not that the original paragraph numbering survives those later source-owned
    subsection replacements.
    """
    replay = pinned_replay("2012/916", mode="finlex_oracle", quiet=True)
    chapter = next(
        (child for child in replay.materialized_state.ir.children if child.kind.name == "CHAPTER" and child.label == "13"),
        None,
    )
    assert chapter is not None, "chapter 13 must exist"
    section = next(
        (child for child in chapter.children if child.kind.name == "SECTION" and child.label == "1"),
        None,
    )
    assert section is not None, "chapter 13 / section 1 must exist"
    heading = next((child.text for child in section.children if child.kind.name == "HEADING"), "")
    assert heading == "Käyttötarkoitukset"
    subsection = next((child for child in section.children if child.kind.name == "SUBSECTION" and child.label == "1"), None)
    assert subsection is not None, "section 1 must keep subsection 1"
    subsection_labels = [child.label for child in section.children if child.kind.name == "SUBSECTION"]
    assert subsection_labels[:4] == ["1", "2", "3", "4"]
    subsection_text = " ".join(
        (child.text or "").strip()
        for child in subsection.children
        if (child.text or "").strip()
    )
    assert "Työ- ja elinkeinotoimiston asiakastietojärjestelmää käytetään" in subsection_text


def test_replay_xml_2012_916_surfaces_degraded_2023_371_subsection_lane() -> None:
    """The degraded 2023/371 subsection lane must stay explicitly owned.

    This statute currently carries accepted uncovered-body degradation for the
    `13 luku 1 § 1 momentti 4 kohta` lane: replay keeps the section family
    alive, but the subsection-targeted follow-up is not a clean deterministic
    apply path. Future runs should not have to rediscover that from scratch.
    """
    replay = pinned_replay("2012/916", mode="finlex_oracle", quiet=True)

    pathology_rows = [
        row
        for row in replay.source_pathology_rows()
        if row.get("source_statute") == "2023/371"
    ]
    assert any(
        row.get("code") == "ITEM_TARGET_STRUCTURE_ABSENT"
        and row.get("target_label") == "1 § 1 mom 4 kohta"
        for row in pathology_rows
    )

    degraded_findings = [
        finding
        for finding in replay.findings
        if finding.kind == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
        and finding.source_statute == "2023/371"
    ]
    assert degraded_findings, "2023/371 degraded uncovered-body lane must stay visible"

    failed_ops = [
        finding
        for finding in replay.findings
        if finding.kind == "APPLY.FAILED_OPERATION"
        and (finding.detail or {}).get("amendment_id") == "2023/371"
    ]
    assert any(
        (finding.detail or {}).get("reason_code") == "no_deterministic_path"
        and (finding.detail or {}).get("target_section") == "1"
        and (finding.detail or {}).get("target_chapter") == "13"
        for finding in failed_ops
    )


def test_replay_xml_1995_370_does_not_leave_stale_cross_chapter_23_snapshots() -> None:
    """Cross-chapter same-label chapter snapshots must not preserve stale 23 § families."""
    replay = pinned_replay("1995/370", mode="finlex_oracle", quiet=True)

    section_paths = {str(addr) for addr in replay.timelines if "section:23" in str(addr)}

    assert "chapter:5/section:23" not in section_paths
    assert "chapter:3/section:23" not in section_paths
    assert "chapter:6/section:23" in section_paths


def test_replay_xml_1997_1339_anaphoric_pykala_ill_with_provenance_qualifier() -> None:
    """Regression for anaphoric 'pykälään, sellaisena kuin se on ..., uusi N momentti/kohta'.

    Amendment 2015/1752 to 1997/1339 (kirjanpitoasetus) has a lisätään clause whose last
    target is '5 a lukuun uusi 2 §'. The clause also contains 'pykälään, sellaisena kuin
    se on osaksi asetuksissa 748/2001 ja 1313/2004, uusi 7 momentti' — an anaphoric section
    reference with an interleaved provenance qualifier (COMMA + CITATION_SPAN before 'uusi').

    The parser previously stopped at 'pykälään' because the handler only handled
    'pykälään uusi N' (no qualifier), not 'pykälään , [CITE] uusi N'. Once that pattern
    broke the parse chain, all subsequent targets including '5 a lukuun uusi 2 §' were
    silently dropped, leaving chapter 5a with only §1 and missing §2.
    """
    replay = pinned_replay("1997/1339", mode="finlex_oracle", quiet=True)
    ch5a = replay.materialized_state.find_chapter("5a")
    assert ch5a is not None, "chapter 5a must be present in replay"
    sec_labels = [c.label for c in ch5a.children if c.kind.name == "SECTION"]
    assert "2" in sec_labels, "§2 must be inserted into chapter 5a by 2015/1752"


def test_replay_xml_2020_87_inserts_subsection_into_existing_section() -> None:
    """Regression for Pattern B3: '4 §:n uusi 2 momentti' must insert a new subsection
    into section 4 rather than replacing the whole section (which would lose subsection 1).

    Amendment 2020/326 uses the pattern '§:GEN uusi N momentti:NOM' — genitive case on
    the section reference followed immediately by 'uusi'.  The parser previously missed
    this pattern and emitted a whole-section L op (momentti=0) instead of a subsection
    INSERT (momentti=2), causing subsection 1 to be overwritten.
    """
    replay = pinned_replay("2020/87", mode="finlex_oracle", quiet=True)
    sec4 = replay.materialized_state.find_section("4")
    assert sec4 is not None, "section 4 must be present in replay"
    sub_labels = [
        child.label
        for child in sec4.children
        if child.kind.name == "SUBSECTION"
    ]
    assert "1" in sub_labels, "subsection 1 must survive after 2020/326 inserts subsection 2"
    assert "2" in sub_labels, "subsection 2 must be inserted by 2020/326"


def test_replay_xml_2017_571_inserts_second_subsection_into_2002_1244_section_1() -> None:
    """DOC:ILL + provenance must still insert subsection 2 into existing section 1.

    2017/571 says ``lisätään asetukseen, sellaisena kuin se on asetuksessa
    543/2015 uusi 1 §:n 2 momentti``. The DOC:ILL parser path previously failed
    to skip the comma+provenance span and degraded this into a whole-section
    insert, wiping the older subsection chain in replay.
    """
    replay = pinned_replay("2002/1244", mode="finlex_oracle", quiet=True)
    sec1 = replay.materialized_state.find_section("1")
    assert sec1 is not None, "section 1 must be present in replay"
    sub_labels = [
        child.label
        for child in sec1.children
        if child.kind.name == "SUBSECTION"
    ]
    assert "1" in sub_labels, "subsection 1 must survive after 2017/571 inserts subsection 2"
    assert "2" in sub_labels, "subsection 2 must be inserted by 2017/571"


def test_replay_xml_1998_986_inserts_provenance_qualified_plural_subsections_into_section_22() -> None:
    """Plural `uusi N ja M momentti` must survive partial PEG success on mixed clauses.

    Amendment 2005/865 to 1998/986 says:
    `muutetaan 22 §:n 1 momentti sekä lisätään 22 §:ään, sellaisena kuin se on
    osaksi ... 693/2003, uusi 5 ja 6 momentti`.

    The PEG/legal-op path currently recovers the replace for `1 momentti` but can
    miss the coordinated inserts `5` and `6`. The fallback insert heuristic must
    be allowed to add those missing subsection INSERT ops without discarding the
    PEG-produced replace.
    """
    replay = pinned_replay("1998/986", mode="finlex_oracle", quiet=True)
    sec22 = replay.materialized_state.find_section("22")
    assert sec22 is not None, "section 22 must be present in replay"
    sub_labels = [
        child.label
        for child in sec22.children
        if child.kind.name == "SUBSECTION"
    ]
    assert "5" in sub_labels, "subsection 5 must be inserted by 2005/865"
    assert "6" in sub_labels, "subsection 6 must be inserted by 2005/865"


def test_replay_xml_2020_811_inserts_4a_and_11a_sections() -> None:
    """Authority-attributed finite-verb insertions must not be dropped pre-Phase-2.

    2021/278 and 2021/407 are phrased as ``Verohallinto lisää ... uuden N §:n``.
    The replay ingress used an operative-keyword guard before
    ``normalize_and_compile_ops()`` and previously omitted finite present
    ``lisää``, so both amendments were silently skipped and sections 11a and 4a
    never entered replay.
    """

    replay = pinned_replay("2020/811", mode="finlex_oracle")

    assert replay.find_section("4a") is not None, "2021/407 must insert section 4a"
    assert replay.find_section("11a") is not None, "2021/278 must insert section 11a"
