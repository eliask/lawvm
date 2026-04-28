from __future__ import annotations

from argparse import Namespace

from lawvm.estonia.residual_inventory import (
    _KNOWN_EE_RESIDUALS,
    get_ee_residual_inventory,
    list_known_ee_residual_inventories,
)
from lawvm.estonia.residual_evidence import (
    build_address_list_family,
    build_inserted_item_omission_family,
    build_inserted_note_omission_family,
    build_shortened_section_family,
)
from lawvm.tools import cli, ee_residual_inventory


def test_get_ee_residual_inventory_liiklusseadus() -> None:
    inventory = get_ee_residual_inventory("193936", "13336397")

    assert inventory is not None
    assert inventory.statute_title == "Liiklusseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 7
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
        "appendix_display_pathology",
    }
    assert any(
        record.address == "chapter:15/section:79/subsection:4"
        and record.bucket == "appendix_display_pathology"
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_vabariigi_presidendi_tookorra_seadus() -> None:
    inventory = get_ee_residual_inventory("108072011074", "127062017011")

    assert inventory is not None
    assert inventory.statute_title == "Vabariigi Presidendi töökorra seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:6/section:30"
        and "does not contain § 30 at all" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:6/section:30/subsection:1"
        and "Käesolev seadus jõustub 2001. aasta 1. septembril." in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_maagaasiseadus() -> None:
    inventory = get_ee_residual_inventory("109082022022", "108102024012")

    assert inventory is not None
    assert inventory.statute_title == "Maagaasiseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_ambiguity",
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:3/section:26_7/subsection:1"
        and record.bucket == "source_ambiguity"
        and "two occurrences of 'gaasivaru'" in record.evidence
        and "does not say 'läbivalt'" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:3/section:26_7/subsection:2/item:6"
        and "full stop" in record.evidence
        and "oracle 108102024012 drops it" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_ehitusseadustiku_ja_planeerimisseaduse_rakendamise_seadus_is_closed() -> None:
    inventory = get_ee_residual_inventory("130122024007", "121112025003")

    assert inventory is None


def test_get_ee_residual_inventory_riikliku_matusetoetuse_seadus() -> None:
    inventory = get_ee_residual_inventory("106122012015", "104122014005")

    assert inventory is None


def test_get_ee_residual_inventory_saastuse_kompleksse_valtimise_ja_kontrollimise_seadus() -> None:
    inventory = get_ee_residual_inventory("115032011021", "121122011018")

    assert inventory is not None
    assert inventory.statute_title == "Saastuse kompleksse vältimise ja kontrollimise seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:2/section:7_2"
        and "121122011001" in record.evidence
        and "Directive 2009/31/EÜ" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_soolise_vordoiguslikkuse_seadus() -> None:
    inventory = get_ee_residual_inventory("122122021038", "130062023072")

    assert inventory is not None
    assert inventory.statute_title == "Soolise võrdõiguslikkuse seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "oracle_correction_notice",
    }
    assert any(
        record.address == "chapter:6"
        and "<veaparandus>" in record.evidence
        and "explicit oracle correction lane" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_teeseadus() -> None:
    inventory = get_ee_residual_inventory("117032011027", "117032011028")

    assert inventory is not None
    assert inventory.statute_title == "Teeseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:4/section:25_2 7"
        and "117032011002" in record.evidence
        and "Directive 2008/96/EÜ" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_mikrolulituse_topoloogia_kaitse_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("106012023046", "106012023047")

    assert inventory is not None
    assert inventory.statute_title == "Mikrolülituse topoloogia kaitse seadus"
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 3
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:6/section:43/subsection:4"
        and "rewrites only the first sentence and repeals only the second" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_tarbijakaitseseadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("131122013007", "131122013008")

    assert inventory is not None
    assert inventory.statute_title == "Tarbijakaitseseadus"
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert {record.address for record in inventory.residuals} == {
        "chapter:6",
        "chapter:6/section:41_1",
        "chapter:6/section:41_1/subsection:1",
    }
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:6/section:41_1/subsection:1"
        and "comma after '§ 49 lõikes 2^3'" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_tooturumeetmete_seadus_compound_repeal_is_closed() -> None:
    inventory = get_ee_residual_inventory("112062025015", "112062025016")

    assert inventory is not None
    assert inventory.statute_title == "Tööturumeetmete seadus"
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert inventory.residuals == ()


def test_get_ee_residual_inventory_riikliku_pensionikindlustuse_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("112062025011", "112062025012")

    assert inventory is not None
    assert inventory.statute_title == "Riikliku pensionikindlustuse seadus"
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert {record.address for record in inventory.residuals} == {
        "chapter:12",
        "chapter:12/section:57",
        "chapter:12/section:57/subsection:1",
        "chapter:12/section:57/subsection:1/item:2",
        "chapter:2",
        "chapter:2/section:7",
        "chapter:2/section:7/subsection:7",
    }
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:2/section:7/subsection:7"
        and "93 identical amendment references" in record.evidence
        and "minsiter" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_kov_valimise_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("120052025002", "120052025003")

    assert inventory is not None
    assert inventory.statute_title == "Kohaliku omavalitsuse volikogu valimise seadus"
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert {record.address for record in inventory.residuals} == {
        "chapter:1",
        "chapter:1/section:5",
        "chapter:1/section:5/subsection:2",
        "chapter:1/section:5/subsection:2/item:1",
        "chapter:1/section:5_1",
        "chapter:1/section:5_1/subsection:1",
    }
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:1/section:5/subsection:2"
        and "45 identical amendment references" in record.evidence
        and "kodakondsuseta isik" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_krediidiandjate_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("113022026005", "113022026006")

    assert inventory is None


def test_get_ee_residual_inventory_tsiviilseadustiku_uldosa_seadus() -> None:
    inventory = get_ee_residual_inventory("122032021008", "131122024048")

    assert inventory is not None
    assert inventory.statute_title == "Tsiviilseadustiku üldosa seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "part:7/chapter:10/division:5/section:160_1 2"
        and "131122024005 explicitly inserts" in record.evidence
        and "Directive (EL) 2020/1828" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_vedelkutuse_erimargistamise_seadus() -> None:
    inventory = get_ee_residual_inventory("116122022028", "130062023098")

    assert inventory is not None
    assert inventory.statute_title == "Vedelkütuse erimärgistamise seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "section:8_3/subsection:1"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_get_ee_residual_inventory_loomakaitseseadus() -> None:
    inventory = get_ee_residual_inventory("116062021002", "127092023009")

    assert inventory is not None
    assert inventory.statute_title == "Loomakaitseseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "chapter:8/section:45/subsection:3"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_get_ee_residual_inventory_rakenduskorgkooli_seadus() -> None:
    inventory = get_ee_residual_inventory("123032015270", "120122016003")

    assert inventory is not None
    assert inventory.statute_title == "Rakenduskõrgkooli seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "chapter:5/section:27_3/subsection:3/item:2"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_get_ee_residual_inventory_sotsiaalmaksuseadus() -> None:
    inventory = get_ee_residual_inventory("130122025034", "130122025035")

    assert inventory is not None
    assert inventory.statute_title == "Sotsiaalmaksuseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "section:3/subsection:1/item:18"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_generated_riigisaladus_current_residual_family_matches_inventory() -> None:
    generated = tuple(
        build_shortened_section_family(
            bucket="source_oracle_drift",
            records=(
                (
                    "chapter:2/division:1/section:8/subsection:1/item:8",
                    "Source act 107032023002 rewrites § 8 p 8 and replay preserves the "
                    "source-side terminal period after the final sentence; oracle "
                    "103022026013 keeps a trailing semicolon instead. This is a bounded "
                    "terminal punctuation oracle-surface drift, not a replay omission.",
                ),
                (
                    "chapter:2/division:3/section:48/subsection:3/item:4",
                    "Replay preserves the source-side terminal semicolon in "
                    "§ 48(3) p 4, while oracle 103022026013 drops it. This is a "
                    "bounded terminal punctuation oracle-surface drift.",
                ),
            ),
        )
    )

    inventory = get_ee_residual_inventory("106052020036", "103022026013")

    assert inventory is not None
    assert len(generated) == 2
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_generated_inserted_note_omission_family_matches_inventory() -> None:
    generated = build_inserted_note_omission_family(
        note_address="chapter:6/section:115_11",
        note_symbol="§ 115^11",
        source_act_id="103022026005",
        oracle_id="103022026015",
    )

    inventory = get_ee_residual_inventory("123122024006", "103022026015")

    assert inventory is not None
    assert len(generated) == 1
    assert generated[0].address == "chapter:6/section:115_11"
    assert generated[0].bucket == "source_oracle_drift"
    assert "oracle 103022026015 omits the inserted § 115^11 note" in generated[0].evidence
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_generated_inserted_item_omission_family_matches_vaarteomenetluse_inventory() -> None:
    generated = build_inserted_item_omission_family(
        item_address="chapter:5/section:31_6/subsection:5",
        source_act_id="105072025001",
        oracle_id="105072025019",
        item_labels=("1", "2", "3", "4", "5", "6", "7", "8"),
    )

    inventory = get_ee_residual_inventory("122032024011", "105072025019")

    assert inventory is not None
    assert len(generated) == 8
    assert generated[0].address == "chapter:5/section:31_6/subsection:5/item:1"
    assert generated[-1].address == "chapter:5/section:31_6/subsection:5/item:8"
    assert all(record.bucket == "source_pathology" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[2:10]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]
    assert inventory.residuals[0].address == "chapter:5/section:31_6/subsection:4"
    assert inventory.residuals[1].address == "chapter:5/section:31_6/subsection:5"


def test_generated_shortened_section_family_matches_ringhaalinguseadus_editorial_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_oracle_drift",
        records=(
            (
                "chapter:1/section:1/subsection:1/item:4",
                "The only in-range amendment between 121122010026 and 121122010027 is 13310847, "
                "and replay applies only that act at the oracle cutoff 2011-01-01. Its "
                "Ringhäälinguseadus block compiles only the euro-conversion rewrites in §§ 43^4 "
                "and 43^5. None of the current divergences are targeted by 13310847; oracle "
                "121122010027 instead normalizes untouched older text via dash spacing, dash "
                "glyphs, quote style, or final punctuation at this address."
            ),
            (
                "chapter:7_1/section:43_5/subsection:1",
                "The only in-range amendment between 121122010026 and 121122010027 is 13310847, "
                "and replay applies only that act at the oracle cutoff 2011-01-01. Its "
                "Ringhäälinguseadus block compiles only the euro-conversion rewrites in §§ 43^4 "
                "and 43^5. None of the current divergences are targeted by 13310847; oracle "
                "121122010027 instead normalizes untouched older text via dash spacing, dash "
                "glyphs, quote style, or final punctuation at this address."
            ),
        ),
    )

    inventory = get_ee_residual_inventory("121122010026", "121122010027")

    assert inventory is not None
    assert len(generated) == 2
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_kutseseadus() -> None:
    inventory = get_ee_residual_inventory("114032014062", "130012015002")

    assert inventory is None


def test_generated_shortened_section_family_matches_uleliigse_laovaru_cluster() -> None:
    generated = build_shortened_section_family(
        records=(
            (
                "section:23/subsection:1",
                "Source act 130062015004 is a generic ministry reorganization rename and "
                "emits the statute-wide text replacement 'Põllumajandusministeerium' -> "
                "'Maaeluministeerium'. Replay preserves that source-backed rename in "
                "§ 23(1), while oracle 101092015036 retains the older ministry name.",
            ),
            (
                "section:23/subsection:4",
                "Source act 130062015004 emits the same generic "
                "'Põllumajandusministeerium' -> 'Maaeluministeerium' rename for the whole "
                "statute. Replay updates § 23(4) accordingly, while oracle 101092015036 "
                "keeps 'Põllumajandusministeerium'.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("115032014084", "101092015036")

    assert inventory is not None
    assert len(generated) == 2
    assert generated[0].address == "section:23/subsection:1"
    assert generated[1].address == "section:23/subsection:4"
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_kriminaalhooldusseadus() -> None:
    inventory = get_ee_residual_inventory("106082022024", "114032025025")

    assert inventory is not None
    assert inventory.statute_title == "Kriminaalhooldusseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:8/section:36/subsection:8"
        and "130122024001" in record.evidence
        and "'Justiitsministeerium' -> 'Justiits- ja Digiministeerium'" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_toolepingu_seadus() -> None:
    inventory = get_ee_residual_inventory("123122024006", "103022026015")

    assert inventory is not None
    assert inventory.statute_title == "Töölepingu seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:6/section:115_11"
        and "103022026005" in record.evidence
        and "cleanly inserts the § 115^11 normitehniline märkus" in record.evidence
        and "omits the inserted § 115^11 note" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_jalitustegevuse_seadus() -> None:
    inventory = get_ee_residual_inventory("13247639", "131012012006")

    assert inventory is not None
    assert inventory.statute_title == "Jälitustegevuse seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "section:6/subsection:1/item:6"
        and "terminal ';'" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "section:10_1/subsection:1/item:3"
        and "terminal ';'" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_ohvriabi_seadus_normitehniline_markus_is_closed() -> None:
    inventory = get_ee_residual_inventory("104112016005", "104012019016")

    assert inventory is not None
    assert inventory.statute_title == "Ohvriabi seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert inventory.residuals == ()


def test_get_ee_residual_inventory_finantskriisi_duplicate_subsection_label_pathology() -> None:
    inventory = get_ee_residual_inventory("111102024005", "111112025017")

    assert inventory is not None
    assert inventory.statute_title == "Finantskriisi ennetamise ja lahendamise seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "chapter:6/division:3/section:59/subsection:5"
    assert inventory.residuals[0].bucket == "source_pathology"
    assert "<loigeNr>5</loigeNr>" in inventory.residuals[0].evidence


def test_generated_shortened_section_family_matches_jalitustegevuse_cluster() -> None:
    generated = build_shortened_section_family(
        records=(
            (
                "section:6/subsection:1/item:6",
                "None of the applied in-range amendments (121032011002, 129122011001, "
                "131012012005) touch § 6(1) p 6; replay preserves the base text with "
                "terminal ';', while oracle 131012012006 normalizes it to '.'.",
            ),
            (
                "section:10_1/subsection:1/item:3",
                "None of the applied in-range amendments (121032011002, 129122011001, "
                "131012012005) touch § 10^1(1) p 3; replay preserves the base text with "
                "terminal ';', while oracle 131012012006 normalizes it to '.'.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("13247639", "131012012006")

    assert inventory is not None
    assert len(generated) == 2
    assert generated[0].address == "section:6/subsection:1/item:6"
    assert generated[1].address == "section:10_1/subsection:1/item:3"
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[0:2]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_kindlustustegevuse() -> None:
    inventory = get_ee_residual_inventory("126042013006", "111042014003")

    assert inventory is not None
    assert inventory.statute_title == "Kindlustustegevuse seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 16
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:11/division:3/section:187"
        and "title-only stub" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:11/division:3/section:187/subsection:1"
        and "lone dash" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:9/division:3/section:125/subsection:1/item:2"
        and "capitalization" in record.evidence
        for record in inventory.residuals
    )


def test_generated_shortened_section_family_matches_kindlustustegevuse_cluster() -> None:
    generated = build_shortened_section_family(
        records=(
            (
                "chapter:10/division:6/section:156/subsection:2",
                "Source act 123122013001 first replaces the base/source wording "
                "'notari või vandetõlgi kinnitatud' with "
                "'vandetõlgi tehtud või notari kinnitatud' and then deletes "
                "'või notari kinnitatud'; replay follows that source-backed end state, "
                "while the oracle keeps the intermediate notary wording.",
            ),
            (
                "chapter:10/division:6/section:158/subsection:2",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud tõlkega'.",
            ),
            (
                "chapter:10/division:7/section:161/subsection:4",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:10/division:7/section:165/subsection:2",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:10/division:7/section:165_1/subsection:13",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("126042013006", "111042014003")

    assert inventory is not None
    assert len(generated) == 5
    assert generated[0].address == "chapter:10/division:6/section:156/subsection:2"
    assert generated[-1].address == "chapter:10/division:7/section:165_1/subsection:13"
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[0:5]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_generated_shortened_section_family_matches_kindlustustegevuse_translation_cluster() -> None:
    generated = build_shortened_section_family(
        records=(
            (
                "chapter:11/division:1/section:180/subsection:10",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:2/division:2/section:35/subsection:2",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud tõlkega'.",
            ),
            (
                "chapter:2/division:2/section:38/subsection:2",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud tõlkega'.",
            ),
            (
                "chapter:2/division:3/section:43/subsection:5",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:2/division:3/section:46/subsection:6",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:2/division:3/section:46_1/subsection:9",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:2/division:3/section:47/subsection:5",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
            (
                "chapter:8/section:109/subsection:10",
                "Source act 123122013001 yields the replay-side final wording "
                "'vandetõlgi tehtud eestikeelse tõlkega'; oracle keeps "
                "'vandetõlgi tehtud või notari kinnitatud eestikeelse tõlkega'.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("126042013006", "111042014003")

    assert inventory is not None
    assert len(generated) == 8
    assert generated[0].address == "chapter:11/division:1/section:180/subsection:10"
    assert generated[-1].address == "chapter:8/section:109/subsection:10"
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[7:15]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_generated_first_residual_inventory_preserves_current_output_shape() -> None:
    for (base_id, oracle_id), legacy_inventory in _KNOWN_EE_RESIDUALS.items():
        inventory = get_ee_residual_inventory(base_id, oracle_id)

        if inventory is None:
            assert (base_id, oracle_id) in {
                ("109042021007", "114032025016"),
                ("106122012015", "104122014005"),
                ("114032014062", "130012015002"),
            }
            continue
        assert inventory is not None
        assert inventory.base_id == legacy_inventory.base_id
        assert inventory.oracle_id == legacy_inventory.oracle_id
        assert inventory.statute_title == legacy_inventory.statute_title
        assert inventory.comparison_class == legacy_inventory.comparison_class
        legacy_by_address = {
            record.address: (record.bucket, record.evidence)
            for record in legacy_inventory.residuals
        }
        inventory_by_address = {
            record.address: (record.bucket, record.evidence)
            for record in inventory.residuals
        }
        assert set(legacy_by_address).issubset(set(inventory_by_address))
        for address, (bucket, _evidence) in legacy_by_address.items():
            assert inventory_by_address[address][0] == bucket


def test_get_ee_residual_inventory_abieluvararegister() -> None:
    inventory = get_ee_residual_inventory("105122014039", "114032025013")

    assert inventory is not None
    assert inventory.statute_title == "Abieluvararegistri seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:1/section:6/subsection:3"
        and "Registritoimikuga tutvumise loa annab notar" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:5/section:42_5/subsection:1"
        and "Justiits- ja Digiministeerium" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_kalapuugiseadus() -> None:
    inventory = get_ee_residual_inventory("108112012002", "103072014023")

    assert inventory is not None
    assert inventory.statute_title == "Kalapüügiseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 4
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert all("section:17_1" not in record.address for record in inventory.residuals)
    assert any(
        record.address == "chapter:3/section:19/subsection:6_3"
        and "põllumajandusminister" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_strateegilise_kauba_seadus() -> None:
    inventory = get_ee_residual_inventory("112022020007", "107052025008")

    assert inventory is not None
    assert inventory.statute_title == "Strateegilise kauba seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:2/division:2/section:25/subsection:3"
        and "117042025001 rewrites § 25(3)" in record.evidence
        and "drops the final period" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_audiitortegevuse_seadus() -> None:
    inventory = get_ee_residual_inventory("114032025030", "107012025013")

    assert inventory is not None
    assert inventory.statute_title == "Audiitortegevuse seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "oracle_correction_notice",
    }
    assert any(
        record.address == "chapter:7/division:1/section:95_2/subsection:1"
        and "107012025001" in record.evidence
        and "kestlikkusaruande audiitorkontrolli" in record.evidence
        and "Riigi Teataja confirmed" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_kalapuugiseadus_2023() -> None:
    inventory = get_ee_residual_inventory("111112022002", "130062023023")

    assert inventory is not None
    assert inventory.statute_title == "Kalapüügiseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert not any(record.address == "chapter:7/section:90_2/subsection:2" for record in inventory.residuals)


def test_generated_shortened_section_family_matches_kalapuugiseadus_ministry_drift_cluster() -> None:
    generated = build_shortened_section_family(
        records=(
            (
                "chapter:7/section:90_2/subsection:1",
                "Source act 130062023001 rewrites § 90^2(1) with replay-side text "
                "mentioning 'Kliimaministeerium'. Oracle carries 'Keskkonnaministeerium' "
                "— another ministry rename drift.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("111112022002", "130062023023")

    assert inventory is not None
    assert len(generated) == 1
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[1:]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]
    assert generated[0].address == "chapter:7/section:90_2/subsection:1"
    assert all(record.bucket == "source_oracle_drift" for record in generated)


def test_get_ee_residual_inventory_kohtutaituri_solved_pair_is_not_published() -> None:
    assert get_ee_residual_inventory("109042021007", "114032025016") is None


def test_generated_shortened_section_family_matches_loomatauditorje_divisions() -> None:
    generated = build_shortened_section_family(
        bucket="source_oracle_drift",
        records=(
            (
                "chapter:2/division:6",
                "Base 128122018041 already carries repealed division 6 with an empty "
                "`jaguPealkiri`, and the in-range amendments 113032019002 / 104122019002 "
                "do not retitle or reinsert that division heading. Oracle 104122019022 "
                "fills in 'Loomsete jäätmete käitlemine' anyway.",
            ),
            (
                "chapter:2/division:7",
                "Base 128122018041 already carries repealed division 7 with an empty "
                "`jaguPealkiri`, and the in-range amendments 113032019002 / 104122019002 "
                "do not retitle or reinsert that division heading. Oracle 104122019022 "
                "fills in 'Loomade ja loomsete saaduste sisse- ja väljavedu' anyway.",
            ),
            (
                "chapter:2/division:8",
                "Base 128122018041 already carries repealed division 8 with an empty "
                "`jaguPealkiri`, and the in-range amendments 113032019002 / 104122019002 "
                "do not retitle or reinsert that division heading. Oracle 104122019022 "
                "fills in 'Veterinaartõendid' anyway.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("128122018041", "104122019022")

    assert inventory is not None
    assert len(generated) == 3
    assert generated[0].address == "chapter:2/division:6"
    assert generated[-1].address == "chapter:2/division:8"
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_loomatauditorje() -> None:
    inventory = get_ee_residual_inventory("128122018041", "104122019022")

    assert inventory is not None
    assert inventory.statute_title == "Loomatauditõrje seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 3
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (
            "chapter:2/division:6",
            "source_oracle_drift",
            "Base 128122018041 already carries repealed division 6 with an empty "
            "`jaguPealkiri`, and the in-range amendments 113032019002 / 104122019002 "
            "do not retitle or reinsert that division heading. Oracle 104122019022 "
            "fills in 'Loomsete jäätmete käitlemine' anyway.",
        ),
        (
            "chapter:2/division:7",
            "source_oracle_drift",
            "Base 128122018041 already carries repealed division 7 with an empty "
            "`jaguPealkiri`, and the in-range amendments 113032019002 / 104122019002 "
            "do not retitle or reinsert that division heading. Oracle 104122019022 "
            "fills in 'Loomade ja loomsete saaduste sisse- ja väljavedu' anyway.",
        ),
        (
            "chapter:2/division:8",
            "source_oracle_drift",
            "Base 128122018041 already carries repealed division 8 with an empty "
            "`jaguPealkiri`, and the in-range amendments 113032019002 / 104122019002 "
            "do not retitle or reinsert that division heading. Oracle 104122019022 "
            "fills in 'Veterinaartõendid' anyway.",
        ),
    ]


def test_get_ee_residual_inventory_riigisaladuse_ja_salastatud_valisteabe() -> None:
    inventory = get_ee_residual_inventory("106052020036", "103022026013")

    assert inventory is not None
    assert inventory.statute_title == "Riigisaladuse ja salastatud välisteabe seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:2/division:1/section:8/subsection:1/item:8"
        and record.bucket == "source_oracle_drift"
        and "terminal punctuation" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_kinnisasja_avalikes_huvides_omandamise_seadus() -> None:
    inventory = get_ee_residual_inventory("123122022037", "104122024010")

    assert inventory is not None
    assert inventory.statute_title == "Kinnisasja avalikes huvides omandamise seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].bucket == "source_oracle_drift"
    assert inventory.residuals[0].address == "chapter:3/section:12/subsection:5"
    assert "oracle-surface punctuation drift" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_elp_rakendamise_seadus() -> None:
    inventory = get_ee_residual_inventory("121052014030", "121052014031")

    assert inventory is not None
    assert inventory.statute_title == "Euroopa Liidu ühise põllumajanduspoliitika rakendamise seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 40
    assert {record.bucket for record in inventory.residuals} == {
        "source_pathology",
    }
    assert any(
        record.address == "chapter:16"
        and "only new amendment reference" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:16/section:97_1/subsection:2"
        and "generic minister-title substitutions" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "chapter:16/section:94/subsection:6_3"
        and "chapter 16 / supervision rewrite" in record.evidence
        for record in inventory.residuals
    )


def test_generated_shortened_section_family_matches_elp_rakendamise_chapter_16_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_pathology",
        records=(
            (
                "chapter:16",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:93",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:93/subsection:1",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:93/subsection:2",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:93/subsection:3",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:93/subsection:4",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:94/subsection:2",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:94/subsection:2_1",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:94/subsection:6_3",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("121052014030", "121052014031")

    assert inventory is not None
    assert len(generated) == 9
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[4:13]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]
    assert generated[0].address == "chapter:16"
    assert generated[-1].address == "chapter:16/section:94/subsection:6_3"
    assert all(record.bucket == "source_pathology" for record in generated)


def test_get_ee_residual_inventory_rahvusvahelise_sojalise_koostoo_seadus() -> None:
    generated = build_address_list_family(
        addresses=(
            "chapter:1/section:4/subsection:4",
            "chapter:2/section:11/subsection:1",
            "chapter:2/section:11/subsection:2",
            "chapter:2/section:12/subsection:2",
            "chapter:2/section:7/subsection:2",
            "chapter:2/section:8/subsection:2",
            "chapter:2/section:8/subsection:4",
            "chapter:2/section:8_2/subsection:3",
            "chapter:3/section:16/subsection:5",
            "chapter:3/section:18/subsection:1",
            "chapter:3/section:18/subsection:2",
            "chapter:3/section:18/subsection:3",
            "chapter:3/section:18/subsection:4",
            "chapter:3/section:19/subsection:2",
            "chapter:3/section:19/subsection:3/item:1",
            "chapter:3/section:19/subsection:4",
            "chapter:3/section:20/subsection:1",
            "chapter:3/section:20/subsection:2",
            "chapter:3/section:21/subsection:2",
            "chapter:4/section:22/subsection:4",
            "chapter:4/section:25/subsection:1",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "The only visible pair-delta amendment between 107032012004 and "
            "101062013014 is source act 101062013001, which emits only three "
            "ops: one Liiklusseadus replacement, one new third sentence in "
            "§ 21(2), and one text insertion in § 23(2). It emits no generic "
            "'kaitseminister' -> 'valdkonna eest vastutav minister' rewrite "
            "family for Rahvusvahelise sõjalise koostöö seadus, so oracle "
            "101062013014 carries a broader forward-looking minister-title "
            "retitle than the visible pair delta supports."
        ),
    )
    inventory = get_ee_residual_inventory("107032012004", "101062013014")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 21
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_reklaamiseadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("106032026008", "106032026009")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "chapter:4/section:29/subsection:3_7"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_get_ee_residual_inventory_atmosfaariohu_same_chain_range_endpoint_superscript_is_closed() -> None:
    inventory = get_ee_residual_inventory("102102025017", "102102025018")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert inventory.residuals == ()


def test_get_ee_residual_inventory_politsei_ja_piirivalve_same_chain_insertions() -> None:
    generated = build_address_list_family(
        addresses=(
            "chapter:2_5",
            "chapter:2_5/section:7_65",
            "chapter:2_5/section:7_65/subsection:1",
            "chapter:2_5/section:7_65/subsection:2",
            "chapter:2_5/section:7_65/subsection:3",
            "chapter:2_5/section:7_65/subsection:4",
            "chapter:2_5/section:7_66",
            "chapter:2_5/section:7_66/subsection:1",
            "chapter:2_5/section:7_66/subsection:2",
            "chapter:2_5/section:7_66/subsection:3",
            "chapter:2_5/section:7_67",
            "chapter:2_5/section:7_67/subsection:1",
            "chapter:2_5/section:7_67/subsection:2",
            "chapter:2_5/section:7_67/subsection:3",
            "chapter:2_6",
            "chapter:2_6/section:7_68",
            "chapter:2_6/section:7_68/subsection:1",
            "chapter:2_6/section:7_68/subsection:2",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Base 123102025002 and oracle 123102025003 expose the same visible "
            "amendment chain, including future source act 123102025001, but replay "
            "emits no same-chain operations for this comparison. Oracle 123102025003 "
            "nevertheless carries the new divisions 2^5 and 2^6 with §§ 7^65–7^68, "
            "so these insertions are treated as same-chain oracle-side drift rather "
            "than open replay-core work."
        ),
    )
    inventory = get_ee_residual_inventory("123102025002", "123102025003")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 18
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_ettevotluse_toetamise_forward_looking_oracle() -> None:
    generated = build_address_list_family(
        addresses=(
            "chapter:2",
            "chapter:2/section:2",
            "chapter:2/section:2/subsection:3",
            "chapter:2/section:4",
            "chapter:2/section:4/subsection:1",
            "chapter:2/section:4/subsection:2",
            "chapter:2/section:4/subsection:3",
            "chapter:2/section:5",
            "chapter:2/section:5/subsection:1",
            "chapter:2/section:5/subsection:1/item:2",
            "chapter:3",
            "chapter:3/section:15",
            "chapter:3/section:15/subsection:3",
            "chapter:3/section:16",
            "chapter:3/section:16/subsection:1",
            "chapter:3/section:18",
            "chapter:3/section:18/subsection:2",
            "chapter:3/section:18/subsection:3",
            "chapter:3/section:18/subsection:7",
            "chapter:3/section:18/subsection:7/item:2",
            "chapter:3/section:19",
            "chapter:3/section:19/subsection:1",
            "chapter:3/section:19/subsection:2",
            "chapter:3/section:20",
            "chapter:3/section:20/subsection:1",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Base 129112010006 is the 2010-11-29 surface, while oracle 129112010007 "
            "is the 2011-01-01 redaction and also carries later local oracle witnesses "
            "such as 2014 minister-title replacements on the affected nodes. The "
            "remaining chapter 2 and chapter 3 divergences therefore reflect a broader "
            "forward-looking oracle surface than the earlier base basis, not unexplained "
            "replay-core drift in the 2010 pair lane."
        ),
    )
    inventory = get_ee_residual_inventory("129112010006", "129112010007")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 25
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_asjaoigusseaduse_rakendamise_forward_looking_oracle() -> None:
    generated = build_address_list_family(
        addresses=(
            "section:12",
            "section:12/subsection:5",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Source act 113032014003 § 74 inserts the phrase beginning "
            "'sealhulgas ehitise jagamist reaalosadeks ...', but its "
            "commencement section 95 delays § 74 to the act's general "
            "effective date of 2018-01-01. The comparison lane here is "
            "2017-03-01, so replay correctly excludes that insertion while "
            "oracle 125012017006 already carries the future text in § 12(5)."
        ),
    )
    inventory = get_ee_residual_inventory("115032013034", "125012017006")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 2
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_vanemahuvitise_forward_looking_oracle() -> None:
    generated = build_address_list_family(
        addresses=(
            "section:3",
            "section:3/subsection:7_2",
            "section:5",
            "section:5/subsection:2",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Source act 108072016001 (Perehüvitiste seadus) inserts "
            "§ 3(7^2) and rewrites § 5(2) in Vanemahüvitise seadus, but "
            "its commencement section 88 sets the act's general effective "
            "date at 2017-01-01. The comparison lane here is 2016-07-18, "
            "so replay correctly excludes those future Vanemahüvitise "
            "changes while oracle 108072016042 already carries the inserted "
            "§ 3(7^2) text and the matching § 5(2) cross-reference."
        ),
    )
    inventory = get_ee_residual_inventory("110012014014", "108072016042")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 4
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_autoveoseaduse_forward_looking_oracle() -> None:
    generated = build_address_list_family(
        addresses=(
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
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Source act 111012018001 inserts § 23^1, repeals § 31^12, and "
            "inserts § 31^13–31^14 into Autoveoseadus, but its commencement "
            "section 81 delays those changes to 2018-06-01. The comparison "
            "lane here is 2018-01-21, so replay correctly excludes the future "
            "new section and renumbered chapter 7^1 structure while oracle "
            "111012018009 already carries the inserted § 23^1 and the future "
            "§ 31^13–31^14 liability regime."
        ),
    )
    inventory = get_ee_residual_inventory("104072017126", "111012018009")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 11
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_taiskasvanute_koolituse_forward_looking_mixed_residuals() -> None:
    inventory = get_ee_residual_inventory("118032011008", "111072013019")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 9
    assert {record.bucket for record in inventory.residuals} == {"source_oracle_drift"}
    assert [
        (record.address, record.bucket) for record in inventory.residuals
    ] == [
        ("chapter:2", "source_oracle_drift"),
        ("chapter:2/section:6", "source_oracle_drift"),
        ("chapter:2/section:6/subsection:1", "source_oracle_drift"),
        ("chapter:2/section:6/subsection:1/item:3", "source_oracle_drift"),
        ("chapter:2/section:6_1", "source_oracle_drift"),
        ("chapter:2/section:6_1/subsection:2", "source_oracle_drift"),
        ("chapter:5", "source_oracle_drift"),
        ("chapter:5/section:16_1", "source_oracle_drift"),
        ("chapter:5/section:16_1/subsection:4", "source_oracle_drift"),
    ]


def test_get_ee_residual_inventory_prokuratuuri_forward_looking_ministry_rename() -> None:
    inventory = get_ee_residual_inventory("102062020008", "114032025020")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 16
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [record.address for record in inventory.residuals] == [
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
    ]


def test_get_ee_residual_inventory_elektrituruseadus_now_clean() -> None:
    inventory = get_ee_residual_inventory("118032026018", "118032026019")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert inventory.residuals == ()


def test_get_ee_residual_inventory_energiamajandus_now_clean() -> None:
    inventory = get_ee_residual_inventory("118032026031", "118032026032")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert inventory.residuals == ()


def test_get_ee_residual_inventory_ettevotlustulu_julgeolekumaks_forward_looking_oracle() -> None:
    generated = build_address_list_family(
        addresses=(
            "section:4",
            "section:4/subsection:3",
            "section:8",
            "section:8/subsection:1",
            "section:8/subsection:3_1",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Source act 102012025002 inserts § 4(3), rewrites § 8(1), and "
            "inserts § 8(3^1) to carry the julgeolekumaksu share into this "
            "statute from 2026-01-01. Later visible pair-delta source "
            "108072025001 only retunes the § 4(1) rate and the § 8(2) "
            "allocation ratios, while 118122025003 emits no target-statute "
            "ops here. Oracle 118122025016 nevertheless omits the "
            "julgeolekumaksu additions still supported by the source chain."
        ),
    )
    inventory = get_ee_residual_inventory("119122024003", "118122025016")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 5
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_korteriomandiseaduse_forward_looking_oracle() -> None:
    generated = build_address_list_family(
        addresses=(
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
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Source act 113032014003 (Korteriomandi- ja "
            "korteriühistuseadus) inserts § 8(1^1), § 13(4)–(5), and the "
            "whole § 16^1 tree into Korteriomandiseadus, but its "
            "commencement section 95 delays those changes to the act's "
            "general effective date of 2018-01-01. The comparison lane "
            "here is 2014-05-22, so replay correctly excludes that future "
            "text while oracle 121052014019 already carries the inserted "
            "subsections and the new § 16^1 ajakohastamine regime."
        ),
    )
    inventory = get_ee_residual_inventory("125052012018", "121052014019")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 11
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_sotsiaalhoolekande_forward_looking_oracle() -> None:
    inventory = get_ee_residual_inventory("130122011047", "113122013023")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 11
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [record.address for record in inventory.residuals] == [
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
    ]


def test_get_ee_residual_inventory_alkoholiseadus_forward_looking_oracle() -> None:
    inventory = get_ee_residual_inventory("120022015005", "109012018006")

    assert inventory is not None
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 11
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert [record.address for record in inventory.residuals] == [
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
    ]


def test_get_ee_residual_inventory_avaliku_teabe_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("106032026004", "106032026005")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 2
    assert [record.address for record in inventory.residuals] == [
        "chapter:4/division:2/section:32_1/subsection:1_2/item:7",
        "chapter:4/division:2/section:32_1/subsection:2_1/item:5",
    ]
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)


def test_get_ee_residual_inventory_vedelkutuse_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("108102024023", "108102024024")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "chapter:1_1/section:2_4/subsection:1_1"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_get_ee_residual_inventory_pakendiseadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("107012026023", "107012026024")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 2
    assert [record.address for record in inventory.residuals] == [
        "chapter:1/section:10_1/subsection:1_1",
        "chapter:3/section:17_2/subsection:4",
    ]
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)


def test_get_ee_residual_inventory_maksukorralduse_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("102102025004", "102102025005")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].address == "chapter:1/division:3_1/section:25_5/subsection:1"
    assert inventory.residuals[0].bucket == "source_oracle_drift"


def test_get_ee_residual_inventory_aktsiisi_seadus_same_chain_editorial_drift() -> None:
    generated = build_address_list_family(
        addresses=(
            "part:2/chapter:7/section:66/subsection:5",
            "part:2/chapter:7/section:66/subsection:6",
            "part:2/chapter:7/section:66/subsection:7",
            "part:2/chapter:7/section:66/subsection:7_1",
            "part:2/chapter:7/section:66/subsection:8",
            "part:2/chapter:7/section:66/subsection:9",
            "part:2/chapter:7/section:66/subsection:10",
            "part:2/chapter:7/section:66/subsection:10_1",
            "part:2/chapter:7/section:66/subsection:10_2",
            "part:2/chapter:7/section:66/subsection:12",
            "part:2/chapter:7/section:66/subsection:16",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Base 101072023008 and oracle 101072023009 are same-chain "
            "tervikteksts for Alkoholi-, tubaka-, kütuse- ja "
            "elektriaktsiisi seadus with grupi_id 163125 and no visible "
            "applied amendment delta between them. Oracle 101072023009 "
            "silently updates the § 66 aktsiisimäär amounts across the "
            "same chain, while the later terviktekst itself yields no "
            "parsed amendment ops introducing those rate changes, so the "
            "tail is editorial oracle drift rather than a replay-side "
            "amendment bug."
        ),
    )
    inventory = get_ee_residual_inventory("101072023008", "101072023009")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(generated) == 11
    assert all(record.bucket == "source_oracle_drift" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_get_ee_residual_inventory_pohikooli_ja_gumnaasiumiseadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("107012026012", "107012026013")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 13
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert inventory.residuals[0].address == "chapter:5/division:3/section:71/subsection:8"
    assert inventory.residuals[-1].address == "chapter:8/division:1/section:100_19/subsection:1"


def test_get_ee_residual_inventory_kutseoppeasutuse_seadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("107012026014", "107012026015")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 11
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert inventory.residuals[0].address == "chapter:7"
    assert inventory.residuals[-1].address == "chapter:7/section:40/subsection:5"
    assert any(
        record.address == "chapter:7/section:40"
        and "21 identical amendment references" in record.evidence
        and "teacher pay subsections" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_vangistusseadus_same_chain_editorial_drift() -> None:
    inventory = get_ee_residual_inventory("126062025020", "126062025021")

    assert inventory is not None
    assert inventory.comparison_class == "same_chain_editorial_drift"
    assert len(inventory.residuals) == 4
    assert all(record.bucket == "source_oracle_drift" for record in inventory.residuals)
    assert inventory.residuals[0].address == "chapter:1"
    assert inventory.residuals[-1].address == "chapter:1/section:5_4/subsection:5/item:6"
    assert any(
        record.address == "chapter:1/section:5_4/subsection:5/item:6"
        and "97 identical amendment references" in record.evidence
        and "Justiits-ja Digiministeeriumi" in record.evidence
        for record in inventory.residuals
    )


def test_generated_address_list_family_matches_pohikooli_ja_gumnaasiumiseadus_cluster() -> None:
    generated = build_address_list_family(
        addresses=(
            "chapter:5/division:3/section:71/subsection:8",
            "chapter:5/division:3/section:71/subsection:9",
            "chapter:5/division:3/section:71/subsection:10",
            "chapter:5/division:3/section:71/subsection:11",
            "chapter:5/division:4/section:75/subsection:2_1",
            "chapter:5/division:4/section:75/subsection:2_2",
            "chapter:8/division:1/section:100_18",
            "chapter:8/division:1/section:100_18/subsection:1",
            "chapter:8/division:1/section:100_18/subsection:1/item:1",
            "chapter:8/division:1/section:100_18/subsection:1/item:2",
            "chapter:8/division:1/section:100_18/subsection:1/item:3",
            "chapter:8/division:1/section:100_19",
            "chapter:8/division:1/section:100_19/subsection:1",
        ),
        bucket="source_oracle_drift",
        evidence=(
            "Base 107012026012 and oracle 107012026013 are same-chain "
            "tervikteksts for Põhikooli- ja gümnaasiumiseadus with "
            "grupi_id 344763 and no visible applied amendment delta "
            "between them. Oracle 107012026013 adds the director "
            "atesteerimise, õpetajate karjääriastmete, and transitional "
            "§§ 100^18–100^19 materials, while the later terviktekst "
            "itself yields no parsed amendment ops introducing those "
            "provisions, so the tail is editorial oracle drift rather "
            "than a replay-side amendment bug."
        ),
    )
    inventory = get_ee_residual_inventory("107012026012", "107012026013")

    assert inventory is not None
    assert [(record.address, record.bucket, record.evidence) for record in inventory.residuals] == [
        (record.address, record.bucket, record.evidence) for record in generated
    ]


def test_generated_shortened_section_family_matches_elp_rakendamise_section_95_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_pathology",
        records=(
            (
                "chapter:16/section:95",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:95/subsection:1",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:95/subsection:2",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:95/subsection:3",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
            (
                "chapter:16/section:95/subsection:5",
                "The only new amendment reference between 121052014030 and "
                "121052014031 is source act 129062014109, which emits only the "
                "generic minister-title substitutions '...minister' -> "
                "'valdkonna eest vastutav minister' for this statute. It emits "
                "no chapter 16, 'haldusjärelevalve', or adjacent järelevalve "
                "regime rewrites, so oracle 121052014031 carries an unsourced "
                "chapter 16 / supervision rewrite relative to the visible pair delta.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("121052014030", "121052014031")

    assert inventory is not None
    assert len(generated) == 5
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[13:18]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]
    assert generated[0].address == "chapter:16/section:95"
    assert generated[-1].address == "chapter:16/section:95/subsection:5"
    assert all(record.bucket == "source_pathology" for record in generated)


def test_generated_shortened_section_family_matches_elp_rakendamise_section_96_97_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_pathology",
        records=(
            ("chapter:16/section:96", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:1/item:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:1/item:2", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:1/item:3", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:1/item:4", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:1/item:5", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:2", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:3", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:4", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:96/subsection:5", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97/subsection:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97/subsection:1/item:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97/subsection:1/item:2", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97/subsection:1/item:3", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97/subsection:2", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97_1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97_1/subsection:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:16/section:97_1/subsection:2", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
        ),
    )

    inventory = get_ee_residual_inventory("121052014030", "121052014031")

    assert inventory is not None
    assert len(generated) == 20
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[18:38]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]
    assert generated[0].address == "chapter:16/section:96"
    assert generated[-1].address == "chapter:16/section:97_1/subsection:2"
    assert all(record.bucket == "source_pathology" for record in generated)


def test_generated_shortened_section_family_matches_elp_rakendamise_spillover_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_pathology",
        records=(
            ("chapter:1/section:1/subsection:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:10/section:76/subsection:7/item:4", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:11/section:80/subsection:3/item:4", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:12/section:83/subsection:8/item:4", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:5/division:3/section:31/subsection:1", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
            ("chapter:6/division:2/section:57_2/subsection:6/item:4", "The only new amendment reference between 121052014030 and 121052014031 is source act 129062014109, which emits only the generic minister-title substitutions '...minister' -> 'valdkonna eest vastutav minister' for this statute. It emits no chapter 16, 'haldusjärelevalve', or adjacent järelevalve regime rewrites, so oracle 121052014031 carries an unsourced chapter 16 / supervision rewrite relative to the visible pair delta."),
        ),
    )

    inventory = get_ee_residual_inventory("121052014030", "121052014031")

    assert inventory is not None
    assert len(generated) == 6
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[0:4] + inventory.residuals[38:40]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]
    assert generated[0].address == "chapter:1/section:1/subsection:1"
    assert generated[-1].address == "chapter:6/division:2/section:57_2/subsection:6/item:4"
    assert all(record.bucket == "source_pathology" for record in generated)


def test_get_ee_residual_inventory_omits_solved_loomade_kauplemise_case() -> None:
    assert get_ee_residual_inventory("116062016016", "128122018040") is None


def test_get_ee_residual_inventory_eesti_territooriumi_haldusjaotuse_seadus() -> None:
    inventory = get_ee_residual_inventory("119032013003", "131122025003")

    assert inventory is not None
    assert inventory.statute_title == "Eesti territooriumi haldusjaotuse seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {"source_oracle_drift"}
    assert any(
        record.address == "chapter:2/section:7/subsection:7_1/item:1"
        and "terminal punctuation" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_eesti_vabariigi_haridusseadus() -> None:
    inventory = get_ee_residual_inventory("115032022004", "101072025003")

    assert inventory is not None
    assert inventory.statute_title == "Eesti Vabariigi haridusseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert inventory.residuals[0].address == "section:36_6/subsection:2_2/item:1"
    assert "109012025001 explicitly rewrites § 36^6(2^2) p 1" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_tootajate_usaldusisiku_seadus() -> None:
    inventory = get_ee_residual_inventory("112112021018", "107012025003")

    assert inventory is not None
    assert inventory.statute_title == "Töötajate usaldusisiku seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert inventory.residuals[0].address == "chapter:4/section:15_1 2"
    assert "normitehniline märkus" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_uleliigse_laovaru_tasu_seadus() -> None:
    inventory = get_ee_residual_inventory("115032014084", "101092015036")

    assert inventory is not None
    assert inventory.statute_title == "Üleliigse laovaru tasu seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "section:23/subsection:1"
        and "'Põllumajandusministeerium' -> 'Maaeluministeerium'" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "section:23/subsection:4"
        and "keeps 'Põllumajandusministeerium'" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_lastekaitseseadus() -> None:
    inventory = get_ee_residual_inventory("106082022030", "131122024023")

    assert inventory is not None
    assert inventory.statute_title == "Lastekaitseseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert inventory.residuals[0].address == "chapter:4/section:20/subsection:7"
    assert "Justiitsministeeriumi" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_advokatuuriseadus() -> None:
    inventory = get_ee_residual_inventory("122122020038", "114032025004")

    assert inventory is not None
    assert inventory.statute_title == "Advokatuuriseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].bucket == "source_oracle_drift"
    assert inventory.residuals[0].address == "chapter:7/section:82_5/subsection:1"
    assert "130122024001" in inventory.residuals[0].evidence
    assert "Justiits- ja Digiministeerium" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_volaoigusseaduse_rakendamise_seadus() -> None:
    inventory = get_ee_residual_inventory("104072024023", "131122024050")

    assert inventory is not None
    assert inventory.statute_title == (
        "Võlaõigusseaduse, tsiviilseadustiku üldosa seaduse ja rahvusvahelise "
        "eraõiguse seaduse rakendamise seadus"
    )
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert inventory.residuals[0].address == "chapter:1/section:9_2"
    assert "standalone normitehniline märkus" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_sihtasutuste_seadus() -> None:
    inventory = get_ee_residual_inventory("120062022025", "123122022031")

    assert inventory is not None
    assert inventory.statute_title == "Sihtasutuste seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:2/section:14/subsection:1/item:10_1"
        and "123122022002" in record.evidence
        and "final punctuation to a period" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_relvaseadus() -> None:
    inventory = get_ee_residual_inventory("130042024004", "112122024004")

    assert inventory is None


def test_get_ee_residual_inventory_uhistranspordiseadus() -> None:
    inventory = get_ee_residual_inventory("119032013007", "112072014164")

    assert inventory is not None
    assert inventory.statute_title == "Ühistranspordiseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert [record.address for record in inventory.residuals] == [
        "chapter:10/section:53_5/subsection:2",
    ]
    assert "113032014004 replaces chapter 10 effective 2014-07-01" in inventory.residuals[0].evidence
    assert "without the terminal period" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_kommertspandiseadus() -> None:
    inventory = get_ee_residual_inventory("118122012017", "123122022029")

    assert inventory is not None
    assert inventory.statute_title == "Kommertspandiseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 1
    assert inventory.residuals[0].bucket == "source_oracle_drift"
    assert inventory.residuals[0].address == "chapter:2/section:7/subsection:1_2"
    assert "rewrites § 7(1^1)" in inventory.residuals[0].evidence
    assert "§ 7(1^2)" in inventory.residuals[0].evidence


def test_get_ee_residual_inventory_taimekaitseseadus() -> None:
    inventory = get_ee_residual_inventory("106052020038", "127092023012")

    assert inventory is None


def test_get_ee_residual_inventory_riigiloivuseadus() -> None:
    inventory = get_ee_residual_inventory("127122013026", "129102014007")

    assert inventory is not None
    assert inventory.statute_title == "Riigilõivuseadus"
    assert inventory.comparison_class == "forward_looking_oracle"
    assert len(inventory.residuals) == 48
    assert {record.bucket for record in inventory.residuals} == {
        "appendix_display_pathology",
        "source_oracle_drift",
        "source_pathology",
    }
    assert any(
        record.address == "part:3/chapter:5/division:2/section:73/subsection:6"
        and record.bucket == "source_oracle_drift"
        and "explicitly repeals § 73(6)" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "part:3/chapter:11/division:4/section:259"
        and record.bucket == "source_pathology"
        and "No parsed amendment in the applied 2014 chain targets § 259" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "part:4/chapter:19/section:339"
        and record.bucket == "source_oracle_drift"
        and "113032014003" in record.evidence
        and "2018-01-01" in record.evidence
        for record in inventory.residuals
    )
    assert any(
        record.address == "part:5/chapter:20/section:357/subsection:3"
        and record.bucket == "appendix_display_pathology"
        and "lisaViide lane" in record.evidence
        for record in inventory.residuals
    )


def test_generated_shortened_section_family_matches_riigiloivuseadus_registry_cluster() -> None:
    generated = build_shortened_section_family(
        records=(
            (
                "part:2/chapter:3/section:20/subsection:1/item:5",
                "Source act 121062014008 replaces the replay/base-side "
                "'kohtu registriosakonna registris' wording with "
                "'Tartu Maakohtu registriosakonna peetavas registris'; "
                "oracle 129102014007 keeps the older generic court wording.",
            ),
            (
                "part:2/chapter:3/section:20/subsection:1/item:6",
                "Source act 121062014008 replaces the replay/base-side "
                "'kohtu registriosakonna registris' wording with "
                "'Tartu Maakohtu registriosakonna peetavas registris'; "
                "oracle 129102014007 keeps the older generic court wording.",
            ),
        ),
    )

    inventory = get_ee_residual_inventory("127122013026", "129102014007")

    assert inventory is not None
    assert len(generated) == 2
    assert generated[0].address == "part:2/chapter:3/section:20/subsection:1/item:5"
    assert generated[1].address == "part:2/chapter:3/section:20/subsection:1/item:6"
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals[:2]
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_vaartpaberituru() -> None:
    inventory = get_ee_residual_inventory("111112025007", "130122025006")

    assert inventory is None


def test_get_ee_residual_inventory_kaibemaksuseadus() -> None:
    inventory = get_ee_residual_inventory("130122025021", "130122025022")

    assert inventory is not None
    assert inventory.statute_title == "Käibemaksuseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 32
    assert {record.bucket for record in inventory.residuals} == {
        "source_pathology",
    }
    assert any(
        record.address == "chapter:4/section:27/subsection:1_5"
        and "108072025001" in record.evidence
        and "0 Käibemaksuseadus ops" in record.evidence
        for record in inventory.residuals
    )


def test_generated_address_list_family_matches_kaibemaksuseadus_cluster() -> None:
    generated = build_address_list_family(
        addresses=(
            "chapter:1",
            "chapter:1/section:2",
            "chapter:1/section:2/subsection:3_1",
            "chapter:1/section:2/subsection:3_1/item:5",
            "chapter:3",
            "chapter:3/section:15",
            "chapter:3/section:15/subsection:3_1",
            "chapter:4",
            "chapter:4/section:26",
            "chapter:4/section:26/subsection:11",
            "chapter:4/section:27/subsection:1",
            "chapter:4/section:27/subsection:1_1",
            "chapter:4/section:27/subsection:1_2",
            "chapter:4/section:27/subsection:1_3",
            "chapter:4/section:27/subsection:1_4",
            "chapter:4/section:27/subsection:1_5",
            "chapter:4/section:27/subsection:1_5/item:1",
            "chapter:4/section:27/subsection:1_5/item:2",
            "chapter:4/section:27",
            "chapter:4/section:27/subsection:2",
            "chapter:4/section:27/subsection:2/item:2",
            "chapter:4/section:28",
            "chapter:4/section:28/subsection:1",
            "chapter:4/section:28/subsection:1/item:1",
            "chapter:4/section:28/subsection:1/item:2",
            "chapter:4/section:28/subsection:2",
            "chapter:4/section:28/subsection:3",
            "chapter:4/section:28/subsection:4",
            "chapter:4/section:28/subsection:5",
            "chapter:4/section:28/subsection:6",
            "chapter:4/section:30",
            "chapter:4/section:30/subsection:6",
        ),
        bucket="source_pathology",
        evidence=(
            "The sole new amendment reference between 130122025021 and "
            "130122025022 is 108072025001, but that source act is an "
            "unrelated omnibus on Ettevõtlustulu lihtsustatud "
            "maksustamise seadus, julgeolekumaksu seadus, and "
            "Tulumaksuseadus. parse_ee_amendment_ops emits 0 "
            "Käibemaksuseadus ops from 108072025001, yet oracle "
            "130122025022 introduces the replay-diverging "
            "käibedeklaratsioon / ühendusesisese käibe changes here."
        ),
    )

    inventory = get_ee_residual_inventory("130122025021", "130122025022")
    assert inventory is not None
    assert {(record.address, record.bucket, record.evidence) for record in generated} == {
        (record.address, record.bucket, record.evidence) for record in inventory.residuals
    }


def test_get_ee_residual_inventory_keskkonnatasude_seadus() -> None:
    inventory = get_ee_residual_inventory("108072025061", "107012026021")

    assert inventory is not None
    assert inventory.statute_title == "Keskkonnatasude seadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 17
    assert {record.bucket for record in inventory.residuals} == {
        "source_pathology",
    }
    assert any(
        record.address == "chapter:3_1/section:21_5/subsection:2"
        and "107012026004" in record.evidence
        and "does not mention §§ 21^2, 21^5, 32(8–10), 55^3, or 68^5" in record.evidence
        for record in inventory.residuals
    )


def test_get_ee_residual_inventory_looduskaitseseadus() -> None:
    inventory = get_ee_residual_inventory("104122024013", "128012026005")

    assert inventory is not None
    assert inventory.statute_title == "Looduskaitseseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 17
    assert {record.bucket for record in inventory.residuals} == {
        "source_pathology",
    }
    assert any(
        record.address == "chapter:8/section:57/subsection:6"
        and "112072025001 and 128012026001" in record.evidence
        and "§ 57(6–7), § 57^1, or § 57^2" in record.evidence
        for record in inventory.residuals
    )


def test_generated_shortened_section_family_matches_looduskaitseseadus_mingi_kahriku_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_pathology",
        records=(
            (
                "chapter:8/section:57/subsection:6",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57/subsection:7",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_1",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_1/subsection:1",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_1/subsection:1/item:1",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_1/subsection:1/item:2",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_1/subsection:1/item:3",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:1",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:2",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:3",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:3/item:1",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:3/item:2",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:3/item:3",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:4",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:5",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
            (
                "chapter:8/section:57_2/subsection:6",
                "The only in-range amendments between 104122024013 and 128012026005 are "
                "112072025001 and 128012026001. Replay applies exactly those acts at the "
                "oracle cutoff 2026-02-07, and they compile only the § 68^3 p 1 repeal plus "
                "the new § 30(3^2) and § 31(2^1) inserts. Neither act mentions § 57(6–7), "
                "§ 57^1, or § 57^2, but oracle 128012026005 nonetheless blanks or omits that "
                "entire mingi/kähriku farm cluster at this address."
            ),
        ),
    )

    inventory = get_ee_residual_inventory("104122024013", "128012026005")

    assert inventory is not None
    assert len(generated) == 17
    assert all(record.bucket == "source_pathology" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_ringhaalinguseadus() -> None:
    inventory = get_ee_residual_inventory("121122010026", "121122010027")

    assert inventory is not None
    assert inventory.statute_title == "Ringhäälinguseadus"
    assert inventory.comparison_class == "commensurable_delta"
    assert len(inventory.residuals) == 2
    assert {record.bucket for record in inventory.residuals} == {
        "source_oracle_drift",
    }
    assert any(
        record.address == "chapter:7_1/section:43_5/subsection:1"
        and "13310847" in record.evidence
        and "dash spacing, dash glyphs, quote style, or final punctuation" in record.evidence
        for record in inventory.residuals
    )


def test_generated_shortened_section_family_matches_keskkonnatasude_tuuleenergia_cluster() -> None:
    generated = build_shortened_section_family(
        bucket="source_pathology",
        records=(
            (
                "chapter:11/section:68_5/subsection:4",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:11/section:68_5/subsection:5",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:3_1/section:21_2/subsection:2",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:3_1/section:21_2/subsection:2_1",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:3_1/section:21_5",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:3_1/section:21_5/subsection:1",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:3_1/section:21_5/subsection:2",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:3_1/section:21_5/subsection:3",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:5/section:32/subsection:10",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:5/section:32/subsection:8",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:5/section:32/subsection:9",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:8/section:55_3/subsection:1",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:8/section:55_3/subsection:10",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:8/section:55_3/subsection:2",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:8/section:55_3/subsection:2_1",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:8/section:55_3/subsection:2_2",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
            (
                "chapter:8/section:55_3/subsection:3",
                "The sole new amendment reference between 108072025061 and 107012026021 is "
                "107012026004, and replay applies only that act at the oracle cutoff 2026-07-01. "
                "Its Keskkonnatasude seaduse § 4 block compiles to 20 ops covering the early 2026 "
                "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, 25^1, 26, "
                "32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention §§ 21^2, 21^5, 32(8–10), "
                "55^3, or 68^5. Oracle 107012026021 nonetheless introduces the replay-diverging "
                "tuuleenergiast elektrienergia tootmise tasu cluster at this address."
            ),
        ),
    )

    inventory = get_ee_residual_inventory("108072025061", "107012026021")

    assert inventory is not None
    assert len(generated) == 17
    assert all(record.bucket == "source_pathology" for record in generated)
    assert [
        (record.address, record.bucket, record.evidence)
        for record in inventory.residuals
    ] == [
        (record.address, record.bucket, record.evidence)
        for record in generated
    ]


def test_get_ee_residual_inventory_korteriomandi_ja_korteriuhistuseadus() -> None:
    inventory = get_ee_residual_inventory("109102020005", "123122022004")

    assert inventory is None


def test_list_known_ee_residual_inventories_contains_active_non_zero_pairs() -> None:
    pairs = {
        (inventory.base_id, inventory.oracle_id)
        for inventory in list_known_ee_residual_inventories()
    }

    assert ("127122013026", "129102014007") in pairs
    assert ("108072011074", "127062017011") in pairs
    assert ("128122018041", "104122019022") in pairs
    assert ("126042013006", "111042014003") in pairs
    assert ("105122014039", "114032025013") in pairs
    assert ("108112012002", "103072014023") in pairs
    assert ("109042021007", "114032025016") not in pairs
    assert ("106052020036", "103022026013") in pairs
    assert ("121052014030", "121052014031") in pairs
    assert ("113022026005", "113022026006") not in pairs
    assert ("130042024004", "112122024004") not in pairs
    assert ("119032013007", "112072014164") in pairs
    assert ("106052020038", "127092023012") not in pairs
    assert ("193936", "13336397") in pairs
    assert ("111112025007", "130122025006") not in pairs
    assert ("108072025061", "107012026021") in pairs
    assert ("104122024013", "128012026005") in pairs
    assert ("121122010026", "121122010027") in pairs
    assert ("109102020005", "123122022004") not in pairs


def test_cli_parser_accepts_ee_residual_inventory() -> None:
    parser = cli._build_parser()

    args = parser.parse_args(
        [
            "ee-residual-inventory",
            "--base-id",
            "193936",
            "--oracle-id",
            "13336397",
            "--json",
        ]
    )

    assert args.command == "ee-residual-inventory"
    assert args.base_id == "193936"
    assert args.oracle_id == "13336397"
    assert args.json is True


def test_ee_residual_inventory_main_prints_summary(capsys) -> None:
    ee_residual_inventory.main(Namespace(base_id="193936", oracle_id="13336397", json=False))

    out = capsys.readouterr().out
    assert "=== EE Residual Inventory ===" in out
    assert "193936 -> 13336397  Liiklusseadus  residuals=7" in out
    assert "appendix_display_pathology=1" in out
    assert "source_oracle_drift=6" in out
    assert "chapter:15/section:79/subsection:4  [appendix_display_pathology]" in out


def test_ee_residual_inventory_main_emits_json(capsys) -> None:
    ee_residual_inventory.main(Namespace(base_id="193936", oracle_id="13336397", json=True))

    out = capsys.readouterr().out
    assert '"base_id": "193936"' in out
    assert '"oracle_id": "13336397"' in out
    assert '"residual_count": 7' in out
    assert '"appendix_display_pathology": 1' in out
