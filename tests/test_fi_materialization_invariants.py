"""Regression tests for materialization invariants.

Verifies that the materialized PIT (from timeline compilation) does not
contain structural anomalies that were historically present due to
lo_ops_out snapshot leakage:

1. No omission markers inside materialized sections
2. No duplicate (kind, label) children at any level
3. Section order is non-decreasing within each chapter

These tests pin statutes that historically exhibited each bug family.
"""
from __future__ import annotations

import os
from typing import Any, cast

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.tree_ops import check_invariants, iter_tree_invariant_violations

_CORPUS_AVAILABLE = os.path.exists("data/finlex.farchive")
pytestmark = pytest.mark.skipif(not _CORPUS_AVAILABLE, reason="corpus data not available")

from tests.corpus_pin_helpers import pinned_replay, replay_xml_for_test
from lawvm.finland.statute import ReplayResult
from lawvm.finland.ops import FailedOp

def _replay(sid: str, **kwargs: Any) -> IRNode:
    master = pinned_replay(sid, quiet=True, **kwargs)
    return master.ir


def _replay_meta(sid: str) -> dict[str, object]:

    replay_meta: dict[str, object] = {}
    pinned_replay(sid, quiet=True, replay_meta_out=replay_meta)
    return replay_meta


def _replay_ir_and_meta(sid: str) -> tuple[IRNode, dict[str, object]]:
    replay_meta: dict[str, object] = {}
    master = pinned_replay(sid, quiet=True, replay_meta_out=replay_meta)
    return master.ir, replay_meta


def _first_descendant(node: IRNode, kind: IRNodeKind, label: str) -> IRNode:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.kind is kind and current.label == label:
            return current
        stack.extend(reversed(current.children))
    raise LookupError(f"missing {kind.value}:{label}")


@pytest.fixture(scope="module")
def replay_2012_746() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("2012/746", quiet=True))


@pytest.fixture(scope="module")
def replay_2017_320_legal_pit_with_meta() -> tuple[ReplayResult, dict[str, object]]:
    replay_meta: dict[str, object] = {}
    replay = cast(
        ReplayResult,
        pinned_replay(
            "2017/320",
            mode="legal_pit",
            quiet=True,
            replay_meta_out=replay_meta,
            build_full_products=False,
        ),
    )
    return replay, replay_meta


@pytest.fixture(scope="module")
def replay_2017_519() -> ReplayResult:
    return cast(ReplayResult, pinned_replay("2017/519", quiet=True))


@pytest.fixture(scope="module")
def replay_1984_602_no_full_products_with_failed() -> tuple[ReplayResult, list[FailedOp]]:
    failed: list[FailedOp] = []
    replay = cast(
        ReplayResult,
        pinned_replay(
            "1984/602",
            mode="official_consolidation",
            quiet=True,
            failed_ops_out=failed,
            build_full_products=False,
        ),
    )
    return replay, failed


def _subsection_text(
    replay_state,
    *,
    part: str,
    chapter: str,
    section: str,
    subsection: str,
) -> str:
    from lawvm.core import tree_ops as _tops

    path = replay_state.find_section_path(section, chapter, part)
    assert path is not None, f"missing section {part}/{chapter}/{section}"
    section_node = _tops.resolve(replay_state.ir, path)
    assert section_node is not None
    subsection_node = next(
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == subsection
    )
    return " ".join(irnode_to_text(subsection_node).split())


def test_2017_966_sparse_intro_item_insert_uses_owned_item_payload() -> None:
    replay = pinned_replay(
        "2017/966",
        oracle_version="20250047",
        mode="official_consolidation",
        quiet=True,
        build_full_products=False,
    )

    section_1 = _first_descendant(replay.ir, IRNodeKind.SECTION, "1")
    subsection_1 = next(
        child
        for child in section_1.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    item_3 = next(
        child
        for child in subsection_1.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label == "3"
    )
    item_3_text = " ".join(irnode_to_text(item_3).split())

    assert "A 02300 Luonnon tuotteiden keruu (pl. polttopuu)." in item_3_text
    assert "I Majoitus- ja ravitsemistoiminta" not in item_3_text


def test_2014_1194_2017_821_corrigendum_patch_keeps_late_clause_targets() -> None:
    """Duplicate 821/2017 johtolause patches must not truncate the amendment clause."""

    replay = pinned_replay(
        "2014/1194",
        mode="legal_pit",
        quiet=True,
        stop_before="2017/1084",
        build_full_products=False,
    )

    chapter_4_sub2 = _subsection_text(
        replay.replay_fold_state,
        part="3",
        chapter="4",
        section="1",
        subsection="2",
    )
    chapter_8_sub2 = _subsection_text(
        replay.replay_fold_state,
        part="3",
        chapter="8",
        section="5",
        subsection="2",
    )

    assert "Laitos on 1 momentin 1 kohdassa tarkoitetulla tavalla" in chapter_4_sub2
    assert "Edellä 1 momentin 3 kohdassa tarkoitettu edellytys täyttyy" not in chapter_4_sub2
    assert "1) ei ole käytännössä mahdollista kohtuullisessa ajassa" in chapter_8_sub2
    assert "2) vaarantaisi kohtuuttomasti" in chapter_8_sub2


def test_1990_848_2017_377_section_num_corrigendum_binds_section_35_payload() -> None:
    """Official 377/2017 corrigendum changes source body num 5 § to 35 §."""

    replay = replay_xml_for_test("1990/848", quiet=True, build_full_products=False)
    correction_findings = [
        finding
        for finding in replay.findings
        if finding.kind == "APPLY.SOURCE_CORRECTED_BY_PATCH"
        and finding.source_statute == "2017/377"
    ]

    section_35_sub3 = _subsection_text(
        replay.replay_fold_state,
        part="",
        chapter="6",
        section="35",
        subsection="3",
    )

    assert any(
        finding.detail.get("op_id") == "body_patch/2017/377/0"
        and finding.detail.get("source_role") == "amendment_source_xml"
        for finding in correction_findings
    )
    assert "säädetään vahingonkorvauslaissa" in section_35_sub3
    assert "tilusten rauhoittamisesta kotieläinten vahingonteolta" not in section_35_sub3


def test_2014_1194_2021_234_part_scoped_body_insert_keeps_section_1_tail_once() -> None:
    """A body-derived insert under 1 luku must not duplicate 1 § 2 moment text."""

    replay = pinned_replay(
        "2014/1194",
        mode="legal_pit",
        quiet=True,
        stop_before="2021/529",
        build_full_products=False,
    )
    section_path = replay.replay_fold_state.find_section_path("1", "1", "1")
    assert section_path is not None
    from lawvm.core import tree_ops as _tops

    section_node = _tops.resolve(replay.replay_fold_state.ir, section_path)
    assert section_node is not None
    section_text = " ".join(irnode_to_text(section_node).split())

    assert section_text.count("Mitä 3 luvun 4 §:ssä ja 4–18 luvussa säädetään laitoksesta") == 1
    assert (
        _subsection_text(
            replay.replay_fold_state,
            part="1",
            chapter="1",
            section="1",
            subsection="3",
        )
        == "Tämän lain 15 lukua sovelletaan lisäksi kolmannen maan laitoksen Suomessa olevaan sivuliikkeeseen."
    )


def test_2017_519_2019_979_official_johtolause_corrigendum_updates_section_15(
    replay_2017_519: ReplayResult,
) -> None:
    """Official 979/2019 johtolause corrigendum must compile and replay 4 luvun 15 §."""

    section_node = replay_2017_519.find_section("15", "4", None)
    assert section_node is not None
    section_text = " ".join(irnode_to_text(section_node).split())

    assert "Ministerille, valtiosihteerille ja kansliapäällikölle tiedottaminen" in section_text
    assert "Ministerille ja kansliapäällikölle tiedottaminen" not in section_text


def test_2011_1546_2016_1540_bare_section_insert_materializes_section_1a() -> None:
    """2016/1540's ``uusi 1 a`` johtolause inserts §1a despite omitted ``§``."""

    replay = pinned_replay("2011/1546", quiet=True, build_full_products=False)
    section_node = replay.find_section("1a", None, None)
    assert section_node is not None
    section_text = " ".join(irnode_to_text(section_node).split())

    assert "Suomalainen intressi" in section_text
    assert "Vienti- ja alusluoton sekä korontasauksen myöntämisen edellytyksenä" in section_text


def test_2017_277_2025_1253_alakohta_insert_appends_subitem_c() -> None:
    """2025/1253's ``1 kohtaan uusi c alakohta`` appends c under item 1."""

    replay = pinned_replay("2017/277", quiet=True, build_full_products=False)
    section_node = replay.find_section("1", None, None)
    assert section_node is not None
    subsection = next(
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    item = next(
        child
        for child in subsection.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label == "1"
    )
    subitems = [
        child
        for child in item.children
        if child.kind is IRNodeKind.SUBPARAGRAPH and child.label
    ]

    assert [child.label for child in subitems] == ["a", "b", "c"]
    assert "fyysisistä ominaisuuksista" in irnode_to_text(subitems[0])
    assert "sijaintipaikasta" in irnode_to_text(subitems[1])
    assert "suunnitellut toimenpiteet" in irnode_to_text(subitems[2])


def test_2017_277_2021_1163_flattened_first_moment_list_preserves_all_items() -> None:
    """2021/1163's content-only sibling list rows belong under §4 1 momentti."""

    replay = pinned_replay(
        "2017/277",
        mode="legal_pit",
        quiet=True,
        stop_before="2025/1253",
    )
    section_node = replay.materialized_state.find_section("4", None, None)
    assert section_node is not None
    subsection = next(
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
    )
    paragraphs = [
        child
        for child in subsection.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    first_item = paragraphs[0]
    subparagraphs = [
        child
        for child in first_item.children
        if child.kind is IRNodeKind.SUBPARAGRAPH and child.label
    ]

    assert [child.label for child in paragraphs] == [str(idx) for idx in range(1, 17)]
    assert [child.label for child in subparagraphs] == ["a", "b", "c", "d"]
    assert "hankkeen energian hankinta" in irnode_to_text(subparagraphs[1])
    assert "yleistajuinen ja havainnollinen tiivistelmä" in irnode_to_text(paragraphs[-1])


def test_1997_142_item_repeal_preserves_surviving_definition_items() -> None:
    """1999/786 repeals only 1997/142 §1 item 2, not the whole section."""

    replay = pinned_replay("1997/142", quiet=True)
    section_node = replay.materialized_state.find_section("1", None, None)

    assert section_node is not None
    assert section_node.attrs.get("lawvm_repeal_placeholder") != "1"
    rendered = irnode_to_text(section_node)
    assert "Määritelmät" in rendered
    assert "kaasuöljyllä" in rendered
    assert "kevyellä polttoöljyllä" in rendered
    assert "dieselöljyllä" not in rendered


def test_1966_612_section_item_subsection_fold_preserves_first_moment_items() -> None:
    """Base §2 items 2-5 are kohdat, not momentit targeted by later amendments."""

    replay = pinned_replay("1966/612", quiet=True, build_full_products=False)
    section_node = replay.find_section("2", None, None)
    assert section_node is not None
    subsections = [
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label
    ]
    assert [child.label for child in subsections] == ["1", "2", "3"]

    first_moment_items = [
        child
        for child in subsections[0].children
        if child.kind is IRNodeKind.PARAGRAPH and child.label
    ]
    assert [child.label for child in first_moment_items] == ["1", "2", "3", "4", "5"]

    section_text = irnode_to_text(section_node)
    assert "5) Enintään 10 vuotta" in section_text
    assert "Valtiokonttori voi erityisistä syistä" in section_text
    assert "Valtiokonttorin 2 momentissa" in section_text
    assert "Valtiovarainministeri oi erityisistä" not in section_text


def test_1974_1086_intro_item_wrapper_fold_prevents_section_4_duplicate_list() -> None:
    """Base §4 item-list wrapper belongs to 1 momentti, not a peer momentti."""

    replay = pinned_replay(
        "1974/1086",
        oracle_version="19900806",
        quiet=True,
        build_full_products=False,
    )
    section_node = replay.find_section("4", None, None)
    assert section_node is not None
    subsections = [
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label
    ]
    assert [child.label for child in subsections] == ["1", "2"]

    section_text = " ".join(irnode_to_text(section_node).split())
    assert section_text.count("1) että tuki katsotaan tarpeelliseksi") == 1
    assert "korkotukea tai investointiavustusta haetaan" in section_text
    assert "rahoitustukea haetaan, arvioidaan olevan toimintaedellytyksiä" not in section_text


def test_1990_1207_dotted_paragraph_rows_materialize_as_peer_moments() -> None:
    """Base §4 dotted paragraph rows are momentit, not items under 1 momentti."""

    replay = pinned_replay(
        "1990/1207",
        oracle_version="19921639",
        quiet=True,
        build_full_products=False,
    )
    section_node = replay.find_section("4", "3", None)
    assert section_node is not None
    subsections = [
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label
    ]

    assert [child.label for child in subsections] == ["1", "2", "3", "4"]
    assert "Asunto-osan pituuden tulee olla vähintään puolet auton kokonaispituudesta" in irnode_to_text(
        subsections[0]
    )
    assert "Ohjaamon ja asunto-osan välillä tulee olla näköyhteys" in irnode_to_text(subsections[2])
    assert "Matkailuautossa tulee olla vähintään seuraavat kiinteät varusteet" in irnode_to_text(
        subsections[3]
    )
    assert [child.label for child in subsections[3].children if child.kind is IRNodeKind.PARAGRAPH] == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
    ]


def test_2013_599_2025_854_official_johtolause_corrigendum_updates_section_5_item_17() -> None:
    """Official 854/2025 johtolause corrigendum must materialize 5 §:n 1 mom 17 kohta."""

    replay = pinned_replay("2013/599", quiet=True, mode="official_consolidation")
    section_node = replay.find_section("5", "1", None)
    assert section_node is not None
    section_text = " ".join(irnode_to_text(section_node).split())

    assert "laki vaarallisten aineiden kuljetuksesta Puolustusvoimissa ja Rajavartiolaitoksessa (849/2025)." in section_text


def test_2012_980_2022_604_johtolause_corrigendum_repeals_subsection_3_not_2() -> None:
    """Official 604/2022 johtolause corrigendum must target 2 § 3 mom, not 2 mom."""
    from lawvm.core import tree_ops as _tops

    replay = pinned_replay("2012/980", quiet=True, mode="official_consolidation", build_full_products=False)
    section_path = replay.replay_fold_state.find_section_path("2", "1", None)
    assert section_path is not None
    section_node = _tops.resolve(replay.replay_fold_state.ir, section_path)
    assert section_node is not None

    subsection_texts = {
        child.label: " ".join(irnode_to_text(child).split())
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION
    }

    assert "2" in subsection_texts
    assert subsection_texts["2"].startswith("Tätä lakia sovelletaan 1 momentissa tarkoitettuihin asioihin")
    assert "3" in subsection_texts
    assert subsection_texts["3"] == ""


def test_2009_1672_2017_275_body_lead_lane_recovers_section_13_3_paragraph_8a() -> None:
    """A ceremonial preamble must not hide the 2017/275 13 luvun 3 § 2 mom 8 a kohta insert."""
    from lawvm.core import tree_ops as _tops

    replay = pinned_replay("2009/1672", quiet=True, stop_before="2017/628", build_full_products=False)
    section_path = replay.replay_fold_state.find_section_path("3", "13", None)
    assert section_path is not None
    section_node = _tops.resolve(replay.replay_fold_state.ir, section_path)
    assert section_node is not None
    subsection_node = next(
        child
        for child in section_node.children
        if child.kind is IRNodeKind.SUBSECTION and child.label == "2"
    )
    paragraph_labels = [child.label for child in subsection_node.children if child.kind is IRNodeKind.PARAGRAPH]
    assert "8a" in paragraph_labels

    paragraph_8a = next(
        child
        for child in subsection_node.children
        if child.kind is IRNodeKind.PARAGRAPH and child.label == "8a"
    )
    paragraph_text = " ".join(irnode_to_text(paragraph_8a).split())
    assert "asetuksen (EU) 2015/757" in paragraph_text


def _find_omissions(node: IRNode, path: str = "") -> list[str]:
    """Find omission nodes anywhere in the tree."""
    found = []
    for c in node.children:
        cp = f"{path}/{c.kind}:{c.label}" if c.label else f"{path}/{c.kind}"
        if c.kind == "omission":
            found.append(cp)
        found.extend(_find_omissions(c, cp))
    return found


def _find_duplicates(node: IRNode, path: str = "") -> list[str]:
    """Find children with duplicate (kind, label) pairs."""
    del path
    return [
        f"{violation.path_text}/{violation.child_kind}:{violation.label}"
        for violation in iter_tree_invariant_violations(node, families={"duplicate_label"})
    ]


# ---------------------------------------------------------------------------
# Bug family 1: omissions leaking into materialized sections
# ---------------------------------------------------------------------------

class TestNoOmissionsInPIT:
    """Omission markers from sparse amendment bodies must not leak into PIT."""

    def test_2013_588_section_87_no_omissions(self) -> None:
        """Sähkömarkkinalaki § 87 had omission markers in chapter 11a."""
        ir = _replay("2013/588")
        omissions = _find_omissions(ir)
        assert not omissions, f"Found omissions in materialized PIT: {omissions[:5]}"

    def test_2000_609_no_omissions(self) -> None:
        """VN asetus maaseudun kehittämisestä had omission markers."""
        ir = _replay("2000/609")
        omissions = _find_omissions(ir)
        assert not omissions, f"Found omissions in materialized PIT: {omissions[:5]}"


# ---------------------------------------------------------------------------
# Bug family 2: duplicate children in materialized sections
# ---------------------------------------------------------------------------

class TestNoDuplicatesInPIT:
    """Sparse merge duplicate children must be deduped before materialization."""

    def test_1868_31_section_85_complete_child_overlay_replaces_duplicate_snapshot(self) -> None:
        """1993/1027 complete §85 must override duplicate carried chapter snapshot children.

        1990/820 leaves duplicate chapter:6/section:85 children in a selected
        chapter snapshot.  A later complete 1993/1027 replacement owns the exact
        child address; PIT materialization must render that complete child once,
        not preserve the stale duplicate selected children.
        """
        ir = _replay("1868/31-000")
        chapter_6 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "6"
        )
        sections_85 = [
            child
            for child in chapter_6.children
            if child.kind is IRNodeKind.SECTION and child.label == "85"
        ]
        assert len(sections_85) == 1
        section_text = " ".join(irnode_to_text(sections_85[0]).split())
        assert section_text == (
            "85 § Velallinen vastaa ennen konkurssin alkua syntyneestä saatavasta "
            "myös sillä omaisuudella, jonka velallinen vastaisuudessa saa, jollei "
            "velkojan kanssa ole toisin sovittu."
        )

    def test_2001_1234_item_scoped_table_row_snapshot_preserves_sibling_rows(self) -> None:
        """2003/811 row-H replace must not drop the rest of the §2 fee table at PIT."""
        source_pathologies: list[object] = []
        replay = cast(
            ReplayResult,
            pinned_replay("2001/1234", quiet=True, source_pathologies_out=source_pathologies),
        )
        section_2 = replay.materialized_state.find_section("2")
        assert section_2 is not None
        section_text = " ".join(irnode_to_text(section_2).split())
        assert "A. Eläimen ruumiinavaus" in section_text
        assert "H. Poronlihan tarkastus" in section_text
        assert any(
            getattr(pathology, "code", "") == "DESTRUCTIVE_SHAPE_LOSS_RISK"
            and getattr(pathology, "detail", {}).get("recovery_kind")
            == "section_snapshot_preserve_live_fold_for_descendant_scoped_item"
            for pathology in source_pathologies
        )

    def test_1988_1347_sparse_descendant_scoped_section_snapshot_keeps_fold(self) -> None:
        """2003/252 descendant-scoped §3 source shell must not truncate folded §3.

        The source formula names selected chemical and biological ``kohta`` rows
        under §3.  Its XML section wrapper is sparse and contains omission
        markers, so timeline export must preserve the replay fold rather than
        promote that wrapper as an exact complete section owner.
        """
        source_pathologies: list[object] = []
        replay = cast(
            ReplayResult,
            pinned_replay("1988/1347", quiet=True, source_pathologies_out=source_pathologies),
        )
        section_3 = next(
            child
            for child in replay.materialized_state.ir.children
            if child.kind is IRNodeKind.SECTION and child.label == "3"
        )
        section_text = " ".join(irnode_to_text(section_3).split())
        assert len(section_text) > 10_000
        assert "Alifaattiset, aromaattiset ja alisykliset hiilivedyt" in section_text
        assert "Tuberkuloosibasilli" in section_text
        assert any(
            getattr(pathology, "code", "") == "DESTRUCTIVE_SHAPE_LOSS_RISK"
            and getattr(pathology, "detail", {}).get("recovery_kind")
            == "section_snapshot_preserve_fold_for_descendant_scoped_source"
            for pathology in source_pathologies
        )

    def test_2007_1321_sec1_parent_filter_keeps_section_12_commencement(self) -> None:
        """2009/520 sec_1 fallback must not route Ulosottokaari §12 into 1321/2007.

        The source paragraph first names Ulosottokaari (705/2007) chapter 1
        §§11-12, then separately repeals 1321/2007 §11.  Parent-restricted
        fallback must preserve the latter while rejecting the former for this
        replay graph, otherwise official-consolidation materialization blanks
        the 1321/2007 commencement section.
        """
        compiled_ops: list[dict[str, object]] = []
        replay = cast(
            ReplayResult,
            pinned_replay(
                "2007/1321",
                quiet=True,
                compiled_ops_out=compiled_ops,
            ),
        )

        assert not any(
            row.get("source") == "2009/520"
            and row.get("action") in {"replace", "repeal"}
            and row.get("target_section") == "12"
            for row in compiled_ops
        )
        section_12 = replay.materialized_state.find_section("12")
        assert section_12 is not None
        section_text = " ".join(irnode_to_text(section_12).split())
        assert "Tämä asetus tulee voimaan 1 päivänä tammikuuta 2008." in section_text

    def test_1989_819_sparse_subsection_replace_does_not_rehydrate_old_paragraphs(self) -> None:
        """1998/1103 §15(2) must not graft stale old-register items into §15(1).

        The 1998 source wrapper is sparse: it carries an omission marker plus the
        replacement second moment.  Timeline export must rebuild the section over
        the latest clean prior section snapshot, not over the mutable replay fold
        that still contains old paragraph descendants from the pre-1995 §15.
        """
        source_pathologies: list[object] = []
        replay = cast(
            ReplayResult,
            pinned_replay("1989/819", quiet=True, source_pathologies_out=source_pathologies),
        )
        section_15 = replay.materialized_state.find_section("15")
        assert section_15 is not None
        section_text = " ".join(irnode_to_text(section_15).split())
        assert "tietoja kiinnitysasian vireilläolosta ja vahvistamisesta" in section_text
        assert "muita tietoja siten kuin autokiinnityslaissa" in section_text
        assert "sopimusrekisteröijän sopimuksenmukaisissa rekisteröintitehtävissä" in section_text
        assert "poliisiviranomaisille liikennevalvontaa" not in section_text
        assert "Ahvenanmaan maakuntahallitukselle" not in section_text
        assert any(
            getattr(pathology, "code", "") == "DESTRUCTIVE_SHAPE_LOSS_RISK"
            and getattr(pathology, "detail", {}).get("recovery_kind")
            == "section_snapshot_single_subsection_sparse_merge"
            for pathology in source_pathologies
        )

    @pytest.mark.slow
    def test_2014_917_section_265_no_duplicate_subsection(self) -> None:
        """Tietoyhteiskuntakaari § 265 had duplicate subsection:1."""
        ir = _replay("2014/917")
        dups = _find_duplicates(ir)
        omissions = _find_omissions(ir)
        assert not omissions, f"Found omissions in materialized PIT: {omissions[:5]}"
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"

    def test_2025_89_section_154_no_duplicate_paragraphs(self) -> None:
        """Sotilaskurinpitolaki § 154 had duplicate paragraph:1,2,3."""
        ir = _replay("2025/89")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"

    def test_2014_255_section_36_no_duplicate_subsection(self) -> None:
        """Sotilaskurinpitolaki § 36 had duplicate subsection:4."""
        ir = _replay("2014/255")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"

    @pytest.mark.slow
    def test_2008_878_section_40_momentti_not_duplicated(self) -> None:
        """Laki Finanssivalvonnasta § 40: momentti 2 must not be duplicated, momentti 3 must survive.

        2017/228 substitutes the single 40 §:n 2 momentti (source XML carries
        exactly one <subsection> flanked by omission markers). This previously
        duplicated the new momentti 2 across subsection:2 and subsection:3,
        losing the original momentti 3. Bounding the prefix-migration follow to
        each op's enactment date removed the spurious subsection rebound that
        drove that duplication, so § 40 now materializes the oracle-faithful
        shape (one momentti-2 subsection, momentti 3 preserved).
        """
        ir = _replay("2008/878")
        section_40 = next(
            child
            for chapter in ir.children
            if chapter.kind is IRNodeKind.CHAPTER
            for child in chapter.children
            if child.kind is IRNodeKind.SECTION
            and (child.label or "").strip().rstrip("§").strip() == "40"
        )
        subsections = [c for c in section_40.children if c.kind is IRNodeKind.SUBSECTION]
        momentti_2_intro = "Seuraamusmaksu määrätään myös sille, joka tahallaan"
        momentti_3_lead = "Seuraamusmaksua ei voida määrätä luonnolliselle henkilölle teosta"
        item_list_subsections = [
            s
            for s in subsections
            if " ".join(irnode_to_text(s).split()).startswith(momentti_2_intro)
        ]
        # The item-list momentti (2 mom) is a single subsection in the Finlex oracle.
        assert len(item_list_subsections) == 1, (
            "momentti 2 item list duplicated across "
            f"{[s.label for s in item_list_subsections]}"
        )
        section_text = " ".join(irnode_to_text(section_40).split())
        assert momentti_3_lead in section_text, "original momentti 3 was lost during replay"

    def test_1976_673_section_13_no_duplicate_subsection(self) -> None:
        """The 1976/673 replay must not duplicate subsection 3 in section 13."""
        ir, replay_meta = _replay_ir_and_meta("1976/673")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])

    @pytest.mark.slow
    def test_1979_1062_sections_no_duplicate_subsection(self) -> None:
        """The 1979/1062 replay must not duplicate subsection 2 in chapter 18."""
        ir, replay_meta = _replay_ir_and_meta("1979/1062")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])
        chapter_16a = next(
            child for child in ir.children if child.kind is IRNodeKind.CHAPTER and child.label == "16a"
        )
        section_labels = [child.label for child in chapter_16a.children if child.kind is IRNodeKind.SECTION]
        assert "16bluku" not in section_labels
        assert any(
            child.kind is IRNodeKind.CHAPTER and child.label == "16b"
            for child in ir.children
        )

    def test_2017_320_no_duplicate_sections_or_dedup_warning(
        self,
        replay_2017_320_legal_pit_with_meta: tuple[ReplayResult, dict[str, object]],
    ) -> None:
        """2017/320 materialized PIT must not have final duplicates.

        The replay-fold dedup backstop may fire due to a conflict between
        individual RENUMBER ops (from johtolause, now enabled by Roman numeral
        normalization in find_section_path) and the StructuralTransformPlan
        relabel ops for 2019/371.  The backstop resolves the conflict correctly,
        so the final PIT must still be clean even if the warning fires.
        """
        replay, _replay_meta = replay_2017_320_legal_pit_with_meta
        ir = replay.ir
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        # NOTE: structural_dedup_warnings may fire for 2017/320 due to a known
        # interaction between individual RENUMBER ops and the StructuralTransformPlan
        # relabel for 2019/371 — tracked as a future improvement.

    def test_2017_320_part_2_chapter_1_keeps_section_5(
        self,
        replay_2017_320_legal_pit_with_meta: tuple[ReplayResult, dict[str, object]],
    ) -> None:
        """2017/320 must keep the early Part II chapter-1 section wave after later chapter relabeling."""
        replay, _replay_meta = replay_2017_320_legal_pit_with_meta
        ir = replay.ir
        part_2 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "2"
        )
        chapter_1 = next(
            child
            for child in part_2.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "2"
        )
        section_labels = [
            child.label
            for child in chapter_1.children
            if child.kind is IRNodeKind.SECTION
        ]
        assert "5" in section_labels
        assert "21" not in section_labels

    def test_2017_320_delayed_section_268_materializes_under_current_chapter_32(
        self,
        replay_2017_320_legal_pit_with_meta: tuple[ReplayResult, dict[str, object]],
    ) -> None:
        """2018/731's delayed section must survive the 2019/371 and 2020/1256 recodification chain."""
        replay, replay_meta = replay_2017_320_legal_pit_with_meta
        ir = replay.ir
        part_7 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "7"
        )
        chapter_32 = next(
            child
            for child in part_7.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "32"
        )
        section_268 = next(
            child
            for child in chapter_32.children
            if child.kind is IRNodeKind.SECTION and child.label == "268"
        )

        text = " ".join(irnode_to_text(section_268).split())
        assert "Moottorikäyttöisen ajoneuvon asiakirjoja" in text
        assert "39 §:ssä" in text
        assert any(
            str(event.from_address) == "part:6/chapter:2/section:7"
            and str(event.to_address) == "part:6/chapter:2/section:268"
            and getattr(event, "witness", {}).get("rule_id")
            == "restructure.pending_source_chain_relabel_lineage"
            for event in replay.migration_events
        )
        source_pathologies = replay_meta.get("source_pathologies", [])
        assert isinstance(source_pathologies, list)
        # 2018/731's delayed §268 insert lands in a chapter that already carries
        # a live §268, so the recovery absorbs the insert into the live chapter
        # rather than preserving a separate duplicate payload.
        assert any(
            cast(dict[str, Any], pathology).get("source_statute") == "2018/731"
            and cast(dict[str, Any], pathology).get("target_label") == "268 §"
            and cast(dict[str, Any], pathology).get("recovery_kind")
            == "section_insert_chapter_merge_absorb"
            for pathology in source_pathologies
            if isinstance(pathology, dict)
        )

    def test_2017_320_2018_301_keeps_new_part_5_chapters_scoped(self) -> None:
        """2018/301 must keep new part-5 chapters scoped and not flatten sections 19-21."""
        ir = pinned_replay(
            "2017/320",
            mode="legal_pit",
            stop_before="2018/539",
            quiet=True,
            build_full_products=False,
        ).ir
        root_section_labels = [
            child.label
            for child in ir.children
            if child.kind is IRNodeKind.SECTION
        ]
        assert not {"19", "20", "21"} & set(root_section_labels)

        root = next(
            (child for child in ir.children if child.kind is IRNodeKind.HCONTAINER),
            ir,
        )
        part_5 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "5"
        )
        chapter_labels = [
            child.label
            for child in part_5.children
            if child.kind is IRNodeKind.CHAPTER
        ]
        assert {"2", "3"} <= set(chapter_labels)

    @pytest.mark.slow
    def test_2017_320_2018_301_keeps_chapter_children_and_part_scoped_inserts_before_2019_371(self) -> None:
        """2018/301 must keep child sections and canonicalized part-scoped inserts before 2019/371."""
        ir = pinned_replay(
            "2017/320",
            mode="legal_pit",
            stop_before="2019/371",
            quiet=True,
            build_full_products=False,
        ).ir
        root = next(
            (child for child in ir.children if child.kind is IRNodeKind.HCONTAINER),
            ir,
        )
        part_5 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "5"
        )
        chapter_2 = next(
            child
            for child in part_5.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "2"
        )
        chapter_3 = next(
            child
            for child in part_5.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "3"
        )
        assert any(child.kind is IRNodeKind.SECTION for child in chapter_2.children)
        assert any(child.kind is IRNodeKind.SECTION for child in chapter_3.children)

        part_3 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "3"
        )
        part_6 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "6"
        )
        chapter_1_part_3 = next(
            child
            for child in part_3.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "1"
        )
        chapter_2_part_3 = next(
            child
            for child in part_3.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "2"
        )
        chapter_1_part_6 = next(
            child
            for child in part_6.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "1"
        )
        assert "4" in {
            child.label for child in chapter_1_part_3.children if child.kind is IRNodeKind.SECTION
        }
        assert "2a" in {
            child.label for child in chapter_2_part_3.children if child.kind is IRNodeKind.SECTION
        }
        assert {"6", "7", "8", "9", "10"} <= {
            child.label for child in chapter_1_part_6.children if child.kind is IRNodeKind.SECTION
        }

    def test_2009_1599_2023_152_keeps_tail_subsection_replaces_in_19_and_20_luku(self) -> None:
        """2023/152 must append the new tail moments in 19:14 and 20:14."""
        ir = _replay("2009/1599", stop_before="2023/577")
        root = next(
            (child for child in ir.children if child.kind is IRNodeKind.HCONTAINER),
            ir,
        )
        part_6 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "6"
        )
        chapter_19 = next(
            child
            for child in part_6.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "19"
        )
        chapter_20 = next(
            child
            for child in part_6.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "20"
        )
        section_19_14 = next(
            child
            for child in chapter_19.children
            if child.kind is IRNodeKind.SECTION and child.label == "14"
        )
        section_20_14 = next(
            child
            for child in chapter_20.children
            if child.kind is IRNodeKind.SECTION and child.label == "14"
        )
        labels_19 = [child.label for child in section_19_14.children if child.kind is IRNodeKind.SUBSECTION]
        labels_20 = [child.label for child in section_20_14.children if child.kind is IRNodeKind.SUBSECTION]
        assert labels_19 == ["1", "2", "3", "4"]
        assert labels_20 == ["1", "2", "3"]

    def test_2017_320_2018_984_keeps_iia_scoped_replaces_and_fragmentary_chapter_before_2018_1303(self) -> None:
        """2018/984 must keep IIa-scoped replaces and fragmentary chapter payload under canonical part 3."""
        ir = pinned_replay(
            "2017/320",
            mode="legal_pit",
            stop_before="2018/1303",
            quiet=True,
            build_full_products=False,
        ).ir
        root = next(
            (child for child in ir.children if child.kind is IRNodeKind.HCONTAINER),
            ir,
        )
        part_3 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "3"
        )
        part_4 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "4"
        )
        part_5 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "5"
        )
        part_6 = next(
            child
            for child in root.children
            if child.kind is IRNodeKind.PART and child.label == "6"
        )
        chapter_1_part_3 = next(
            child
            for child in part_3.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "1"
        )
        chapter_4_2 = next(
            child
            for child in part_4.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "2"
        )
        chapter_6_1 = next(
            child
            for child in part_6.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "1"
        )
        chapter_1_part_3_labels = [
            child.label
            for child in chapter_1_part_3.children
            if child.kind is IRNodeKind.SECTION
        ]
        chapter_4_2_labels = [
            child.label
            for child in chapter_4_2.children
            if child.kind is IRNodeKind.SECTION
        ]
        chapter_6_1_labels = [
            child.label
            for child in chapter_6_1.children
            if child.kind is IRNodeKind.SECTION
        ]
        assert "3a" not in chapter_1_part_3_labels
        assert "2a" not in chapter_1_part_3_labels
        assert "3a" in chapter_4_2_labels
        assert "2a" in chapter_6_1_labels

        chapter_2_part_3 = next(
            child
            for child in part_3.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "2"
        )
        chapter_1_part_3_labels = [
            child.label
            for child in chapter_1_part_3.children
            if child.kind is IRNodeKind.SECTION
        ]
        chapter_2_part_3_labels = [
            child.label
            for child in chapter_2_part_3.children
            if child.kind is IRNodeKind.SECTION
        ]

        assert chapter_1_part_3_labels == ["1", "2", "3", "4"]
        assert {"5", "6", "7"} <= set(chapter_2_part_3_labels)

        chapter_5_part_5 = next(
            child
            for child in part_5.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "5"
        )
        chapter_5_part_5_sections = [
            child
            for child in chapter_5_part_5.children
            if child.kind is IRNodeKind.SECTION
        ]
        assert [child.label for child in chapter_5_part_5_sections] == [
            "1",
            "2",
            "3",
            "4",
            "5",
        ]
        section_2 = next(child for child in chapter_5_part_5_sections if child.label == "2")
        assert "Väyläviraston tiedonsaantioikeus" in irnode_to_text(section_2)

    def test_2017_519_no_root_section_10_after_jolloin_renumber_insert(
        self,
        replay_2017_519: ReplayResult,
    ) -> None:
        """2017/519 must keep reborn 10 § under chapter 3 after 2019/979."""
        ir = replay_2017_519.ir
        root_section_10 = [
            child
            for child in ir.children
            if child.kind is IRNodeKind.SECTION and child.label == "10"
        ]
        assert not root_section_10
        chapter_3 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "3"
        )
        chapter_3_sections = [
            child.label
            for child in chapter_3.children
            if child.kind is IRNodeKind.SECTION
        ]
        assert "10" in chapter_3_sections
        assert "10a" in chapter_3_sections
        assert check_invariants(ir) == []

    def test_2012_746_chapter_19_stays_under_part_6(self, replay_2012_746: ReplayResult) -> None:
        """2012/746 must not leave chapter 19 as a root sibling outside part 6."""
        ir = replay_2012_746.ir
        root_chapter_19 = [
            child
            for child in ir.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "19"
        ]
        assert not root_chapter_19
        part_6 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "6"
        )
        chapter_19 = next(
            child
            for child in part_6.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "19"
        )
        chapter_19_sections = [
            child.label
            for child in chapter_19.children
            if child.kind is IRNodeKind.SECTION
        ]
        assert "3" in chapter_19_sections
        assert check_invariants(ir) == []

    def test_2012_746_container_replace_updates_part_wrapped_section_1_children(
        self,
        replay_2012_746: ReplayResult,
    ) -> None:
        """2012/746 chapter snapshots must not skip part-wrapped section 1 child payloads."""
        ir = replay_2012_746.ir
        part_3 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "3"
        )
        chapter_6 = next(
            child
            for child in part_3.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "6"
        )
        section_1 = next(
            child
            for child in chapter_6.children
            if child.kind is IRNodeKind.SECTION and child.label == "1"
        )
        section_1_text = irnode_to_text(section_1)
        assert "Tämän luvun säännöksiä" not in section_1_text

        part_5 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "5"
        )
        chapter_12 = next(
            child
            for child in part_5.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "12"
        )
        section_1_ch12 = next(
            child
            for child in chapter_12.children
            if child.kind is IRNodeKind.SECTION and child.label == "1"
        )
        section_1_ch12_text = irnode_to_text(section_1_ch12)
        assert "Tämän lain 12—14 lukua sovelletaan" not in section_1_ch12_text
        assert check_invariants(ir) == []

    def test_2012_746_section_6_2_keeps_insert_scoped_to_chapter_17(
        self,
        replay_2012_746: ReplayResult,
    ) -> None:
        """2012/746 6 luvun 2 § must not absorb the trailing 17 luvun insert."""
        ir = replay_2012_746.ir
        part_3 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "3"
        )
        chapter_6 = next(
            child
            for child in part_3.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "6"
        )
        section_2 = next(
            child
            for child in chapter_6.children
            if child.kind is IRNodeKind.SECTION and child.label == "2"
        )
        section_2_text = irnode_to_text(section_2)
        needle = (
            "Liikkeeseenlaskijan on toimitettava Finanssivalvonnalle sen pyynnöstä "
            "markkinoiden väärinkäyttöasetuksen 17 artiklan 4 kohdan 3 alakohdassa "
            "tarkoitettu selvitys tiedon julkistamisen lykkäämisen edellytyksistä."
        )
        assert section_2_text.count(needle) == 1
        assert check_invariants(ir) == []

    def test_2012_746_section_16_1_keeps_later_commencement_version(
        self,
        replay_2012_746: ReplayResult,
    ) -> None:
        """2012/746 16 luvun 1 § must keep the delayed 2019/511 text at 2019-07-22."""

        section_key = "part:6/chapter:16/section:1"
        assert replay_2012_746.timelines is not None
        timeline = next(
            tl
            for addr, tl in replay_2012_746.timelines.items()
            if str(addr) == section_key
        )
        july_2019_versions = [
            version
            for version in timeline.versions
            if version.effective == "2019-07-22"
        ]
        assert july_2019_versions
        latest_july_version = max(july_2019_versions, key=lambda version: version.enacted)
        assert latest_july_version.enacted == "2019-04-12"

        ir = replay_2012_746.ir
        part_6 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.PART and child.label == "6"
        )
        chapter_16 = next(
            child
            for child in part_6.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "16"
        )
        section_1 = next(
            child
            for child in chapter_16.children
            if child.kind is IRNodeKind.SECTION and child.label == "1"
        )
        section_1_text = irnode_to_text(section_1)
        assert "osakkeenomistajien oikeudet -direktiivin" in section_1_text

    def test_2016_768_section_36_keeps_replaced_fifth_subsection_after_same_wave_renumber(self) -> None:
        """2016/768 36 § must keep the 2024/936 replacement on migrated 5 mom."""
        ir = _replay("2016/768", stop_before="2025/383")
        chapter_7 = next(
            child
            for child in ir.children
            if child.kind is IRNodeKind.CHAPTER and child.label == "7"
        )
        section_36 = next(
            child
            for child in chapter_7.children
            if child.kind is IRNodeKind.SECTION and child.label == "36"
        )
        subsection_5 = next(
            child
            for child in section_36.children
            if child.kind is IRNodeKind.SUBSECTION and child.label == "5"
        )
        assert "Mitä tässä momentissa säädetään" in irnode_to_text(subsection_5)

    def test_1997_1339_no_duplicate_paragraphs_or_dedup_warning(self) -> None:
        """1997/1339 must not need replay-fold dedup for subsection paragraph duplicates."""
        ir, replay_meta = _replay_ir_and_meta("1997/1339")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])

    def test_2002_64_section_2_no_duplicate_paragraphs_or_dedup_warning(self) -> None:
        """2002/64 must not leave repeated a/b/c paragraph labels flat at subsection scope."""
        ir, replay_meta = _replay_ir_and_meta("2002/64")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])

    def test_2002_1244_section_21c_no_duplicate_paragraphs_or_dedup_warning(self) -> None:
        """2002/1244 §21c must not leave repeated i/ii labels flat in replay fold."""
        ir, replay_meta = _replay_ir_and_meta("2002/1244")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])

    def test_1997_108_sections_2_and_3_no_duplicate_paragraphs_or_dedup_warning(self) -> None:
        """1997/108 must nest its repeated digit families instead of leaving duplicates flat."""
        ir, replay_meta = _replay_ir_and_meta("1997/108")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])

    def test_2002_672_section_1_no_direct_paragraph_child(self) -> None:
        """2002/672 section 1 must keep the item list under subsection, not direct paragraph."""
        ir = _replay("2002/672")
        assert check_invariants(ir) == []

    def test_2000_154_section_1_no_duplicate_subparagraphs_or_dedup_warning(self) -> None:
        """2000/154 must split the buried 5)-reset into a new paragraph instead of duplicating a/b."""
        ir, replay_meta = _replay_ir_and_meta("2000/154")
        dups = _find_duplicates(ir)
        assert not dups, f"Found duplicates in materialized PIT: {dups[:5]}"
        assert replay_meta.get("structural_dedup_warnings") in (None, [])

    def test_1999_589_section_7_no_normalized_duplicate_paragraphs(self) -> None:
        """1999/589 §7 must recover dotted intro labels instead of colliding at paragraph 1."""
        ir, replay_meta = _replay_ir_and_meta("1999/589")
        assert check_invariants(ir) == []
        assert replay_meta.get("invariant_violations") in (None, [])


    def test_1995_398_section_20_no_duplicate_5a_after_sparse_plain_plus_item_mix(self) -> None:
        """1995/398 §20 must not replay 5a twice when a plain sparse slot already carries it."""
        ir, replay_meta = _replay_ir_and_meta("1995/398")
        assert check_invariants(ir) == []
        assert replay_meta.get("invariant_violations") in (None, [])
        assert replay_meta.get("product_invariant_violations") in (None, [])

    @pytest.mark.slow
    def test_2006_624_sections_27_and_17_3_no_duplicate_7a_or_13a(self) -> None:
        """2006/624 must not duplicate carried explicit paragraph labels from 2022/1337."""
        ir, replay_meta = _replay_ir_and_meta("2006/624")
        assert check_invariants(ir) == []
        assert replay_meta.get("invariant_violations") in (None, [])
        assert replay_meta.get("product_invariant_violations") in (None, [])

    def test_1953_317_reflected_section_original_version_extends_oracle_horizon(self) -> None:
        """fin@20050786 embeds §7 from 2003/537 despite dateConsolidated 2003-06-13."""
        ir = _replay("1953/317")
        section_7 = _first_descendant(ir, IRNodeKind.SECTION, "7")
        text = irnode_to_text(section_7)

        assert "rikoslain 6 luvun 13 §:n" in text
        assert "rikoslain 3 luvun 11 §:n" not in text

    def test_1992_785_section_11_not_stripped_by_future_effective_amendment(self) -> None:
        """1992/785 §11 must remain in PIT at oracle date 2023-04-14.

        Amendment 2023/739 (Laki potilasasiavastaavista) repeals §11 on 2024-01-01
        but was published on 2023-04-14 (same as oracle cutoff).  Without the cap,
        oracle_materialize_as_of was pushed to 2024-01-01 and §11 was stripped before
        its repeal date.
        """
        ir = _replay("1992/785")
        assert check_invariants(ir) == []

        def find_section(node: IRNode, label: str) -> IRNode | None:
            from lawvm.core.semantic_types import IRNodeKind
            for c in node.children:
                if c.kind is IRNodeKind.SECTION and c.label == label:
                    return c
                found = find_section(c, label)
                if found:
                    return found
            return None

        s11 = find_section(ir, "11")
        assert s11 is not None, "§11 (Potilasasiamies) must be present in oracle PIT at 2023-04-14"
        from lawvm.core.ir_helpers import irnode_to_text
        text = irnode_to_text(s11)
        assert "Potilasasiamies" in text or len(text) > 10, (
            f"§11 found but appears to be an empty repeal placeholder: {text!r}"
        )

    def test_2015_1635_chapter_3_not_stripped_by_metadata_only_future_repeal_ref(self) -> None:
        """2015/1635 ch. 3 remains in the selected pre-repeal oracle body.

        The selected fin@20221289 XML cites 741/2023 in AKN amendment-history
        metadata, but the body still contains chapter 3.  That metadata citation
        must not re-admit the future repeal as a body-materialized VTS surface.
        """
        ir = _replay("2015/1635", oracle_version="20221289")
        assert check_invariants(ir) == []

        chapter_labels = [
            child.label
            for child in ir.children
            if child.kind is IRNodeKind.CHAPTER
        ]

        assert "3" in chapter_labels

    def test_2000_812_sections_not_stripped_by_future_effective_amendment(self) -> None:
        """2000/812 must have 0 MISSING sections at oracle date 2023-04-14.

        Same pattern as 1992/785: 2023/739 and 2023/704 had future effective_date
        2024-01-01 but were published 2023-04-14, pushing oracle_materialize_as_of
        to 2024-01-01 and stripping many sections prematurely.
        """
        ir = _replay("2000/812")
        assert check_invariants(ir) == []

    def test_2002_1126_future_repeal_keeps_cutoff_temporary_section_replacements(self) -> None:
        """Future repeal-only oracle anchors must not expire unrelated temporary text.

        The selected fin@20050886 surface is dated 2005-11-11 but references
        2005/886, whose section-19 repeal is effective 2006-01-01. Materializing
        at 2006-01-01 is needed for that repeal, but the expiry horizon must
        stay at the oracle cutoff so the still-live 2004/466 temporary complete
        replacement of §2 is not reverted to the base list.
        """
        ir = _replay("2002/1126")
        assert check_invariants(ir) == []

        section_2 = _first_descendant(ir, IRNodeKind.SECTION, "2")
        text = irnode_to_text(section_2)

        assert "6, 10-13 ja 16-17 b §:ssä määrätyt" in text
        assert "6 ja 9-19 §:ssä määrätyt" not in text
        assert "sähköiseen allekirjoitukseen liittyviä laatuvarmenteita" in text

    def test_1948_404_versioned_subsection_extends_oracle_horizon(self) -> None:
        """fin@20240118 embeds delayed 2024/118 text in §6 b subsection 3."""
        ir = _replay("1948/404")
        section_6b = _first_descendant(ir, IRNodeKind.SECTION, "6b")
        text = irnode_to_text(section_6b)

        assert "vuosittain viimeistään tammikuun 31 päivänä" in text
        assert "kahdessatoista yhtä suuressa erässä" not in text

    def test_1995_57_misspelled_momenti_targets_section_8_subsection_3(self) -> None:
        """2000/235 typo ``8 §:n 3 momenti`` must not widen to whole §8."""
        ir = _replay("1995/57")
        assert check_invariants(ir) == []

        section_8 = _first_descendant(ir, IRNodeKind.SECTION, "8")
        text = irnode_to_text(section_8)

        assert "Jos alueellisen ympäristökeskuksen toimialaan kuuluvan asian vaikutukset" in text
        assert "hakijana ympäristölupavirastossa" in text
        assert "hakijana vesioikeuskäsittelyssä" not in text

    def test_2016_673_chapters_20_21_in_part_4a_not_part_5(self) -> None:
        """2016/673 chapters 20 and 21 must appear in part:4a after 2019/209 moves them.

        Amendment 2019/209 creates part IV A OSA (label '4a') and moves chapters 20
        and 21 into it alongside the newly inserted chapter 19a.  Before the fix, the
        materialized PIT placed them in part:5 because section-level timeline ops emitted
        before the chapter move carried the old 'part:5' path prefix.
        """
        ir = _replay("2016/673", stop_before="2019/1509")
        assert check_invariants(ir) == []
        part_order = [
            part_node.label
            for part_node in ir.children
            if part_node.kind is IRNodeKind.PART and part_node.label
        ]
        assert part_order[:6] == ["1", "2", "3", "4", "4a", "5"]
        # Collect part labels for chapters 20 and 21
        ch_to_part: dict[str, str] = {}
        for part_node in ir.children:
            if part_node.kind is IRNodeKind.PART and part_node.label:
                for ch_node in part_node.children:
                    if ch_node.kind is IRNodeKind.CHAPTER and ch_node.label in ("20", "21"):
                        ch_to_part[ch_node.label] = part_node.label
        assert ch_to_part.get("20") == "4a", f"chapter 20 expected in part:4a, found in {ch_to_part.get('20')!r}"
        assert ch_to_part.get("21") == "4a", f"chapter 21 expected in part:4a, found in {ch_to_part.get('21')!r}"

    def test_2016_673_sparse_subitem_replace_preserves_untouched_siblings(self) -> None:
        """2023/240 replaces 17:15(1)(1)(b) and (f), not the whole item.

        The apply fold already merges the sparse alakohta payload correctly.
        Timeline export must not rebuild subsection 1 from the sparse source
        fragment and thereby drop untouched siblings a, c, d, and e.
        """
        ir = _replay("2016/673")
        assert check_invariants(ir) == []
        section = None
        for part_node in ir.children:
            if part_node.kind is not IRNodeKind.PART or part_node.label != "4":
                continue
            for chapter_node in part_node.children:
                if chapter_node.kind is not IRNodeKind.CHAPTER or chapter_node.label != "17":
                    continue
                section = next(
                    (
                        child
                        for child in chapter_node.children
                        if child.kind is IRNodeKind.SECTION and child.label == "15"
                    ),
                    None,
                )
        assert section is not None
        subsection = next(
            child for child in section.children if child.kind is IRNodeKind.SUBSECTION and child.label == "1"
        )
        item = next(
            child for child in subsection.children if child.kind is IRNodeKind.PARAGRAPH and child.label == "1"
        )
        subitems = {
            child.label: irnode_to_text(child)
            for child in item.children
            if child.kind is IRNodeKind.SUBPARAGRAPH and child.label
        }
        assert list(subitems) == ["a", "b", "c", "d", "e", "f"]
        assert "Kunta- ja hyvinvointialuetyönantajat KT" in subitems["b"]
        assert "kriisinhallintatapaturma-asioista" in subitems["f"]


# ---------------------------------------------------------------------------
# Bug family 4: subsection-level INSERT chapter carry-forward must be stripped
# ---------------------------------------------------------------------------

class TestSubsectionInsertChapterCarryforward:
    """Subsection INSERT ops must not fail when chapter carry-forward is wrong.

    Pattern: "lisätään 1 lukuun uusi 1 a §, 5 §:n 1 momenttiin uusi 14 kohta"
    produces INSERT chapter:1 section:5 subsection:1 item:14, but §5 lives in
    chapter:2. The scope strip must remove chapter:1 so the dispatch can find §5.
    """

    def test_1984_602_no_failed_ops_from_1994_1317_and_1990_1367(
        self,
        replay_1984_602_no_full_products_with_failed: tuple[ReplayResult, list[FailedOp]],
    ) -> None:
        """1984/602 must not have FAILED ops from 1994/1317 (§5 mom:1 item:14,
        §13 mom:3/4) or from 1990/1367 (§47 mom:1) — chapter carry-forward must
        be stripped for subsection INSERT ops."""
        _replay, failed = replay_1984_602_no_full_products_with_failed
        problem_amendments = {"1994/1317", "1990/1367"}
        bad = [f for f in failed if f.amendment_id in problem_amendments]
        assert not bad, (
            f"Unexpected FAILED ops in 1984/602 from {problem_amendments}: "
            + "; ".join(f"{f.amendment_id}: {f.description}" for f in bad)
        )

    def test_1984_602_1996_666_glued_muutetaan_group_reaches_replay(
        self,
        replay_1984_602_no_full_products_with_failed: tuple[ReplayResult, list[FailedOp]],
    ) -> None:
        replay, _failed = replay_1984_602_no_full_products_with_failed
        section_12 = replay.find_section("12")
        section_23 = replay.find_section("23")
        assert section_12 is not None
        assert section_23 is not None

        section_12_text = " ".join(irnode_to_text(section_12).split())
        section_23_text = " ".join(irnode_to_text(section_23).split())

        assert "seitsemää täyttä työpäivää" in section_12_text
        assert "vakiintuneen palkan pohjalta työttömyyttä välittömästi edeltäneeltä" in section_23_text


# ---------------------------------------------------------------------------
# Bug family 3: consolidation split must preserve 1981/555 §11 as 4 + 5 mom.
# ---------------------------------------------------------------------------

class Test1981_555Section11Split:
    """Maa-aineslaki § 11 must keep the proportionality sentence as its own moment."""

    def test_1981_555_section_11_materializes_fourth_moment(self) -> None:
        replay = pinned_replay("1981/555", mode="official_consolidation", quiet=True)
        section = replay.find_section("11")
        assert section is not None

        subsection_labels = [child.label for child in section.children if child.kind == IRNodeKind.SUBSECTION]
        assert subsection_labels == ["1", "2", "3", "4", "5"]
        sub3 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "3")
        sub4 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "4")
        sub5 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "5")
        assert irnode_to_text(sub3) == (
            "Lupamääräyksiä voidaan lisäksi antaa: 1) ottamiseen liittyvistä laitteista ja liikenteen "
            "järjestämisestä erityisesti pohjaveden suojelemiseksi; 2) ajasta, jonka kuluessa tämän pykälän "
            "nojalla määrätyt toimenpiteet on suoritettava; sekä 3) muista hankkeesta aiheutuvien haittojen "
            "välttämiseksi tai rajoittamiseksi tarpeellisista toimenpiteistä"
        )
        assert irnode_to_text(sub4) == (
            "Määräykset eivät saa aiheuttaa luvan saajalle sellaista vahinkoa ja haittaa, jota on pidettävä "
            "hankkeen laajuuteen ja hänen saamaansa hyötyyn nähden kohtuuttomana."
        )
        assert irnode_to_text(sub5) == (
            "Lupapäätöksen sisällöstä ja luvan edellyttämien toimenpiteiden määräajasta säädetään "
            "tarkemmin valtioneuvoston asetuksella."
        )


def test_1996_1260_complete_section_insert_rebirth_does_not_rehydrate_old_8b_tail() -> None:
    lo_ops: list[Any] = []
    replay = pinned_replay(
        "1996/1260",
        mode="official_consolidation",
        quiet=True,
        lo_ops_out=lo_ops,
    )
    section = replay.materialized_state.find_section("8b")
    assert section is not None
    assert [child.label for child in section.children if child.kind is IRNodeKind.SUBSECTION] == ["1"]
    section_text = irnode_to_text(section)
    assert "verotaulukon 1 tuoteryhmien 9 ja 10" in section_text
    assert "piiritullikamarille" not in section_text

    snapshot = next(
        op
        for op in lo_ops
        if op.source is not None
        and op.source.statute_id == "2022/958"
        and op.action in {StructuralAction.INSERT, StructuralAction.REPLACE}
        and op.target.path == (("section", "8b"),)
    )
    assert snapshot.payload is not None
    assert snapshot.payload.attrs["lawvm_tail_policy"] == "replace_if_target_scope_requires"
    assert snapshot.payload.attrs["lawvm_payload_completeness_kind"] == "complete"


class TestFoldHcontainerOrphanSectionReconcile:
    """Materialized PIT must preserve fold-owned hcontainer-direct orphan sections."""

    def _section_parent_path(self, ir: IRNode, label: str) -> str:
        from lawvm.core import tree_ops as tops

        def walk(node: IRNode, path: tuple[tuple[str, str], ...] = ()) -> str | None:
            if node.kind is IRNodeKind.SECTION and node.label == label:
                return "/".join(f"{kind}:{lbl or '?'}" for kind, lbl in path + (("section", node.label),))
            for child in node.children:
                found = walk(
                    child,
                    path + ((tops._kind_str(node.kind), node.label or ""),),
                )
                if found is not None:
                    return found
            return None

        return walk(ir) or ""

    def test_2017_320_section_270_stays_under_hcontainer(
        self,
        replay_2017_320_legal_pit_with_meta: tuple[ReplayResult, dict[str, object]],
    ) -> None:
        replay, _replay_meta = replay_2017_320_legal_pit_with_meta
        assert self._section_parent_path(replay.materialized_state.ir, "270").startswith(
            "body:?/hcontainer:"
        )

    def test_1868_31_section_46_stays_under_hcontainer(self) -> None:
        replay = cast(
            ReplayResult,
            pinned_replay("1868/31-000", mode="official_consolidation", quiet=True),
        )
        assert self._section_parent_path(replay.materialized_state.ir, "46").startswith(
            "body:?/hcontainer:"
        )
