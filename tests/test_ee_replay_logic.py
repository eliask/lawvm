from __future__ import annotations
from lawvm.core.ir import IRStatute

from types import SimpleNamespace

from lawvm.estonia.fetch import AmendmentRef
from lawvm.estonia.ee_instruction_waist import make_section_selection_meta
from lawvm.estonia.grafter import _ee_apply_text_replace_value, apply_ee_ops
from lawvm.estonia.replay import (
    _derive_ee_temporal_expiry_events,
    _ee_filter_cancelled_pending_refs,
    _ee_filter_ops_for_ref_slice,
    replay_ee_to_pit,
)
from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, StructuralAction
from lawvm.core.ir import OperationSource
from lawvm.core.semantic_types import IRNodeKind


def _ref(akt_viide: str, passed: str, joustumine: str) -> AmendmentRef:
    return AmendmentRef(aktViide=akt_viide, passed=passed, joustumine=joustumine)


def test_filter_cancelled_pending_refs_drops_future_effect_source_repealed_before_commencement(
    monkeypatch,
) -> None:
    source_xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi><nimi><pealkiri>Ettevõtlustulu lihtsustatud maksustamise seaduse ja tulumaksuseaduse muutmise ning julgeolekumaksu seaduse kehtetuks tunnistamise seadus</pealkiri></nimi></aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Ettevõtlustulu lihtsustatud maksustamise seaduse muutmine</paragrahvPealkiri>
          <sisuTekst><tavatekst>Ettevõtlustulu lihtsustatud maksustamise seaduses tehakse järgmised muudatused.</tavatekst></sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>4</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst><tavatekst>Käesoleva seaduse §-d 1 ja 3 jõustuvad 2026. aasta 1. jaanuaril.</tavatekst></sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")
    repealer_xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi><nimi><pealkiri>Ettevõtlustulu lihtsustatud maksustamise seaduse ja tulumaksuseaduse muutmise ning julgeolekumaksu seaduse kehtetuks tunnistamise seaduse muutmine</pealkiri></nimi></aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Ettevõtlustulu lihtsustatud maksustamise seaduse ja tulumaksuseaduse muutmise ning julgeolekumaksu seaduse kehtetuks tunnistamise seaduse muutmine</paragrahvPealkiri>
          <sisuTekst><tavatekst>Ettevõtlustulu lihtsustatud maksustamise seaduse ja tulumaksuseaduse muutmise ning julgeolekumaksu seaduse kehtetuks tunnistamise seaduse §-d 1 ja 3 ning § 4 lõige 1 jäetakse välja.</tavatekst></sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    xml_by_id = {
        "108072025001": source_xml,
        "118122025003": repealer_xml,
    }
    monkeypatch.setattr(
        "lawvm.estonia.replay.fetch_rt_xml",
        lambda akt_viide, archive: xml_by_id[akt_viide],
    )

    refs = [
        _ref("108072025001", "2025-06-18", "2026-01-01"),
        _ref("118122025003", "2025-12-03", "2026-01-01"),
    ]

    filtered = _ee_filter_cancelled_pending_refs(
        refs,
        target_title="Ettevõtlustulu lihtsustatud maksustamise seadus",
        archive=None,
    )

    assert [ref.aktViide for ref in filtered] == ["118122025003"]


def test_filter_cancelled_pending_refs_drops_future_effect_source_rewritten_before_commencement(
    monkeypatch,
) -> None:
    source_xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi><nimi><pealkiri>Alusharidusseaduse ning Eesti Vabariigi haridusseaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seadus</pealkiri></nimi></aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>75</paragrahvNr>
          <paragrahvPealkiri>Toiduseaduse muutmine</paragrahvPealkiri>
          <sisuTekst><tavatekst>Toiduseaduse § 8 lõike 1 punktis 1² asendatakse sõnad „koolieelne lasteasutus” sõnadega „lastehoid, lasteaed”.</tavatekst></sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")
    rewriter_xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi><nimi><pealkiri>Alusharidusseaduse ning Eesti Vabariigi haridusseaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seaduse muutmise seadus</pealkiri></nimi></aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Alusharidusseaduse ning Eesti Vabariigi haridusseaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>4)</b> paragrahvi 75 tekst muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„Toiduseaduse § 8 lõike 1 punktis 1<sup>2</sup> asendatakse sõnad „koolieelne lasteasutus” sõnaga „lasteaed”.”.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    xml_by_id = {
        "109012025001": source_xml,
        "101072025001": rewriter_xml,
    }
    monkeypatch.setattr(
        "lawvm.estonia.replay.fetch_rt_xml",
        lambda akt_viide, archive: xml_by_id[akt_viide],
    )

    refs = [
        _ref("109012025001", "2024-12-11", "2025-09-01"),
        _ref("101072025001", "2025-06-18", "2025-09-01"),
    ]

    filtered = _ee_filter_cancelled_pending_refs(
        refs,
        target_title="Toiduseadus",
        archive=None,
    )

    assert [ref.aktViide for ref in filtered] == ["101072025001"]


def test_filter_ops_for_ref_slice_prefers_clause_local_effective_ops_for_later_same_act_slice() -> None:
    ref = _ref("13361493", "2010-09-16", "2012-01-01")
    base_refs = (
        _ref("13361493", "2010-09-16", "2010-10-15"),
        _ref("103022011001", "2011-02-13", "2011-02-13"),
    )
    ops = [
        LegalOperation(
            op_id="earlier-slice-op",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "8_1"),)),
            payload=IRNode(kind=IRNodeKind.CONTENT, text="earlier"),
            source=OperationSource(statute_id="ee/13361493", effective="", raw_text="§ 65"),
        ),
        LegalOperation(
            op_id="later-slice-op",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
            source=OperationSource(statute_id="ee/13361493", effective="2012-01-01", raw_text="§ 66"),
        ),
    ]

    filtered = _ee_filter_ops_for_ref_slice(
        ops,
        ref=ref,
        base_refs=base_refs,
    )

    assert [op.op_id for op in filtered] == ["later-slice-op"]


def test_filter_ops_for_ref_slice_keeps_unsliced_and_current_local_ops_on_earliest_slice() -> None:
    ref = _ref("130062020007", "2020-06-15", "2020-07-01")
    ops = [
        LegalOperation(
            op_id="unsliced-op",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.CONTENT, text="base"),
            source=OperationSource(statute_id="ee/130062020007", effective="", raw_text="§ 1 p 3"),
        ),
        LegalOperation(
            op_id="earliest-local-op",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "111"),)),
            payload=IRNode(kind=IRNodeKind.CONTENT, text="early"),
            source=OperationSource(statute_id="ee/130062020007", effective="2020-07-01", raw_text="§ 1 p 55"),
        ),
        LegalOperation(
            op_id="later-local-op",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "88"), ("subsection", "3"))),
            source=OperationSource(statute_id="ee/130062020007", effective="2021-01-01", raw_text="§ 1 p 56"),
        ),
    ]

    filtered = _ee_filter_ops_for_ref_slice(
        ops,
        ref=ref,
        base_refs=(),
        all_refs=(
            _ref("130062020007", "2020-06-15", "2020-07-01"),
            _ref("130062020007", "2020-06-15", "2021-01-01"),
        ),
    )

    assert [op.op_id for op in filtered] == ["unsliced-op", "earliest-local-op"]


def test_filter_ops_for_ref_slice_keeps_retroactive_local_ops_on_earliest_slice() -> None:
    ref = _ref("121042020001", "2020-04-15", "2020-04-22")
    ops = [
        LegalOperation(
            op_id="retroactive-local-op",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "25_2"),)),
            payload=IRNode(kind=IRNodeKind.CONTENT, text="retroactive"),
            source=OperationSource(
                statute_id="ee/121042020001",
                effective="2020-01-01",
                raw_text="§ 10 p 2",
            ),
        ),
    ]

    filtered = _ee_filter_ops_for_ref_slice(
        ops,
        ref=ref,
        base_refs=(),
        all_refs=(ref,),
        as_of="2021-04-01",
    )

    assert [op.op_id for op in filtered] == ["retroactive-local-op"]


def test_filter_ops_for_ref_slice_keeps_later_local_ops_when_no_later_ref_slice_exists() -> None:
    ref = _ref("127122016002", "2016-12-27", "2018-01-01")
    base_refs = (
        _ref("127122016002", "2016-12-27", "2017-01-06"),
        _ref("127122016002", "2016-12-27", "2018-01-01"),
    )
    ops = [
        LegalOperation(
            op_id="earliest-local-op",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "26_1"), ("subsection", "4_4"))),
            source=OperationSource(statute_id="ee/127122016002", effective="2018-01-01", raw_text="§ 1 p 3"),
        ),
        LegalOperation(
            op_id="later-local-op",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "26_1"), ("subsection", "4_15"))),
            payload=IRNode(kind=IRNodeKind.CONTENT, text="later"),
            source=OperationSource(statute_id="ee/127122016002", effective="2018-12-01", raw_text="§ 1 p 7"),
        ),
        LegalOperation(
            op_id="latest-local-op",
            sequence=3,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "26_1"), ("subsection", "14"))),
            source=OperationSource(statute_id="ee/127122016002", effective="2019-01-01", raw_text="§ 1 p 12"),
        ),
    ]

    filtered = _ee_filter_ops_for_ref_slice(
        ops,
        ref=ref,
        base_refs=base_refs,
        all_refs=(ref,),
        as_of="2019-01-01",
    )

    assert [op.op_id for op in filtered] == [
        "earliest-local-op",
        "later-local-op",
        "latest-local-op",
    ]


def test_replay_ee_to_pit_keeps_payload_embedded_alates_phrase_inside_replacement_text() -> None:
    result = replay_ee_to_pit(
        "117042021005",
        as_of="2021-12-04",
        oracle_id="103122021023",
    )

    assert result.error is None
    assert result.n_ops == 2


def test_replay_ee_to_pit_does_not_reapply_unsliced_insert_on_later_same_act_slice() -> None:
    result = replay_ee_to_pit(
        "110112022009",
        as_of="2025-01-01",
        oracle_id="104122024022",
    )

    assert result.error is None
    assert result.divergences == []
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_replay_ee_to_pit_closes_spordiseadus_insert_after_duplication_family() -> None:
    result = replay_ee_to_pit(
        "110072025004",
        as_of="2026-01-01",
        oracle_id="110072025005",
    )

    assert result.error is None
    assert result.divergences == []


def test_replay_ee_to_pit_respects_old_format_commencement_range_after_item_gap() -> None:
    result = replay_ee_to_pit(
        "119032013007",
        as_of="2015-01-01",
        oracle_id="112072014164",
    )

    assert result.error is None
    assert [str(div.address) for div in result.divergences] == [
        "chapter:10",
        "chapter:10/section:53_5",
        "chapter:10/section:53_5/subsection:2",
    ]


def test_replay_ee_to_pit_respects_multiple_partial_commencement_dates_in_one_note() -> None:
    result = replay_ee_to_pit(
        "128122011069",
        as_of="2014-07-01",
        oracle_id="129062014128",
    )

    assert result.error is None
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_replay_ee_to_pit_keeps_retroactive_local_insertions_from_earliest_slice() -> None:
    result = replay_ee_to_pit(
        "127022019013",
        as_of="2021-04-01",
        oracle_id="122032021011",
    )

    assert result.error is None
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_replay_ee_to_pit_ignores_sentence_note_phrases_inside_replacement_payload() -> None:
    result = replay_ee_to_pit(
        "121122016033",
        as_of="2017-01-01",
        oracle_id="121122016034",
    )

    assert result.error is None
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_replay_ee_to_pit_respects_new_format_html_commencement_item_slices() -> None:
    result = replay_ee_to_pit(
        "131032022006",
        as_of="2025-01-01",
        oracle_id="129062024012",
    )

    assert result.error is None
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_replay_ee_to_pit_keeps_mixed_delete_and_replace_sentence_targets_distinct() -> None:
    result = replay_ee_to_pit(
        "130122025021",
        as_of="2027-01-01",
        oracle_id="130122025022",
    )

    assert result.error is None
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_replay_ee_to_pit_superscript_subsection_repeals_do_not_clear_plain_base_subsection() -> None:
    result = replay_ee_to_pit(
        "127092024014",
        as_of="2025-10-01",
        oracle_id="107052025002",
    )

    assert result.error is None
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    assert result.divergences == []


def test_case_inflected_delete_matches_plural_used_phrase_forms() -> None:
    text = "Keskkonnaministeerium kaasab töösse maavalitsusi ja kohalikke omavalitsusi."

    updated = _ee_apply_text_replace_value(
        text,
        "maavalitsused ja",
        "",
        case_inflected=True,
    )

    assert updated == "Keskkonnaministeerium kaasab töösse kohalikke omavalitsusi."


def test_case_inflected_delete_matches_trailing_punctuation_forms() -> None:
    text = (
        "Keskkonnaamet kaasab tegevuskava koostamisse vesikonna territooriumil "
        "asuvaid maavalitsusi, kohalikke omavalitsusi ning teisi asjast huvitatud isikuid."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "maavalitsus,",
        "",
        case_inflected=True,
    )

    assert (
        updated
        == "Keskkonnaamet kaasab tegevuskava koostamisse vesikonna territooriumil "
        "asuvaid kohalikke omavalitsusi ning teisi asjast huvitatud isikuid."
    )


def test_apply_ee_ops_expands_plain_section_repeal_ranges_over_live_superscript_sections() -> None:
    base = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    text="Chapter",
                    children=(
                        IRNode(kind=IRNodeKind.SECTION, label="8", text="base 8"),
                        IRNode(kind=IRNodeKind.SECTION, label="8_1", text="base 8_1"),
                        IRNode(kind=IRNodeKind.SECTION, label="9", text="base 9"),
                        IRNode(kind=IRNodeKind.SECTION, label="10", text="base 10"),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="plain-range-repeal",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "8"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={
                "section_selection_meta": make_section_selection_meta(
                    explicit_labels=("8", "9"),
                    plain_numeric_ranges=(("8", "9"),),
                )
            },
        ),
        source=OperationSource(
            statute_id="ee/test-amendment",
            raw_text="§-d 8–9 tunnistatakse kehtetuks",
        ),
    )

    replayed = apply_ee_ops(base, [op])
    chapter = replayed.body.children[0]
    sections = {child.label: child for child in chapter.children}

    assert sections["8"].attrs.get("kehtetu") is True
    assert sections["8_1"].attrs.get("kehtetu") is True
    assert sections["9"].attrs.get("kehtetu") is True
    assert sections["10"].attrs.get("kehtetu") is None


def test_apply_ee_ops_subsection_insert_is_noop_when_identical_slot_already_exists() -> None:
    base = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    text="Chapter",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="26_6",
                            text="Title",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Existing"),
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="4_1",
                                    text="Riigipiiri uletava pohjaveekogumi korral kooskolastatakse.",
                                ),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="After"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="subsection-insert-same-slot",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "26_6"), ("subsection", "4_1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="(4 1) Riigipiiri uletava pohjaveekogumi korral kooskolastatakse.",
        ),
        source=OperationSource(statute_id="ee/test-amendment", raw_text="§ 1 p 3"),
    )

    replayed = apply_ee_ops(base, [op])
    chapter = replayed.body.children[0]
    section = chapter.children[0]
    subsection = next(
        child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "4_1"
    )

    assert subsection.text == "Riigipiiri uletava pohjaveekogumi korral kooskolastatakse."


def test_replay_ee_to_pit_covers_live_superscript_section_inside_plain_repeal_range() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "103022011005",
        "2012-01-01",
        archive=archive,
        oracle_id="103022011007",
    )

    assert result.error is None
    assert result.oracle_id == "103022011007"
    assert not any(
        str(div.address).startswith("chapter:2/section:8_1")
        for div in result.divergences
    )


def test_replay_ee_to_pit_treats_kehtetu_child_section_heading_as_empty_in_parent_comparison() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "116062020010",
        "2025-09-01",
        archive=archive,
        oracle_id="102012025043",
    )

    assert result.error is None
    assert result.oracle_id == "102012025043"
    assert result.divergences == []


def test_replay_ee_to_pit_does_not_reapply_whole_mixed_commencement_act_on_later_slice() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "106052020038",
        "2023-07-01",
        archive=archive,
        oracle_id="127092023012",
    )

    assert result.error is None
    assert result.oracle_id == "127092023012"
    assert result.divergences == []


def test_replay_ee_to_pit_honors_mixed_global_replace_exclusions_in_mahepollumajandus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "124112016002",
        "2023-07-01",
        archive=archive,
        oracle_id="127092023006",
    )

    assert result.error is None
    assert result.oracle_id == "127092023006"
    assert result.divergences == []


def test_replay_ee_to_pit_handles_elukaaslane_comma_insert_after_family_in_perekonnaseadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "111012023012",
        "2025-01-01",
        archive=archive,
        oracle_id="107052025017",
    )

    assert result.error is None
    assert result.oracle_id == "107052025017"
    assert result.divergences == []


def test_replay_ee_to_pit_handles_insert_after_sonu_once_in_ohvriabi() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "104112016005",
        "2019-01-14",
        archive=archive,
        oracle_id="104012019016",
    )

    assert result.error is None
    assert result.oracle_id == "104012019016"
    assert result.divergences == []


def test_replay_ee_to_pit_preserves_semicolon_before_empty_item_stubs_in_huvikooli() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "103052017009",
        "2025-09-01",
        archive=archive,
        oracle_id="109012025004",
    )

    assert result.error is None
    assert result.oracle_id == "109012025004"
    assert result.divergences == []


def test_replay_ee_to_pit_applies_labivalt_insert_after_to_all_matches_in_fims() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "123122022027",
        "2025-01-01",
        archive=archive,
        oracle_id="114032025014",
    )

    assert result.error is None
    assert result.oracle_id == "114032025014"
    assert result.divergences == []


def test_replay_ee_to_pit_keeps_duplicate_top_level_parts_distinct_in_laevaregistrid() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "114032025021",
        "2025-05-01",
        archive=archive,
        oracle_id="117042025031",
    )

    assert result.error is None
    assert result.oracle_id == "117042025031"
    assert result.divergences == []


def test_replay_ee_to_pit_fans_out_shared_heading_rename_in_meretoo() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "118032022002",
        "2026-02-07",
        archive=archive,
        oracle_id="128012026006",
    )

    assert result.error is None
    assert result.oracle_id == "128012026006"
    assert result.divergences == []


def test_replay_ee_to_pit_carries_s_inflected_wrapper_intro_context_in_lahetatud_tootajad() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "105102022003",
        "2026-01-01",
        archive=archive,
        oracle_id="117042025018",
    )

    assert result.error is None
    assert result.oracle_id == "117042025018"
    assert result.divergences == []


def test_replay_ee_to_pit_collapses_generic_minister_tail_in_rahuaja_riigikaitse() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "120032013023",
        "2015-09-01",
        archive=archive,
        oracle_id="101092015026",
    )

    assert result.error is None
    assert result.oracle_id == "101092015026"
    assert result.divergences == []


def test_replay_ee_to_pit_applies_maavalitsus_delete_rewrites_in_veeseadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "127122016006",
        "2019-01-01",
        archive=archive,
        oracle_id="112122018081",
    )

    assert result.error is None
    assert result.oracle_id == "112122018081"
    assert result.divergences == []


def test_replay_ee_to_pit_closes_advokatuur_chapter4_and_leaves_only_oracle_drift_tail() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "122122020038",
        "2025-01-01",
        archive=archive,
        oracle_id="114032025004",
    )

    assert result.error is None
    assert result.oracle_id == "114032025004"
    assert {str(div.address) for div in result.divergences} == {
        "chapter:7",
        "chapter:7/section:82_5",
        "chapter:7/section:82_5/subsection:1",
    }


def test_replay_ee_to_pit_keeps_nominative_ministry_in_vaarteomenetlus_coordination() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "122032024011",
        "2025-07-06",
        archive=archive,
        oracle_id="105072025019",
    )

    assert result.error is None
    assert result.oracle_id == "105072025019"
    assert result.divergences == []


def test_replay_ee_to_pit_preserves_duplicate_loige_numbers_in_raudteeseadus_section_20() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "129112018003",
        "2019-11-30",
        archive=archive,
        oracle_id="129112019003",
    )

    assert result.error is None
    assert result.oracle_id == "129112019003"
    assert result.divergences == []


def test_replay_ee_to_pit_respects_old_format_partial_commencement_in_riigipiiri() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "107062024013",
        "2025-10-12",
        archive=archive,
        oracle_id="107062024014",
    )

    assert result.error is None
    assert result.oracle_id == "107062024014"
    assert result.divergences == []


def test_replay_ee_to_pit_replays_riigikogu_term_start_slice_in_erakonnaseadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "105022014005",
        "2023-02-01",
        archive=archive,
        oracle_id="105052022008",
    )

    assert result.error is None
    assert result.oracle_id == "105052022008"
    assert result.divergences == []


def test_replay_ee_to_pit_does_not_reapply_earlier_old_format_target_on_later_omnibus_slice() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "121052014030",
        "2014-07-01",
        archive=archive,
        oracle_id="121052014031",
    )

    assert result.error is None
    assert result.oracle_id == "121052014031"
    assert {
        str(div.address)
        for div in result.divergences
    } == {
        "chapter:5",
        "chapter:5/division:3",
        "chapter:5/division:3/section:31",
        "chapter:5/division:3/section:31/subsection:1",
        "chapter:16",
        "chapter:16/section:94",
        "chapter:16/section:94/subsection:6_3",
        "chapter:16/section:95",
        "chapter:16/section:95/subsection:3",
    }


def test_replay_ee_to_pit_handles_year_prefixed_target_title_in_riigieelarve_amendment() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "126062024004",
        "2024-12-12",
        archive=archive,
        oracle_id="111122024014",
    )

    assert result.error is None
    assert result.oracle_id == "111122024014"
    assert result.divergences == []


def test_replay_ee_to_pit_repeals_companion_subsection_tail_in_planeerimisseadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "111062024012",
        "2025-01-01",
        archive=archive,
        oracle_id="130122024014",
    )

    assert result.error is None
    assert result.oracle_id == "130122024014"
    assert result.divergences == []


def test_replay_ee_to_pit_handles_section_scoped_target_paragraph_title_in_asjaoigus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "111112025002",
        "2026-02-07",
        archive=archive,
        oracle_id="128012026007",
    )

    assert result.error is None
    assert result.oracle_id == "128012026007"
    assert result.divergences == []


def test_replay_ee_to_pit_finalizes_last_live_item_before_short_repeal_tail_in_fkes() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "111102024005",
        "2025-11-21",
        archive=archive,
        oracle_id="111112025017",
    )

    assert result.error is None
    assert result.oracle_id == "111112025017"
    assert result.divergences == []


def test_replay_ee_to_pit_keeps_subsection_intro_text_replace_local_in_maagaasiseadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "109082022022",
        "2024-10-18",
        archive=archive,
        oracle_id="108102024012",
    )

    assert result.error is None
    assert result.oracle_id == "108102024012"
    divergence_addresses = {str(div.address) for div in result.divergences}
    assert "chapter:3/section:23_3" not in divergence_addresses
    assert "chapter:3/section:26_7/subsection:2/item:1" not in divergence_addresses
    assert "chapter:3/section:26_7/subsection:2/item:3" not in divergence_addresses
    assert {
        "chapter:3/section:26_7/subsection:1",
        "chapter:3/section:26_7/subsection:2",
        "chapter:3/section:26_7/subsection:2/item:6",
    }.issubset(divergence_addresses)


def test_replay_ee_to_pit_applies_case_inflected_protocol_compound_rewrite() -> None:
    from lawvm.estonia.fetch import open_rt_archive

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "112032019073",
        "2023-07-18",
        archive=archive,
        oracle_id="115072023052",
    )

    assert result.error is None
    divergence_by_address = {str(div.address): div for div in result.divergences}
    assert "chapter:2/section:7" not in divergence_by_address
    assert "chapter:3/section:10/subsection:2" not in divergence_by_address
    assert "chapter:3/section:10/subsection:4" not in divergence_by_address
    assert "chapter:3/section:10/subsection:5" not in divergence_by_address
    subsection_1 = divergence_by_address["chapter:3/section:10/subsection:1"]
    assert "transfusiooniprotokollis" in (subsection_1.ops_text or "")
    assert "transfusiooniprotokolliks" in (subsection_1.consolidated_text or "")


def test_replay_ee_to_pit_applies_old_format_typographic_quote_text_replace() -> None:
    from lawvm.estonia.fetch import open_rt_archive

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "129122014074",
        "2016-02-21",
        archive=archive,
        oracle_id="118022016007",
    )

    assert result.error is None
    assert result.divergences == []


def test_replay_ee_to_pit_recovers_marutaudi_old_format_regulation_items() -> None:
    from lawvm.estonia.fetch import open_rt_archive

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "107072011007",
        "2019-12-14",
        archive=archive,
        oracle_id="111122019011",
    )

    assert result.error is None
    assert result.n_ops == 40
    assert {str(div.address) for div in result.divergences} == {
        "chapter:4",
        "chapter:4/division:3",
        "chapter:4/division:3/section:15",
        "chapter:4/division:3/section:15/subsection:5",
    }


def test_replay_ee_to_pit_recovers_newcastle_single_clause_and_nested_quote_families() -> None:
    from lawvm.estonia.fetch import open_rt_archive

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "131052011009",
        "2019-12-14",
        archive=archive,
        oracle_id="111122019012",
    )

    assert result.error is None
    assert result.n_ops == 73
    divergence_addresses = {str(div.address) for div in result.divergences}
    assert "chapter:2/section:4/subsection:3/item:7" not in divergence_addresses
    assert "chapter:2/section:4_1/subsection:8" not in divergence_addresses
    assert "chapter:3/division:1/section:8/subsection:1/item:8" not in divergence_addresses
    assert "chapter:3/division:3/section:16/subsection:3/item:3" not in divergence_addresses


def test_replay_ee_to_pit_classifies_vereulekanne_protocol_case_oracle_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "112032019073",
        "2023-07-18",
        archive=archive,
        oracle_id="115072023052",
    )

    assert result.error is None
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert "chapter:3/section:10/subsection:1" in divergence_addresses
    residual_summary = build_ee_residual_summary(
        base_id="112032019073",
        oracle_id="115072023052",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)
    assert residual_summary.matched_current_bucket_counts == {"source_oracle_drift": len(divergence_addresses)}


def test_replay_ee_to_pit_handles_finite_verb_taotlusel_genitive_in_riigisaladus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "106052020036",
        "2026-02-04",
        archive=archive,
        oracle_id="103022026013",
    )

    assert result.error is None
    assert result.oracle_id == "103022026013"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert "chapter:2/division:3/section:23/subsection:2/item:3" not in divergence_addresses
    residual_summary = build_ee_residual_summary(
        base_id="106052020036",
        oracle_id="103022026013",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0


def test_replay_ee_to_pit_replays_later_same_act_default_slice_in_perehuvitised() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "108042025003",
        "2026-10-01",
        archive=archive,
        oracle_id="108042025004",
    )

    assert result.error is None
    assert result.oracle_id == "108042025004"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert "section:63_11" not in divergence_addresses
    assert "section:4/subsection:1" not in divergence_addresses
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    residual_summary = build_ee_residual_summary(
        base_id="108042025003",
        oracle_id="108042025004",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_closes_vaarismetalltoodete_same_chain_target_block() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "130042024013",
        "2025-07-01",
        archive=archive,
        oracle_id="130042024014",
    )

    assert result.error is None
    assert result.oracle_id == "130042024014"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="130042024013",
        oracle_id="130042024014",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_closes_kasuliku_mudeli_2023_patendiamet_cleanup() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "130032023005",
        "2023-08-31",
        archive=archive,
        oracle_id="130032023006",
    )

    assert result.error is None
    assert result.oracle_id == "130032023006"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="130032023005",
        oracle_id="130032023006",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_adjudicates_mikrolulituse_topoloogia_same_chain_tail() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "106012023046",
        "2023-08-31",
        archive=archive,
        oracle_id="106012023047",
    )

    assert result.error is None
    assert result.oracle_id == "106012023047"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:6",
        "chapter:6/section:43",
        "chapter:6/section:43/subsection:4",
    }
    residual_summary = build_ee_residual_summary(
        base_id="106012023046",
        oracle_id="106012023047",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_tarbijakaitseseadus_same_chain_punctuation_tail() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "131122013007",
        "2014-06-13",
        archive=archive,
        oracle_id="131122013008",
    )

    assert result.error is None
    assert result.oracle_id == "131122013008"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:6",
        "chapter:6/section:41_1",
        "chapter:6/section:41_1/subsection:1",
    }
    residual_summary = build_ee_residual_summary(
        base_id="131122013007",
        oracle_id="131122013008",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_asjaoigusseaduse_rakendamise_forward_looking_slice() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "115032013034",
        "2017-03-01",
        archive=archive,
        oracle_id="125012017006",
    )

    assert result.error is None
    assert result.oracle_id == "125012017006"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "section:12",
        "section:12/subsection:5",
    }
    residual_summary = build_ee_residual_summary(
        base_id="115032013034",
        oracle_id="125012017006",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_vanemahuvitise_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "110012014014",
        "2016-07-18",
        archive=archive,
        oracle_id="108072016042",
    )

    assert result.error is None
    assert result.oracle_id == "108072016042"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "section:3",
        "section:3/subsection:7_2",
        "section:5",
        "section:5/subsection:2",
    }
    residual_summary = build_ee_residual_summary(
        base_id="110012014014",
        oracle_id="108072016042",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_autoveoseaduse_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "104072017126",
        "2018-01-21",
        archive=archive,
        oracle_id="111012018009",
    )

    assert result.error is None
    assert result.oracle_id == "111012018009"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:6",
        "chapter:6/section:23_1",
        "chapter:6/section:23_1/subsection:1",
        "chapter:7_1",
        "chapter:7_1/section:31_12",
        "chapter:7_1/section:31_12/subsection:1",
        "chapter:7_1/section:31_13",
        "chapter:7_1/section:31_13/subsection:1",
        "chapter:7_1/section:31_13/subsection:2",
        "chapter:7_1/section:31_14",
        "chapter:7_1/section:31_14/subsection:1",
    }
    residual_summary = build_ee_residual_summary(
        base_id="104072017126",
        oracle_id="111012018009",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_taiskasvanute_koolituse_mixed_noncore_row() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "118032011008",
        "2013-09-01",
        archive=archive,
        oracle_id="111072013019",
    )

    assert result.error is None
    assert result.oracle_id == "111072013019"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:2",
        "chapter:2/section:6",
        "chapter:2/section:6/subsection:1",
        "chapter:2/section:6/subsection:1/item:3",
        "chapter:2/section:6_1",
        "chapter:2/section:6_1/subsection:2",
        "chapter:5",
        "chapter:5/section:16_1",
        "chapter:5/section:16_1/subsection:4",
    }
    residual_summary = build_ee_residual_summary(
        base_id="118032011008",
        oracle_id="111072013019",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_prokuratuuri_forward_looking_ministry_rename() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "102062020008",
        "2024-03-15",
        archive=archive,
        oracle_id="114032025020",
    )

    assert result.error is None
    assert result.oracle_id == "114032025020"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:1",
        "chapter:1/section:1",
        "chapter:1/section:1/subsection:1",
        "chapter:2",
        "chapter:2/section:9",
        "chapter:2/section:9/subsection:1",
        "chapter:4",
        "chapter:4/division:6",
        "chapter:4/division:6/section:43",
        "chapter:4/division:6/section:43/subsection:2",
        "chapter:4/division:8",
        "chapter:4/division:8/section:52",
        "chapter:4/division:8/section:52/subsection:2",
        "chapter:4/division:8/section:52/subsection:3",
        "chapter:4/division:8/section:52/subsection:4",
        "chapter:4/division:8/section:52/subsection:5",
    }
    residual_summary = build_ee_residual_summary(
        base_id="102062020008",
        oracle_id="114032025020",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_ettevotlustulu_julgeolekumaks_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "119122024003",
        "2026-01-01",
        archive=archive,
        oracle_id="118122025016",
    )

    assert result.error is None
    assert result.oracle_id == "118122025016"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "section:4",
        "section:4/subsection:3",
        "section:8",
        "section:8/subsection:1",
        "section:8/subsection:3_1",
    }
    residual_summary = build_ee_residual_summary(
        base_id="119122024003",
        oracle_id="118122025016",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_korteriomandiseaduse_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "125052012018",
        "2014-05-22",
        archive=archive,
        oracle_id="121052014019",
    )

    assert result.error is None
    assert result.oracle_id == "121052014019"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:2",
        "chapter:2/section:8",
        "chapter:2/section:8/subsection:1_1",
        "chapter:2/section:13",
        "chapter:2/section:13/subsection:4",
        "chapter:2/section:13/subsection:5",
        "chapter:2/section:16_1",
        "chapter:2/section:16_1/subsection:1",
        "chapter:2/section:16_1/subsection:1/item:1",
        "chapter:2/section:16_1/subsection:1/item:2",
        "chapter:2/section:16_1/subsection:2",
    }
    residual_summary = build_ee_residual_summary(
        base_id="125052012018",
        oracle_id="121052014019",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_sotsiaalhoolekande_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "130122011047",
        "2013-12-23",
        archive=archive,
        oracle_id="113122013023",
    )

    assert result.error is None
    assert result.oracle_id == "113122013023"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:3_1",
        "chapter:3_1/section:21_3",
        "chapter:3_1/section:21_3/subsection:2",
        "chapter:3_1/section:21_3/subsection:2/item:7",
        "chapter:4",
        "chapter:4/section:22_1",
        "chapter:4/section:22_1/subsection:3",
        "chapter:4/section:22_1/subsection:3/item:2",
        "chapter:4/section:23_1",
        "chapter:4/section:23_1/subsection:5",
        "chapter:4/section:23_1/subsection:5/item:1",
    }
    residual_summary = build_ee_residual_summary(
        base_id="130122011047",
        oracle_id="113122013023",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_alkoholiseadus_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "120022015005",
        "2018-01-19",
        archive=archive,
        oracle_id="109012018006",
    )

    assert result.error is None
    assert result.oracle_id == "109012018006"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert set(divergence_addresses) == {
        "chapter:2",
        "chapter:2/division:6",
        "chapter:2/division:6/section:40",
        "chapter:2/division:6/section:40/subsection:1_2",
        "chapter:2/division:6/section:40/subsection:1_3",
        "chapter:2/division:6/section:40/subsection:2_1",
        "chapter:2/division:6/section:40/subsection:4",
        "chapter:2/division:6/section:42",
        "chapter:2/division:6/section:42/subsection:1",
        "chapter:2/division:6/section:42/subsection:1/item:1",
        "chapter:2/division:6/section:42/subsection:1/item:3",
    }
    residual_summary = build_ee_residual_summary(
        base_id="120022015005",
        oracle_id="109012018006",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_riigiloivuseadus_forward_looking_oracle() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "127122013026",
        "2014-12-01",
        archive=archive,
        oracle_id="129102014007",
    )

    assert result.error is None
    assert result.oracle_id == "129102014007"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="127122013026",
        oracle_id="129102014007",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_elektrituruseadus_now_replays_cleanly() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "118032026018",
        "2026-08-01",
        archive=archive,
        oracle_id="118032026019",
    )

    assert result.error is None
    assert result.oracle_id == "118032026019"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert divergence_addresses == ()
    residual_summary = build_ee_residual_summary(
        base_id="118032026018",
        oracle_id="118032026019",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_energiamajandus_now_replays_cleanly() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "118032026031",
        "2030-01-01",
        archive=archive,
        oracle_id="118032026032",
    )

    assert result.error is None
    assert result.oracle_id == "118032026032"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert divergence_addresses == ()
    residual_summary = build_ee_residual_summary(
        base_id="118032026031",
        oracle_id="118032026032",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_keeps_plain_range_endpoint_superscript_subsection() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "102102025017",
        "2026-01-01",
        archive=archive,
        oracle_id="102102025018",
    )

    assert result.error is None
    assert result.oracle_id == "102102025018"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert divergence_addresses == ()
    residual_summary = build_ee_residual_summary(
        base_id="102102025017",
        oracle_id="102102025018",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_closes_volgade_umberkujundamise_kohtujurist_case_family() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "104012021044",
        "2021-02-01",
        archive=archive,
        oracle_id="104012021045",
    )

    assert result.error is None
    assert result.oracle_id == "104012021045"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="104012021044",
        oracle_id="104012021045",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_applies_section_and_chapter_compound_repeal() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "112062025015",
        "2026-01-01",
        archive=archive,
        oracle_id="112062025016",
    )

    assert result.error is None
    assert result.oracle_id == "112062025016"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    assert divergence_addresses == ()
    residual_summary = build_ee_residual_summary(
        base_id="112062025015",
        oracle_id="112062025016",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_adjudicates_riikliku_pensionikindlustuse_same_chain_editorial_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "112062025011",
        "2037-01-01",
        archive=archive,
        oracle_id="112062025012",
    )

    assert result.error is None
    assert result.oracle_id == "112062025012"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="112062025011",
        oracle_id="112062025012",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_kov_valimise_seadus_same_chain_editorial_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "120052025002",
        "2025-07-09",
        archive=archive,
        oracle_id="120052025003",
    )

    assert result.error is None
    assert result.oracle_id == "120052025003"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="120052025002",
        oracle_id="120052025003",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_krediidiandjate_seadus_same_chain_editorial_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "113022026005",
        "2029-04-01",
        archive=archive,
        oracle_id="113022026006",
    )

    assert result.error is None
    assert result.oracle_id == "113022026006"
    assert result.divergences == []
    assert len(result.temporal_events) == 1
    event = result.temporal_events[0]
    assert event.kind == "expire"
    assert event.expires == "2029-04-01"
    assert event.scope.address_prefixes == (
        LegalAddress(path=(("chapter", "1"), ("section", "2"), ("subsection", "6"))),
    )


def test_replay_ee_to_pit_closes_rahvastikuregister_duplicate_head_after_delete() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "118102024004",
        "2025-12-02",
        archive=archive,
        oracle_id="121112025007",
    )

    assert result.error is None
    assert result.oracle_id == "121112025007"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="118102024004",
        oracle_id="121112025007",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_replays_same_chain_whole_act_default_slice_in_maamaksuseadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "130062024004",
        "2026-01-01",
        archive=archive,
        oracle_id="130062024005",
    )

    assert result.error is None
    assert result.oracle_id == "130062024005"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="130062024004",
        oracle_id="130062024005",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_ignores_quoted_commencement_payloads_in_mittetulundusuhingute_seadus() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "120122018007",
        "2020-05-24",
        archive=archive,
        oracle_id="123052020006",
    )

    assert result.error is None
    assert result.oracle_id == "123052020006"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="120122018007",
        oracle_id="123052020006",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_replays_headerless_old_format_omnibus_paragraph_for_ettevotluse_toetamine() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "129112010006",
        "2011-01-01",
        archive=archive,
        oracle_id="129112010007",
    )

    assert result.error is None
    assert result.oracle_id == "129112010007"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="129112010006",
        oracle_id="129112010007",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_politsei_ja_piirivalve_same_chain_insertions() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "123102025002",
        "2025-10-23",
        archive=archive,
        oracle_id="123102025003",
    )

    assert result.error is None
    assert result.oracle_id == "123102025003"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="123102025002",
        oracle_id="123102025003",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_kutseoppeasutuse_seadus_same_chain_editorial_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "107012026014",
        "2026-06-01",
        archive=archive,
        oracle_id="107012026015",
    )

    assert result.error is None
    assert result.oracle_id == "107012026015"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="107012026014",
        oracle_id="107012026015",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_adjudicates_vangistusseadus_same_chain_editorial_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "126062025020",
        "2027-01-01",
        archive=archive,
        oracle_id="126062025021",
    )

    assert result.error is None
    assert result.oracle_id == "126062025021"
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="126062025020",
        oracle_id="126062025021",
        divergence_addresses=divergence_addresses,
    )
    assert residual_summary is not None
    assert residual_summary.unknown_current_divergence_count == 0
    assert residual_summary.matched_current_divergence_count == len(divergence_addresses)


def test_replay_ee_to_pit_closes_valjateenitud_aastate_pensionide_same_chain_targeting() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.residual_reporting import build_ee_residual_summary
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "122022023015",
        "2027-01-01",
        archive=archive,
        oracle_id="122022023016",
    )

    assert result.error is None
    assert result.oracle_id == "122022023016"
    assert result.n_mismatch == 0
    assert result.n_ops_missing == 0
    assert result.n_con_missing == 0
    divergence_addresses = tuple(str(div.address) for div in result.divergences)
    residual_summary = build_ee_residual_summary(
        base_id="122022023015",
        oracle_id="122022023016",
        divergence_addresses=divergence_addresses,
    )
    if residual_summary is not None:
        assert residual_summary.unknown_current_divergence_count == 0
        assert residual_summary.matched_current_divergence_count == 0


def test_replay_ee_to_pit_closes_sihtasutuste_register_cleanup_and_leaves_only_oracle_drift() -> None:
    from lawvm.estonia.fetch import open_rt_archive
    from lawvm.estonia.replay import replay_ee_to_pit

    archive = open_rt_archive(readonly=True)

    result = replay_ee_to_pit(
        "120062022025",
        "2023-02-01",
        archive=archive,
        oracle_id="123122022031",
    )

    assert result.error is None
    assert result.oracle_id == "123122022031"
    assert {str(div.address) for div in result.divergences} == {
        "chapter:2",
        "chapter:2/section:14",
        "chapter:2/section:14/subsection:1",
        "chapter:2/section:14/subsection:1/item:10_1",
        "chapter:6",
        "chapter:6/section:57_1",
        "chapter:6/section:57_1/subsection:1",
    }


def test_replay_ee_to_pit_threads_temporal_events_into_compile_timelines(
    monkeypatch,
    tmp_path,
) -> None:
    from lawvm.estonia import replay as ee_replay

    event = TemporalEvent(
        event_id="ee-event",
        kind="commence",
        scope=TemporalScope(),
        effective="2025-01-01",
        source=OperationSource(statute_id="ee/BASE", effective="2025-01-01"),
        group_id="g:ee",
    )
    seen: dict[str, object] = {}
    base = IRStatute(
        statute_id="ee/BASE",
        title="Test",
        body=IRNode(kind=IRNodeKind.BODY),
    )

    monkeypatch.setattr(ee_replay, "open_rt_archive", lambda: object())
    monkeypatch.setattr(ee_replay, "fetch_rt_xml", lambda akt_viide, archive: b"<xml/>")
    monkeypatch.setattr(ee_replay, "parse_ee_statute", lambda xml, statute_id: base)
    monkeypatch.setattr(
        ee_replay,
        "plan_ee_oracle_pair",
        lambda **kwargs: SimpleNamespace(
            plan=SimpleNamespace(
                grupi_id="g1",
                oracle_id=None,
                source_basis=SimpleNamespace(value="oracle"),
                comparison_class="oracle",
                source_adjudication=None,
                oracle_is_base=True,
                oracle_refs=[],
                amendments_to_apply=[],
                base_is_consolidated=False,
                base_refs=[SimpleNamespace(aktViide="1991/1", joustumine="2025-01-01", passed="2024-01-01")],
            ),
            oracle_xml=None,
        ),
    )
    monkeypatch.setattr(ee_replay, "_ee_filter_cancelled_pending_refs", lambda refs, **kwargs: refs)
    monkeypatch.setattr(ee_replay, "parse_ee_amendment_ops", lambda xml, statute_id, target_title=None: [])
    monkeypatch.setattr(ee_replay, "apply_ee_ops", lambda statute, ops, **kwargs: statute)

    def fake_compile_timelines(base_ir, lo_ops_out, temporal_events=()):
        seen["temporal_events"] = temporal_events
        return {"seen": True}

    monkeypatch.setattr(ee_replay, "compile_timelines", fake_compile_timelines)
    monkeypatch.setattr(ee_replay, "materialize_pit", lambda timelines, as_of, base: base)
    monkeypatch.setattr(ee_replay, "ingest_consolidated", lambda oracle, as_of: oracle)
    monkeypatch.setattr(ee_replay, "verify_consistency", lambda *args, **kwargs: [])

    result = ee_replay.replay_ee_to_pit(
        "1991_1",
        "2025-01-01",
        temporal_events=(event,),
    )

    assert seen["temporal_events"] == (event,)
    assert result.temporal_events == (event,)


def test_derive_ee_temporal_expiry_events_from_kehtib_kuni_clause() -> None:
    op = LegalOperation(
        op_id="ee-test-expiry",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "114"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="(2) Käesoleva seaduse § 2 lõige 6 kehtib kuni 2029. aasta 31. märtsini.",
        ),
        source=OperationSource(statute_id="ee/113022026001", effective="2026-02-23"),
    )

    events = _derive_ee_temporal_expiry_events([op], target_statute="ee/113022026005")

    assert len(events) == 1
    event = events[0]
    assert event.kind == "expire"
    assert event.expires == "2029-04-01"
    assert event.scope.target_statute == "ee/113022026005"
    assert event.scope.address_prefixes == (
        LegalAddress(path=(("section", "2"), ("subsection", "6"))),
    )
    assert event.source is not None
    assert event.source.expires == "2029-04-01"
