from __future__ import annotations
from lawvm.core.ir import IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_to_text

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import ingest_consolidated, verify_consistency
from lawvm.estonia.compare import (
    EENormalizationRuleClass,
    get_ee_comparison_normalization_rule_classes,
    get_ee_comparison_normalization_rules,
    get_ee_comparison_non_silent_normalization_rule_classes,
    get_ee_comparison_non_silent_normalization_rules,
    irnode_to_ee_comparison_text,
    normalize_ee_comparison_text,
)


def test_normalize_ee_comparison_text_cleans_editorial_noise_only() -> None:
    text = (
        "sõidu\xadmeeriku 18?aastasele B?kategooria D- kategooria "
        "[ RT I 2002, 38, 234 artikli 7 lõigetes 1–11ja 12a "
        "sõidumeerikuganõukogu määruse"
    )

    assert normalize_ee_comparison_text(text) == (
        "sõidumeeriku 18-aastasele B-kategooria D-kategooria "
        "[RT I 2002, 38, 234 artikli 7 lõigetes 1–11 ja 12a "
        "sõidumeerikuga nõukogu määruse"
    )


def test_normalize_ee_comparison_text_closes_en_dash_digit_spacing_gap() -> None:
    text = "käesoleva paragrahvi lõigetes 1– 2 1 sätestatud määras"

    assert normalize_ee_comparison_text(text) == "käesoleva paragrahvi lõigetes 1–2 1 sätestatud määras"


def test_normalize_ee_comparison_text_restores_space_after_period_before_ja() -> None:
    text = "käesoleva seaduse § 57 9 4.ja 5. lõiget"

    assert normalize_ee_comparison_text(text) == "käesoleva seaduse § 57 9 4. ja 5. lõiget"


def test_normalize_ee_comparison_text_normalizes_section_sign_structural_dash() -> None:
    assert normalize_ee_comparison_text("alusharidusseaduse §‑des 50–52 sätestatut") == (
        "alusharidusseaduse §-des 50–52 sätestatut"
    )


def test_normalize_ee_comparison_text_unifies_figure_dash_with_en_dash() -> None:
    assert normalize_ee_comparison_text("§ 36 lõigetes 2‒4") == "§ 36 lõigetes 2–4"


def test_normalize_ee_comparison_text_normalizes_numeric_range_hyphen() -> None:
    assert normalize_ee_comparison_text("punktid 3-6 ja vastuvõttu 80-100 protsendi") == (
        "punktid 3–6 ja vastuvõttu 80–100 protsendi"
    )


def test_normalize_ee_comparison_text_collapses_formula_slash_spacing() -> None:
    assert normalize_ee_comparison_text("poorsusnäitaja O 90 /d 90 väiksem kui 5") == (
        "poorsusnäitaja O90/d90 väiksem kui 5"
    )


def test_normalize_ee_comparison_text_collapses_degree_spacing() -> None:
    assert normalize_ee_comparison_text("eesvoolu teljest 70–80 º nurga all") == (
        "eesvoolu teljest 70–80º nurga all"
    )


def test_normalize_ee_comparison_text_collapses_single_letter_formula_subscript_spacing() -> None:
    assert normalize_ee_comparison_text("suhtarvu (edaspidi O 90/d 90) alusel") == (
        "suhtarvu (edaspidi O90/d90) alusel"
    )


def test_normalize_ee_comparison_text_normalizes_leading_footnote_marker_spacing() -> None:
    assert normalize_ee_comparison_text("1 Põllumajandusministeeriumi kogumik") == (
        "1Põllumajandusministeeriumi kogumik"
    )
    assert normalize_ee_comparison_text("eelmine lause. 1 Põllumajandusministeeriumi kogumik") == (
        "eelmine lause. 1Põllumajandusministeeriumi kogumik"
    )


def test_normalize_ee_comparison_text_normalizes_standard_identifier_dash() -> None:
    assert normalize_ee_comparison_text("standardi EVS 906 ja EVS-EN 16798–1 nõudeid") == (
        "standardi EVS 906 ja EVS-EN 16798-1 nõudeid"
    )


def test_normalize_ee_comparison_text_normalizes_alphanumeric_phrase_dash_surfaces() -> None:
    assert normalize_ee_comparison_text("juhul – ringhäälinguloa") == "juhul-ringhäälinguloa"
    assert normalize_ee_comparison_text("ja-programmide") == "ja-programmide"


def test_normalize_ee_comparison_text_normalizes_quote_style_surfaces() -> None:
    assert normalize_ee_comparison_text('Riigiettevõtte «Eesti Telekommunikatsioonid»') == (
        'Riigiettevõtte "Eesti Telekommunikatsioonid"'
    )


def test_normalize_ee_comparison_text_normalizes_inline_superscript_section_suffixes() -> None:
    assert normalize_ee_comparison_text("§-des 45¹–45³ sätestatud") == "§-des 45 1–45 3 sätestatud"


def test_normalize_ee_comparison_text_drops_leading_orphan_subsection_parenthesis() -> None:
    assert normalize_ee_comparison_text(") Lisaks sellele") == "Lisaks sellele"
    assert normalize_ee_comparison_text("Eelmine lause. ) Lisaks sellele") == "Eelmine lause. Lisaks sellele"


def test_normalize_ee_comparison_text_normalizes_ascii_one_third_list_fraction() -> None:
    assert normalize_ee_comparison_text("rakendatakse 33 1/3-list protsendimäära") == (
        "rakendatakse 33 ⅓-list protsendimäära"
    )


def test_normalize_ee_comparison_text_empties_valja_jaetud_placeholder() -> None:
    assert normalize_ee_comparison_text("[Välja jäetud]") == ""


def test_normalize_ee_comparison_text_empties_kaesolevast_tekstist_valja_jaetud_placeholder() -> None:
    assert normalize_ee_comparison_text("[Käesolevast tekstist välja jäetud.]") == ""


def test_normalize_ee_comparison_text_empties_bare_dash_placeholder() -> None:
    assert normalize_ee_comparison_text("–") == ""


def test_normalize_ee_comparison_text_fixes_fused_dash_before_kuni() -> None:
    text = "kuriteo jälitustoimikud-kuni kuriteo aegumistähtaja möödumiseni;"

    assert normalize_ee_comparison_text(text) == (
        "kuriteo jälitustoimikud – kuni kuriteo aegumistähtaja möödumiseni;"
    )


def test_normalize_ee_comparison_text_cleans_bounded_liiklusseadus_surface_artifacts() -> None:
    text = (
        "ööpäevase sõiduaja nõuete rikkumine; ööpäevase puhkeaja nõuete rikkumine; "
        "kuni kaks tundi lühema ööpäevase puhkeaja kasutamine; "
        "paigaldatudmehaanilise või digitaalsesõidumeeriku salvestuslehtede kasutamine"
    )

    assert normalize_ee_comparison_text(text) == (
        "ööpäevase sõiduajanõuete rikkumine; ööpäevase puhkeajanõuete rikkumine; "
        "kuni kaks tundilühema ööpäevase puhkeaja kasutamine; "
        "paigaldatud mehaanilise või digitaalse sõidumeeriku salvestuslehtede kasutamine"
    )


def test_normalize_ee_comparison_text_collapses_bounded_committee_dash_surface() -> None:
    text = "valitsusasutuse ametnike konkursi-ja atesteerimiskomisjon – ministeeriumide"

    assert normalize_ee_comparison_text(text) == (
        "valitsusasutuse ametnike konkursi-ja atesteerimiskomisjon-ministeeriumide"
    )


def test_normalize_ee_comparison_text_reconciles_bounded_politseiasutus_first_sentence_surface() -> None:
    text = (
        "Politseiasutuse avalikule teenistujale ei kohaldata valveaja rakendamisel "
        "töölepingu seaduse §-s 48 sätestatut."
    )

    assert normalize_ee_comparison_text(text) == (
        "Politsei-ja Piirivalveameti avalikule teenistujale ei kohaldata valveaja "
        "rakendamisel töölepingu seaduse §-s 48 sätestatut."
    )


def test_normalize_ee_comparison_text_reconciles_bounded_politsei_plural_genitive_surface() -> None:
    text = "Politsei-ja Piirivalveametite avalikele teenistujatele."

    assert normalize_ee_comparison_text(text) == (
        "Politsei-ja Piirivalveameti avalikele teenistujatele."
    )


def test_ee_comparison_normalization_rules_expose_explicit_buckets() -> None:
    rule_classes = get_ee_comparison_normalization_rule_classes()
    rules = get_ee_comparison_normalization_rules()
    non_silent_rule_classes = get_ee_comparison_non_silent_normalization_rule_classes()
    non_silent_rules = get_ee_comparison_non_silent_normalization_rules()

    assert rule_classes == tuple(EENormalizationRuleClass)
    assert non_silent_rule_classes == (
        EENormalizationRuleClass.lexical_institutional_drift,
        EENormalizationRuleClass.manual_exception,
    )
    assert len(non_silent_rules) == 2
    assert all(
        rule.rule_class in non_silent_rule_classes
        for rule in non_silent_rules
    )
    assert {
        EENormalizationRuleClass.encoding_layout,
        EENormalizationRuleClass.punctuation,
        EENormalizationRuleClass.placeholder_equivalence,
        EENormalizationRuleClass.lexical_institutional_drift,
        EENormalizationRuleClass.manual_exception,
    } <= set(rule_classes)
    assert any(rule.rule_class == EENormalizationRuleClass.encoding_layout and rule.name == "soft_hyphen" for rule in rules)
    assert any(rule.rule_class == EENormalizationRuleClass.punctuation and rule.name == "committee_dash" for rule in rules)
    assert any(
        rule.rule_class == EENormalizationRuleClass.placeholder_equivalence and rule.name == "bare_dash_placeholder"
        for rule in rules
    )
    assert any(
        rule.rule_class == EENormalizationRuleClass.lexical_institutional_drift and rule.name == "politseiasutus_rename"
        for rule in rules
    )
    assert {
        rule.name for rule in non_silent_rules
    } == {
        "politseiasutus_rename",
        "politsei_plural_rename",
    }


def test_verify_consistency_accepts_text_normalizer() -> None:
    addr = LegalAddress(path=(("section", "1"),))
    ops_tl = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="18-aastasele"),
                )
            ],
        )
    }
    oracle = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="18?aastasele"),),
        ),
    )
    con_tl = ingest_consolidated(oracle, as_of="0000-00-00")

    raw_divs = verify_consistency(
        ops_tl,
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_text,
    )
    assert len(raw_divs) == 1

    norm_divs = verify_consistency(
        ops_tl,
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_text,
        text_normalizer=normalize_ee_comparison_text,
    )
    assert norm_divs == []


def test_irnode_to_ee_comparison_text_empties_kehtetu_section_title_stub() -> None:
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="85_7",
        text="Tasu või hüve seoses korralduse edastamisega",
        attrs={"kehtetu": True},
        children=(),
    )

    assert irnode_to_ee_comparison_text(node) == ""


def test_irnode_to_ee_comparison_text_empties_titled_placeholder_section_stub() -> None:
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="183",
        text="Muudatused varasemates õigusaktides",
        children=(IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="[Käesolevast tekstist välja jäetud.]",
            ),),
    )

    assert irnode_to_ee_comparison_text(node) == ""


def test_irnode_to_ee_comparison_text_omits_kehtetu_child_section_text_from_parent_serialization() -> None:
    node = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="2",
        text="NOORSOOTÖÖ KORRALDAMINE",
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="13",
                text="Riiklik või haldusjärelevalve projektlaagri üle",
            ),
            IRNode(
                kind=IRNodeKind.SECTION,
                label="13_1",
                text="Riikliku järelevalve erimeetmed",
                attrs={"kehtetu": True},
                children=(),
            ),
        ),
    )

    assert irnode_to_ee_comparison_text(node) == (
        "NOORSOOTÖÖ KORRALDAMINE Riiklik või haldusjärelevalve projektlaagri üle"
    )


def test_normalize_ee_comparison_text_fixes_fused_heakskiitmist_surface() -> None:
    assert normalize_ee_comparison_text(
        "Enne muudatusteheakskiitmist Vabariigi Valitsuses."
    ) == "Enne muudatuste heakskiitmist Vabariigi Valitsuses."


def test_verify_consistency_can_treat_missing_and_empty_kehtetu_stub_as_equal() -> None:
    oracle = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="147_1",
                    text="Kellade sünkroniseerimine",
                    attrs={"kehtetu": True},
                    children=(),
                ),),
        ),
    )
    con_tl = ingest_consolidated(oracle, as_of="0000-00-00")

    raw_divs = verify_consistency(
        {},
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_ee_comparison_text,
        text_normalizer=normalize_ee_comparison_text,
    )
    assert len(raw_divs) == 1

    norm_divs = verify_consistency(
        {},
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_ee_comparison_text,
        text_normalizer=normalize_ee_comparison_text,
        missing_equals_empty=True,
    )
    assert norm_divs == []


def test_verify_consistency_treats_valja_jaetud_subsection_and_empty_oracle_as_equal() -> None:
    section_addr = LegalAddress(path=(("section", "5"),))
    addr = LegalAddress(path=(("section", "5"), ("subsection", "1")))
    ops_tl = {
        section_addr: ProvisionTimeline(
            address=section_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text=""),
                )
            ],
        ),
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    content=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="[Välja jäetud]"),
                )
            ],
        )
    }
    oracle = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="5",
                    text="",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=""),),
                ),),
        ),
    )
    con_tl = ingest_consolidated(oracle, as_of="0000-00-00")

    norm_divs = verify_consistency(
        ops_tl,
        con_tl,
        as_of="0000-00-00",
        irnode_to_text=irnode_to_ee_comparison_text,
        text_normalizer=normalize_ee_comparison_text,
    )
    assert norm_divs == []
